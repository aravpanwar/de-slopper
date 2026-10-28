import builtins
import keyword
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

import libcst as cst
from libcst.metadata import FunctionScope, MetadataWrapper, ScopeProvider

from deslopper.edits import Edit
from deslopper.python.index import Definition, Index, collect_names, docstring_of
from deslopper.python.rewrite import Constant, FunctionPlan, Plan, Rewrite
from deslopper.python.text import (
    Refusal,
    comment_lines,
    docstring_source,
    message,
    string_literal,
)
from deslopper.receipts import Entry

PROBE_TIMEOUT = 36
DYNAMIC_NAMES = ("eval", "exec", "locals", "vars")
# Importing these runs code with side effects (a browser, a printout).
UNSAFE_MODULES = {"antigravity", "this", "turtledemo", "idlelib", "__main__"}
# Python calls these expecting an exception as the normal way to finish or
# to say "not here", so logging every raise would flood stderr.
ROUTINE_RAISERS = {
    "__next__", "__anext__", "__getattr__", "__getattribute__", "__getitem__", "__missing__",
}


def apply_edits(source: str, edits: list[Edit], name: str) -> tuple[str, list[Entry]]:
    if not edits:
        return source, []
    bom = source.startswith("\ufeff")
    try:
        module = cst.parse_module(source[1:] if bom else source)
    except cst.ParserSyntaxError as err:
        reason = f"expected valid Python, got a syntax error on line {err.raw_line}"
        return source, [Entry(name, e.kind, "skipped", reason=reason, line=e.line) for e in edits]

    planner = Planner(module)
    entries = []
    for edit in edits:
        handler = getattr(planner, f"do_{edit.kind}", None)
        try:
            if handler is None:
                raise Refusal(f"expected a known python edit kind, got {edit.kind}")
            detail, line = handler(edit)
        except Refusal as refusal:
            entries.append(Entry(name, edit.kind, "skipped", reason=str(refusal), line=edit.line))
        else:
            entries.append(Entry(name, edit.kind, "applied", line=line, detail=detail))

    if not planner.plan.touched():
        return source, entries
    try:
        output = planner.module.visit(Rewrite(planner.plan)).code
    except (cst.CSTValidationError, cst.ParserSyntaxError) as err:
        reason = f"failed to rewrite the file: {err}"
        return source, [
            Entry(e.file, e.kind, "dropped", reason=reason, line=e.line) if e.status == "applied" else e
            for e in entries
        ]
    return ("\ufeff" if bom else "") + output, entries


@dataclass(frozen=True)
class LoggerChoice:
    name: str
    import_logging: bool
    assign: bool


