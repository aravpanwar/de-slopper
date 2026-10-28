import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from deslopper import cli
from deslopper.providers.base import Provider, ProviderError
from deslopper.providers.dummy import DummyProvider
from deslopper.providers.recorded import RecordedProvider
from deslopper.sweep import sweep_directory, sweep_source
from deslopper.web import ultra
from deslopper.web.checks import check_output

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
REPLIES = TESTS / "replies" / "ultra"

GOOD = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 60'><defs><linearGradient id='g'>"
        "<stop offset='0' stop-color='#7c3aed'/></linearGradient></defs>"
        "<rect width='120' height='60' rx='20' fill='url(#g)'/><use href='#g'/></svg>")


def svg_with(inner, attrs="", ns="xmlns='http://www.w3.org/2000/svg'"):
    return f"<svg {ns} viewBox='0 0 10 10' {attrs}>{inner}</svg>"


class Drawer(Provider):
    name = "drawer"
    model = "drawer-1"
    prompt_version = "1"

    def __init__(self, svgs=None, model="drawer-1"):
        self.model = model
        self.svgs = svgs
        self.draws = []

    def plan(self, kind, source, context):
        return '{"edits": []}'

    def write_tests(self, source, context):
        return ""

    def version(self, kind):
        return f"{kind}:1"

    def draw(self, request, context):
        self.draws.append(json.loads(request))
        images = self.draws[-1]["images"]
        svgs = self.svgs or [GOOD] * len(images)
        return json.dumps({"images": [{"id": i["id"], "svg": s} for i, s in zip(images, svgs)]})


def test_a_clean_svg_is_accepted_and_gets_its_missing_basics():
    text, reason = ultra.clean_svg("<svg viewBox='0 0 120 60'><rect width='120' height='60'/></svg>")
    assert reason is None
    assert "xmlns='http://www.w3.org/2000/svg'" in text or 'xmlns="http://www.w3.org/2000/svg"' in text
    assert 'width="120"' in text and 'height="60"' in text
    sized = GOOD.replace("viewBox='0 0 120 60'", "viewBox='0 0 120 60' width='120' height='60'")
    assert ultra.clean_svg(sized) == (sized + "\n", None)


@pytest.mark.parametrize("markup, reason", [
    (svg_with("<script>alert(1)</script>"), "expected no script element"),
    (svg_with("<rect/>", "onload='run()'"), "expected no onload attribute"),
    (svg_with("<rect onclick='x()'/>"), "expected no onclick attribute"),
    (svg_with("<foreignObject><div>hi</div></foreignObject>"), "expected no foreignobject element"),
    (svg_with("<image href='data:image/png;base64,AAAA'/>"), "expected no image element"),
    (svg_with("<a href='https://evil.test'><rect/></a>"), "expected only links to ids inside the svg"),
    (svg_with("<a href='javascript:alert(1)'><rect/></a>"), "expected only links to ids inside the svg"),
    (svg_with("<use xlink:href='https://x.test/a.svg#a'/>", ns="xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'"),
     "expected only links to ids inside the svg"),
    (svg_with("<rect fill='url(https://x.test/a.svg#g)'/>"), "expected url() to point at an id inside the svg"),
    (svg_with("<rect style='fill:url(\"http://x.test/g\")'/>"), "expected url() to point at an id inside the svg"),
    (svg_with("<style>.a { fill: url(//x.test/g) }</style>"), "expected url() to point at an id inside the svg"),
    (svg_with("<style>@import 'https://x.test/a.css';</style>"), "expected no script or import in the svg"),
    (svg_with("<set attributeName='href' to='https://x.test'/>"), "expected set not to change a link"),
    (svg_with("<div xmlns='http://www.w3.org/1999/xhtml'>hi</div>"), "expected svg elements only"),
    (svg_with("<rect/>").replace("<svg", "<?xml-stylesheet href='a.css'?><svg"), "expected an svg without processing instructions"),
    ("<!DOCTYPE svg [<!ENTITY a 'b'>]>" + svg_with("<text>&a;</text>"), "expected an svg without a doctype or entities"),
    ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><rect></svg>", "expected a well-formed svg"),
    ("not svg at all", "expected a well-formed svg"),
    ("<html xmlns='http://www.w3.org/1999/xhtml'><body/></html>", "expected an svg element as the root, got html"),
    ("<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>", "expected a viewBox of four numbers"),
    ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 0 5'><rect/></svg>", "expected a viewBox of four numbers"),
    (svg_with("<text>" + "x" * ultra.MAX_SVG_BYTES + "</text>"), "expected an svg of at most"),
])
def test_an_svg_that_is_unsafe_or_broken_is_refused_with_its_reason(markup, reason):
    text, why = ultra.clean_svg(markup)
    assert text is None and why.startswith(reason)


