import ast
import json
import textwrap

from deslopper.textutil import Refusal, clean, one_line


def string_literal(text: str) -> str:
    quoted = json.dumps(one_line(text), ensure_ascii=False)
    # Python's tokenizer accepts these inside a string, but line-splitting
    # tools do not, so they are written as escapes.
    for char in ("\u0085", "\u2028", "\u2029"):
        quoted = quoted.replace(char, f"\\u{ord(char):04x}")
    return quoted


def comment_lines(text: str) -> list[str]:
    lines = []
    for raw in clean(text).splitlines():
        body = raw.strip().lstrip("#").strip()
        if body:
            lines.append(f"# {body}")
    return lines


def message(text: str) -> str:
    # Message fields come back from the model with code pasted into them
    # ("if x is None: return []", "logging.info(...)"). They are only ever
    # used as a string literal, and text that parses as code is refused so
    # the output never carries a log line made of source code.
    collapsed = one_line(text)
    if not collapsed:
        raise Refusal("expected a message string")
    if looks_like_code(text):
        raise Refusal("expected a message string, got code")
    return collapsed


def looks_like_code(text: str) -> bool:
    try:
        tree = ast.parse(textwrap.dedent(clean(text)).strip())
    except (SyntaxError, ValueError):
        return False
    for node in tree.body:
        if not isinstance(node, ast.Expr) or isinstance(node.value, ast.Call):
            return True
    return False


def docstring_source(text: str, indent: str) -> str:
    lines = textwrap.dedent(clean(text)).strip("\n").splitlines()
    escaped = [line.replace("\\", "\\\\").replace('"""', '""\\"') for line in lines]
    if len(escaped) == 1:
        single = escaped[0].strip()
        if single.endswith('"'):
            single = single[:-1] + '\\"'
        return f'"""{single}"""'
    body = [escaped[0].strip()] + [indent + line if line.strip() else "" for line in escaped[1:]]
    return '"""' + "\n".join(body) + "\n" + indent + '"""'
