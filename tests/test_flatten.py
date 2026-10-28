import pytest
from bs4 import BeautifulSoup

from conftest import CannedProvider
from deslopper.edits import Edit
from deslopper.sweep import sweep_source
from deslopper.web import hidden, styles
from deslopper.web.checks import check_output
from deslopper.web.edits import apply_edits


def soup(markup):
    return BeautifulSoup(markup, "html.parser")


def only_edits(entries):
    return [e for e in entries if e.kind not in ("strip",)]


MENU = "<ul><li>Features</li><li>Pricing</li><li>Support</li><li>About us</li></ul>"
TWICE = f"<html><body>\n<div id=\"a\">Common Links {MENU}</div>\n<p>Some text in between the two blocks.</p>\n<div id=\"b\">Common Links {MENU}</div>\n</body></html>\n"


def test_a_repeated_block_is_found_without_a_browser_and_kept_hidden():
    edits = hidden.duplicate_edits(TWICE)
    assert [(e.args["line"], e.args["tag"], e.args["nth"]) for e in edits] == [(4, "div", 0)]
    output, entries = apply_edits(TWICE, edits + [Edit("add_classes", {"line": 3, "tag": "p", "classes": "x"})], "t.html")
    tree = soup(output)
    assert "hidden" in tree.find("div", id="b")["class"] and "hidden" not in tree.find("div", id="a")["class"]
    assert [e.status for e in only_edits(entries)] == ["applied", "applied"]
    assert check_output(TWICE, output, None).problem is None


def test_short_or_nested_repeats_are_not_taken_for_a_hidden_copy():
    assert hidden.duplicate_edits("<body><div>Home</div><div>Home</div></body>") == []
    assert hidden.duplicate_edits(f"<body><div>Same words in this block {MENU}</div></body>") == []
    nested = f"<body><div id=\"o\"><div id=\"i\">Common Links {MENU}</div></div></body>"
    assert hidden.duplicate_edits(nested) == []


def test_a_sweep_hides_the_copy_and_says_how_it_found_it(cache):
    reply = '{"edits": [{"kind": "add_classes", "line": 3, "tag": "p", "classes": "text-lg"}]}'
    result = sweep_source("html", TWICE, "t.html", CannedProvider(reply), cache)
    kinds = [(e.kind, e.status) for e in result.entries]
    assert ("hide", "applied") in kinds
    assert any(e.kind == "hide" and e.status == "note" and e.reason ==
               "found 1 hidden elements by the text repeating, because no browser was available"
               for e in result.entries)
    assert "hidden" in soup(result.output).find("div", id="b")["class"]


def test_nothing_is_hidden_when_the_model_changed_nothing(cache):
    result = sweep_source("html", TWICE, "t.html", CannedProvider("[]"), cache)
    assert result.output == TWICE and not [e for e in result.entries if e.kind == "hide"]


def test_an_element_can_be_picked_by_position_among_those_on_a_line():
    page = "<p><b>one</b> <b>two</b> <b>three</b></p>"
    output, entries = apply_edits(page, [Edit("hide", {"line": 1, "tag": "b", "nth": 1}),
                                         Edit("hide", {"line": 1, "tag": "b", "nth": 9})], "p.html")
    assert soup(output).find_all("b")[1]["class"][-1] == "hidden"
    assert "hidden" not in soup(output).find_all("b")[0]["class"]
    assert entries[1].reason == "expected one of the 3 matching elements on line 1"


def test_a_hidden_legacy_tag_stays_hidden_instead_of_being_unwrapped():
    page = "<body><center>secret words</center><p>shown</p></body>"
    output, _ = apply_edits(page, [Edit("hide", {"line": 1, "tag": "center"})], "c.html")
    div = soup(output).find("div", class_="hidden")
    assert div is not None and div.get_text() == "secret words" and not soup(output).find("center")


def test_the_browser_keys_map_back_to_the_same_elements():
    source = f"<body><div class=\"m  n\">Common Links {MENU}</div><div class=\"m n\">Common Links {MENU}</div></body>"
    key = "DIV||m n|Common Links Features Pricing Support About us"
    edits = hidden._edits_for(source, {(key, 1)})
    assert [(e.args["tag"], e.args["nth"]) for e in edits] == [("div", 1)]
    assert hidden._edits_for(source, {(key, 5)}) == []


@pytest.mark.browser
def test_a_real_browser_reports_what_the_stylesheet_hides(tmp_path):
    pytest.importorskip("playwright.sync_api")
    page = tmp_path / "p.html"
    page.write_text(
        "<html><head><style>.m{display:none}.v{visibility:hidden}</style></head><body>\n"
        f"<div class=\"d\">Common Links {MENU}</div>\n"
        f"<div class=\"m\">Common Links {MENU}</div>\n"
        "<p class=\"v\">ghost words here</p>\n<p>plain visible text</p>\n</body></html>\n", encoding="utf-8")
    found = hidden.browser_hidden(page)
    if found is None:
        pytest.skip("no browser could be launched")
    edits, method = hidden.hidden_edits(page.read_text(encoding="utf-8"), page)
    assert method == "browser"
    assert [(e.args["tag"], e.args["line"]) for e in edits] == [("div", 3), ("p", 4)]
    output, _ = apply_edits(page.read_text(encoding="utf-8"), edits, "p.html")
    tree = soup(output)
    assert "hidden" in tree.find("div", class_="m")["class"] and "hidden" not in tree.find("div", class_="d")["class"]


