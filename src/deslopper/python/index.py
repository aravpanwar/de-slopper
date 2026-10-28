from dataclasses import dataclass

import libcst as cst
from libcst.metadata import PositionProvider


@dataclass
class Literal:
    node: cst.BaseExpression
    value: int | float | str
    top: int


@dataclass
class Definition:
    node: cst.FunctionDef | cst.ClassDef
    qualname: str
    top_level: bool
    indent: str
    line: int


@dataclass
class ModuleState:
    # Where new imports, the logger and constants go: after this top-level
    # statement index, which is -1 for the very top of the file.
    insert_after: int
    has_logging_import: bool
    logger_name: str | None


def docstring_of(body: cst.BaseSuite) -> cst.BaseExpression | None:
    if isinstance(body, cst.IndentedBlock):
        first = body.body[0] if body.body else None
    else:
        first = body
    return _docstring_expr(first)


def module_docstring(module: cst.Module) -> cst.BaseExpression | None:
    return _docstring_expr(module.body[0]) if module.body else None


def _docstring_expr(statement: cst.CSTNode | None) -> cst.BaseExpression | None:
    if not isinstance(statement, (cst.SimpleStatementLine, cst.SimpleStatementSuite)):
        return None
    if not statement.body or not isinstance(statement.body[0], cst.Expr):
        return None
    value = statement.body[0].value
    if isinstance(value, cst.SimpleString) and isinstance(value.evaluated_value, str):
        return value
    if isinstance(value, cst.ConcatenatedString):
        return value
    return None


