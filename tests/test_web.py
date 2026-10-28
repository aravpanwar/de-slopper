import pytest
from bs4 import BeautifulSoup

from conftest import CannedProvider
from deslopper.edits import Edit
from deslopper.engines import PassthroughEngine
from deslopper.sweep import copy_page_assets, sweep_directory, sweep_source
from deslopper.web.assets import copy_assets
from deslopper.web.checks import check_output
from deslopper.web.edits import apply_edits

PAGE = (
    "<html>\n<head>\n<title>T</title>\n</head>\n<body>\n<h1>Hello</h1>\n"
    '<p>Text <a href="a.html">link</a></p>\n<table><tr><td>cell</td></tr></table>\n<ul>\n'
    '<li>one</li>\n</ul>\n<form action="/x"><input name="q"></form>\n<script>var x = 1;</script>\n'
    "</body>\n</html>\n"
)
FEATURES = [{"emoji": "🚀", "title": f"Feature {n}", "text": "Fast."} for n in range(3)]


def run(kind, **args):
    output, entries = apply_edits(PAGE, [Edit(kind, args)], "p.html")
    return output, entries[0]


def refusal(kind, **args):
    output, entry = run(kind, **args)
    assert entry.status == "skipped", entry
    assert output == PAGE
    return entry.reason


def soup(markup):
    return BeautifulSoup(markup, "html.parser")


def only_edits(entries):
    return [e for e in entries if e.kind != "strip"]


def test_add_classes_merges_with_existing_classes_and_sets_up_the_head():
    page = PAGE.replace("<h1>", '<h1 class="old">')
    output, entry = apply_edits(page, [Edit("add_classes", {"line": 6, "tag": "h1", "classes": "text-4xl old font-bold"})], "p.html")
    assert entry[0].status == "applied" and entry[0].line == 6
    tree = soup(output)
    classes = tree.h1["class"]
    assert classes[0] == "old" and classes[-1] == "font-bold" and classes.count("text-4xl") == 1
    assert tree.find("script", src="https://cdn.tailwindcss.com/3.4.17")
    assert tree.find("link", href=lambda h: h and "family=Inter" in h)
    assert tree.find("meta", attrs={"name": "viewport"}) and tree.find("meta", charset=True)
    assert "dark:bg-slate-950" in tree.body["class"][3] or "dark:bg-slate-950" in tree.body["class"]
    assert str(tree).startswith("<!DOCTYPE html>")


@pytest.mark.parametrize("classes, reason", [
    ('text-xl" onclick="alert(1)', 'expected a Tailwind class name, got text-xl"'),
    ("<script>", "expected a Tailwind class name, got <script>"),
    ("a=b", "expected a Tailwind class name, got a=b"),
    ("x" * 61, f"expected a Tailwind class name, got {'x' * 61}"),
    ("", "expected a string of classes"),
    (7, "expected a string of classes"),
    (" ".join(f"c{n}" for n in range(41)), "expected at most 40 classes, got 41"),
])
def test_add_classes_accepts_class_names_and_nothing_else(classes, reason):
    assert refusal("add_classes", line=6, classes=classes) == reason


def test_add_classes_accepts_tailwind_syntax_and_lists():
    classes = ["hover:scale-105", "w-1/2", "bg-white/80", "dark:bg-[#123456]", "!mt-4"]
    output, entry = run("add_classes", line=6, classes=classes)
    assert entry.status == "applied" and soup(output).h1["class"][-5:] == classes


def test_add_classes_resolves_elements_by_line_and_tag():
    assert refusal("add_classes", line=3, classes="x") == "expected an element that can be styled, got title"
    assert refusal("add_classes", line=99, classes="x") == "expected an element starting on line 99"
    assert refusal("add_classes", line=6, tag="p", classes="x") == "expected an element named p starting on line 6"
    assert refusal("add_classes", line="six", classes="x") == "expected an integer line"
    output, _ = run("add_classes", line=7, tag="a", classes="font-medium")
    assert "font-medium" in soup(output).a["class"]


def test_wrap_builds_the_layout_around_the_original_element():
    output, entry = run("wrap", line=6, tag="h1", layout="card")
    assert entry.status == "applied"
    card = soup(output).h1.parent
    assert card.name == "div" and "rounded-2xl" in card["class"] and card.has_attr("data-fade")
    assert run("wrap", line=8, layout="section")[0].count("<section") == 1


