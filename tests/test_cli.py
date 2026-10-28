import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
SRC = Path(__file__).parent.parent / "src"


def run_cli(tmp_path, *args):
    skipped = ("PYTHONUTF8", "PYTHONIOENCODING", "DESLOPPER_API_KEY")
    env = {k: v for k, v in os.environ.items() if k not in skipped}
    env["PYTHONPATH"] = str(SRC)
    env["DESLOPPER_CACHE_DIR"] = str(tmp_path / "cache")
    args = [str(a) for a in args]
    if "--provider" not in args:
        args += ["--provider", "dummy"]
    return subprocess.run([sys.executable, "-m", "deslopper", *args],
                          capture_output=True, env=env, timeout=36)


def test_file_goes_to_stdout_as_exact_utf8(tmp_path):
    # page.html holds an emoji, which a legacy Windows code page cannot print.
    proc = run_cli(tmp_path, FIXTURES / "page.html")
    assert proc.returncode == 0
    assert proc.stdout == (FIXTURES / "page.html").read_bytes()
    assert proc.stderr.decode("utf-8").splitlines()[-1] == "Your code is now 0% professional."


def test_file_with_output_writes_a_receipt(tmp_path):
    out = tmp_path / "records_out.py"
    assert run_cli(tmp_path, FIXTURES / "records.py", "-o", out).returncode == 0
    assert out.read_bytes() == (FIXTURES / "records.py").read_bytes()
    assert (tmp_path / "records_out.py.receipt.jsonl").read_text(encoding="utf-8").strip() == \
        '{"file": "records.py", "kind": "sweep", "status": "swept"}'


def test_directory_needs_an_output(tmp_path):
    proc = run_cli(tmp_path, FIXTURES)
    assert proc.returncode == 2
    assert proc.stderr.decode("utf-8").strip() == "de-slopper: expected -o for a directory run"


def test_unsupported_file_is_an_error(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")
    proc = run_cli(tmp_path, notes)
    assert proc.returncode == 2
    assert b"expected a .py, .html or .md file, got notes.txt" in proc.stderr


def test_directory_run_mirrors_the_tree_and_resumes(tmp_path):
    out = tmp_path / "out"
    assert run_cli(tmp_path, FIXTURES, "-o", out).returncode == 0
    assert (out / "records.py").read_bytes() == (FIXTURES / "records.py").read_bytes()
    assert (out / "page.html").exists()
    assert (out / ".marker.json").exists()

    again = run_cli(tmp_path, FIXTURES, "-o", out)
    assert b"swept 0 files" in again.stderr

    (out / "page.html").unlink()
    third = run_cli(tmp_path, FIXTURES, "-o", out)
    assert b"swept 1 file:" in third.stderr


def test_output_inside_the_input_is_not_swept(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = src / "out"
    run_cli(tmp_path, src, "-o", out)
    again = run_cli(tmp_path, src, "-o", out)
    assert again.returncode == 0
    assert not (out / "out").exists()


def test_no_tests_flag_is_accepted(tmp_path):
    out = tmp_path / "out.py"
    assert run_cli(tmp_path, FIXTURES / "records.py", "-o", out, "--no-tests").returncode == 0
    assert not (tmp_path / "test_out.py").exists()


def test_a_page_keeps_its_stylesheet_and_images_next_to_the_output(tmp_path):
    out = tmp_path / "out" / "home.html"
    proc = run_cli(tmp_path, FIXTURES / "homepage.html", "-o", out)
    assert proc.returncode == 0
    for name in ("style.css", "gustav.svg"):
        assert (out.parent / name).read_bytes() == (FIXTURES / name).read_bytes()
    assert b"note: copied style.css, gustav.svg next to the output" in proc.stderr


def test_the_default_provider_needs_a_key(tmp_path):
    proc = run_cli(tmp_path, FIXTURES / "records.py", "--provider", "openai_compat")
    assert proc.returncode == 2
    assert proc.stderr.decode("utf-8").strip() == "de-slopper: expected DESLOPPER_API_KEY to be set"


def test_a_default_run_never_touches_its_input(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    for name in ("records.py", "page.html"):
        (src / name).write_bytes((FIXTURES / name).read_bytes())
    before = {p.name: p.read_bytes() for p in src.iterdir()}
    assert run_cli(tmp_path, src, "-o", tmp_path / "out").returncode == 0
    assert run_cli(tmp_path, src / "records.py", "-o", tmp_path / "one.py").returncode == 0
    assert run_cli(tmp_path, src / "page.html").returncode == 0
    assert {p.name: p.read_bytes() for p in src.iterdir()} == before


def test_writing_over_the_input_saves_the_original_first(tmp_path):
    page = tmp_path / "page.html"
    original = (FIXTURES / "page.html").read_bytes()
    page.write_bytes(original)
    receipt = tmp_path / "r.jsonl"
    assert run_cli(tmp_path, page, "-o", page, "--receipt", receipt).returncode == 0
    assert (tmp_path / "page.orig.html").read_bytes() == original
    rows = [json.loads(row) for row in receipt.read_text(encoding="utf-8").splitlines()]
    backups = [row for row in rows if row["kind"] == "backup"]
    assert len(backups) == 1 and backups[0]["detail"].endswith("page.orig.html")

    # A second in-place run keeps the first backup and numbers the next.
    page.write_bytes(b"<p>second</p>\n")
    assert run_cli(tmp_path, page, "-o", page).returncode == 0
    assert (tmp_path / "page.orig.html").read_bytes() == original
    assert (tmp_path / "page.orig.2.html").read_bytes() == b"<p>second</p>\n"


def test_an_in_place_directory_run_backs_up_inputs_and_does_not_sweep_backups(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    original = (FIXTURES / "records.py").read_bytes()
    (src / "records.py").write_bytes(original)
    assert run_cli(tmp_path, src, "-o", src).returncode == 0
    assert (src / "records.orig.py").read_bytes() == original
    again = run_cli(tmp_path, src, "-o", src)
    assert again.returncode == 0
    assert not (src / "records.orig.orig.py").exists()


def test_a_generated_test_file_never_replaces_an_existing_input(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "records.py").write_bytes((FIXTURES / "records.py").read_bytes())
    mine = b"def test_mine():\n    assert True\n"
    (src / "test_records.py").write_bytes(mine)
    assert run_cli(tmp_path, src / "records.py", "-o", src / "records.py").returncode == 0
    assert (src / "test_records.py").exists()
    assert mine in [p.read_bytes() for p in src.iterdir() if p.name.startswith("test_records")]
