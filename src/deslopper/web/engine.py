from pathlib import Path

from deslopper.edits import Edit
from deslopper.engines import Verdict
from deslopper.receipts import Entry
from deslopper.web.checks import check_output
from deslopper.web.edits import apply_edits


class HtmlEngine:
    # Nothing here is dropped kind by kind: an edit cannot delete content,
    # so a failed check means something is wrong, and no output is written.
    drop_order: tuple[str, ...] = ()
    writes_on_failure = False

    def apply(self, source: str, edits: list[Edit], name: str) -> tuple[str, list[Entry]]:
        return apply_edits(source, edits, name)

    def check(self, source: str, output: str, origin: Path | None) -> Verdict:
        return check_output(source, output, origin)