PAGE = """<html><body>
<header><img src="img/acme-logo.png" alt="Acme logo" width="160" height="48"></header>
<h2>Our sponsors</h2>
<div class="row"><img src="s/one.png" alt="One"><img src="s/two.png" alt="Two"><img src="s/three.png" alt="Three"></div>
<h2>The workshop</h2>
<p>Gustav at work <img src="gustav.jpg" alt="Gustav at the bench"></p>
<img src="icons/arrow.png" alt="" width="16" height="16"><img src="gustav.jpg">
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
</body></html>
"""


def test_each_image_gets_its_file_alt_text_nearby_words_and_a_rough_role():
    images, notes = ultra.find_images(PAGE, "p.html")
    roles = {i.file: i.role for i in images}
    assert roles == {"acme-logo.png": "logo", "one.png": "sponsor logo", "two.png": "sponsor logo",
                     "three.png": "sponsor logo", "gustav.jpg": "hero or photo", "arrow.png": "icon"}
    assert [i.id for i in images] == [1, 2, 3, 4, 5, 6]
    photo = next(i for i in images if i.file == "gustav.jpg")
    assert photo.alt == "Gustav at the bench" and photo.heading == "The workshop" and "Gustav at work" in photo.text
    logo = images[0]
    assert (logo.width, logo.height) == ("160", "48")
    assert [(n.status, n.detail) for n in notes] == [("skipped", "data: image")]


def test_a_script_that_builds_images_is_noted_and_not_drawn():
    page = "<body><img src='a.png'><script>var i = document.createElement('img'); i.src = 'b.png';</script></body>"
    images, notes = ultra.find_images(page, "p.html")
    assert [i.file for i in images] == ["a.png"]
    assert any("a script builds" in (n.reason or "") for n in notes)


def swap(page, svgs, tmp_path, name="p.html"):
    images, _ = ultra.find_images(page, name)
    reply = json.dumps({"images": [{"id": i + 1, "svg": s} for i, s in enumerate(svgs) if s is not None]})
    return ultra.swap_images(page, name, images, reply, tmp_path)


def test_every_swap_and_every_refusal_is_in_the_receipt_and_a_refused_image_keeps_its_original(tmp_path):
    page = "<body><img src='img/a.png' alt='a'><img src='img/b.png'><img src='c.png'></body>"
    result = swap(page, [GOOD, svg_with("<script/>"), None], tmp_path)
    assert [(e.kind, e.status) for e in result.entries] == [
        ("ultra", "applied"), ("ultra", "rejected"), ("ultra", "skipped")]
    assert result.entries[0].detail == "img/a.png -> images/a.ultra.svg"
    assert result.entries[1].detail == "img/b.png" and result.entries[1].reason == "expected no script element in the svg"
    assert result.entries[2].reason == "expected an svg for image 3, got none"
    srcs = [i["src"] for i in BeautifulSoup(result.markup, "html.parser").find_all("img")]
    assert srcs == ["images/a.ultra.svg", "img/b.png", "c.png"]
    assert [d.path for d in result.drawings] == ["images/a.ultra.svg"]


def test_a_reply_that_is_not_json_changes_nothing(tmp_path):
    images, _ = ultra.find_images("<img src='a.png'>", "p.html")
    result = ultra.swap_images("<img src='a.png'>", "p.html", images, "no json here", tmp_path)
    assert result.markup == "<img src='a.png'>" and result.drawings == ()
    assert [(e.status, e.reason) for e in result.entries] == [("rejected", "expected JSON, parse failed at character 0")]


def test_two_images_with_one_name_get_two_files_and_an_existing_different_drawing_is_never_replaced(tmp_path):
    page = "<body><img src='x/logo.png'><img src='y/logo.png'></body>"
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "logo.ultra.svg").write_text("old", encoding="utf-8")
    result = swap(page, [GOOD, GOOD], tmp_path)
    assert [d.path for d in result.drawings] == ["images/logo-2.ultra.svg", "images/logo-3.ultra.svg"]
    assert (tmp_path / "images" / "logo.ultra.svg").read_text(encoding="utf-8") == "old"
    ultra.write_drawings(tmp_path, result.drawings)
    # The same drawing again reuses its own file.
    again = swap(page, [GOOD, GOOD], tmp_path)
    assert [d.path for d in again.drawings] == ["images/logo-2.ultra.svg", "images/logo-3.ultra.svg"]