class NameCollector(cst.CSTVisitor):
    """Names a subtree reads or binds, ignoring attribute and keyword names."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.declared: set[str] = set()
        self._skip: set[int] = set()

    def visit_Attribute(self, node: cst.Attribute) -> None:
        self._skip.add(id(node.attr))

    def visit_Arg(self, node: cst.Arg) -> None:
        if node.keyword is not None:
            self._skip.add(id(node.keyword))

    def visit_Name(self, node: cst.Name) -> None:
        if id(node) not in self._skip:
            self.names.add(node.value)

    def visit_Global(self, node: cst.Global) -> None:
        self.declared.update(item.name.value for item in node.names)

    def visit_Nonlocal(self, node: cst.Nonlocal) -> None:
        self.declared.update(item.name.value for item in node.names)


def collect_names(node: cst.CSTNode) -> NameCollector:
    collector = NameCollector()
    node.visit(collector)
    return collector


class Index(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, module: cst.Module) -> None:
        self.module = module
        self.statements: dict[int, list[cst.CSTNode]] = {}
        self.functions: dict[str, list[Definition]] = {}
        self.classes: dict[str, list[Definition]] = {}
        self.literals: dict[int, list[Literal]] = {}
        self.names: set[str] = set()
        self.state = module_state(module)

        self._top_ids = {id(stmt): i for i, stmt in enumerate(module.body)}
        self._top = -1
        self._stack: list[str] = []
        self._indents: list[str] = []
        self._elifs: set[int] = set()
        self._skip: set[int] = set()
        self._annotation = 0
        self._pattern = 0

        doc = module_docstring(module)
        if doc is not None:
            self._skip.add(id(doc))

    def _line(self, node: cst.CSTNode) -> int:
        return self.get_metadata(PositionProvider, node).start.line

    def on_visit(self, node: cst.CSTNode) -> bool:
        self._top = self._top_ids.get(id(node), self._top)
        if isinstance(node, cst.If) and isinstance(node.orelse, cst.If):
            self._elifs.add(id(node.orelse))
        if isinstance(node, (cst.SimpleStatementLine, cst.BaseCompoundStatement)) \
                and id(node) not in self._elifs:
            self.statements.setdefault(self._line(node), []).append(node)
            if isinstance(node, (cst.FunctionDef, cst.ClassDef)) and node.decorators:
                self.statements.setdefault(self._line(node.decorators[0]), []).append(node)
        return super().on_visit(node)

    def visit_IndentedBlock(self, node: cst.IndentedBlock) -> None:
        self._indents.append(node.indent or self.module.default_indent)

    def leave_IndentedBlock(self, original_node: cst.IndentedBlock) -> None:
        self._indents.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._enter_definition(node, self.functions)

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
        self._stack.pop()

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._enter_definition(node, self.classes)

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        self._stack.pop()

    def _enter_definition(self, node, registry) -> None:
        top_level = not self._stack and id(node) in self._top_ids
        self._stack.append(node.name.value)
        if isinstance(node.body, cst.IndentedBlock):
            own = node.body.indent or self.module.default_indent
        else:
            own = self.module.default_indent
        info = Definition(node, ".".join(self._stack), top_level,
                          "".join(self._indents) + own, self._line(node))
        registry.setdefault(info.qualname, []).append(info)
        doc = docstring_of(node.body)
        if doc is not None:
            self._skip.add(id(doc))

    def visit_Name(self, node: cst.Name) -> None:
        self.names.add(node.value)

    def visit_Annotation(self, node: cst.Annotation) -> None:
        self._annotation += 1

    def leave_Annotation(self, original_node: cst.Annotation) -> None:
        self._annotation -= 1

    def visit_MatchCase_pattern(self, node: cst.MatchCase) -> None:
        self._pattern += 1

    def leave_MatchCase_pattern(self, node: cst.MatchCase) -> None:
        self._pattern -= 1

    def visit_ConcatenatedString(self, node: cst.ConcatenatedString) -> None:
        # Replacing one half of "a" "b" with a name would not parse.
        self._skip.add(id(node.left))
        self._skip.add(id(node.right))

    def visit_Integer(self, node: cst.Integer) -> None:
        self._literal(node, node.evaluated_value)

    def visit_Float(self, node: cst.Float) -> None:
        self._literal(node, node.evaluated_value)

    def visit_SimpleString(self, node: cst.SimpleString) -> None:
        value = node.evaluated_value
        if isinstance(value, str):
            self._literal(node, value)

    def visit_UnaryOperation(self, node: cst.UnaryOperation) -> None:
        inner = node.expression
        if (isinstance(node.operator, cst.Minus) and isinstance(inner, (cst.Integer, cst.Float))
                and not node.lpar and not inner.lpar):
            self._literal(node, -inner.evaluated_value)

    def _literal(self, node: cst.BaseExpression, value) -> None:
        if id(node) in self._skip or self._annotation or self._pattern:
            return
        self.literals.setdefault(self._line(node), []).append(Literal(node, value, self._top))


def module_state(module: cst.Module) -> ModuleState:
    insert_after = 0 if module_docstring(module) is not None else -1
    has_logging = False
    logger_name = None
    for i, statement in enumerate(module.body):
        if isinstance(statement, (cst.FunctionDef, cst.ClassDef)):
            break
        if not isinstance(statement, cst.SimpleStatementLine):
            continue
        for small in statement.body:
            if isinstance(small, (cst.Import, cst.ImportFrom)):
                insert_after = i
            if isinstance(small, cst.Import) and any(
                isinstance(a.name, cst.Name) and a.name.value == "logging" and a.asname is None
                for a in small.names
            ):
                has_logging = True
            name = _logger_assignment(small)
            if name and logger_name is None:
                logger_name = name
    return ModuleState(insert_after, has_logging, logger_name)


def _logger_assignment(small: cst.BaseSmallStatement) -> str | None:
    if not isinstance(small, cst.Assign) or len(small.targets) != 1:
        return None
    target, value = small.targets[0].target, small.value
    if not isinstance(target, cst.Name) or not isinstance(value, cst.Call):
        return None
    func = value.func
    if (isinstance(func, cst.Attribute) and func.attr.value == "getLogger"
            and isinstance(func.value, cst.Name) and func.value.value == "logging"):
        return target.value
    return None
