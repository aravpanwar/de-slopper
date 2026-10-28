from dataclasses import dataclass, field

import libcst as cst

from deslopper.python.index import docstring_of


@dataclass
class FunctionPlan:
    docstring: str | None = None
    log: str | None = None
    guards: list[tuple[str, str]] = field(default_factory=list)
    try_message: str | None = None
    exc_name: str = "exc"


@dataclass
class Constant:
    name: str
    source: str
    comment: list[str]


@dataclass
class Plan:
    comments: dict[int, list[str]] = field(default_factory=dict)
    renames: dict[int, str] = field(default_factory=dict)
    literals: dict[int, str] = field(default_factory=dict)
    functions: dict[int, FunctionPlan] = field(default_factory=dict)
    class_docstrings: dict[int, str] = field(default_factory=dict)
    wrappers: dict[int, tuple[str, str, str, bool, str | None]] = field(default_factory=dict)
    imports: dict[str, None] = field(default_factory=dict)
    logger_name: str | None = None
    logger_assignment: bool = False
    constants: list[Constant] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    footer: list[str] = field(default_factory=list)
    insert_after: int = -1

    def touched(self) -> bool:
        return any((self.comments, self.renames, self.literals, self.functions,
                    self.class_docstrings, self.wrappers, self.imports, self.logger_assignment,
                    self.constants, self.header, self.footer))


def comment_line(text: str) -> cst.EmptyLine:
    return cst.EmptyLine(comment=cst.Comment(text))


def as_block(body: cst.BaseSuite) -> cst.IndentedBlock:
    # A one-line body such as "def f(): return 1" has no room for new
    # statements until it becomes a real block.
    if isinstance(body, cst.IndentedBlock):
        return body
    return cst.IndentedBlock(
        header=body.trailing_whitespace,
        body=[cst.SimpleStatementLine(body=body.body)],
    )


