import json
import re
import tomllib
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path

STATUSES = ("applied", "skipped", "rejected", "dropped")
PRINTED = STATUSES + ("note",)


@dataclass(frozen=True)
class Entry:
    file: str
    kind: str
    status: str
    reason: str | None = None
    line: int | None = None
    detail: str | None = None
    counts: dict[str, int] | None = None

    def to_json(self) -> str:
        data = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(data, ensure_ascii=False)


@dataclass(frozen=True)
class Tell:
    habit: str
    ancestor: str


@dataclass(frozen=True)
class Tone:
    name: str
    habit: str
    ancestor: str
    pattern: re.Pattern


def _tells_table() -> dict:
    text = resources.files("deslopper").joinpath("tells.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def load_tells() -> dict[str, Tell]:
    return {kind: Tell(**fields) for kind, fields in _tells_table().items() if kind != "tone"}


def load_tones() -> list[Tone]:
    table = _tells_table().get("tone", {})
    return [Tone(name, f["habit"], f["ancestor"], re.compile(f["pattern"])) for name, f in table.items()]


def count_tones(prose: str, tones: list[Tone]) -> list[tuple[Tone, int]]:
    found = [(tone, len(tone.pattern.findall(prose))) for tone in tones]
    return [(tone, n) for tone, n in found if n]


def append_entries(path: Path, entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(entry.to_json() + "\n")


def professional_percent(applied: int) -> int:
    # Never reaches 100, because there is always one more comment to add.
    return min(99, round(100 * applied / (applied + 1)))


LINES_SHOWN = 6


def summarize(entries: list[Entry], tells: dict[str, Tell],
              tones: list[tuple[Tone, int]] = ()) -> str:
    files = {e.file for e in entries}
    lines: list[str] = []
    current = None
    for (file, status, kind, reason), group in _groups(entries).items():
        if len(files) > 1 and file != current:
            current = file
            lines.append(file)
        if status == "note":
            lines.append(f"note: {reason}")
            continue
        head = f"{kind} x{len(group)}{_where(group)}{_tests(group)}"
        if status == "applied":
            lines.append(head)
            lines.extend(_pairing(kind, tells))
        else:
            lines.append(f"{status} {head}: {reason}")

    for tone, found in tones:
        lines.append(f"tone: {tone.name.replace('_', ' ')} x{found}")
        lines.extend([f"    AI habit:      {tone.habit}", f"    human version: {tone.ancestor}"])

    counts = {status: sum(e.status == status for e in entries) for status in STATUSES}
    swept = sum(e.status == "swept" for e in entries)
    noun = "file" if swept == 1 else "files"
    lines.append(
        f"swept {swept} {noun}: {counts['applied']} applied, {counts['skipped']} skipped, "
        f"{counts['rejected']} rejected, {counts['dropped']} dropped"
    )
    lines.append(f"Your code is now {professional_percent(counts['applied'])}% professional.")
    return "\n".join(lines)


def _groups(entries: list[Entry]) -> dict[tuple, list[Entry]]:
    groups: dict[tuple, list[Entry]] = {}
    for entry in entries:
        if entry.status not in PRINTED:
            continue
        reason = None if entry.status == "applied" else entry.reason
        groups.setdefault((entry.file, entry.status, entry.kind, reason), []).append(entry)
    return groups


def _where(group: list[Entry]) -> str:
    numbers = [str(e.line) for e in group if e.line is not None]
    if not numbers:
        return ""
    more = ", ..." if len(numbers) > LINES_SHOWN else ""
    label = "line" if len(numbers) == 1 else "lines"
    return f" ({label} {', '.join(numbers[:LINES_SHOWN])}{more})"


def _tests(group: list[Entry]) -> str:
    totals: dict[str, int] = {}
    for entry in group:
        for key, value in (entry.counts or {}).items():
            totals[key] = totals.get(key, 0) + value
    if not totals:
        return ""
    return " (" + ", ".join(f"{value} {key}" for key, value in totals.items()) + ")"


def _pairing(kind: str, tells: dict[str, Tell]) -> list[str]:
    tell = tells.get(kind)
    if tell is None:
        return []
    return [f"    AI habit:      {tell.habit}", f"    human version: {tell.ancestor}"]
