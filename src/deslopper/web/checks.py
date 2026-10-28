from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction

from deslopper.engines import Verdict

NOT_TEXT = (Comment, Declaration, Doctype, ProcessingInstruction)
FORM_CONTROLS = ("input", "select", "textarea", "button", "output", "fieldset", "object")


def check_output(source: str, output: str, origin: Path | None,
                 swaps: dict[str, str] | None = None) -> Verdict:
    """Compare what the page holds before and after. In ultra mode `swaps` maps an image src to the
    drawing that replaced it, and an img src may differ from the original only that way."""
    before, after = _inventory(source), _inventory(output)
    if swaps:
        back = {new: old for old, new in swaps.items()}
        after["image or file (src)"] = Counter(back.get(s, s) for s in _srcs(output))
        known = {*swaps.values(), *(str(t["src"]) for t in _soup(source).find_all("img", src=True))}
        stray = [s for s in _srcs(output, "img") if s not in known]
        if stray:
            return Verdict(f"expected every img src to be an original or a drawing, got {stray[0][:60]!r}")
    for label, original in before.items():
        missing = original - after[label]
        if missing:
            item = next(iter(missing))
            shown = item if isinstance(item, str) else " ".join(map(str, item))
            return Verdict(f"expected every original {label} to survive, missing {shown[:60]!r}")
    return Verdict()


def _soup(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup.removeprefix("\ufeff"), "html.parser")


def _srcs(markup: str, name: str | bool = True) -> list[str]:
    return [str(t["src"]) for t in _soup(markup).find_all(name, src=True)]


def _inventory(markup: str) -> dict[str, Counter]:
    soup = _soup(markup)
    # Words, not text nodes: removing a font tag joins the text around it
    # into one node, and every word of it must still be there.
    texts: Counter = Counter()
    for node in soup.find_all(string=True):
        if isinstance(node, NOT_TEXT) or _inside(node, ("script", "style")):
            continue
        texts.update(node.split())

    # Stylesheet links are not content: the original styling is replaced.
    links = Counter(str(t["href"]) for t in soup.find_all(href=True) if t.name != "link")
    sources = Counter(str(t["src"]) for t in soup.find_all(src=True))
    names = Counter(str(t["name"]) for t in soup.find_all(FORM_CONTROLS, attrs={"name": True}))
    scripts = Counter(
        (str(t.get("src", "")), " ".join(t.get_text().split())) for t in soup.find_all("script")
    )
    return {"text": texts, "link (href)": links, "image or file (src)": sources,
            "form field name": names, "script": scripts}


def _inside(node: NavigableString, names: tuple[str, ...]) -> bool:
    parent = node.parent
    return parent is not None and parent.name in names
