from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from deslopper.edits import Edit
from deslopper.receipts import Entry


@dataclass(frozen=True)
class Verdict:
    problem: str | None = None
    notes: tuple[str, ...] = ()


class Engine(Protocol):
    # Edit kinds in the order they are given up when a check fails, riskiest first.
    drop_order: tuple[str, ...]
    # False when a failed check means no output file at all, rather than the original.
    writes_on_failure: bool

    def apply(self, source: str, edits: list[Edit], name: str) -> tuple[str, list[Entry]]:
        """Apply the edits, returning the new source and an entry per edit."""

    def check(self, source: str, output: str, origin: Path | None) -> Verdict:
        """Judge the output, returning the reason it is unacceptable, if any."""


class PassthroughEngine:
    drop_order: tuple[str, ...] = ()
    writes_on_failure = True

    def __init__(self, label: str):
        self.label = label

    def apply(self, source: str, edits: list[Edit], name: str) -> tuple[str, list[Entry]]:
        reason = f"edit engine for {self.label} files not built yet"
        return source, [Entry(name, e.kind, "skipped", reason=reason, line=e.line) for e in edits]

    def check(self, source: str, output: str, origin: Path | None) -> Verdict:
        return Verdict()