class Planner:
    def __init__(self, module: cst.Module):
        self.wrapper = MetadataWrapper(module)
        self.module = self.wrapper.module
        self.scopes = self.wrapper.resolve(ScopeProvider)
        self.index = Index(self.module)
        self.wrapper.visit(self.index)
        self.plan = Plan(insert_after=self.index.state.insert_after)
        # Names the edits introduce, so two edits cannot pick the same one.
        self.claimed: set[str] = set()
        self._renamed: set[tuple[int, str]] = set()

    # field access

    @staticmethod
    def _text(edit: Edit, field: str) -> str:
        value = edit.args.get(field)
        if not isinstance(value, str) or not value.strip():
            raise Refusal(f"expected a string {field}")
        return value

    @staticmethod
    def _int(edit: Edit, field: str) -> int:
        value = edit.args.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise Refusal(f"expected an integer {field}")
        return value

    @staticmethod
    def _identifier(name: str) -> str:
        if not name.isidentifier() or keyword.iskeyword(name) or keyword.issoftkeyword(name):
            raise Refusal(f"expected a valid identifier, got {name}")
        return name

    def _unused(self, name: str) -> str:
        if name in self.index.names or name in self.claimed:
            raise Refusal(f"expected an unused name, got {name}")
        return name

    @staticmethod
    def _candidates(registry: dict[str, list[Definition]], target: str) -> list[Definition]:
        found = registry.get(target)
        if not found:
            # Models often give a bare method name for Class.method.
            found = [d for q, ds in registry.items() if q.rsplit(".", 1)[-1] == target for d in ds]
        return found

    def _lookup(self, registry: dict[str, list[Definition]], target: str, what: str) -> Definition:
        found = self._candidates(registry, target)
        if not found:
            raise Refusal(f"expected a {what} named {target}")
        if len(found) > 1:
            raise Refusal(f"expected one {what} named {target}, found {len(found)}")
        return found[0]

    def _function(self, name: str) -> Definition:
        return self._lookup(self.index.functions, name, "function")

    def _function_plan(self, info: Definition) -> FunctionPlan:
        return self.plan.functions.setdefault(id(info.node), FunctionPlan())

    # comments

    def do_add_comment(self, edit: Edit):
        line = self._int(edit, "line")
        lines = comment_lines(self._text(edit, "text"))
        nodes = self.index.statements.get(line)
        if not nodes:
            raise Refusal(f"expected a statement start on line {line}")
        if not lines:
            raise Refusal("expected comment text")
        self.plan.comments.setdefault(id(nodes[0]), []).extend(lines)
        return f'added "{lines[0]}"', line

    def do_add_header(self, edit: Edit):
        lines = self._block_comment(edit)
        self.plan.header.extend(lines)
        return f'added "{lines[0]}" at the top', None

    def do_add_footer(self, edit: Edit):
        lines = self._block_comment(edit)
        self.plan.footer.extend(lines)
        return f'added "{lines[0]}" at the bottom', None

    def _block_comment(self, edit: Edit) -> list[str]:
        lines = comment_lines(self._text(edit, "text"))
        if not lines:
            raise Refusal("expected comment text")
        return lines

    # docstrings

    def do_add_docstring(self, edit: Edit):
        target = self._text(edit, "target")
        text = self._text(edit, "text")
        registry = self.index.functions
        if not self._candidates(registry, target):
            registry = self.index.classes
        info = self._lookup(registry, target, "function or class")
        if docstring_of(info.node.body) is not None:
            raise Refusal(f"expected {target} without a docstring")
        source = docstring_source(text, info.indent)
        if isinstance(info.node, cst.FunctionDef):
            plan = self.plan.functions.get(id(info.node))
            if plan and plan.docstring:
                raise Refusal(f"expected one docstring for {target}")
            self._function_plan(info).docstring = source
        else:
            if id(info.node) in self.plan.class_docstrings:
                raise Refusal(f"expected one docstring for {target}")
            self.plan.class_docstrings[id(info.node)] = source
        return f"added a docstring to {target}", info.line

    # rename

    def do_rename(self, edit: Edit):
        scope_name = self._text(edit, "scope")
        old = self._text(edit, "old")
        new = self._identifier(self._text(edit, "new"))
        info = self._function(scope_name)
        fn = info.node
        if new == old:
            raise Refusal(f"expected a new name different from {old}")
        if new.startswith("__"):
            raise Refusal(f"expected a name without leading double underscores, got {new}")
        if (id(fn), old) in self._renamed:
            raise Refusal(f"expected {old} to be renamed once")

        params = _param_names(fn)
        if old in params:
            raise Refusal(f"expected a local variable, got the parameter {old}")
        names = collect_names(fn)
        if old in names.declared:
            raise Refusal(f"expected {old} without a global or nonlocal declaration")
        dynamic = [n for n in DYNAMIC_NAMES if n in names.names]
        if dynamic:
            raise Refusal(f"expected {scope_name} without {dynamic[0]}()")

        scope = self.scopes.get(fn.body)
        if not isinstance(scope, FunctionScope):
            raise Refusal(f"expected a function scope for {scope_name}")
        assignments = list(scope.assignments[old])
        if not assignments:
            raise Refusal(f"expected {old} to be assigned in {scope_name}")
        targets: set[int] = set()
        for assignment in assignments:
            if not isinstance(assignment.node, cst.Name):
                raise Refusal(f"expected {old} to be a plain variable")
            targets.add(id(assignment.node))
            for access in assignment.references:
                if not isinstance(access.node, cst.Name):
                    raise Refusal(f"expected every use of {old} to be a name")
                targets.add(id(access.node))

        if new in names.names or new in params or new in self.claimed:
            raise Refusal(f"expected a name unused in {scope_name}, got {new}")
        if hasattr(builtins, new):
            raise Refusal(f"expected a name that is not a builtin, got {new}")
        if _equals_specifier(fn, targets):
            raise Refusal(f"expected {old} outside an f-string = specifier")

        self.claimed.add(new)
        self._renamed.add((id(fn), old))
        self.plan.renames.update(dict.fromkeys(targets, new))
        return f"renamed {old} to {new} in {scope_name}", info.line

    # logging edits

    def _logger(self, info: Definition) -> "LoggerChoice":
        # The import and the logger assignment are only planned once an
        # edit that needs them is certain to apply, so a refused edit
        # never leaves them behind.
        plan = self.plan
        if plan.logger_name is not None:
            choice = LoggerChoice(plan.logger_name, False, False)
        else:
            choice = self._pick_logger()
        scope = self.scopes.get(info.node.body)
        if isinstance(scope, FunctionScope) and list(scope.assignments[choice.name]):
            raise Refusal(f"expected {choice.name} not to be rebound in {info.qualname}")
        return choice

    def _pick_logger(self) -> "LoggerChoice":
        state = self.index.state
        taken = self.index.names | self.claimed
        import_logging = not state.has_logging_import and "import logging" not in self.plan.imports
        if import_logging and "logging" in taken:
            raise Refusal("expected logging to be unused or the standard module")
        if state.logger_name:
            return LoggerChoice(state.logger_name, import_logging, False)
        candidates = ["logger", "log", "module_logger"] + [f"logger_{n}" for n in range(1, 100)]
        return LoggerChoice(next(c for c in candidates if c not in taken), import_logging, True)

    def _commit(self, choice: "LoggerChoice") -> None:
        if choice.import_logging:
            self.plan.imports["import logging"] = None
            self.claimed.add("logging")
        if choice.assign:
            self.plan.logger_assignment = True
            self.claimed.add(choice.name)
        self.plan.logger_name = choice.name

    def do_add_log(self, edit: Edit):
        info = self._function(self._text(edit, "function"))
        text = message(self._text(edit, "text"))
        plan = self.plan.functions.get(id(info.node))
        if plan and plan.log:
            raise Refusal(f"expected one add_log for {info.qualname}")
        self._commit(self._logger(info))
        self._function_plan(info).log = string_literal(text)
        return f'logged entry to {info.qualname}: "{text}"', info.line

    def do_add_guard(self, edit: Edit):
        info = self._function(self._text(edit, "function"))
        arg = self._text(edit, "arg")
        text = message(self._text(edit, "text"))
        if arg not in _param_names(info.node):
            raise Refusal(f"expected a parameter of {info.qualname}, got {arg}")
        plan = self.plan.functions.get(id(info.node))
        if plan and any(existing == arg for existing, _ in plan.guards):
            raise Refusal(f"expected one guard for {arg} in {info.qualname}")
        self._commit(self._logger(info))
        self._function_plan(info).guards.append((arg, string_literal(text)))
        return f"added a None guard for {arg} in {info.qualname}", info.line

    def do_wrap_try(self, edit: Edit):
        info = self._function(self._text(edit, "function"))
        text = message(self._text(edit, "log_text"))
        plan = self.plan.functions.get(id(info.node))
        if plan and plan.try_message:
            raise Refusal(f"expected {info.qualname} to be wrapped once")
        if info.node.name.value in ROUTINE_RAISERS:
            raise Refusal(f"expected {info.qualname} not to raise as routine flow")
        if _statements_after_docstring(info.node) == 0:
            raise Refusal(f"expected a body after the docstring in {info.qualname}")
        choice = self._logger(info)
        used = collect_names(info.node).names | self.claimed | {choice.name}
        exc = next(c for c in ["e", "exc", "err", "error"] + [f"e{n}" for n in range(1, 100)]
                   if c not in used)
        self._commit(choice)
        function_plan = self._function_plan(info)
        function_plan.exc_name = exc
        function_plan.try_message = string_literal(text.replace("%", "%%") + ": %s")
        return f"wrapped {info.qualname} in try/except", info.line

    # imports and constants

    def do_add_import(self, edit: Edit):
        module = self._text(edit, "module")
        names = edit.args.get("names") or []
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise Refusal("expected a list of names")
        parts = module.split(".")
        if not all(p.isidentifier() and not keyword.iskeyword(p) for p in parts):
            raise Refusal(f"expected a module name, got {module}")
        top = parts[0]
        if module == "__future__" or top not in sys.stdlib_module_names or top in UNSAFE_MODULES:
            raise Refusal(f"expected a standard library module, got {module}")

        plain = not names or names == [module]
        if plain:
            text, bound = f"import {module}", {top}
        else:
            for n in names:
                self._identifier(n)
            text, bound = f"from {module} import {', '.join(names)}", set(names)
        if not _importable(module, () if plain else tuple(names)):
            raise Refusal(f"expected {module} to provide {', '.join(names) or module}")

        if text not in self.plan.imports:
            taken = bound & (self.index.names | self.claimed)
            if taken:
                raise Refusal(f"expected unused names, got {', '.join(sorted(taken))}")
            self.plan.imports[text] = None
            self.claimed.update(bound)
        return f"added {text}", None

    def do_extract_constant(self, edit: Edit):
        line = self._int(edit, "line")
        value = edit.args.get("value")
        name = self._unused(self._identifier(self._text(edit, "name")))
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise Refusal("expected an int, float or str value")
        found = [lit for lit in self.index.literals.get(line, [])
                 if type(lit.value) is type(value) and lit.value == value]
        if not found:
            raise Refusal(f"expected the literal {value!r} on line {line}")
        if any(lit.top <= self.plan.insert_after for lit in found):
            raise Refusal(f"expected the literal {value!r} after the import block")
        comment = edit.args.get("comment")
        lines = comment_lines(comment) if isinstance(comment, str) else []
        source = self.module.code_for_node(found[0].node)
        self.plan.literals.update({id(lit.node): name for lit in found})
        self.plan.constants.append(Constant(name, source, lines))
        self.claimed.add(name)
        return f"moved {value!r} to {name}", line

    def do_add_wrapper_class(self, edit: Edit):
        info = self._function(self._text(edit, "function"))
        class_name = self._identifier(self._text(edit, "class_name"))
        if not info.top_level:
            raise Refusal(f"expected a module-level function, got {info.qualname}")
        instance = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
        if instance == class_name:
            raise Refusal(f"expected a CapWords class name, got {class_name}")
        if id(info.node) in self.plan.wrappers:
            raise Refusal(f"expected {info.qualname} to be wrapped once")
        docstring = edit.args.get("docstring")
        if docstring is not None and not isinstance(docstring, str):
            raise Refusal("expected a string docstring")
        self._unused(class_name)
        self._unused(instance)
        source = None
        if docstring and docstring.strip():
            source = docstring_source(docstring, self.module.default_indent)
        self.plan.wrappers[id(info.node)] = (
            info.node.name.value, class_name, instance, info.node.asynchronous is not None, source)
        self.claimed.update((class_name, instance))
        return f"wrapped {info.qualname} in {class_name}", info.line


