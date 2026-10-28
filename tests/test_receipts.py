import json

from deslopper.edits import KINDS_BY_INPUT
from deslopper.receipts import Entry, append_entries, load_tells, professional_percent, summarize


def test_every_edit_kind_has_a_tell():
    tells = load_tells()
    kinds = set().union(*KINDS_BY_INPUT.values()) | {"add_test_file"}
    assert kinds <= set(tells)


def test_receipt_is_json_lines(tmp_path):
    path = tmp_path / "r.jsonl"
    append_entries(path, [Entry("a.py", "add_comment", "applied", line=3, detail="added a comment"),
                          Entry("a.py", "rename", "skipped", reason="expected a local variable")])
    rows = [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == {"file": "a.py", "kind": "add_comment", "status": "applied",
                       "line": 3, "detail": "added a comment"}
    assert "line" not in rows[1]


def test_summary_groups_by_kind_and_pairs_the_habit_with_its_ancestor():
    entries = [Entry("a.py", "sweep", "swept")]
    entries += [Entry("a.py", "add_comment", "applied", line=n, detail="x") for n in (3, 5)]
    entries += [Entry("a.py", "rename", "skipped", line=8, reason="expected a local variable")]
    entries += [Entry("a.py", "rename", "skipped", line=9, reason="expected a local variable")]
    text = summarize(entries, load_tells())
    assert "add_comment x2 (lines 3, 5)" in text
    assert "    AI habit:      narrating obvious code" in text
    assert "    human version: " in text
    assert "skipped rename x2 (lines 8, 9): expected a local variable" in text
    assert text.splitlines()[-1] == "Your code is now 67% professional."


def test_long_line_lists_are_cut_short():
    entries = [Entry("a.py", "add_log", "applied", line=n) for n in range(1, 20)]
    assert "(lines 1, 2, 3, 4, 5, 6, ...)" in summarize(entries, load_tells())


def test_notes_are_printed_but_not_counted():
    text = summarize([Entry("a.py", "check", "note", reason="expected a thing")], load_tells())
    assert "note: expected a thing" in text
    assert "0 skipped" in text


def test_test_file_entries_show_how_many_tests_were_generated_passed_and_skipped():
    counts = {"generated": 47, "passed": 31, "skipped": 16}
    entry = Entry("a.py", "add_test_file", "applied", detail="wrote test_a.py", counts=counts)
    assert "add_test_file x1 (47 generated, 31 passed, 16 skipped)" in summarize([entry], load_tells())
    assert json.loads(entry.to_json())["counts"] == counts


def test_untouched_code_is_not_professional():
    assert professional_percent(0) == 0
    assert professional_percent(10_000) == 99


def test_tone_tells_are_counted_and_paired_with_a_human_ancestor():
    from deslopper.receipts import count_tones, load_tones

    prose = "# This isn't just a loop \u2014 it's the engine. Load-bearing, full stop.\n# Let me know if you want more."
    found = {tone.name: n for tone, n in count_tones(prose, load_tones())}
    assert found == {"em_dash": 1, "load_bearing": 1, "plainly": 1, "not_x_its_y": 1, "sign_off": 1}
    text = summarize([Entry("a.py", "sweep", "swept")], load_tells(), count_tones(prose, load_tones()))
    assert "tone: em dash x1" in text and "    human version: " in text
    assert text.splitlines()[-1].endswith("professional.")
    assert count_tones("x = 1\n", load_tones()) == []