@pytest.mark.parametrize("line, tag", [(8, "td"), (8, "tr"), (10, "li"), (3, "title"), (5, "body")])
def test_wrap_refuses_structural_elements(line, tag):
    assert refusal("wrap", line=line, tag=tag, layout="card").startswith("expected an element that can be wrapped")


def test_wrap_refuses_a_bad_layout_and_a_second_wrap():
    assert refusal("wrap", line=6, layout="marquee") == "expected a layout of section, card, grid, container, got marquee"
    output, entries = apply_edits(PAGE, [Edit("wrap", {"line": 6, "layout": "card"}),
                                         Edit("wrap", {"line": 6, "layout": "grid"})], "p.html")
    assert [e.status for e in only_edits(entries)] == ["applied", "skipped"]
    assert entries[1].reason == "expected <h1> to be wrapped once"


def test_model_text_is_always_escaped_never_markup():
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    output, _ = run("insert_block", block="hero", headline=evil, subheadline="Fine", cta='"Go" & <go>')
    assert "<script>alert(1)" not in output and "onerror=alert(2)>" not in output
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in output
    assert '"Go" &amp; &lt;go&gt;' in output
    assert len(soup(output).find_all("script")) == 2 + 1  # the page's own, Tailwind, and its config


def test_blocks_go_around_the_original_content_in_a_fixed_order():
    edits = [
        Edit("insert_block", {"block": "footer", "text": "Built with ❤️ and AI"}),
        Edit("insert_block", {"block": "faq", "faq": [{"q": "Why?", "a": "Because."}, {"q": "How?", "a": "Magic."}]}),
        Edit("insert_block", {"block": "features", "features": FEATURES}),
        Edit("insert_block", {"block": "hero", "headline": "Big", "subheadline": "Bold"}),
        Edit("insert_block", {"block": "testimonials", "testimonials": [{"name": "Gustav Victor", "role": "CEO", "quote": "Great."}]}),
        Edit("insert_block", {"block": "stats", "stats": [{"value": "10k+", "label": "Users"}, {"value": "99%", "label": "Up"}]}),
        Edit("insert_block", {"block": "cta", "headline": "Go", "button": "Now"}),
    ]
    output, entries = apply_edits(PAGE, edits, "p.html")
    assert {e.status for e in entries} == {"applied"}
    order = [t["data-slop"] for t in soup(output).body.find_all(attrs={"data-slop": True}, recursive=False)]
    assert order == ["hero", "stats", "features", "original", "testimonials", "faq", "cta", "footer"]
    assert soup(output).find("a", attrs={"data-cta": True})["href"] == "#features"
    assert 'id="features"' in output


def test_original_content_goes_in_a_centered_container_in_its_original_order():
    output, _ = run("add_script", script="fade_in_on_scroll")
    tree = soup(output)
    main = tree.body.find("main", attrs={"data-slop": "original"})
    assert "max-w-6xl" in main["class"] and "mx-auto" in main["class"]
    assert [t.name for t in main.find_all(["h1", "p", "table", "ul", "form", "script"], recursive=False)] \
        == ["h1", "p", "table", "ul", "form", "script"]
    empty, _ = apply_edits("<body></body>", [Edit("add_script", {"script": "dark_mode_toggle"})], "e.html")
    assert not soup(empty).find("main")


def test_block_content_is_validated():
    assert refusal("insert_block", block="pricing") == "expected a block of hero, stats, features, testimonials, faq, cta, footer, got pricing"
    assert refusal("insert_block", block="hero", headline="x") == "expected a string subheadline"
    assert refusal("insert_block", block="hero", headline="x" * 121, subheadline="y") == "expected headline of at most 120 characters"
    assert refusal("insert_block", block="features", features=FEATURES[:2]) == "expected at least 3 features, got 2"
    assert refusal("insert_block", block="stats", stats=[{"value": "1"}, {"value": "2"}]) == "expected a string label"
    assert refusal("insert_block", block="faq", faq="no") == "expected a list of faq"
    assert refusal("insert_block", block="faq", faq=[1, 2]) == "expected each of the faq to be an object"
    assert refusal("insert_block", block="footer") == "expected a string text"


