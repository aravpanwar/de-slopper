from pathlib import Path

from bs4 import BeautifulSoup, Tag

from deslopper.edits import Edit
from deslopper.web.screenshot import NAVIGATION_TIMEOUT_MS, VIEWPORT, open_browser

# What the original stylesheet may have kept out of sight is not rendered
# elements at all, so these never count.
SKIPPED = frozenset({"head", "script", "style", "meta", "link", "title", "noscript", "template", "base",
                     "area", "param", "source", "track", "datalist"})
# Without a browser, a block that repeats the words of an earlier block is taken for a
# copy the stylesheet hid, such as a mobile menu.
BLOCKS = frozenset({"div", "section", "nav", "ul", "ol", "aside", "header", "footer", "article",
                    "table", "form", "dl", "main"})
MIN_WORDS = 4

# One entry for each topmost element the browser hides, as its key and which
# of the elements with that key it is. The key is built from things a browser and
# a parser agree on, because they disagree on structure (an implied tbody, for one).
FIND_HIDDEN = """() => {
  const skip = new Set(%s);
  const norm = (text) => text.split(/\\s+/).filter(Boolean).join(" ");
  const words = (element) => {
    const parts = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement && node.parentElement.tagName;
      if (parent !== "SCRIPT" && parent !== "STYLE") { parts.push(node.nodeValue); }
    }
    return norm(parts.join(" "));
  };
  const hidden = (element) => {
    const style = getComputedStyle(element);
    return style.display === "none" || style.visibility === "hidden";
  };
  const seen = new Map();
  const found = [];
  for (const element of document.body.querySelectorAll("*")) {
    if (skip.has(element.tagName)) { continue; }
    const key = [element.tagName, element.getAttribute("id") || "", norm(element.getAttribute("class") || ""),
                 words(element)].join("|");
    const index = seen.get(key) || 0;
    seen.set(key, index + 1);
    const parent = element.parentElement;
    if (hidden(element) && !(parent && hidden(parent))) { found.push([key, index]); }
  }
  return found;
}""" % ("[" + ", ".join(f'"{name.upper()}"' for name in sorted(SKIPPED)) + "]")


def hidden_edits(source: str, origin: Path | None) -> tuple[list[Edit], str]:
    """Hide edits for what the original page hid, and how they were found: browser or duplicates."""
    found = browser_hidden(origin)
    if found is None:
        return duplicate_edits(source), "duplicates"
    return _edits_for(source, found), "browser"


def browser_hidden(origin: Path | None) -> set[tuple[str, int]] | None:
    """What the original page's own CSS hides at desktop width, or None when no browser can say."""
    if origin is None or origin.suffix.lower() not in (".html", ".htm") or not origin.is_file():
        return None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = open_browser(playwright)
            try:
                page = browser.new_context(viewport=VIEWPORT, color_scheme="light").new_page()
                page.goto(origin.resolve().as_uri(), wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
                return {(key, index) for key, index in page.evaluate(FIND_HIDDEN)}
            finally:
                browser.close()
    except Exception:
        # No playwright, no browser or a page that will not load: the caller has a fallback.
        return None


def _edits_for(source: str, found: set[tuple[str, int]]) -> list[Edit]:
    soup, body = _parse(source)
    seen: dict[str, int] = {}
    edits = []
    for tag in body.find_all(True):
        if tag.name in SKIPPED:
            continue
        key = "|".join([tag.name.upper(), str(tag.get("id") or ""), " ".join(tag.get("class") or []),
                        " ".join(tag.get_text(" ").split())])
        index = seen.get(key, 0)
        seen[key] = index + 1
        if (key, index) in found:
            edits.append(_hide(soup, tag))
    return edits


def duplicate_edits(source: str) -> list[Edit]:
    soup, body = _parse(source)
    seen: dict[str, list[Tag]] = {}
    hidden: list[Tag] = []
    for tag in body.find_all(True):
        if tag.name not in BLOCKS or any(tag is h or h in tag.parents for h in hidden):
            continue
        text = " ".join(tag.get_text(" ").split())
        if len(text.split()) < MIN_WORDS:
            continue
        earlier = seen.setdefault(text, [])
        related = any(e in tag.parents or tag in e.parents for e in earlier)
        if earlier and not related:
            hidden.append(tag)
        else:
            earlier.append(tag)
    return [_hide(soup, tag) for tag in hidden]


def _parse(source: str):
    soup = BeautifulSoup(source.removeprefix("\ufeff"), "html.parser")
    return soup, soup.find("body") or soup


def _hide(soup: BeautifulSoup, tag: Tag) -> Edit:
    same_line = [t for t in soup.find_all(tag.name) if t.sourceline == tag.sourceline]
    return Edit("hide", {"line": tag.sourceline, "tag": tag.name,
                         "nth": next(i for i, t in enumerate(same_line) if t is tag)})