def _param_names(fn: cst.FunctionDef) -> list[str]:
    params = fn.params
    names = [p.name.value for p in (*params.posonly_params, *params.params, *params.kwonly_params)]
    if isinstance(params.star_arg, cst.Param):
        names.append(params.star_arg.name.value)
    if params.star_kwarg is not None:
        names.append(params.star_kwarg.name.value)
    return names


def _statements_after_docstring(fn: cst.FunctionDef) -> int:
    has_doc = docstring_of(fn.body) is not None
    count = len(fn.body.body) if isinstance(fn.body, cst.IndentedBlock) else 1
    return count - (1 if has_doc else 0)


class _NameIds(cst.CSTVisitor):
    def __init__(self) -> None:
        self.ids: set[int] = set()

    def visit_Name(self, node: cst.Name) -> None:
        self.ids.add(id(node))


class _EqualsSpecifier(cst.CSTVisitor):
    def __init__(self, targets: set[int]) -> None:
        self.targets = targets
        self.hit = False

    def visit_FormattedStringExpression(self, node: cst.FormattedStringExpression) -> None:
        if node.equal is not None:
            found = _NameIds()
            node.expression.visit(found)
            self.hit = self.hit or bool(found.ids & self.targets)


def _equals_specifier(fn: cst.FunctionDef, targets: set[int]) -> bool:
    visitor = _EqualsSpecifier(targets)
    fn.visit(visitor)
    return visitor.hit


@lru_cache(maxsize=None)
def _importable(module: str, names: tuple[str, ...]) -> bool:
    script = (
        "import importlib, importlib.util, sys\n"
        "m = importlib.import_module(sys.argv[1])\n"
        "def has(n):\n"
        "    if hasattr(m, n):\n"
        "        return True\n"
        "    try:\n"
        "        return importlib.util.find_spec(sys.argv[1] + '.' + n) is not None\n"
        "    except Exception:\n"
        "        return False\n"
        "sys.exit(0 if all(has(n) for n in sys.argv[2:]) else 1)\n"
    )
    try:
        proc = subprocess.run([sys.executable, "-I", "-c", script, module, *names],
                              capture_output=True, stdin=subprocess.DEVNULL, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0
