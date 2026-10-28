import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from deslopper.cache import Cache
from deslopper.errors import SweepError
from deslopper.extras import component_name, page_extras
from deslopper.engines import Engine, PassthroughEngine, Verdict
from deslopper.fsutil import write_atomic
from deslopper.marker import Marker
from deslopper.providers.base import Provider
from deslopper.purify import purify
from deslopper.python.engine import PythonEngine
from deslopper.python.testgen import make_tests
from deslopper.web.assets import copy_assets
from deslopper.web.engine import HtmlEngine
from deslopper.web.hidden import hidden_edits
from deslopper.web.markdown_input import markdown_to_page
from deslopper.receipts import Entry, append_entries

EXTENSIONS = {".py": "python", ".html": "html", ".htm": "html", ".md": "markdown", ".markdown": "markdown"}
SKIPPED_DIRS = {"__pycache__", "node_modules"}
MARKER_NAME = ".marker.json"
RECEIPT_NAME = "receipt.jsonl"
BACKUP_NAME = re.compile(r"\.orig(\.\d+)?\.[^.]+$")


ENGINES: dict[str, Engine] = {
    "python": PythonEngine(),
    "html": HtmlEngine(),
}


@dataclass(frozen=True)
class SweepResult:
    output: str | None
    entries: list[Entry]


def kind_of(path: Path) -> str | None:
    return EXTENSIONS.get(path.suffix.lower())


def read_source(path: Path) -> str:
    # Decoded from bytes so line endings pass through untouched.
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise SweepError(f"expected a UTF-8 file, got undecodable bytes in {path}") from None
    except OSError as err:
        raise SweepError(f"couldn't read {path}: {err.strerror}") from None


def prepare(kind: str, source: str, name: str) -> tuple[str, str]:
    """Markdown becomes a plain page first, and from there it is swept like any other page."""
    if kind == "markdown":
        return "html", markdown_to_page(source, Path(name).stem)
    return kind, source


def output_name(kind: str, name: str) -> str:
    return str(Path(name).with_suffix(".html").as_posix()) if kind == "markdown" else name


def write_output(path: Path, text: str) -> None:
    write_atomic(path, text.encode("utf-8"))


def backup_original(target: Path, name: str, inputs: frozenset[Path]) -> list[Entry]:
    """Before a write lands on an input file, keep the untouched original beside it."""
    if not target.is_file() or target.resolve() not in inputs:
        return []
    backup = _backup_path(target)
    shutil.copyfile(target, backup)
    return [Entry(name, "backup", "note", reason=f"saved the original as {backup.name} before writing over it",
                  detail=str(backup))]


def _backup_path(target: Path) -> Path:
    # An existing backup is never replaced: a second in-place run keeps the
    # first original and numbers the next one.
    number = 1
    while True:
        tag = "orig" if number == 1 else f"orig.{number}"
        backup = target.with_name(f"{target.stem}.{tag}{target.suffix}")
        if not backup.exists():
            return backup
        number += 1


def sweep_source(kind: str, source: str, name: str, provider: Provider, cache: Cache,
                 engine: Engine | None = None, origin: Path | None = None,
                 detect_hidden: bool = True) -> SweepResult:
    engine = engine or ENGINES[kind]
    reply = _plan(kind, source, name, provider, cache)
    edits, rejections = purify(reply, kind)

    notes: list[Entry] = []
    if kind == "html" and edits and detect_hidden:
        # Stripping the stylesheet would show what it hid, such as a mobile copy of the menu.
        hidden, method = hidden_edits(source, origin)
        if hidden:
            edits = edits + hidden
            how = ("with a browser" if method == "browser"
                   else "by the text repeating, because no browser was available")
            notes.append(Entry(name, "hide", "note", reason=f"found {len(hidden)} hidden elements {how}"))

    entries = [Entry(name, "sweep", "swept")]
    for rejection in rejections:
        detail = f"edit {rejection.index}" if rejection.index is not None else None
        entries.append(Entry(name, rejection.kind or "reply", "rejected",
                             reason=rejection.reason, detail=detail))

    output, applied = engine.apply(source, edits, name)
    verdict = _check(engine, source, output, origin)

    # A failed check gives up one edit kind at a time, riskiest first, and
    # tries again, so one bad kind costs only its own edits.
    reasons: dict[str, str] = {}
    active = edits
    for kind_name in engine.drop_order:
        if not verdict.problem:
            break
        reasons[kind_name] = verdict.problem
        active = [e for e in active if e.kind != kind_name]
        output, _ = engine.apply(source, active, name)
        verdict = _check(engine, source, output, origin)
    notes += [Entry(name, "check", "note", reason=note) for note in verdict.notes]
    if verdict.problem:
        output = source if engine.writes_on_failure else None
        for entry in applied:
            reasons.setdefault(entry.kind, verdict.problem)
        if output is None:
            notes.append(Entry(name, "check", "note", reason=f"output not written for {name}"))

    applied = [replace(e, status="dropped", reason=reasons[e.kind])
               if e.status == "applied" and e.kind in reasons else e for e in applied]
    return SweepResult(output, entries + applied + notes)


