from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction

from deslopper.engines import Verdict

NOT_TEXT = (Comment, Declaration, Doctype, ProcessingInstruction)
FORM_CONTROLS = ("input", "select", "textarea", "button", "output", "fieldset", "object")


def check_output(source: str, output: str, origin: Path | None) -> Verdict:
    before, after = _inventory(source), _inventory(output)
    for label, original in before.items():
        missing = original - after[label]
        if missing:
            item = next(iter(missing))
            shown = item if isinstance(item, str) else " ".join(map(str, item))
            return Verdict(f"expected every original {label} to survive, missing {shown[:60]!r}")
    return Verdict()


def _inventory(markup: str) -> dict[str, Counter]:
    soup = BeautifulSoup(markup.removeprefix("\ufeff"), "html.parser")
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
