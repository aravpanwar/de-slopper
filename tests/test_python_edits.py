import pytest

from deslopper.edits import Edit
from deslopper.python.edits import apply_edits

SRC = '''import json


def total(items, scale):
    result = 0
    for item in items:
        result = result + item * scale
    return result


class Box:
    def size(self):
        return 3
'''


def run(source, kind, **args):
    output, entries = apply_edits(source, [Edit(kind, args)], "t.py")
    return output, entries[0]


def refusal(source, kind, **args):
    output, entry = run(source, kind, **args)
    assert entry.status == "skipped", entry
    assert output == source
    return entry.reason


def test_add_comment_goes_above_the_statement_and_keeps_human_comments():
    source = "x = 1\n# human note\ny = 2  # trailing\n"
    output, entry = run(source, "add_comment", line=3, text="Assign y")
    assert output == "x = 1\n# human note\n# Assign y\ny = 2  # trailing\n"
    assert entry.status == "applied" and entry.line == 3


def test_add_comment_refuses_lines_that_are_not_statement_starts():
    assert refusal(SRC, "add_comment", line=3, text="x") == "expected a statement start on line 3"
    elif_source = "if a:\n    pass\nelif b:\n    pass\nelse:\n    pass\n"
    assert "statement start" in refusal(elif_source, "add_comment", line=3, text="x")
    assert "statement start" in refusal(elif_source, "add_comment", line=5, text="x")


def test_add_docstring_on_function_and_class():
    output, _ = run(SRC, "add_docstring", target="total", text="Sum the items.\n\nReturns:\n    The sum.")
    assert '    """Sum the items.\n\n    Returns:\n        The sum.\n    """\n    result = 0' in output
    output, _ = run(SRC, "add_docstring", target="Box", text="A box.")
    assert 'class Box:\n    """A box."""\n    def size' in output


def test_add_docstring_refuses_an_existing_docstring():
    source = 'def f():\n    """Already here."""\n    return 1\n'
    assert refusal(source, "add_docstring", target="f", text="New") == "expected f without a docstring"


def test_add_docstring_escapes_quotes_and_handles_one_liners():
    output, _ = run("def f(): return 1\n", "add_docstring", target="f", text='Say """hi"""\\')
    compile(output, "t.py", "exec")
    namespace = {}
    exec(output, namespace)
    assert namespace["f"].__doc__ == 'Say """hi"""\\'
    assert namespace["f"]() == 1


def test_rename_covers_every_use_in_the_scope():
    output, entry = run(SRC, "rename", scope="total", old="result", new="running_total")
    assert output.count("running_total") == 4 and "result" not in output
    assert entry.status == "applied"


def test_rename_reaches_closures_but_not_keyword_arguments():
    source = "def f():\n    x = 1\n    def g():\n        return h(x=x)\n    return g\n"
    output, _ = run(source, "rename", scope="f", old="x", new="value")
    assert "return h(x=value)" in output and "    value = 1" in output


@pytest.mark.parametrize("scope, old, new, reason", [
    ("total", "scale", "scale_factor", "expected a local variable, got the parameter scale"),
    ("total", "result", "item", "expected a name unused in total, got item"),
    ("total", "result", "scale", "expected a name unused in total, got scale"),
    ("total", "result", "class", "expected a valid identifier, got class"),
    ("total", "result", "not valid", "expected a valid identifier, got not valid"),
    ("total", "result", "sum", "expected a name that is not a builtin, got sum"),
    ("total", "result", "result", "expected a new name different from result"),
    ("total", "missing", "other", "expected missing to be assigned in total"),
    ("nothing", "result", "other", "expected a function named nothing"),
])
def test_rename_refusals(scope, old, new, reason):
    assert refusal(SRC, "rename", scope=scope, old=old, new=new) == reason


@pytest.mark.parametrize("body, old, reason", [
    ("    x = 1\n    return locals()\n", "x", "expected f without locals()"),
    ("    x = 1\n    return eval('x')\n", "x", "expected f without eval()"),
    ("    global x\n    x = 1\n", "x", "expected x without a global or nonlocal declaration"),
    ("    x = 1\n    return f'{x=}'\n", "x", "expected x outside an f-string = specifier"),
    ("    import x\n    return x\n", "x", "expected x to be a plain variable"),
])
def test_rename_refuses_unsafe_scopes(body, old, reason):
    assert refusal(f"def f():\n{body}", "rename", scope="f", old=old, new="renamed_value") == reason


def test_rename_refuses_a_second_use_of_the_same_new_name():
    source = "def f():\n    a = 1\n    b = 2\n    return a + b\n"
    output, entries = apply_edits(source, [
        Edit("rename", {"scope": "f", "old": "a", "new": "first"}),
        Edit("rename", {"scope": "f", "old": "b", "new": "first"}),
    ], "t.py")
    assert [e.status for e in entries] == ["applied", "skipped"]
    assert "first = 1" in output and "b = 2" in output


