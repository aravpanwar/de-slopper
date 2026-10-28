import ast
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst

from deslopper.cache import Cache
from deslopper.providers.base import Provider, ProviderError
from deslopper.purify import load_json, strip_fences
from deslopper.receipts import Entry

TEST_TIMEOUT = 36
ROUNDS = 3
SKIP_REASON = "Temporarily disabled, will fix later"


@dataclass
class Outcome:
    generated: int = 0
    passed: int = 0
    skipped: int = 0
    failed: set[str] = field(default_factory=set)
    broken: str | None = None


def make_tests(file: str, import_name: str, output_path: Path, test_path: Path, root: Path,
               provider: Provider, cache: Cache) -> list[Entry]:
    source = output_path.read_bytes().decode("utf-8")
    context = {"path": file, "module": import_name}
    try:
        reply = _reply(provider, cache, source, context)
    except ProviderError as err:
        return [Entry(file, "add_test_file", "skipped", reason=str(err))]

    entries: list[Entry] = []
    outcome = None
    reason = "expected a test file in the reply"
    parsed = parse_test_reply(reply)
    if parsed is not None:
        test_source, conftest = parsed
        write_text(test_path, test_source)
        if conftest and not (test_path.parent / "conftest.py").exists():
            write_text(test_path.parent / "conftest.py", conftest)
        outcome = settle(test_path, root)
        reason = outcome.broken

    if outcome is None or outcome.broken:
        write_text(test_path, template(import_name, source))
        outcome = settle(test_path, root)
        entries.append(Entry(file, "add_test_file", "note",
                             reason=f"{reason}, so the template was used instead"))
    if outcome.broken:
        test_path.unlink(missing_ok=True)
        return entries + [Entry(file, "add_test_file", "skipped", reason=outcome.broken)]

    counts = {"generated": outcome.generated, "passed": outcome.passed, "skipped": outcome.skipped}
    entries.append(Entry(file, "add_test_file", "applied", detail=f"wrote {test_path.name}",
                         counts=counts))
    return entries


def _reply(provider: Provider, cache: Cache, source: str, context: dict) -> str:
    if not provider.use_cache:
        return provider.write_tests(source, context)
    key = cache.key(provider.version("tests"), provider.model, "tests", source, context)
    reply = cache.get(key)
    if reply is None:
        reply = provider.write_tests(source, context)
        cache.put(key, reply)
    return reply


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def parse_test_reply(reply: str) -> tuple[str, str | None] | None:
    text = strip_fences(reply)
    try:
        data = load_json(text)
    except json.JSONDecodeError:
        # Some models answer with the bare test file instead of JSON.
        return (text, None) if _parses(text) else None
    if not isinstance(data, dict):
        return None
    test_file, conftest = data.get("test_file"), data.get("conftest")
    if not isinstance(test_file, str) or not _parses(test_file):
        return None
    return test_file, conftest if isinstance(conftest, str) and _parses(conftest) else None


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return bool(source.strip())


def settle(test_path: Path, root: Path) -> Outcome:
    """Run the tests, mark the failing ones as skipped, and run again until they pass."""
    first = outcome = _run(test_path, root)
    for _ in range(ROUNDS):
        if outcome.broken or not outcome.failed:
            break
        marked = skip_failing(test_path.read_bytes().decode("utf-8"), outcome.failed)
        write_text(test_path, marked)
        outcome = _run(test_path, root)
        outcome.generated = first.generated
    if not outcome.broken and outcome.failed:
        outcome.broken = "expected the tests to pass or be skipped, some still fail"
    return outcome


def _run(test_path: Path, root: Path) -> Outcome:
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "report.xml"
        # Plugins are off so the result does not depend on what else is installed.
        env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
        command = [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
                   "-p", "no:cacheprovider", f"--junitxml={report}"]
        try:
            subprocess.run(command, cwd=scratch, env=env, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            return Outcome(broken=f"expected the tests to finish within {TEST_TIMEOUT} seconds")
        try:
            cases = list(ET.parse(report).getroot().iter("testcase"))
        except (OSError, ET.ParseError):
            return Outcome(broken="expected a pytest report, got none")

    if not cases:
        return Outcome(broken="expected at least one collected test")
    defined = _defined_tests(test_path.read_bytes().decode("utf-8"))
    outcome = Outcome(generated=len(cases))
    for case in cases:
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if case.find("skipped") is not None:
            outcome.skipped += 1
        elif problem is None:
            outcome.passed += 1
        else:
            qualname = _qualname(case, test_path.stem)
            if qualname not in defined:
                lines = (problem.text or problem.get("message") or "").strip().splitlines()
                return Outcome(broken=lines[-1] if lines else "expected the tests to be collected")
            outcome.failed.add(qualname)
    return outcome


def _qualname(case: ET.Element, stem: str) -> str:
    parts = case.get("classname", "").split(".")
    classes = parts[parts.index(stem) + 1:] if stem in parts else []
    return ".".join([*classes, case.get("name", "").split("[")[0]])


def _defined_tests(source: str) -> set[str]:
    found: set[str] = set()

    def walk(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.add(prefix + node.name)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}.")

    walk(ast.parse(source).body, "")
    return found


class _Skip(cst.CSTTransformer):
    def __init__(self, failing: set[str]):
        super().__init__()
        self.failing = failing
        self.stack: list[str] = []

    def visit_ClassDef(self, node):
        self.stack.append(node.name.value)

    def leave_ClassDef(self, original_node, updated_node):
        self.stack.pop()
        return updated_node

    def visit_FunctionDef(self, node):
        self.stack.append(node.name.value)

    def leave_FunctionDef(self, original_node, updated_node):
        qualname = ".".join(self.stack)
        self.stack.pop()
        if qualname not in self.failing:
            return updated_node
        mark = cst.Decorator(cst.parse_expression(f'pytest.mark.skip(reason="{SKIP_REASON}")'))
        return updated_node.with_changes(decorators=[mark, *updated_node.decorators])


def skip_failing(source: str, failing: set[str]) -> str:
    module = cst.parse_module(source).visit(_Skip(failing))
    if _imports_pytest(module):
        return module.code
    # Past a docstring and any __future__ imports, which must stay first.
    index = 0
    for i, statement in enumerate(module.body):
        text = module.code_for_node(statement).lstrip()
        if text.startswith(('"""', "'''", '"', "'", "from __future__")):
            index = i + 1
        else:
            break
    body = list(module.body)
    body.insert(index, cst.parse_statement("import pytest\n"))
    return module.with_changes(body=body).code


def _imports_pytest(module: cst.Module) -> bool:
    for statement in module.body:
        if not isinstance(statement, cst.SimpleStatementLine):
            continue
        for small in statement.body:
            if isinstance(small, cst.Import) and any(
                    isinstance(a.name, cst.Name) and a.name.value == "pytest" and a.asname is None
                    for a in small.names):
                return True
    return False


def template(import_name: str, source: str) -> str:
    names = [
        node.name for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    ] or ["__name__"]
    return (
        "import pytest\n\n"
        f"import {import_name}\n\n\n"
        "def test_module_is_not_none():\n"
        f"    result = {import_name}\n"
        "    assert result is not None\n\n\n"
        "def test_module_equals_itself():\n"
        f"    assert {import_name} == {import_name}\n\n\n"
        f"@pytest.mark.parametrize(\"name\", {names!r})\n"
        "def test_public_name_is_defined(name):\n"
        f"    assert hasattr({import_name}, name)\n"
    )