def test_a_block_may_only_be_inserted_once_and_only_three_features_are_used():
    output, entries = apply_edits(PAGE, [
        Edit("insert_block", {"block": "features", "features": FEATURES + FEATURES}),
        Edit("insert_block", {"block": "features", "features": FEATURES}),
    ], "p.html")
    assert [e.status for e in only_edits(entries)] == ["applied", "skipped"]
    assert entries[1].reason == "expected one features block"
    assert output.count("rounded-2xl bg-white p-8 shadow-xl shadow-purple-500") == 3


def test_footer_always_says_built_with_a_heart_exactly_once():
    plain, _ = run("insert_block", block="footer", text="Made in a small town")
    assert plain.count("Built with ❤️") == 1
    said, _ = run("insert_block", block="footer", text="Built with ❤️ and AI")
    assert said.count("Built with") == 1


def test_scripts_are_written_by_the_tool_and_both_are_optional_and_once():
    output, entries = apply_edits(PAGE, [Edit("add_script", {"script": "dark_mode_toggle"}),
                                         Edit("add_script", {"script": "fade_in_on_scroll"}),
                                         Edit("add_script", {"script": "dark_mode_toggle"}),
                                         Edit("add_script", {"script": "confetti"})], "p.html")
    assert [e.status for e in only_edits(entries)] == ["applied", "applied", "skipped", "skipped"]
    assert entries[3].reason == "expected a script of dark_mode_toggle, fade_in_on_scroll, got confetti"
    tree = soup(output)
    assert tree.find("button", id="theme-toggle") and "localStorage" in output
    assert "IntersectionObserver" in output and ".fade-in" in tree.head.find("style").string


def test_set_head_adds_meta_tags_and_never_duplicates_one():
    page = PAGE.replace("<title>", '<meta name="description" content="mine"><title>')
    output, entries = apply_edits(page, [Edit("set_head", {
        "description": "New", "keywords": ["a", "b"], "theme_color": "#7c3aed"})], "p.html")
    tree = soup(output)
    assert [m["content"] for m in tree.find_all("meta", attrs={"name": "description"})] == ["mine"]
    assert tree.find("meta", attrs={"name": "keywords"})["content"] == "a, b"
    assert tree.find("meta", attrs={"name": "theme-color"})["content"] == "#7c3aed"
    assert refusal("set_head", theme_color="purple") == "expected a theme_color like #7c3aed, got purple"
    assert refusal("set_head") == "expected a description, keywords or theme_color"


def test_a_fragment_gets_a_document_around_it_and_loses_nothing():
    fragment = "<h1>Hi</h1><p>text <a href='x.html'>x</a></p>"
    output, _ = apply_edits(fragment, [Edit("add_classes", {"line": 1, "tag": "h1", "classes": "text-2xl"})], "f.html")
    tree = soup(output)
    assert tree.html and tree.head and tree.body.h1.get_text() == "Hi"
    assert check_output(fragment, output, None).problem is None


def test_a_page_that_every_edit_skipped_is_returned_untouched():
    output, entries = apply_edits(PAGE, [Edit("add_classes", {"line": 99, "classes": "x"})], "p.html")
    assert output == PAGE and entries[0].status == "skipped"
    assert apply_edits(PAGE, [], "p.html") == (PAGE, [])


def test_the_content_check_notices_anything_missing_and_ignores_additions():
    base = PAGE
    assert check_output(base, base.replace("<h1>", "<h1 class='a'>") + "<p>extra</p>", None).problem is None
    assert check_output(base, base.replace("Hello", "Goodbye"), None).problem == \
        "expected every original text to survive, missing 'Hello'"
    assert check_output(base, base.replace('href="a.html"', ""), None).problem == \
        "expected every original link (href) to survive, missing 'a.html'"
    assert check_output('<img src="p.png">', "<img>", None).problem == \
        "expected every original image or file (src) to survive, missing 'p.png'"
    assert check_output(base, base.replace('name="q"', ""), None).problem == \
        "expected every original form field name to survive, missing 'q'"
    assert check_output(base, base.replace("var x = 1;", "var x = 2;"), None).problem.startswith(
        "expected every original script to survive")
    # The original styling is replaced on purpose, so losing it is fine.
    assert check_output('<link rel="stylesheet" href="s.css"><style>p{}</style><p>a</p>', "<p>a</p>", None).problem is None


