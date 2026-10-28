from dataclasses import dataclass
from typing import Any

PYTHON_KINDS = frozenset({
    "add_comment",
    "add_docstring",
    "rename",
    "wrap_try",
    "add_log",
    "add_guard",
    "add_import",
    "extract_constant",
    "add_wrapper_class",
    "add_header",
    "add_footer",
})

HTML_KINDS = frozenset({
    "set_head",
    "add_classes",
    "restyle",
    "regroup",
    "unwrap",
    "wrap",
    "insert_block",
    "add_script",
})

KINDS_BY_INPUT = {
    "python": PYTHON_KINDS,
    "html": HTML_KINDS,
}


@dataclass(frozen=True)
class Edit:
    kind: str
    args: dict[str, Any]

    @property
    def line(self) -> int | None:
        value = self.args.get("line")
        return value if isinstance(value, int) and not isinstance(value, bool) else None
