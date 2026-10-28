import json
import re
from dataclasses import dataclass

from deslopper.edits import KINDS_BY_INPUT, Edit

FENCE = re.compile(r"```[\w-]*[ \t]*\r?\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Rejection:
    kind: str | None
    reason: str
    index: int | None = None


def load_json(text: str):
    # Models sometimes end a complete object with stray characters, such as
    # a second closing brace, so the first JSON value is what counts.
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        if err.msg != "Extra data":
            raise
        return json.JSONDecoder().raw_decode(text.lstrip())[0]


def strip_fences(reply: str) -> str:
    match = FENCE.search(reply)
    if match:
        return match.group(1).strip()
    text = reply.strip()
    # A reply cut off by the token limit can open a fence and never close it.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    return text.strip()


def purify(reply: str, input_kind: str) -> tuple[list[Edit], list[Rejection]]:
    try:
        data = load_json(strip_fences(reply))
    except json.JSONDecodeError as err:
        return [], [Rejection(None, f"expected JSON, parse failed at character {err.pos}")]

    # JSON output modes usually force a top-level object, so the list may
    # arrive wrapped as {"edits": [...]}.
    if isinstance(data, dict) and "edits" in data:
        data = data["edits"]
    if not isinstance(data, list):
        return [], [Rejection(None, "expected a JSON list of edits")]

    known = KINDS_BY_INPUT[input_kind]
    edits: list[Edit] = []
    rejections: list[Rejection] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            rejections.append(Rejection(None, f"expected an edit object, got {type(item).__name__}", index))
            continue
        # Models sometimes name the field "type" instead of "kind".
        field = "kind" if "kind" in item else "type"
        kind = item.get(field)
        if not isinstance(kind, str):
            rejections.append(Rejection(None, "expected a string kind", index))
            continue
        if kind not in known:
            rejections.append(Rejection(kind, f"expected a known {input_kind} edit kind, got {kind}", index))
            continue
        edits.append(Edit(kind, {k: v for k, v in item.items() if k != field}))
    return edits, rejections