def test_the_content_check_compares_counts_and_ignores_whitespace_and_comments():
    assert check_output("<p>a</p><p>a</p>", "<p>a</p>", None).problem == \
        "expected every original text to survive, missing 'a'"
    assert check_output("<p>a   b</p><!-- c -->", "<p>\n a b </p>", None).problem is None


def test_assets_next_to_a_page_are_copied_without_overwriting_or_escaping(tmp_path):
    source, dest = tmp_path / "in", tmp_path / "out"
    (source / "img").mkdir(parents=True)
    (source / "style.css").write_text("body{}", encoding="utf-8")
    (source / "img" / "a.png").write_bytes(b"png")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    dest.mkdir()
    (dest / "keep.css").write_text("mine", encoding="utf-8")
    (source / "keep.css").write_text("theirs", encoding="utf-8")
    page = ('<link rel="stylesheet" href="style.css"><link rel="stylesheet" href="keep.css">'
            '<img src="img/a.png"><img src="missing.png"><img src="https://x.test/b.png">'
            '<img src="/abs.png"><img src="../secret.txt"><link rel="canonical" href="a.html">')
    assert copy_assets(source, dest, page) == ["style.css", "img/a.png"]
    assert (dest / "keep.css").read_text(encoding="utf-8") == "mine"
    assert not (dest / "secret.txt").exists() and not (tmp_path / "out" / "abs.png").exists()
    assert copy_page_assets("python", "a.py", page, source, dest) == []


class FailingHtmlEngine(PassthroughEngine):
    writes_on_failure = False

    def apply(self, source, edits, name):
        entries = super().apply(source, edits, name)[1]
        return source + "<p>new</p>", [e.__class__(e.file, e.kind, "applied") for e in entries]

    def check(self, source, output, origin):
        from deslopper.engines import Verdict
        return Verdict("expected every original text to survive, missing 'x'")


def test_a_failed_page_check_writes_no_output_and_says_why(cache):
    reply = '{"edits": [{"kind": "add_classes", "line": 1, "classes": "x"}]}'
    result = sweep_source("html", PAGE, "p.html", CannedProvider(reply), cache, engine=FailingHtmlEngine("html"))
    assert result.output is None
    statuses = {e.kind: e.status for e in result.entries}
    assert statuses["add_classes"] == "dropped"
    assert any(e.status == "note" and e.reason == "output not written for p.html" for e in result.entries)


def test_a_directory_run_copies_the_stylesheet_and_skips_a_page_that_failed(tmp_path, cache, monkeypatch):
    root, out = tmp_path / "in", tmp_path / "out"
    root.mkdir()
    (root / "home.html").write_text('<link rel="stylesheet" href="s.css"><p>hi</p>', encoding="utf-8")
    (root / "s.css").write_text("p{}", encoding="utf-8")
    entries = sweep_directory(root, out, CannedProvider("[]"), cache)
    assert (out / "s.css").read_text(encoding="utf-8") == "p{}"
    assert [e.reason for e in entries if e.kind == "assets"] == ["copied s.css next to the output"]

    from deslopper import sweep
    monkeypatch.setitem(sweep.ENGINES, "html", FailingHtmlEngine("html"))
    reply = '{"edits": [{"kind": "add_classes", "line": 1, "classes": "x"}]}'
    sweep_directory(root, tmp_path / "out2", CannedProvider(reply), cache)
    assert not (tmp_path / "out2" / "home.html").exists()
    assert not (tmp_path / "out2" / ".marker.json").exists()


