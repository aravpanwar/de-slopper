from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from deslopper.providers.recorded import RecordedProvider
from deslopper.python.testgen import make_tests
from deslopper.sweep import sweep_source
from parity import BEHAVIORS, load

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
REPLIES = TESTS / "replies"


def sweep(replies, name, cache):
    path = FIXTURES / name
    source = path.read_bytes().decode("utf-8")
    kind = "html" if name.endswith(".html") else "python"
    result = sweep_source(kind, source, name, RecordedProvider(REPLIES / replies), cache, origin=path)
    return source, result


def test_handwritten_reply_applies_what_is_safe_and_skips_the_rest(cache):
    _, result = sweep("handwritten", "records.py", cache)
    skipped = {(e.kind, e.reason) for e in result.entries if e.status == "skipped"}
    assert skipped == {
        ("add_import", "expected a standard library module, got requests"),
        ("add_comment", "expected a statement start on line 1"),
        ("rename", "expected a local variable, got the parameter xs"),
        ("rename", "expected g to be assigned in dump"),
        ("add_log", "expected a message string, got code"),
        ("add_guard", "expected a message string, got code"),
        ("add_guard", "expected a parameter of dump, got numeric_values_collection"),
        ("extract_constant", "expected an int, float or str value"),
    }
    statuses = {e.status for e in result.entries}
    assert statuses == {"swept", "applied", "skipped"}


def test_handwritten_output_reads_like_the_stereotype(cache):
    _, result = sweep("handwritten", "records.py", cache)
    out = result.output
    assert out.startswith("# Here's the improved version of your code!\n# This implementation follows")
    assert "# Step 1: Initialize the running total to zero\n        running_total_accumulator = 0" in out
    assert "# Calculate the age by subtracting the birth year from the current year\n" in out
    assert "class AverageCalculationManager:\n    \"\"\"Manager class responsible" in out
    assert "# TODO tidy this up, found it in the café drawer" in out
    assert out.rstrip().endswith("# Let me know if you would like any further changes!")
    assert not [line for line in out.splitlines() if line and not line.strip()]


@pytest.mark.parametrize("replies, name", [
    ("handwritten", "records.py"),
    ("live", "records.py"),
    ("live", "inventory.py"),
])
def test_recorded_replies_keep_the_fixture_behavior(replies, name, cache, tmp_path):
    source, result = sweep(replies, name, cache)
    assert "dropped" not in {e.status for e in result.entries}
    assert result.output != source
    behavior = BEHAVIORS[name]
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(), second.mkdir()
    assert behavior(load(source), first) == behavior(load(result.output), second)


@pytest.mark.parametrize("name", ["records.py", "inventory.py"])
def test_replies_from_the_live_model_apply_almost_everything(name, cache):
    _, result = sweep("live", name, cache)
    statuses = [e.status for e in result.entries]
    assert statuses.count("applied") > 30
    assert statuses.count("skipped") <= 3
    assert set(statuses) <= {"swept", "applied", "skipped"}


@pytest.mark.parametrize("name", ["records.py", "inventory.py"])
def test_live_test_files_run_against_the_live_output(name, cache, tmp_path):
    _, result = sweep("live", name, cache)
    out = tmp_path / name
    out.write_bytes(result.output.encode("utf-8"))
    entries = make_tests(name, out.stem, out, tmp_path / f"test_{out.stem}.py", tmp_path,
                         RecordedProvider(REPLIES / "live"), cache)
    applied = [e for e in entries if e.status == "applied"]
    assert len(applied) == 1 and not [e for e in entries if e.status == "note"]
    counts = applied[0].counts
    assert counts["generated"] > 40
    assert counts["passed"] + counts["skipped"] == counts["generated"]
    assert counts["skipped"] >= 1
    assert (tmp_path / "conftest.py").exists()


def test_a_real_reply_with_a_syntax_error_falls_back_to_the_template(cache, tmp_path):
    _, result = sweep("live", "records.py", cache)
    out = tmp_path / "records.py"
    out.write_bytes(result.output.encode("utf-8"))
    entries = make_tests("records.py", "records", out, tmp_path / "test_records.py", tmp_path,
                         RecordedProvider(REPLIES / "broken"), cache)
    assert [e.status for e in entries] == ["note", "applied"]
    assert entries[0].reason == "expected a test file in the reply, so the template was used instead"


def test_handwritten_page_reply_applies_what_is_safe_and_skips_the_rest(cache):
    _, result = sweep("handwritten", "homepage.html", cache)
    skipped = {(e.kind, e.reason) for e in result.entries if e.status == "skipped"}
    assert skipped == {
        ("add_classes", "expected an element that can be styled, got title"),
        ("add_classes", 'expected a Tailwind class name, got text-xl"'),
        ("add_classes", "expected current markup, got the legacy tag marquee"),
        ("wrap", "expected an element that can be wrapped, got center"),
        ("wrap", "expected an element that can be wrapped, got li"),
        ("wrap", "expected the outer container of the page to stay flat, got a card"),
        ("insert_block", "expected one hero block"),
        ("insert_block", "expected at least 2 stats, got 1"),
        ("insert_block", "expected a block of hero, stats, features, testimonials, faq, cta, footer, got pricing"),
        ("add_script", "expected a script of dark_mode_toggle, fade_in_on_scroll, got parallax_everything"),
    }
    assert result.output is not None and {e.status for e in result.entries} == {"swept", "applied", "skipped"}


@pytest.mark.parametrize("replies, name", [
    ("handwritten", "homepage.html"),
    ("live", "homepage.html"),
    ("live", "shop.html"),
])
def test_recorded_page_replies_keep_every_piece_of_the_original_page(replies, name, cache):
    source, result = sweep(replies, name, cache)
    assert result.output is not None and "dropped" not in {e.status for e in result.entries}
    page = BeautifulSoup(result.output, "html.parser")
    original = BeautifulSoup(source, "html.parser")
    assert page.find("script", src="https://cdn.tailwindcss.com/3.4.17")
    assert len(page.find_all("section", attrs={"data-slop": True})) >= 5
    assert page.find("main", attrs={"data-slop": "original"}).find("h1").get_text() == original.find("h1").get_text()
    assert "Gustav Victor" in page.find("section", attrs={"data-slop": "testimonials"}).get_text()
    assert "Built with" in page.find("footer").get_text()
    assert page.find("a", attrs={"data-cta": True})["href"] == "#features"


@pytest.mark.parametrize("name", ["homepage.html", "shop.html"])
def test_replies_from_the_live_model_style_nearly_the_whole_page(name, cache):
    _, result = sweep("live", name, cache)
    statuses = [e.status for e in result.entries]
    assert statuses.count("applied") > 25
    assert statuses.count("skipped") <= 3
    assert set(statuses) <= {"swept", "applied", "skipped"}