def test_wrap_try_keeps_the_docstring_outside_and_picks_a_fresh_name():
    source = 'def f(x):\n    """Doc."""\n    e = x\n    return e\n'
    output, _ = run(source, "wrap_try", function="f", log_text="Failed in f: 100%")
    assert output == (
        'import logging\n\nlogger = logging.getLogger(__name__)\n\n\n'
        'def f(x):\n    """Doc."""\n    try:\n        e = x\n        return e\n'
        '    except Exception as exc:\n        logger.error("Failed in f: 100%%: %s", exc)\n        raise\n'
    )


def test_wrap_try_refuses_an_empty_body_and_works_on_one_liners():
    assert "expected a body after the docstring" in refusal('def f():\n    """Doc."""\n', "wrap_try", function="f", log_text="x")
    output, _ = run("def f(): return 1\n", "wrap_try", function="f", log_text="Failed")
    assert exec(output, namespace := {}) is None and namespace["f"]() == 1


def test_wrap_try_refuses_methods_that_raise_as_routine_flow():
    source = "class It:\n    def __next__(self):\n        raise StopIteration\n"
    assert refusal(source, "wrap_try", function="It.__next__", log_text="x") \
        == "expected It.__next__ not to raise as routine flow"


def test_add_log_emits_its_own_logger_call_with_the_message_only():
    output, _ = run("def f():\n    return 1\n", "add_log", function="f", text="Entering f with %s")
    assert 'def f():\n    logger.info("Entering f with %s")\n    return 1\n' in output
    assert output.startswith("import logging\n\nlogger = logging.getLogger(__name__)\n")


def test_logger_setup_reuses_what_exists_and_avoids_a_taken_name():
    source = "import logging\n\nlog = logging.getLogger('x')\n\n\ndef f():\n    return 1\n"
    output, _ = run(source, "add_log", function="f", text="Hi")
    assert output.count("import logging") == 1 and '    log.info("Hi")' in output

    source = "import logging\nlogger = make_thing()\n\n\ndef f():\n    return 1\n"
    output, _ = run(source, "add_log", function="f", text="Hi")
    assert 'log = logging.getLogger(__name__)' in output and '    log.info("Hi")' in output


def test_logger_refused_when_the_function_rebinds_the_name():
    source = "import logging\nlogger = logging.getLogger(__name__)\n\n\ndef f(logger):\n    return 1\n"
    assert refusal(source, "add_log", function="f", text="Hi") == "expected logger not to be rebound in f"


@pytest.mark.parametrize("text", [
    "if x is None:\n    logging.warning('x')\n    return []",
    "iteration_count = 0",
    "logging.info('Entering %s', name)",
    "return []",
])
def test_message_fields_holding_code_are_refused(text):
    source = "def f(x):\n    return x\n"
    assert refusal(source, "add_log", function="f", text=text) == "expected a message string, got code"
    assert refusal(source, "add_guard", function="f", arg="x", text=text) == "expected a message string, got code"
    assert refusal(source, "wrap_try", function="f", log_text=text) == "expected a message string, got code"


def test_add_guard_only_warns():
    output, _ = run("def f(x):\n    return x\n", "add_guard", function="f", arg="x", text="x is missing")
    assert 'def f(x):\n    if x is None:\n        logger.warning("x is missing")\n    return x\n' in output


def test_add_guard_refuses_a_name_that_is_not_a_parameter():
    source = "def f(x):\n    y = 1\n    return x\n"
    assert refusal(source, "add_guard", function="f", arg="y", text="y") == "expected a parameter of f, got y"


def test_add_import_variants():
    output, _ = run("import json\n\n\ndef f():\n    return 1\n", "add_import", module="typing", names=["List", "Dict"])
    assert output.startswith("import json\nfrom typing import List, Dict\n")
    output, _ = run("x = 1\n", "add_import", module="logging", names=["logging"])
    assert output == "import logging\n\nx = 1\n"
    output, _ = run("x = 1\n", "add_import", module="os.path")
    assert output.startswith("import os.path\n")


def test_add_import_refusals():
    assert "standard library" in refusal("x = 1\n", "add_import", module="requests", names=["get"])
    assert "standard library" in refusal("x = 1\n", "add_import", module="__future__", names=["annotations"])
    assert refusal("x = 1\n", "add_import", module="typing", names=["Nope"]) == "expected typing to provide Nope"
    assert refusal("List = 1\n", "add_import", module="typing", names=["List"]) == "expected unused names, got List"


def test_extract_constant_replaces_the_literal_on_that_line():
    source = "import os\n\n\ndef f(n):\n    return n * 86400 + 86400\n"
    output, entry = run(source, "extract_constant", line=5, value=86400, name="SECONDS_PER_DAY",
                        comment="One day in seconds")
    assert output == ("import os\n\n# One day in seconds\nSECONDS_PER_DAY = 86400\n\n\n"
                      "def f(n):\n    return n * SECONDS_PER_DAY + SECONDS_PER_DAY\n")
    assert entry.status == "applied"


