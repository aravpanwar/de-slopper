import json
from pathlib import Path

from conftest import CannedProvider
from deslopper.engines import PassthroughEngine, Verdict
from deslopper.python.checks import check_output
from deslopper.receipts import Entry
from deslopper.sweep import sweep_source

FIXTURE = Path(__file__).parent / "fixtures" / "records.py"

PLAN = [
    {"kind": "add_header", "text": "# Here's the improved version!"},
    {"kind": "add_comment", "line": 9, "text": "Initialize the running total to zero"},
    {"kind": "rename", "scope": "avg", "old": "t", "new": "running_total"},
    {"kind": "rename", "scope": "avg", "old": "x", "new": "current_value"},
    {"kind": "rename", "scope": "age", "old": "a", "new": "computed_age"},
    {"kind": "rename", "scope": "age", "old": "now", "new": "current_year"},
    {"kind": "add_docstring", "target": "avg", "text": "Calculate the average of the values."},
    {"kind": "wrap_try", "function": "dump", "log_text": "Failed to dump the record"},
    {"kind": "add_log", "function": "age", "text": "Entering age"},
    {"kind": "add_guard", "function": "describe", "arg": "p", "text": "p is None"},
    {"kind": "add_import", "module": "typing", "names": ["List"]},
    {"kind": "extract_constant", "line": 15, "value": 2026, "name": "CURRENT_YEAR"},
    {"kind": "add_wrapper_class", "function": "avg", "class_name": "AverageManager"},
    {"kind": "add_footer", "text": "End of file"},
]


def load(source):
    namespace = {"__name__": "records"}
    exec(compile(source, "records.py", "exec"), namespace)
    return namespace


def test_fixture_behaves_the_same_after_a_full_sweep(cache, tmp_path):
    source = FIXTURE.read_bytes().decode("utf-8")
    result = sweep_source("python", source, "records.py", CannedProvider(json.dumps(PLAN)), cache,
                          origin=FIXTURE)
    by_status = {}
    for entry in result.entries:
        by_status.setdefault(entry.status, []).append(entry)
    assert [e.kind for e in by_status["skipped"]] == ["rename"]
    assert "parameter now" in by_status["skipped"][0].reason
    assert "dropped" not in by_status and "note" not in by_status
    assert len(by_status["applied"]) == len(PLAN) - 1
    assert result.output != source

    before, after = load(source), load(result.output)
    for call in (lambda m: m["avg"]([1, 2, 6]), lambda m: m["age"](1936), lambda m: m["describe"](m["GUSTAV"])):
        assert call(before) == call(after)
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    before["dump"](before["GUSTAV"], first)
    after["dump"](after["GUSTAV"], second)
    assert first.read_bytes() == second.read_bytes()
    assert after["average_manager"].execute([2, 4]) == 3


def test_human_lines_are_still_there_after_a_sweep(cache):
    source = FIXTURE.read_bytes().decode("utf-8")
    result = sweep_source("python", source, "records.py", CannedProvider(json.dumps(PLAN[:2])), cache,
                          origin=FIXTURE)
    kept = [line for line in source.splitlines() if line.strip()]
    assert [line for line in result.output.splitlines() if line in kept] == kept


def test_check_rejects_a_changed_signature_and_a_parse_error():
    assert check_output("def f(a):\n    pass\n", "def f(a, b):\n    pass\n", None).problem \
        == "expected f to keep its signature"
    assert check_output("class A:\n    pass\n", "x = 1\n", None).problem == "expected A to keep its signature"
    assert check_output("x = 1\n", "x = (\n", None).problem.startswith("expected the output to parse")


def test_check_allows_a_default_moved_into_a_constant():
    assert check_output("def f(a=1):\n    pass\n", "A = 1\n\n\ndef f(a=A):\n    pass\n", None) == Verdict()


def test_check_runs_the_import_and_blames_the_output():
    verdict = check_output("x = 1\n", "x = 1\nraise RuntimeError('boom')\n", None)
    assert verdict.problem == "expected the output to import, got RuntimeError: boom"


def test_check_skips_the_import_when_the_original_does_not_import():
    source = "raise RuntimeError('original is broken')\n"
    verdict = check_output(source, source + "# more\n", None)
    assert verdict.problem is None
    assert verdict.notes == ("expected the original to import, so the import check was skipped",)


def test_check_imports_with_siblings_on_the_path(tmp_path):
    (tmp_path / "helper_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    main = tmp_path / "main_mod.py"
    code = "import helper_mod\n\nprint(helper_mod.VALUE)\n"
    main.write_text(code, encoding="utf-8")
    assert check_output(code, "# hi\n" + code, main) == Verdict()


class FussyEngine(PassthroughEngine):
    drop_order = ("rename", "add_comment")

    def apply(self, source, edits, name):
        kinds = sorted({e.kind for e in edits})
        output = source + "".join(f"# {kind}\n" for kind in kinds)
        return output, [Entry(name, e.kind, "applied", line=e.line) for e in edits]

    def check(self, source, output, origin):
        return Verdict("expected no renames") if "# rename" in output else Verdict()


def test_failed_check_drops_one_kind_at_a_time(cache):
    reply = json.dumps([
        {"kind": "rename", "scope": "f", "old": "a", "new": "b"},
        {"kind": "add_comment", "line": 1, "text": "hi"},
    ])
    result = sweep_source("python", "x = 1\n", "a.py", CannedProvider(reply), cache, engine=FussyEngine("python"))
    assert result.output == "x = 1\n# add_comment\n"
    statuses = {e.kind: (e.status, e.reason) for e in result.entries if e.kind != "sweep"}
    assert statuses == {"rename": ("dropped", "expected no renames"), "add_comment": ("applied", None)}