def test_a_page_draws_at_most_a_set_number_of_images_and_the_rest_keep_theirs(tmp_path):
    page = "<body>" + "".join(f"<img src='i{n}.png'>" for n in range(ultra.MAX_IMAGES + 2)) + "</body>"
    images, _ = ultra.find_images(page, "p.html")
    assert len(json.loads(ultra.request_text("p.html", images))["images"]) == ultra.MAX_IMAGES
    result = swap(page, [GOOD] * ultra.MAX_IMAGES, tmp_path)
    assert len(result.drawings) == ultra.MAX_IMAGES
    assert sum(e.status == "skipped" for e in result.entries) == 2


def test_the_content_check_lets_an_img_src_change_only_to_a_generated_file():
    source = "<body><p>one two</p><img src='a.png'><script src='a.js'></script></body>"
    swapped = "<body><p>one two</p><img src='images/a.ultra.svg'><script src='a.js'></script></body>"
    assert check_output(source, swapped, None, {"a.png": "images/a.ultra.svg"}).problem is None
    # Without the mapping a changed src is still a missing original.
    assert "missing 'a.png'" in check_output(source, swapped, None).problem
    stray = swapped.replace("images/a.ultra.svg", "https://x.test/other.png")
    assert "expected every img src to be an original or a drawing" in check_output(
        source, stray, None, {"a.png": "images/a.ultra.svg"}).problem
    assert "missing 'a.js'" in check_output(source, swapped.replace("a.js", "b.js"), None,
                                          {"a.png": "images/a.ultra.svg"}).problem
    assert "missing" in check_output(source, swapped.replace("one two", "one"), None,
                                     {"a.png": "images/a.ultra.svg"}).problem


def test_ultra_is_one_request_for_the_page_and_a_rerun_is_cached_per_model(cache):
    page = "<html><body><h1>Hi</h1><img src='a.png' alt='A'><img src='b.png' alt='B'></body></html>"
    first = Drawer()
    result = sweep_source("html", page, "p.html", first, cache, ultra=True, art_dir=Path("."))
    assert len(first.draws) == 1 and [i["file"] for i in first.draws[0]["images"]] == ["a.png", "b.png"]
    assert len(result.drawings) == 2 and result.kept is not None and "a.png" in result.kept
    assert sweep_source("html", page, "p.html", Drawer(), cache, ultra=True, art_dir=Path(".")).drawings
    again = Drawer()
    sweep_source("html", page, "p.html", again, cache, ultra=True, art_dir=Path("."))
    assert again.draws == []
    other = Drawer(model="drawer-2")
    sweep_source("html", page, "p.html", other, cache, ultra=True, art_dir=Path("."))
    assert len(other.draws) == 1


def test_one_bad_drawing_does_not_stop_the_good_one(cache, tmp_path):
    page = "<html><body><img src='a.png'><img src='b.png'></body></html>"
    result = sweep_source("html", page, "p.html", Drawer([svg_with("<script/>"), GOOD]), cache,
                          ultra=True, art_dir=tmp_path)
    assert [d.src for d in result.drawings] == ["b.png"]
    statuses = [(e.kind, e.status) for e in result.entries if e.kind == "ultra"]
    assert statuses == [("ultra", "rejected"), ("ultra", "applied")]


def test_a_page_without_images_and_a_python_file_are_noted(cache):
    page = sweep_source("html", "<html><body><p>nothing here</p></body></html>", "p.html", Drawer(), cache,
                        ultra=True, art_dir=Path("."))
    assert page.drawings == () and any(e.reason == "found no images to draw" for e in page.entries)
    code = sweep_source("python", "x = 1\n", "m.py", Drawer(), cache, ultra=True)
    assert [(e.kind, e.status, e.reason) for e in code.entries if e.kind == "ultra"] == [
        ("ultra", "skipped", "expected a page, got a python file")]


def test_a_provider_that_cannot_draw_says_so(cache):
    with pytest.raises(ProviderError, match="expected a provider that can draw images, got dummy"):
        sweep_source("html", "<html><body><img src='a.png'></body></html>", "p.html", DummyProvider(), cache,
                     ultra=True, art_dir=Path("."))