class Rewrite(cst.CSTTransformer):
    def __init__(self, plan: Plan):
        super().__init__()
        self.plan = plan

    def on_leave(self, original_node, updated_node):
        updated = super().on_leave(original_node, updated_node)
        lines = self.plan.comments.get(id(original_node))
        if lines and hasattr(updated, "leading_lines"):
            updated = updated.with_changes(
                leading_lines=[*updated.leading_lines, *map(comment_line, lines)])
        return updated

    def leave_Name(self, original_node, updated_node):
        new = self.plan.renames.get(id(original_node))
        return updated_node.with_changes(value=new) if new else updated_node

    def _constant(self, original_node, updated_node):
        name = self.plan.literals.get(id(original_node))
        if name is None:
            return updated_node
        return cst.Name(name, lpar=updated_node.lpar, rpar=updated_node.rpar)

    leave_Integer = leave_Float = leave_SimpleString = leave_UnaryOperation = _constant

    def leave_FunctionDef(self, original_node, updated_node):
        plan = self.plan.functions.get(id(original_node))
        if plan is None:
            return updated_node
        block = as_block(updated_node.body)
        statements = list(block.body)
        has_doc = docstring_of(block) is not None
        head = statements[:1] if has_doc else []
        rest = statements[1:] if has_doc else statements
        if plan.docstring:
            head = [_docstring_statement(plan.docstring)]
        logger = self.plan.logger_name
        entry = []
        if plan.log:
            entry.append(cst.parse_statement(f"{logger}.info({plan.log})\n"))
        for arg, text in plan.guards:
            entry.append(cst.parse_statement(
                f"if {arg} is None:\n    {logger}.warning({text})\n"))
        rest = entry + rest
        if plan.try_message:
            rest = [self._try(rest, plan)]
        return updated_node.with_changes(body=block.with_changes(body=head + rest))

    def _try(self, statements, plan: FunctionPlan) -> cst.Try:
        template = cst.parse_statement(
            "try:\n    pass\n"
            f"except Exception as {plan.exc_name}:\n"
            f"    {self.plan.logger_name}.error({plan.try_message}, {plan.exc_name})\n"
            "    raise\n")
        return template.with_changes(body=cst.IndentedBlock(body=statements))

    def leave_ClassDef(self, original_node, updated_node):
        text = self.plan.class_docstrings.get(id(original_node))
        if text is None:
            return updated_node
        block = as_block(updated_node.body)
        return updated_node.with_changes(
            body=block.with_changes(body=[_docstring_statement(text), *block.body]))

    def leave_Module(self, original_node, updated_node):
        plan = self.plan
        block = self._block()
        body: list[cst.BaseStatement] = []
        after_block = None
        if plan.insert_after == -1 and block:
            body.extend(block)
            after_block = len(body)
        for index, (original, updated) in enumerate(zip(original_node.body, updated_node.body)):
            body.append(updated)
            wrapper = plan.wrappers.get(id(original))
            if wrapper:
                body.extend(self._wrapper(*wrapper))
            if index == plan.insert_after and block:
                body.extend(block)
                after_block = len(body)
        if after_block is not None and after_block < len(body):
            body[after_block] = _spaced_above(body[after_block])
        header = _with_header(updated_node.header, plan.header)
        footer = list(updated_node.footer)
        if plan.footer:
            footer += [cst.EmptyLine(), *map(comment_line, plan.footer)]
        return updated_node.with_changes(body=body, header=header, footer=footer)

    def _wrapper(self, function, class_name, instance, is_async, docstring) -> list[cst.BaseStatement]:
        keyword, call = ("async def", "await ") if is_async else ("def", "")
        cls = cst.parse_statement(
            f"class {class_name}:\n"
            f"    {keyword} execute(self, *args, **kwargs):\n"
            f"        return {call}{function}(*args, **kwargs)\n")
        if docstring:
            method = cls.body.body[0].with_changes(leading_lines=[cst.EmptyLine(indent=False)])
            cls = cls.with_changes(body=cls.body.with_changes(
                body=[_docstring_statement(docstring), method]))
        instance_line = cst.parse_statement(f"{instance} = {class_name}()\n")
        return [
            cls.with_changes(leading_lines=[cst.EmptyLine(), cst.EmptyLine()]),
            instance_line.with_changes(leading_lines=[cst.EmptyLine(), cst.EmptyLine()]),
        ]

    def _block(self) -> list[cst.BaseStatement]:
        plan = self.plan
        block: list[cst.BaseStatement] = [cst.parse_statement(f"{text}\n") for text in plan.imports]
        spaced = plan.insert_after >= 0 or bool(block)
        if plan.logger_assignment:
            statement = cst.parse_statement(f"{plan.logger_name} = logging.getLogger(__name__)\n")
            block.append(_spaced(statement, spaced))
            spaced = True
        for constant in plan.constants:
            statement = cst.parse_statement(f"{constant.name} = {constant.source}\n")
            lines = [cst.EmptyLine()] if spaced else []
            lines.extend(map(comment_line, constant.comment))
            block.append(statement.with_changes(leading_lines=lines))
            spaced = False
        return block


def _spaced(statement: cst.BaseStatement, spaced: bool) -> cst.BaseStatement:
    return statement.with_changes(leading_lines=[cst.EmptyLine()]) if spaced else statement


def _spaced_above(statement: cst.BaseStatement) -> cst.BaseStatement:
    lines = statement.leading_lines
    wanted = 2 if isinstance(statement, (cst.FunctionDef, cst.ClassDef)) else 1
    blanks = 0
    for line in lines:
        if line.comment is not None:
            break
        blanks += 1
    if blanks >= wanted:
        return statement
    return statement.with_changes(
        leading_lines=[*(cst.EmptyLine() for _ in range(wanted - blanks)), *lines])


def _docstring_statement(source: str) -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(body=[cst.Expr(cst.SimpleString(source))])


def _with_header(header, lines: list[str]):
    if not lines:
        return header
    # The shebang and the encoding cookie only work on the first two lines.
    keep = 0
    for index, line in enumerate(header[:2]):
        comment = line.comment.value if line.comment else ""
        if comment.startswith("#!") or "coding" in comment:
            keep = index + 1
    new = [*map(comment_line, lines), cst.EmptyLine()]
    return [*header[:keep], *new, *header[keep:]]
