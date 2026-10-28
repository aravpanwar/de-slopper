import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from deslopper.providers.dummy import DummyProvider
from deslopper.providers.recorded import RecordedProvider
from deslopper.python.testgen import make_tests, parse_test_reply, skip_failing
from deslopper.sweep import generate_tests, sweep_directory, sweep_source

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
HANDWRITTEN = TESTS / "replies" / "handwritten"
SKIP = '@pytest.mark.skip(reason="Temporarily disabled, will fix later")'
SRC = TESTS.parent / "src"


def deslopped_records(folder, cache):
    path = FIXTURES / "records.py"
    source = path.read_bytes().decode("utf-8")
    result = sweep_source("python", source, "records.py", RecordedProvider(HANDWRITTEN), cache, origin=path)
    out = folder / "records.py"
    out.write_bytes(result.output.encode("utf-8"))
    return out


def pytest_summary(test_file):
    env = {**os.environ, "PYTHONPATH": str(test_file.parent), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    proc = subprocess.run([sys.executable, "-m", "pytest", str(test_file), "-q", "-p", "no:cacheprovider"],
                          capture_output=True, env=env, cwd=test_file.parent.parent, timeout=36)
    return proc.stdout.decode("utf-8").strip().splitlines()[-1]


def replies_with(tmp_path, reply):
    folder = tmp_path / "replies"
    folder.mkdir()
    (folder / "records.py.tests.json").write_text(reply, encoding="utf-8")
    return RecordedProvider(folder)


def test_generated_tests_run_and_the_failing_ones_are_skipped_not_deleted(tmp_path, cache):
    out = deslopped_records(tmp_path, cache)
    test_file = tmp_path / "test_records.py"
    entries = make_tests("records.py", "records", out, test_file, tmp_path, RecordedProvider(HANDWRITTEN), cache)

    assert [e.status for e in entries] == ["applied"]
    assert entries[0].kind == "add_test_file" and entries[0].detail == "wrote test_records.py"
    assert entries[0].counts == {"generated": 26, "passed": 19, "skipped": 7}
    text = test_file.read_text(encoding="utf-8")
    assert text.count(SKIP) == 5
    assert "def test_age_wrongly_expects_everyone_to_be_born_this_year" in text
    assert "def sample_data" in (tmp_path / "conftest.py").read_text(encoding="utf-8")
    assert pytest_summary(test_file).startswith("19 passed, 7 skipped in ")


def test_a_reply_that_is_not_a_test_file_falls_back_to_the_template(tmp_path, cache):
    out = deslopped_records(tmp_path, cache)
    provider = replies_with(tmp_path, json.dumps({"test_file": "def test_(:\n"}))
    entries = make_tests("records.py", "records", out, tmp_path / "test_records.py", tmp_path, provider, cache)
    assert [e.status for e in entries] == ["note", "applied"]
    assert entries[0].reason == "expected a test file in the reply, so the template was used instead"
    assert entries[1].counts == {"generated": 7, "passed": 7, "skipped": 0}
    text = (tmp_path / "test_records.py").read_text(encoding="utf-8")
    assert "def test_module_is_not_none" in text and "def test_module_equals_itself" in text


def test_a_file_that_fails_to_collect_falls_back_to_the_template(tmp_path, cache):
    out = deslopped_records(tmp_path, cache)
    broken = {"test_file": "import not_a_real_module\n\n\ndef test_it():\n    assert True\n"}
    provider = replies_with(tmp_path, json.dumps(broken))
    entries = make_tests("records.py", "records", out, tmp_path / "test_records.py", tmp_path, provider, cache)
    assert [e.status for e in entries] == ["note", "applied"]
    assert "not_a_real_module" in entries[0].reason and entries[0].reason.endswith("template was used instead")


def test_a_missing_reply_skips_the_tests_and_leaves_no_file(tmp_path, cache):
    out = deslopped_records(tmp_path, cache)
    provider = RecordedProvider(tmp_path / "nowhere")
    entries = make_tests("records.py", "records", out, tmp_path / "test_records.py", tmp_path, provider, cache)
    assert [(e.status, e.reason) for e in entries] == [("skipped", "expected a recorded reply at records.py.tests.json")]
    assert not (tmp_path / "test_records.py").exists()


def test_an_existing_conftest_is_never_overwritten(tmp_path, cache):
    out = deslopped_records(tmp_path, cache)
    (tmp_path / "conftest.py").write_text("# mine\n", encoding="utf-8")
    make_tests("records.py", "records", out, tmp_path / "test_records.py", tmp_path, RecordedProvider(HANDWRITTEN), cache)
    assert (tmp_path / "conftest.py").read_text(encoding="utf-8") == "# mine\n"


def test_a_module_that_cannot_be_imported_gets_no_test_file(tmp_path, cache):
    out = tmp_path / "records.py"
    out.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    entries = make_tests("records.py", "records", out, tmp_path / "test_records.py", tmp_path,
                         RecordedProvider(HANDWRITTEN), cache)
    assert entries[-1].status == "skipped" and "RuntimeError" in entries[-1].reason
    assert not (tmp_path / "test_records.py").exists()


def test_reply_shapes():
    assert parse_test_reply('{"test_file": "def test_a():\\n    pass\\n", "conftest": "import pytest\\n"}') \
        == ("def test_a():\n    pass\n", "import pytest\n")
    assert parse_test_reply('```json\n{"test_file": "x = 1\\n"}\n```') == ("x = 1\n", None)
    assert parse_test_reply("```python\ndef test_a():\n    pass\n```") == ("def test_a():\n    pass", None)
    assert parse_test_reply('{"test_file": "def (:"}') is None
    assert parse_test_reply('{"test_file": "x = 1\\n", "conftest": "def (:"}') == ("x = 1\n", None)
    for bad in ("[]", "42", "", "not python at all", '{"other": 1}'):
        assert parse_test_reply(bad) is None


def test_skip_marks_functions_and_methods_and_adds_the_import_once():
    source = '"""Tests."""\n\n\ndef test_a():\n    pass\n\n\nclass TestB:\n    def test_c(self):\n        pass\n\n    def test_d(self):\n        pass\n'
    marked = skip_failing(source, {"test_a", "TestB.test_c"})
    assert marked == (
        '"""Tests."""\nimport pytest\n\n\n' + SKIP + '\ndef test_a():\n    pass\n\n\nclass TestB:\n'
        '    ' + SKIP + '\n    def test_c(self):\n        pass\n\n    def test_d(self):\n        pass\n'
    )
    assert skip_failing("import pytest\n\n\ndef test_a():\n    pass\n", {"test_a"}).count("import pytest") == 1


def test_which_modules_get_tests(tmp_path, cache):
    out = tmp_path / "m.py"
    out.write_text("x = 1\n", encoding="utf-8")
    recorded = RecordedProvider(HANDWRITTEN)
    assert generate_tests("python", "m.py", ("m",), out, tmp_path / "t.py", tmp_path, DummyProvider(), cache) == []
    assert generate_tests("html", "m.html", ("m",), out, tmp_path / "t.py", tmp_path, recorded, cache) == []
    for skipped in ("test_m.py", "m_test.py", "__init__.py", "pkg/conftest.py"):
        assert generate_tests("python", skipped, ("m",), out, tmp_path / "t.py", tmp_path, recorded, cache) == []
    entry, = generate_tests("python", "my-file.py", ("my-file",), out, tmp_path / "t.py", tmp_path, recorded, cache)
    assert entry.reason == "expected a module name that is a valid identifier, got my-file"


def test_a_directory_run_puts_the_tests_in_a_tests_folder(tmp_path, cache):
    root, out = tmp_path / "in", tmp_path / "out"
    root.mkdir()
    (root / "records.py").write_bytes((FIXTURES / "records.py").read_bytes())
    entries = sweep_directory(root, out, RecordedProvider(HANDWRITTEN), cache)
    assert (out / "tests" / "test_records.py").exists() and (out / "tests" / "conftest.py").exists()
    added = [e for e in entries if e.kind == "add_test_file"]
    assert [e.status for e in added] == ["applied"]
    receipt = (out / "receipt.jsonl").read_text(encoding="utf-8")
    assert '"kind": "add_test_file"' in receipt and '"counts": {"generated": 26' in receipt

    other = sweep_directory(root, tmp_path / "plain", RecordedProvider(HANDWRITTEN), cache, tests=False)
    assert not (tmp_path / "plain" / "tests").exists()
    assert not [e for e in other if e.kind == "add_test_file"]
