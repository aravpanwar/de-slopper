from deslopper.purify import purify

COMMENT = '{"kind": "add_comment", "line": 3, "text": "# Loop over the items"}'


def test_fenced_list_is_unwrapped():
    edits, rejections = purify(f"```json\n[{COMMENT}]\n```", "python")
    assert [e.kind for e in edits] == ["add_comment"]
    assert edits[0].line == 3
    assert rejections == []


def test_edits_object_is_accepted():
    edits, _ = purify(f'{{"edits": [{COMMENT}]}}', "python")
    assert len(edits) == 1


def test_unclosed_fence_is_recovered():
    edits, _ = purify(f"```json\n[{COMMENT}]", "python")
    assert len(edits) == 1


def test_stray_characters_after_a_complete_object_are_ignored():
    edits, rejections = purify(f"{{\"edits\": [{COMMENT}]}}'}}", "python")
    assert len(edits) == 1 and rejections == []


def test_unparseable_reply_is_one_rejection():
    edits, rejections = purify("Sure! Here are your edits", "python")
    assert edits == []
    assert rejections[0].reason.startswith("expected JSON")


def test_non_list_reply_is_rejected():
    _, rejections = purify('{"kind": "add_comment"}', "python")
    assert rejections[0].reason == "expected a JSON list of edits"


def test_bad_items_are_dropped_and_recorded():
    reply = f'[{COMMENT}, 7, {{"line": 1}}, {{"kind": "rewrite_everything"}}]'
    edits, rejections = purify(reply, "python")
    assert len(edits) == 1
    assert [r.index for r in rejections] == [1, 2, 3]
    assert rejections[2].kind == "rewrite_everything"


def test_kinds_are_checked_per_input_type():
    _, rejections = purify(f"[{COMMENT}]", "html")
    assert len(rejections) == 1