LEGACY = (
    '<html><head><title>Old</title><style>p { color: red }</style>'
    '<link rel="stylesheet" href="old.css"><link rel="icon" href="fav.ico"></head>\n'
    '<body bgcolor="#ffcc00" text="#000000" link="#0000ff">\n'
    '<center><font face="Comic Sans MS" color="red" size="5">Welcome</font> to my <blink>page</blink></center>\n'
    '<marquee scrollamount="3">Gustav says hello</marquee>\n'
    '<table width="600" border="1" cellpadding="4" align="center"><tr><td width="50%" bgcolor="#eee">cell</td></tr></table>\n'
    '<img src="g.png" width="120" height="80" align="left">\n'
    '<ul>\n<li>one</li>\n<li>two</li>\n<li>three</li>\n</ul>\n'
    '<p style="color: blue" align="center">Plain <a href="x.html">link</a></p>\n'
    '<input name="q" size="20"><input type="submit" value="Go"><hr size="1" noshade>\n'
    '</body></html>\n'
)


def legacy(*edits):
    output, entries = apply_edits(LEGACY, list(edits), "o.html")
    return soup(output), entries


def test_the_original_styling_is_removed_and_the_words_are_kept():
    tree, entries = legacy(Edit("add_script", {"script": "dark_mode_toggle"}))
    assert not tree.find_all(["font", "center", "marquee", "blink"])
    assert not tree.find("style", string=lambda t: t and "color: red" in t)
    assert not tree.find("link", href="old.css") and tree.find("link", href="fav.ico")
    body = tree.body
    for attribute in ("bgcolor", "text", "link"):
        assert not body.has_attr(attribute)
    for tag in tree.find_all(["table", "td", "p", "hr"]):
        left = [a for a in ("style", "align", "bgcolor", "width", "border", "cellpadding", "size", "noshade")
                if tag.has_attr(a)]
        assert left == [], tag
    assert tree.img["width"] == "120" and not tree.img.has_attr("align")
    assert tree.find("input", attrs={"name": "q"})["size"] == "20"
    text = tree.find("main", attrs={"data-slop": "original"}).get_text(" ", strip=True)
    assert "Welcome to my page" in text and "Gustav says hello" in text
    strip = [e for e in entries if e.kind == "strip"][0]
    assert strip.status == "applied" and strip.counts["tags"] == 4 and strip.counts["styles"] == 2
    assert strip.counts["attributes"] == 14
    assert check_output(LEGACY, str(tree), None).problem is None


def test_every_original_element_gets_utility_classes():
    tree, _ = legacy(Edit("add_script", {"script": "dark_mode_toggle"}))
    main = tree.find("main", attrs={"data-slop": "original"})
    bare = [t.name for t in main.find_all(True) if not t.get("class") and t.name not in ("br", "script")]
    assert bare == []
    assert "rounded-2xl" in tree.table["class"] and "shadow-xl" in tree.table["class"]
    assert "rounded-full" in tree.find("input", attrs={"type": "submit"})["class"]
    assert "rounded-xl" in tree.find("input", attrs={"name": "q"})["class"]


def test_restyle_gives_an_element_a_named_style_once():
    tree, entries = legacy(Edit("restyle", {"line": 12, "tag": "a", "style": "pill_button"}),
                           Edit("restyle", {"line": 12, "tag": "a", "style": "badge"}))
    assert [e.status for e in only_edits(entries)] == ["applied", "skipped"]
    assert entries[1].reason == "expected <a> to be styled once"
    assert "rounded-full" in tree.a["class"] and "from-violet-600" in tree.a["class"]
    assert "underline" not in tree.a["class"]


@pytest.mark.parametrize("args, reason", [
    ({"line": 12, "tag": "a", "style": "sparkle"},
     "expected a style of gradient_text, pill_button, card, pricing_card, frame, table, lead, badge, callout, got sparkle"),
    ({"line": 12, "tag": "p", "style": "pill_button"}, "expected pill_button on a, button, input, got p"),
    ({"line": 3, "tag": "font", "style": "card"}, "expected current markup, got the legacy tag font"),
    ({"line": 1, "tag": "title", "style": "card"}, "expected an element that can be styled, got title"),
])
def test_restyle_refuses_what_does_not_fit(args, reason):
    output, entries = apply_edits(LEGACY, [Edit("restyle", args)], "o.html")
    assert entries[0].status == "skipped" and entries[0].reason == reason
    assert output == LEGACY


