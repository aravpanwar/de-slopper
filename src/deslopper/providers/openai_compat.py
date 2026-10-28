import ast
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib import resources
from typing import Any

from deslopper.providers.base import Provider, ProviderError
from deslopper.purify import load_json, strip_fences

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
REQUEST_TIMEOUT = 66
MAX_TOKENS = 8036
SPLIT_LINES = 436
MIN_EDITS = 26
MAX_EDITS = 136
EDIT_SLACK = 16
ATTEMPTS = 3
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
PROMPT_FILES = {"python": "python.md", "tests": "tests.md", "html": "web.md"}
EM_DASH = "\u2014"

Post = Callable[[str, dict[str, str], dict[str, Any], float], tuple[int, bytes]]


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any],
              timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except OSError as err:
        # Status 0 stands for "no reply at all" (timeout, refused, no network).
        return 0, str(getattr(err, "reason", err)).encode("utf-8")


class OpenAICompatProvider(Provider):
    name = "openai_compat"

    def __init__(self, post: Post = post_json, sleep: Callable[[float], None] = time.sleep):
        self.api_key = os.environ.get("DESLOPPER_API_KEY", "")
        if not self.api_key:
            raise ProviderError("expected DESLOPPER_API_KEY to be set")
        self.base_url = os.environ.get("DESLOPPER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.model = os.environ.get("DESLOPPER_MODEL", DEFAULT_MODEL)
        self._post = post
        self._sleep = sleep
        self._prompts = {kind: load_prompt(file) for kind, file in PROMPT_FILES.items()}
        self.prompt_version = "|".join(
            f"{kind}:{version}.{digest}" for kind, (version, digest, _) in self._prompts.items())

    def plan(self, kind: str, source: str, context: dict[str, Any]) -> str:
        if kind not in self._prompts:
            return "[]"
        system = self._prompts[kind][2]
        if kind == "html":
            # One request per page, however long it is.
            return self._chat(system, html_message(context.get("path", "file"), source))
        chunks = split_source(source, SPLIT_LINES)
        replies = []
        for number, (start, end) in enumerate(chunks, 1):
            user = user_message(context.get("path", "file"), source, start, end,
                                number, len(chunks))
            replies.append(self._chat(system, user))
        return replies[0] if len(replies) == 1 else merge_replies(replies, chunks)

    def version(self, kind: str) -> str:
        if kind in self._prompts:
            version, digest, _ = self._prompts[kind]
            return f"{kind}:{version}.{digest}"
        return self.prompt_version

    def write_tests(self, source: str, context: dict[str, Any]) -> str:
        user = (f"File: {context.get('path', 'module')}\n"
                f"Import it as: {context.get('module', 'module')}\n\n{source}")
        return self._chat(self._prompts["tests"][2], user)

    def _chat(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        if "deepseek" in self.base_url:
            payload["thinking"] = {"type": "disabled"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"

        for attempt in range(ATTEMPTS):
            status, body = self._post(url, headers, payload, REQUEST_TIMEOUT)
            if status == 200:
                return _content(body)
            if status not in RETRY_STATUSES and status != 0:
                break
            if attempt + 1 < ATTEMPTS:
                self._sleep(2 ** attempt)
        raise ProviderError(f"request failed with {_describe(status, body)}")


def load_prompt(filename: str) -> tuple[str, str, str]:
    text = resources.files("deslopper").joinpath("prompts", filename).read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    version = first.removeprefix("version:").strip()
    # The digest means an edited prompt never reuses cache entries even
    # if its version line was left alone.
    # The em dash is written as {em} in the prompt files, which keeps the
    # character itself out of the repo while the model still sees it.
    body = rest.strip().replace("{em}", EM_DASH) + "\n"
    return version, hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], body


def _content(body: bytes) -> str:
    try:
        return json.loads(body)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError("expected a chat completion reply, got an unexpected shape") from None


def _describe(status: int, body: bytes) -> str:
    text = body.decode("utf-8", "replace").strip()
    try:
        text = json.loads(text)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        pass
    label = f"status {status}" if status else "no reply"
    return f"{label}: {str(text)[:200]}" if text else label


def source_lines(source: str) -> list[str]:
    # Python counts only \n, \r and \r\n as line ends, unlike str.splitlines.
    lines = re.split(r"\r\n|\r|\n", source)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def split_source(source: str, limit: int) -> list[tuple[int, int]]:
    """Line ranges (1-based, inclusive) of at most `limit` lines, cut between top-level statements."""
    total = len(source_lines(source))
    if total <= limit:
        return [(1, max(total, 1))]
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [(1, total)]

    spans = []
    for node in tree.body:
        first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        spans.append((first, node.end_lineno))

    chunks: list[tuple[int, int]] = []
    start = 1
    end = 0
    for first, last in spans:
        if end and last - start + 1 > limit:
            chunks.append((start, first - 1))
            start = first
        end = last
    chunks.append((start, total))
    return chunks


def user_message(path: str, source: str, start: int, end: int, part: int, parts: int) -> str:
    lines = source_lines(source)
    numbered = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
    code = sum(1 for n in range(start, end + 1) if lines[n - 1].strip()
               and not lines[n - 1].lstrip().startswith("#"))
    target = max(MIN_EDITS, min(MAX_EDITS, round(code * 1.4)))
    head = f"File: {path}\n"
    if parts > 1:
        head += (f"Part {part} of {parts}: lines {start} to {end} of {len(lines)}. "
                 "Only edit code in these lines. ")
        head += "Add the header here. " if part == 1 else "Do not add a header. "
        head += "Add the footer here.\n" if part == parts else "Do not add a footer.\n"
    head += f"Aim for at least {target} edits and at most {target + EDIT_SLACK}.\n\n"
    return head + numbered + "\n"


def html_message(path: str, source: str) -> str:
    lines = source_lines(source)
    numbered = "\n".join(f"{n}: {line}" for n, line in enumerate(lines, 1))
    return f"File: {path}\n\n{numbered}\n"


def merge_replies(replies: list[str], chunks: list[tuple[int, int]]) -> str:
    merged: list[Any] = []
    for reply, (start, end) in zip(replies, chunks):
        try:
            data = load_json(strip_fences(reply))
        except json.JSONDecodeError:
            merged.append(f"expected JSON for lines {start} to {end}")
            continue
        if isinstance(data, dict) and "edits" in data:
            data = data["edits"]
        if isinstance(data, list):
            merged.extend(data)
        else:
            merged.append(f"expected a list of edits for lines {start} to {end}")
    return json.dumps({"edits": merged}, ensure_ascii=False)
