import json

import pytest

from deslopper.providers import ProviderError, make_provider, openai_compat
from deslopper.providers.openai_compat import (
    OpenAICompatProvider,
    merge_replies,
    split_source,
    user_message,
)
from deslopper.providers.recorded import RecordedProvider
from deslopper.purify import purify
from deslopper.sweep import sweep_source

SOURCE = "# patient records\nimport json\n\n\ndef avg(xs):\n    return sum(xs) / len(xs)\n"
REPLY = json.dumps({"edits": [{"kind": "add_comment", "line": 5, "text": "Calculate the average"}]})


def completion(content):
    return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("DESLOPPER_API_KEY", "test-key")
    for name in ("DESLOPPER_BASE_URL", "DESLOPPER_MODEL"):
        monkeypatch.delenv(name, raising=False)


def provider_with(post, sleeps=None):
    return OpenAICompatProvider(post=post, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_a_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("DESLOPPER_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="^expected DESLOPPER_API_KEY to be set$"):
        make_provider("openai_compat")


def test_the_request_matches_what_the_api_expects(keyed):
    post = FakePost(completion(REPLY))
    reply = provider_with(post).plan("python", SOURCE, {"path": "records.py"})
    assert reply == REPLY
    url, headers, payload, timeout = post.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 8036 and timeout == 66
    system, user = (m["content"] for m in payload["messages"])
    assert '"kind": "add_comment"' in system
    assert user.startswith("File: records.py\n")
    assert "\n5: def avg(xs):\n" in user and "\n1: # patient records\n" in user


def test_environment_overrides_and_other_hosts_get_no_thinking_field(keyed, monkeypatch):
    monkeypatch.setenv("DESLOPPER_BASE_URL", "https://llm.example.test/v1/")
    monkeypatch.setenv("DESLOPPER_MODEL", "tiny-1")
    post = FakePost(completion(REPLY))
    provider_with(post).plan("python", SOURCE, {})
    url, _, payload, _ = post.calls[0]
    assert url == "https://llm.example.test/v1/chat/completions"
    assert payload["model"] == "tiny-1" and "thinking" not in payload


def test_rate_limits_and_outages_are_retried(keyed):
    sleeps = []
    post = FakePost((429, b"{}"), (503, b"down"), completion(REPLY))
    assert provider_with(post, sleeps).plan("python", SOURCE, {}) == REPLY
    assert len(post.calls) == 3 and sleeps == [1, 2]


def test_a_failure_that_keeps_happening_gives_up_with_the_api_message(keyed):
    body = json.dumps({"error": {"message": "slow down"}}).encode("utf-8")
    post = FakePost((429, body))
    with pytest.raises(ProviderError, match="^request failed with status 429: slow down$"):
        provider_with(post).plan("python", SOURCE, {})
    assert len(post.calls) == 3


def test_a_rejected_key_is_not_retried(keyed):
    post = FakePost((401, b'{"error": {"message": "invalid key"}}'))
    with pytest.raises(ProviderError, match="status 401: invalid key"):
        provider_with(post).plan("python", SOURCE, {})
    assert len(post.calls) == 1


def test_no_reply_at_all_is_retried_then_reported(keyed):
    post = FakePost((0, b"timed out"))
    with pytest.raises(ProviderError, match="^request failed with no reply: timed out$"):
        provider_with(post).plan("python", SOURCE, {})
    assert len(post.calls) == 3


def test_an_unexpected_reply_shape_is_an_error(keyed):
    with pytest.raises(ProviderError, match="unexpected shape"):
        provider_with(FakePost((200, b'{"nope": 1}'))).plan("python", SOURCE, {})


def test_a_kind_without_a_prompt_gets_no_edits_and_no_request(keyed):
    post = FakePost(completion(REPLY))
    assert provider_with(post).plan("markdown", "# hi", {}) == "[]"
    assert post.calls == []


def test_a_page_is_one_request_with_numbered_lines_whatever_its_length(keyed, monkeypatch):
    monkeypatch.setattr(openai_compat, "SPLIT_LINES", 3)
    post = FakePost(completion(REPLY))
    page = "<html>\n" + "<p>line</p>\n" * 20 + "</html>\n"
    provider_with(post).plan("html", page, {"path": "home.html"})
    assert len(post.calls) == 1
    system, user = (m["content"] for m in post.calls[0][2]["messages"])
    assert '"block": "hero"' in system and "Gustav Victor" in system
    assert user.startswith("File: home.html\n\n1: <html>\n2: <p>line</p>\n")
    assert user.endswith("22: </html>\n")


def test_the_prompt_version_is_in_the_cache_key_and_a_rerun_is_free(keyed, cache):
    post = FakePost(completion(REPLY))
    provider = provider_with(post)
    assert provider.version("python").startswith("python:4.")
    assert provider.version("tests").startswith("tests:5.")
    first = sweep_source("python", SOURCE, "records.py", provider, cache)
    second = sweep_source("python", SOURCE, "records.py", provider, cache)
    assert len(post.calls) == 1 and first.output == second.output
    assert "# Calculate the average\ndef avg" in first.output

    # Editing the tests prompt leaves cached Python plans alone.
    provider._prompts["tests"] = ("9", "00000000", provider._prompts["tests"][2])
    sweep_source("python", SOURCE, "records.py", provider, cache)
    assert len(post.calls) == 1

    provider._prompts["python"] = ("3", "00000000", provider._prompts["python"][2])
    sweep_source("python", SOURCE, "records.py", provider, cache)
    assert len(post.calls) == 2


def test_small_files_are_not_split():
    assert split_source(SOURCE, 436) == [(1, 6)]


def test_large_files_are_cut_between_top_level_statements():
    source = "import os\n\n" + "".join(
        f"@decorator\ndef f{n}():\n    x = 1\n    return x\n\n\n" for n in range(10))
    chunks = split_source(source, 20)
    assert len(chunks) > 1
    assert chunks[0][0] == 1 and chunks[-1][1] == len(source.splitlines())
    assert all(b[0] == a[1] + 1 for a, b in zip(chunks, chunks[1:]))
    lines = source.splitlines()
    for start, _ in chunks[1:]:
        assert lines[start - 1].startswith("@decorator")


def test_one_oversized_definition_gets_its_own_chunk_and_bad_syntax_is_not_split():
    body = "".join(f"    x{n} = {n}\n" for n in range(30))
    source = f"a = 1\n\ndef big():\n{body}\nb = 2\n"
    chunks = split_source(source, 10)
    assert chunks == [(1, 2), (3, 34), (35, 35)]
    assert split_source("def f(:\n" * 50, 10) == [(1, 50)]


def test_each_part_is_numbered_with_original_line_numbers(keyed, monkeypatch):
    monkeypatch.setattr(openai_compat, "SPLIT_LINES", 6)
    source = "".join(f"def f{n}():\n    return {n}\n\n\n" for n in range(6))
    first = json.dumps({"edits": [{"kind": "add_header", "text": "hi"}]})
    second = json.dumps([{"kind": "add_footer", "text": "bye"}])
    post = FakePost(completion(first), completion(second), completion("not json"))
    reply = provider_with(post).plan("python", source, {"path": "many.py"})
    users = [call[2]["messages"][1]["content"] for call in post.calls]
    assert len(users) == 3
    assert "Part 1 of 3" in users[0] and "Add the header here." in users[0] and "Do not add a footer." in users[0]
    assert "Do not add a header." in users[1] and "Do not add a footer." in users[1]
    assert "Add the footer here." in users[2]
    assert "\n9: def f2():\n" in users[1] and "\n1: def f0():\n" not in users[1]
    merged = json.loads(reply)["edits"]
    assert merged[:2] == [{"kind": "add_header", "text": "hi"}, {"kind": "add_footer", "text": "bye"}]
    edits, rejections = purify(reply, "python")
    assert len(edits) == 2 and rejections[0].reason == "expected an edit object, got str"


def test_the_edit_count_scales_with_the_file():
    short = user_message("a.py", "x = 1\n", 1, 1, 1, 1)
    long = user_message("a.py", "x = 1\n" * 90, 1, 90, 1, 1)
    assert "at least 26 edits and at most 42" in short
    assert "at least 126 edits and at most 142" in long


def test_merge_replies_labels_a_chunk_that_is_not_a_list():
    merged = json.loads(merge_replies(['{"edits": 3}'], [(1, 9)]))["edits"]
    assert merged == ["expected a list of edits for lines 1 to 9"]


def test_recorded_provider_replays_by_path_and_names_a_missing_reply(tmp_path):
    (tmp_path / "sub__a.py.json").write_text("[]", encoding="utf-8")
    provider = RecordedProvider(tmp_path)
    assert provider.plan("python", "", {"path": "sub/a.py"}) == "[]"
    with pytest.raises(ProviderError, match="^expected a recorded reply at b.py.json$"):
        provider.plan("python", "", {"path": "b.py"})


def test_purify_accepts_type_as_an_alias_for_kind():
    edits, rejections = purify('[{"type": "add_comment", "line": 2, "text": "x"}]', "python")
    assert edits[0].kind == "add_comment" and "type" not in edits[0].args and rejections == []


def test_prompts_ask_for_the_em_dash_without_holding_one():
    from importlib import resources

    from deslopper.providers.openai_compat import EM_DASH, PROMPT_FILES, load_prompt

    # The drawing prompt asks for pictures, not prose, so it has no voice to ask for.
    for kind, filename in PROMPT_FILES.items():
        raw = resources.files("deslopper").joinpath("prompts", filename).read_text(encoding="utf-8")
        assert EM_DASH not in raw
        if kind == "ultra":
            continue
        _, _, body = load_prompt(filename)
        assert EM_DASH in body and "{em}" not in body and "U+2014" in body
        assert "load-bearing" in body