def test_regroup_turns_a_list_into_a_grid_of_cards():
    tree, entries = legacy(Edit("regroup", {"line": 7, "tag": "ul", "layout": "feature_grid"}))
    ul = tree.find("ul")
    assert entries[0].status == "applied" and "grid" in ul["class"] and "lg:grid-cols-3" in ul["class"]
    assert all("rounded-2xl" in li["class"] for li in ul.find_all("li"))
    assert [li.get_text() for li in ul.find_all("li")] == ["one", "two", "three"]


@pytest.mark.parametrize("args, reason", [
    ({"line": 7, "tag": "ul", "layout": "carousel"},
     "expected a layout of feature_grid, duo_grid, stat_row, card_stack, gallery, logo_cloud, got carousel"),
    ({"line": 5, "tag": "table", "layout": "feature_grid"}, "expected a container of div, ol, section, ul, got table"),
])
def test_regroup_refuses_a_bad_layout_or_container(args, reason):
    output, entries = apply_edits(LEGACY, [Edit("regroup", args)], "o.html")
    assert entries[0].reason == reason


def test_regroup_needs_a_few_items_and_only_list_items_in_a_list():
    page = "<ul><li>only</li></ul><ol><li>a</li><li>b</li></ol>"
    _, entries = apply_edits(page, [Edit("regroup", {"line": 1, "tag": "ul", "layout": "duo_grid"}),
                                    Edit("regroup", {"line": 1, "tag": "ol", "layout": "duo_grid"})], "l.html")
    assert entries[0].reason == "expected 2 to 12 items inside <ul>, got 1"
    assert entries[1].status == "applied"
    mixed = "<ul><li>a</li><p>b</p></ul>"
    _, entries = apply_edits(mixed, [Edit("regroup", {"line": 1, "tag": "ul", "layout": "duo_grid"})], "m.html")
    assert entries[0].reason == "expected only list items inside <ul>"


def test_an_item_of_a_regrouped_list_cannot_be_styled_again():
    page = "<ul><li>a</li><li>b</li></ul>"
    _, entries = apply_edits(page, [Edit("regroup", {"line": 1, "tag": "ul", "layout": "duo_grid"}),
                                    Edit("restyle", {"line": 1, "tag": "li", "style": "badge"})], "l.html")
    assert [e.status for e in only_edits(entries)] == ["applied", "skipped"]
    assert entries[1].reason == "expected <li> to be styled once"


def test_unwrap_removes_a_legacy_tag_and_keeps_its_text():
    tree, entries = legacy(Edit("unwrap", {"line": 3, "tag": "font"}),
                           Edit("unwrap", {"line": 3, "tag": "font"}),
                           Edit("unwrap", {"line": 5, "tag": "table"}))
    assert [e.status for e in only_edits(entries)] == ["applied", "skipped", "skipped"]
    assert entries[1].reason == "expected <font> to be unwrapped once"
    assert entries[2].reason == "expected legacy markup of big, blink, center, font, marquee, nobr, got table"
    assert "Welcome" in tree.get_text()


def test_the_content_check_counts_words_so_unwrapping_cannot_lose_any():
    assert check_output("<p>a <font>b</font> c</p>", "<p>a b c</p>", None).problem is None
    assert check_output("<p>a <font>b</font> c</p>", "<p>a c</p>", None).problem == \
        "expected every original text to survive, missing 'b'"


def test_a_page_with_reworded_text_fails_the_check():
    output, _ = apply_edits(LEGACY, [Edit("add_script", {"script": "dark_mode_toggle"})], "o.html")
    assert check_output(LEGACY, output, None).problem is None
    assert check_output(LEGACY, output.replace("Gustav says hello", "Gustav says hi"), None).problem == \
        "expected every original text to survive, missing 'hello'"


def test_a_text_color_cannot_turn_gradient_text_back_into_plain_text():
    tree, _ = legacy(Edit("add_classes", {"line": 3, "tag": "center", "classes": "x"}),
                     Edit("add_classes", {"line": 12, "tag": "p", "classes": "text-slate-900 dark:text-white text-5xl"}),
                     Edit("restyle", {"line": 12, "tag": "p", "style": "gradient_text"}))
    classes = tree.find("p", attrs={"class": True})["class"]
    assert "text-transparent" in classes and "text-5xl" in classes
    assert "text-slate-900" not in classes and "dark:text-white" not in classes