def _check(engine: Engine, source: str, output: str, origin: Path | None) -> Verdict:
    if output == source:
        return Verdict()
    return engine.check(source, output, origin)


def generate_tests(kind: str, name: str, parts: tuple[str, ...], output: Path, test_path: Path,
                   root: Path, provider: Provider, cache: Cache,
                   inputs: frozenset[Path] = frozenset()) -> list[Entry]:
    """Write and verify a test file for one swept Python module, or return no entries."""
    base = name.rsplit("/", 1)[-1]
    if (kind != "python" or not provider.writes_tests or base in {"__init__.py", "__main__.py", "conftest.py"}
            or base.startswith("test_") or base.endswith("_test.py")):
        return []
    if not all(part.isidentifier() for part in parts):
        reason = f"expected a module name that is a valid identifier, got {'.'.join(parts)}"
        return [Entry(name, "add_test_file", "skipped", reason=reason)]
    saved = backup_original(test_path, name, inputs)
    return saved + make_tests(name, ".".join(parts), output, test_path, root, provider, cache)


def sweep_directory(root: Path, out: Path, provider: Provider, cache: Cache,
                    receipt: Path | None = None, tests: bool = True,
                    react: bool = False, screenshot: bool = False) -> list[Entry]:
    receipt = receipt or out / RECEIPT_NAME
    marker_path = out / MARKER_NAME
    # Without a marker this is a fresh run, so the old receipt is stale.
    if not marker_path.exists() and receipt.exists():
        receipt.unlink()
    marker = Marker(marker_path)

    inputs = _find_inputs(root, out)
    if not inputs:
        raise SweepError(f"expected .py or .html files under {root}, found none")
    originals = frozenset(path.resolve() for path, _ in inputs)

    entries: list[Entry] = []
    for path, kind in inputs:
        name = path.relative_to(root).as_posix()
        source = read_source(path)
        target = out / output_name(kind, name)
        if marker.has(name, source) and target.exists():
            continue
        page_kind, text = prepare(kind, source, name)
        result = sweep_source(page_kind, text, name, provider, cache, origin=path)
        found = list(result.entries)
        if result.output is not None:
            found += backup_original(target, name, originals)
            write_output(target, result.output)
            found += copy_page_assets(page_kind, name, result.output, path.parent, target.parent)
            if tests:
                parts = Path(name).with_suffix("").parts
                test_path = out / "tests" / f"test_{'_'.join(parts)}.py"
                found += generate_tests(kind, name, parts, target, test_path, out, provider, cache,
                                        originals)
            found += page_extras(page_kind, name, path, text, result.output, target, react, screenshot,
                                 component_name(target.stem, single=False))
            marker.record(name, source)
        append_entries(receipt, found)
        entries.extend(found)
    return entries


def copy_page_assets(kind: str, name: str, source: str, source_dir: Path,
                     dest_dir: Path) -> list[Entry]:
    """Copy what the swept page still links to next to its output, so it still looks the way it should."""
    if kind != "html":
        return []
    copied = copy_assets(source_dir, dest_dir, source)
    if not copied:
        return []
    return [Entry(name, "assets", "note", reason=f"copied {', '.join(copied)} next to the output")]


def _plan(kind: str, source: str, name: str, provider: Provider, cache: Cache) -> str:
    context = {"path": name}
    if not provider.use_cache:
        return provider.plan(kind, source, context)
    key = cache.key(provider.version(kind), provider.model, kind, source, context)
    reply = cache.get(key)
    if reply is None:
        reply = provider.plan(kind, source, context)
        cache.put(key, reply)
    return reply


def _find_inputs(root: Path, out: Path) -> list[tuple[Path, str]]:
    # The output directory may sit inside the input tree, and sweeping our
    # own output would make every rerun grow.
    skip = out.resolve()
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d for d in dirs
            if d not in SKIPPED_DIRS and not d.startswith(".")
            and (Path(folder) / d).resolve() != skip
        )
        for filename in sorted(files):
            path = Path(folder) / filename
            kind = kind_of(path)
            # Saved originals are never swept.
            if kind and not BACKUP_NAME.search(filename):
                found.append((path, kind))
    return found