def sweep_recorded(name, tmp_path, cache):
    path = FIXTURES / name
    source = path.read_bytes().decode("utf-8")
    return source, sweep_source("html", source, name, RecordedProvider(REPLIES), cache, origin=path,
                                ultra=True, art_dir=tmp_path)


def test_recorded_end_to_end_a_page_gets_its_picture_drawn_and_keeps_everything_else(cache, tmp_path):
    source, result = sweep_recorded("homepage.html", tmp_path, cache)
    page = BeautifulSoup(result.output, "html.parser")
    assert page.find("img")["src"] == "images/gustav.ultra.svg"
    assert "gustav.svg" not in result.output and "gustav.svg" in result.kept
    assert "dropped" not in {e.status for e in result.entries}
    swaps = [e for e in result.entries if e.kind == "ultra"]
    assert [(e.status, e.detail) for e in swaps] == [("applied", "gustav.svg -> images/gustav.ultra.svg")]
    assert check_output(source, result.output, FIXTURES / "homepage.html", {"gustav.svg": "images/gustav.ultra.svg"}).problem is None
    ultra.write_drawings(tmp_path, result.drawings)
    saved = (tmp_path / "images" / "gustav.ultra.svg").read_text(encoding="utf-8")
    assert saved.startswith("<svg") and "linearGradient" in saved


def test_recorded_end_to_end_an_unsafe_drawing_leaves_the_original_image(cache, tmp_path):
    _, result = sweep_recorded("shop.html", tmp_path, cache)
    assert BeautifulSoup(result.output, "html.parser").find("img")["src"] == "shopfront.svg"
    assert result.drawings == () and result.kept is not None
    assert [(e.status, e.reason) for e in result.entries if e.kind == "ultra"] == [
        ("rejected", "expected no script element in the svg")]


def test_a_directory_run_writes_the_drawings_next_to_the_page_and_keeps_the_original_files(cache, tmp_path):
    folder, out = tmp_path / "in", tmp_path / "out"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "p.html").write_text("<html><body><p>hello there</p><img src='pic.png'></body></html>",
                                           encoding="utf-8")
    (folder / "sub" / "pic.png").write_bytes(b"\x89PNG not really")
    entries = sweep_directory(folder, out, Drawer(), cache, tests=False, ultra=True)
    assert (out / "sub" / "images" / "pic.ultra.svg").is_file()
    assert (out / "sub" / "pic.png").read_bytes() == b"\x89PNG not really"
    assert 'src="images/pic.ultra.svg"' in (out / "sub" / "p.html").read_text(encoding="utf-8")
    assert any(e.kind == "ultra" and e.status == "applied" for e in entries)
    receipt = [json.loads(line) for line in (out / "receipt.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {"file": "sub/p.html", "kind": "ultra", "status": "applied",
            "detail": "pic.png -> images/pic.ultra.svg"} in receipt
    assert sweep_directory(folder, out, Drawer(), cache, tests=False, ultra=True) == []
    # A run without drawings did not finish what an ultra run asks for, and the other way round.
    assert sweep_directory(folder, out, Drawer(), cache, tests=False) != []
    assert sweep_directory(folder, out, Drawer(), cache, tests=False, ultra=True) != []


def test_the_flag_needs_an_output_and_the_page_is_written_with_it(tmp_path, monkeypatch, capsys):
    page = tmp_path / "p.html"
    page.write_text("<html><body><p>hello there</p><img src='a.png'></body></html>", encoding="utf-8")
    monkeypatch.setattr(cli, "make_provider", lambda name: Drawer())
    assert cli.main([str(page), "--ultra"]) == 2
    assert "expected -o with --ultra" in capsys.readouterr().err
    out = tmp_path / "out" / "q.html"
    assert cli.main([str(page), "-o", str(out), "--ultra"]) == 0
    assert 'src="images/a.ultra.svg"' in out.read_text(encoding="utf-8")
    assert (out.parent / "images" / "a.ultra.svg").is_file()
    assert "ultra x1" in capsys.readouterr().err


def test_the_prompt_loads_with_its_own_version_and_the_aesthetic():
    from deslopper.providers.openai_compat import load_prompt
    version, digest, body = load_prompt("ultra.md")
    assert version == "1" and len(digest) == 8
    for word in ("squircle", "glassmorphism", "wobbl", "viewBox", "under about 3 KB"):
        assert word in body
