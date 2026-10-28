from pathlib import Path

from deslopper.edits import Edit
from deslopper.engines import Verdict
from deslopper.python.checks import check_output
from deslopper.python.edits import apply_edits
from deslopper.receipts import Entry


class PythonEngine:
    writes_on_failure = True

    # Renames come first because they touch the most code, and the two
    # comment kinds last because they cannot break anything.
    drop_order = (
        "rename",
        "extract_constant",
        "add_wrapper_class",
        "wrap_try",
        "add_guard",
        "add_log",
        "add_import",
        "add_docstring",
        "add_comment",
        "add_header",
        "add_footer",
    )

    def apply(self, source: str, edits: list[Edit], name: str) -> tuple[str, list[Entry]]:
        return apply_edits(source, edits, name)

    def check(self, source: str, output: str, origin: Path | None) -> Verdict:
        return check_output(source, output, origin)
