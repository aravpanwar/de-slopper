import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from deslopper.engines import Verdict

IMPORT_TIMEOUT = 36

PROBE = (
    "import importlib.util, sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "spec = importlib.util.spec_from_file_location('_deslopper_probe', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "sys.modules['_deslopper_probe'] = module\n"
    "spec.loader.exec_module(module)\n"
)

_original_imports: dict[tuple[str, str], str | None] = {}


def check_output(source: str, output: str, origin: Path | None) -> Verdict:
    source, output = source.removeprefix("\ufeff"), output.removeprefix("\ufeff")
    try:
        compile(output, "<output>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as err:
        where = getattr(err, "lineno", None)
        suffix = f" on line {where}" if where else ""
        return Verdict(f"expected the output to parse, got {getattr(err, 'msg', err)}{suffix}")

    broken = _changed_signature(source, output)
    if broken:
        return Verdict(f"expected {broken} to keep its signature")

    key = (source, str(origin))
    if key not in _original_imports:
        _original_imports[key] = _import_failure(source, origin)
    if _original_imports[key] is not None:
        return Verdict(notes=("expected the original to import, so the import check was skipped",))
    failure = _import_failure(output, origin)
    if failure:
        return Verdict(f"expected the output to import, got {failure}")
    return Verdict()


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple:
    args = node.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    # Defaults are compared by presence only: moving a literal default into
    # a module constant keeps the behavior but changes its expression.
    return (
        type(node).__name__,
        [a.arg for a in every],
        len(args.defaults),
        [d is None for d in args.kw_defaults],
        [ast.dump(a.annotation) if a.annotation else None for a in every],
        ast.dump(node.returns) if node.returns else None,
    )


def _top_level(tree: ast.Module) -> dict[str, tuple | None]:
    found: dict[str, tuple | None] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = _signature(node)
        elif isinstance(node, ast.ClassDef):
            found[node.name] = None
    return found


def _changed_signature(source: str, output: str) -> str | None:
    before = _top_level(ast.parse(source))
    after = _top_level(ast.parse(output))
    for name, signature in before.items():
        if name not in after or after[name] != signature:
            return name
    return None


def _import_failure(code: str, origin: Path | None) -> str | None:
    # The module runs from a temporary copy with its own directory on the
    # path, so sibling imports resolve the way they do for the original.
    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "_deslopper_probe.py"
        probe.write_bytes(code.encode("utf-8"))
        cwd = str(origin.resolve().parent) if origin else scratch
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run(
                [sys.executable, "-c", PROBE, str(probe), cwd],
                cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL,
                timeout=IMPORT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"no result within {IMPORT_TIMEOUT} seconds"
    if proc.returncode == 0:
        return None
    lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return lines[-1] if lines else f"exit code {proc.returncode}"