def test_without_playwright_or_a_file_the_browser_answer_is_none(tmp_path):
    assert hidden.browser_hidden(None) is None
    assert hidden.browser_hidden(tmp_path / "gone.html") is None
    notes = tmp_path / "n.md"
    notes.write_text("# x", encoding="utf-8")
    assert hidden.browser_hidden(notes) is None


SHELL = ('<html><body><center><table border="2" width="600"><tr><td colspan="2"><h1>Title</h1></td></tr>'
         '<tr><td valign="top">Menu</td><td><table><tr><td>inner cell</td></tr></table></td></tr>'
         "</table></center></body></html>")


def test_the_outer_layout_table_is_flattened_and_inner_tables_keep_their_look():
    edits = [Edit("restyle", {"line": 1, "tag": "table", "style": "table"}),
             Edit("wrap", {"line": 1, "tag": "table", "layout": "card"}),
             Edit("wrap", {"line": 1, "tag": "table", "layout": "section"})]
    output, entries = apply_edits(SHELL, edits, "s.html")
    assert [e.status for e in only_edits(entries)] == ["applied", "skipped", "applied"]
    assert entries[1].reason == "expected the outer container of the page to stay flat, got a card"
    outer, inner = soup(output).find_all("table")
    assert outer["class"] == styles.FLAT_SHELL["table"].split()
    assert not [c for c in outer["class"] if c.startswith(("rounded", "shadow", "ring", "overflow"))]
    assert "border-spacing-0" in outer["class"] and "shadow-xl" not in outer["class"]
    cells = [td["class"] for td in outer.find_all("td") if td.find_parent("table") is outer]
    assert all(c == styles.FLAT_CELLS["td"].split() for c in cells)
    assert "shadow-xl" in inner["class"] and "px-4" in inner.td["class"]


def test_a_page_wrapped_in_one_div_is_flat_too_and_a_page_of_many_blocks_has_no_shell():
    output, _ = apply_edits("<body><div><h1>A</h1><p>b</p></div></body>", [Edit("restyle", {"line": 1, "tag": "div", "style": "card"})], "d.html")
    assert soup(output).find("div")["class"] == ["space-y-8"]
    many, _ = apply_edits("<body><div>one</div><div>two</div></body>",
                          [Edit("restyle", {"line": 1, "tag": "div", "style": "card"})], "m.html")
    assert "rounded-2xl" in soup(many).find("div")["class"]
    assert styles.outer_layout(soup("<body><p>a</p></body>")) is None
    assert styles.outer_layout(soup("<body>text<div>x</div></body>")) is None


def test_a_card_is_not_put_inside_a_card():
    page = "<body><h1>T</h1><form action=\"/q\"><input name=\"q\"><button>Go</button></form><p>after</p></body>"
    output, entries = apply_edits(page, [Edit("wrap", {"line": 1, "tag": "form", "layout": "card"})], "f.html")
    tree = soup(output)
    form = tree.find("form")
    assert "rounded-2xl" in form.parent["class"] and "shadow-xl" in form.parent["class"]
    assert form["class"] == ["space-y-4"]
    assert "rounded-xl" in tree.find("input")["class"] and "rounded-full" in tree.find("button")["class"]
    outside, _ = apply_edits(page, [Edit("add_script", {"script": "dark_mode_toggle"})], "f.html")
    assert "rounded-2xl" in soup(outside).find("form")["class"]


LOGOS = ('<body><h1>Sponsors</h1><div id="s"><a href="a.html"><img src="a.png"></a>'
         '<a href="b.html"><img src="b.png"></a><a href="c.html"><img src="c.png"></a></div>'
         '<img id="main" src="m.png"></body>')


def test_logos_become_a_trusted_by_row_of_small_grayscale_images():
    output, entries = apply_edits(LOGOS, [Edit("regroup", {"line": 1, "tag": "div", "layout": "logo_cloud"})], "l.html")
    assert entries[0].status == "applied" and "3 logos" in entries[0].detail
    tree = soup(output)
    cloud = tree.find("div", id="s")
    assert "flex-wrap" in cloud["class"] and "justify-center" in cloud["class"]
    for image in cloud.find_all("img"):
        classes = image["class"]
        assert {"h-10", "grayscale", "opacity-70", "hover:opacity-100"} <= set(classes)
    caption = cloud.find_previous_sibling("p")
    assert caption.get_text() == "Trusted by teams everywhere" and "uppercase" in caption["class"]
    assert check_output(LOGOS, output, None).problem is None