def test_extract_constant_handles_strings_and_negative_numbers():
    output, _ = run("def f():\n    return 'abc', -1\n", "extract_constant", line=2, value="abc", name="LABEL")
    assert "LABEL = 'abc'" in output and "return LABEL, -1" in output
    output, _ = run("def f():\n    return 'abc', -1\n", "extract_constant", line=2, value=-1, name="MINUS_ONE")
    assert "MINUS_ONE = -1" in output and "return 'abc', MINUS_ONE" in output


@pytest.mark.parametrize("source, line, value, reason", [
    ("def f():\n    return 1\n", 2, 2, "expected the literal 2 on line 2"),
    ("def f():\n    return 1\n", 2, 1.0, "expected the literal 1.0 on line 2"),
    ("def f():\n    return 1\n", 2, True, "expected an int, float or str value"),
    ("def f(x: 'int'):\n    return 1\n", 1, "int", "expected the literal 'int' on line 1"),
    ("def f():\n    return 'a' 'b'\n", 2, "a", "expected the literal 'a' on line 2"),
    ('def f():\n    """doc"""\n    return 1\n', 2, "doc", "expected the literal 'doc' on line 2"),
    ("match x:\n    case 1:\n        pass\n", 2, 1, "expected the literal 1 on line 2"),
    ("DEBUG = 10\nimport os\n\n\ndef f():\n    pass\n", 1, 10, "expected the literal 10 after the import block"),
])
def test_extract_constant_refusals(source, line, value, reason):
    assert refusal(source, "extract_constant", line=line, value=value, name="CONSTANT") == reason


def test_extract_constant_refuses_a_name_already_in_use():
    assert refusal("CONSTANT = 1\nx = 2\n", "extract_constant", line=2, value=2, name="CONSTANT") \
        == "expected an unused name, got CONSTANT"


def test_add_wrapper_class_delegates_and_adds_an_instance():
    source = "def calc(a):\n    return a + 1\n\n\nx = 1\n"
    output, _ = run(source, "add_wrapper_class", function="calc", class_name="CalcManager")
    namespace = {}
    exec(output, namespace)
    assert namespace["calc_manager"].execute(2) == 3 == namespace["calc"](2)


def test_add_wrapper_class_refusals():
    methods = "class A:\n    def m(self):\n        return 1\n"
    assert refusal(methods, "add_wrapper_class", function="A.m", class_name="MManager") \
        == "expected a module-level function, got A.m"
    source = "def calc():\n    pass\n\n\nclass CalcManager:\n    pass\n"
    assert refusal(source, "add_wrapper_class", function="calc", class_name="CalcManager") \
        == "expected an unused name, got CalcManager"
    assert "CapWords" in refusal("def calc():\n    pass\n", "add_wrapper_class", function="calc", class_name="manager")


def test_header_and_footer_are_normalized_to_comment_lines():
    source = "#!/usr/bin/env python\n# -*- coding: utf-8 -*-\nx = 1\n"
    output, _ = apply_edits(source, [
        Edit("add_header", {"text": "# Here's the improved version!\nNow with more comments\n\n## done"}),
        Edit("add_footer", {"text": "End of file"}),
    ], "t.py")
    assert output == (
        "#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n"
        "# Here's the improved version!\n# Now with more comments\n# done\n\n"
        "x = 1\n\n# End of file\n"
    )


def test_text_in_comments_never_becomes_code():
    output, _ = run("x = 1\n", "add_comment", line=1, text="def calc(a,b,c):\nimport os; os.remove('x')")
    assert output == "# def calc(a,b,c):\n# import os; os.remove('x')\nx = 1\n"


def test_crlf_bom_and_unchanged_source_survive():
    source = "\ufeffdef f():\r\n    return 1\r\n"
    output, _ = run(source, "add_comment", line=1, text="Hi")
    assert output == "\ufeff# Hi\r\ndef f():\r\n    return 1\r\n"
    assert run(source, "add_comment", line=9, text="Hi")[0] == source


def test_unparseable_source_skips_every_edit():
    output, entries = apply_edits("def f(:\n", [Edit("add_comment", {"line": 1, "text": "x"})], "t.py")
    assert output == "def f(:\n"
    assert entries[0].reason == "expected valid Python, got a syntax error on line 1"


def test_bare_method_names_resolve_when_unique():
    source = "class A:\n    def run(self):\n        return 1\n"
    output, _ = run(source, "add_docstring", target="run", text="Runs.")
    assert '    def run(self):\n        """Runs."""' in output
    two = source + "\nclass B:\n    def run(self):\n        return 2\n"
    assert refusal(two, "add_docstring", target="run", text="Runs.") == "expected one function or class named run, found 2"




def test_add_wrapper_class_docstring_is_optional_and_must_be_a_string():
    source = "def calc():\n    pass\n"
    output, _ = run(source, "add_wrapper_class", function="calc", class_name="CalcManager",
                    docstring="Manages calc.\n\nSecond paragraph.")
    assert 'class CalcManager:\n    """Manages calc.\n\n    Second paragraph.\n    """\n\n    def execute' in output
    assert refusal(source, "add_wrapper_class", function="calc", class_name="CalcManager", docstring=5) \
        == "expected a string docstring"