def test_a_logo_cloud_needs_images_and_each_one_is_styled_once():
    reason = apply_edits(LOGOS, [Edit("regroup", {"line": 1, "tag": "h1", "layout": "logo_cloud"})], "l.html")[1][0].reason
    assert reason.startswith("expected a container of")
    one = '<body><div><img src="a.png"></div><div>x</div></body>'
    assert apply_edits(one, [Edit("regroup", {"line": 1, "tag": "div", "layout": "logo_cloud"})], "l.html")[1][0].reason \
        == "expected 2 to 26 logos inside <div>, got 1"
    twice = [Edit("regroup", {"line": 1, "tag": "div", "layout": "logo_cloud"}),
             Edit("restyle", {"line": 1, "tag": "img", "style": "frame"})]
    _, entries = apply_edits(LOGOS, twice, "l.html")
    assert entries[0].status == "applied"


def test_ordinary_images_stay_small_and_only_one_frame_is_hero_sized():
    edits = [Edit("restyle", {"line": 1, "tag": "img", "style": "frame"}),
             Edit("restyle", {"line": 1, "tag": "img", "style": "frame", "nth": 1})]
    page = '<body><img id="a" src="a.png"><img id="b" src="b.png"><img id="c" src="c.png"></body>'
    output, entries = apply_edits(page, edits, "i.html")
    assert [e.status for e in only_edits(entries)] == ["applied", "skipped"]
    assert entries[1].reason == "expected one frame on the page"
    tree = soup(output)
    assert "max-h-96" in tree.find("img", id="a")["class"]
    for name in ("b", "c"):
        classes = tree.find("img", id=name)["class"]
        assert "max-h-64" in classes and "w-auto" in classes and "mx-auto" in classes


def test_images_added_by_a_script_get_a_cap_from_the_page_head():
    output, _ = apply_edits("<body><p>a</p></body>", [Edit("add_script", {"script": "dark_mode_toggle"})], "c.html")
    css = "".join(style.string for style in soup(output).head.find_all("style"))
    assert 'main[data-slop="original"] img:not([class])' in css and "max-height:4rem" in css


def test_a_fixed_dark_body_from_the_model_cannot_leave_the_page_half_dark():
    classes = "bg-gradient-to-b from-slate-900 via-purple-950 to-slate-900 text-slate-100 dark:from-black from-purple-50 min-h-screen"
    output, entries = apply_edits("<body><p>a</p></body>", [Edit("add_classes", {"line": 1, "tag": "body", "classes": classes})], "b.html")
    body = soup(output).body["class"]
    assert entries[0].status == "applied"
    for dropped in ("from-slate-900", "via-purple-950", "to-slate-900", "text-slate-100"):
        assert dropped not in body
    assert {"bg-gradient-to-b", "dark:from-black", "from-purple-50", "min-h-screen", "bg-white", "text-slate-900"} <= set(body)


def test_a_size_from_the_model_replaces_the_automatic_one_on_an_image():
    page = '<body><img id="a" src="a.png"><img id="b" src="b.png"><img id="c" src="c.png"></body>'
    edits = [Edit("add_classes", {"line": 1, "tag": "img", "classes": "h-8 opacity-70"}),
             Edit("add_classes", {"line": 1, "tag": "img", "nth": 1, "classes": "w-24"}),
             Edit("add_classes", {"line": 1, "tag": "img", "nth": 2, "classes": "opacity-50"})]
    tree = soup(apply_edits(page, edits, "i.html")[0])
    a, b, c = (tree.find("img", id=name)["class"] for name in "abc")
    assert "h-8" in a and "h-auto" not in a and "w-auto" in a
    assert "w-24" in b and "w-auto" not in b and "h-auto" in b
    assert "h-auto" in c and "w-auto" in c


def test_classes_the_model_adds_to_the_outer_table_and_its_cells_cannot_bring_the_card_back():
    box = "rounded-2xl shadow-xl ring-1 border bg-slate-50 p-6 border-spacing-2 mx-auto"
    edits = [Edit("add_classes", {"line": 1, "tag": "table", "classes": box}),
             Edit("add_classes", {"line": 1, "tag": "td", "nth": 0, "classes": "bg-slate-50 rounded-xl text-center"})]
    output, entries = apply_edits(SHELL, edits, "s.html")
    outer = soup(output).find("table")
    assert entries[0].status == "applied" and entries[1].status == "applied"
    assert not {"rounded-2xl", "shadow-xl", "ring-1", "border", "bg-slate-50", "p-6"} & set(outer["class"])
    assert {"mx-auto", "border-spacing-2"} <= set(outer["class"])
    header = outer.find("td")
    assert "text-center" in header["class"] and not {"bg-slate-50", "rounded-xl"} & set(header["class"])


def test_the_outer_container_is_found_beside_a_footer_line_and_inside_center():
    page = ("<html><body><center><table border=\"2\"><tr><td>Main words of the page live in this table cell right here"
            "</td></tr></table></center><p>Best viewed in a browser</p></body></html>")
    tree = soup(apply_edits(page, [Edit("add_script", {"script": "dark_mode_toggle"})], "o.html")[0])
    assert tree.find("table")["class"] == styles.FLAT_SHELL["table"].split()
    assert styles.outer_layout(soup("<body><div>a b c d</div><div>e f g h</div></body>")) is None
    assert styles.outer_layout(soup("<body><h1>just a heading with words</h1></body>")) is None
