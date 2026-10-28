import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Markup that only exists to style a page. It is removed with its text kept.
LEGACY_TAGS = frozenset({"font", "center", "marquee", "blink", "big", "nobr"})
# Void or empty legacy tags that carry nothing, so they are dropped outright.
LEGACY_VOID = frozenset({"basefont"})

ALWAYS_DROPPED = frozenset({
    "style", "align", "valign", "bgcolor", "background", "cellpadding", "cellspacing", "border",
    "bordercolor", "hspace", "vspace", "nowrap", "noshade", "clear", "frame", "rules", "char",
    "charoff", "bgproperties", "color", "face",
})
BODY_ONLY = frozenset({"text", "link", "vlink", "alink", "leftmargin", "topmargin",
                       "marginwidth", "marginheight"})
SIZE_ON = frozenset({"hr", "font", "basefont"})
# Width and height on these are part of what they show, not how a page is laid out.
MEDIA = frozenset({"img", "video", "canvas", "iframe", "embed", "object", "svg", "source"})

GRADIENT_TEXT = ("bg-gradient-to-r from-violet-600 to-indigo-600 bg-clip-text font-extrabold "
                 "tracking-tight text-transparent")
PILL = ("inline-flex items-center justify-center rounded-full bg-gradient-to-r from-violet-600 "
        "to-indigo-600 px-6 py-2.5 text-sm font-semibold text-white no-underline shadow-lg "
        "transition hover:scale-105 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-violet-500")
CARD = ("rounded-2xl border border-slate-200 bg-white/80 p-6 shadow-lg backdrop-blur transition "
        "hover:-translate-y-1 hover:shadow-xl dark:border-slate-700 dark:bg-slate-800/70")
FRAME = ("mx-auto h-auto max-h-96 w-auto max-w-full rounded-3xl object-contain shadow-2xl ring-1 "
         "ring-slate-200 dark:ring-slate-700")
STAT = ("rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-600 p-6 text-2xl font-bold "
        "text-white shadow-lg")
TABLE = ("w-full border-separate border-spacing-0 overflow-hidden rounded-2xl text-left shadow-xl "
         "ring-1 ring-slate-200 dark:ring-slate-700")
FIELD = ("w-full rounded-xl border border-slate-300 bg-white px-4 py-2 shadow-sm focus:border-violet-500 "
         "focus:outline-none focus:ring-2 focus:ring-violet-500/40 dark:border-slate-600 dark:bg-slate-900")

LOGO = ("h-10 w-auto max-w-[10rem] object-contain grayscale opacity-70 transition "
        "hover:opacity-100 hover:grayscale-0")
LOGO_CELL = "inline-flex items-center"
LOGO_CAPTION = "mb-4 text-center text-xs font-semibold uppercase tracking-widest text-slate-400"
LOGO_CAPTION_TEXT = "Trusted by teams everywhere"
MAX_LOGOS = 26
# Images that a script adds after the page loads never get a class, so they get a cap here.
IMAGE_CAP_CSS = ('main[data-slop="original"] img:not([class]){display:inline-block;height:auto;width:auto;'
                 "max-height:4rem;max-width:12rem;margin:.5rem 1rem}")

# What the model may ask for on one element, by name.
PRESETS = {
    "gradient_text": GRADIENT_TEXT,
    "pill_button": PILL,
    "card": CARD,
    "pricing_card": ("rounded-3xl border-2 border-violet-500 bg-white p-8 text-center shadow-2xl "
                     "dark:bg-slate-900"),
    "frame": FRAME,
    "table": TABLE,
    "lead": "text-xl leading-relaxed text-slate-600 dark:text-slate-300",
    "badge": ("inline-flex rounded-full bg-violet-100 px-3 py-1 text-xs font-semibold text-violet-700 "
              "dark:bg-violet-900/40 dark:text-violet-200"),
    "callout": ("rounded-xl border-l-4 border-violet-500 bg-violet-50 p-4 text-slate-700 "
                "dark:bg-violet-950/40 dark:text-slate-200"),
}
# A preset that only makes sense on some elements. The rest go anywhere.
PRESET_TAGS = {
    "pill_button": frozenset({"a", "button", "input"}),
    "table": frozenset({"table"}),
    "frame": frozenset({"img", "figure", "video", "iframe", "div"}),
    "gradient_text": frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "a", "b", "strong", "em", "i"}),
}

# How a list or a run of blocks becomes a grid: classes for the container
# and for each child.
GROUPS = {
    "feature_grid": ("grid list-none gap-6 p-0 sm:grid-cols-2 lg:grid-cols-3", CARD),
    "duo_grid": ("grid list-none gap-6 p-0 md:grid-cols-2", CARD),
    "stat_row": ("grid list-none grid-cols-2 gap-4 p-0 text-center md:grid-cols-4", STAT),
    "card_stack": ("flex list-none flex-col gap-4 p-0", CARD),
    "gallery": ("grid list-none grid-cols-2 gap-4 p-0 md:grid-cols-3",
                "overflow-hidden rounded-2xl shadow-lg"),
    "logo_cloud": ("flex list-none flex-wrap items-center justify-center gap-x-10 gap-y-6 p-0", LOGO_CELL),
}
GROUP_CONTAINERS = frozenset({"ul", "ol", "div", "section"})
MAX_GROUP = 16

_HEADING = "tracking-tight text-slate-900 dark:text-white"
DEFAULTS = {
    "h1": f"text-4xl sm:text-5xl {GRADIENT_TEXT}",
    "h2": f"text-3xl {GRADIENT_TEXT}",
    "h3": f"text-2xl font-bold {_HEADING}",
    "h4": f"text-xl font-semibold {_HEADING}",
    "h5": f"text-lg font-semibold {_HEADING}",
    "h6": "text-base font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400",
    "p": "text-lg leading-relaxed text-slate-600 dark:text-slate-300",
    "a": ("font-medium text-violet-600 underline decoration-violet-300 underline-offset-4 transition "
          "hover:text-indigo-600 dark:text-violet-300"),
    "ul": "list-disc space-y-2 pl-6 marker:text-violet-500",
    "ol": "list-decimal space-y-2 pl-6 marker:text-violet-500",
    "li": "text-slate-600 dark:text-slate-300",
    "dl": "space-y-3",
    "dt": "font-semibold text-slate-900 dark:text-white",
    "dd": "ml-4 text-slate-600 dark:text-slate-300",
    "table": TABLE,
    "thead": "text-sm",
    "tbody": "align-top",
    "tfoot": "bg-slate-100 font-semibold dark:bg-slate-800",
    "tr": "transition odd:bg-slate-50 hover:bg-violet-50 dark:odd:bg-slate-800/60 dark:hover:bg-slate-800",
    "th": ("bg-gradient-to-r from-violet-600 to-indigo-600 px-4 py-3 text-sm font-semibold uppercase "
           "tracking-wide text-white"),
    "td": "border-t border-slate-200 px-4 py-3 text-slate-700 dark:border-slate-700 dark:text-slate-200",
    "caption": "caption-bottom p-3 text-sm text-slate-500 dark:text-slate-400",
    "img": "mx-auto my-4 h-auto max-h-64 w-auto max-w-full rounded-xl object-contain",
    "figure": "mx-auto space-y-2",
    "figcaption": "text-center text-sm text-slate-500 dark:text-slate-400",
    "video": "w-full rounded-2xl shadow-lg",
    "iframe": "w-full rounded-2xl shadow-lg",
    "canvas": "mx-auto rounded-2xl shadow-lg",
    "svg": "mx-auto",
    "hr": "my-10 h-px border-0 bg-gradient-to-r from-transparent via-violet-400 to-transparent",
    "form": ("space-y-4 rounded-2xl border border-slate-200 bg-white/70 p-6 shadow-lg "
             "dark:border-slate-700 dark:bg-slate-800/60"),
    "label": "block text-sm font-semibold text-slate-700 dark:text-slate-200",
    "select": FIELD,
    "textarea": FIELD,
    "button": PILL,
    "fieldset": "rounded-2xl border border-slate-200 p-4 dark:border-slate-700",
    "legend": "px-2 font-semibold text-violet-600 dark:text-violet-300",
    "blockquote": ("rounded-r-xl border-l-4 border-violet-500 bg-violet-50 px-6 py-4 italic text-slate-700 "
                   "dark:bg-violet-950/40 dark:text-slate-200"),
    "pre": "overflow-x-auto rounded-2xl bg-slate-900 p-4 text-sm text-slate-100 shadow-lg",
    "code": ("rounded bg-slate-100 px-1.5 py-0.5 font-mono text-sm text-violet-700 dark:bg-slate-800 "
             "dark:text-violet-300"),
    "small": "text-sm text-slate-500 dark:text-slate-400",
    "b": "font-semibold text-slate-900 dark:text-white",
    "strong": "font-semibold text-slate-900 dark:text-white",
    "em": "italic text-violet-700 dark:text-violet-300",
    "i": "italic text-violet-700 dark:text-violet-300",
    "u": "underline decoration-violet-400 underline-offset-4",
    "span": "font-medium",
    "div": "space-y-4",
    "section": "space-y-4 py-6",
    "article": CARD,
    "aside": CARD,
    "header": "space-y-4",
    "footer": "border-t border-slate-200 pt-6 text-sm text-slate-500 dark:border-slate-700",
    "nav": "flex flex-wrap items-center gap-4",
    "main": "mx-auto max-w-5xl",
    "address": "not-italic text-slate-600 dark:text-slate-300",
    "details": "rounded-xl border border-slate-200 p-4 dark:border-slate-700",
    "summary": "cursor-pointer font-semibold text-slate-900 dark:text-white",
}
# The page's outer layout table or div sits in the page column like the generated sections:
# no border, no shadow, no card.
SHELLS = frozenset({"table", "div", "section", "main", "article"})
SHELL_PASS = LEGACY_TAGS | {"html", "body"}
SHELL_SHARE = 6  # tenths of the page words
INVISIBLE = frozenset({"head", "script", "style", "link", "meta", "noscript", "title", "base"})
FLAT_SHELL = {"table": "w-full border-separate border-spacing-0", "div": "space-y-8", "section": "space-y-8",
              "main": "space-y-8", "article": "space-y-8"}
FLAT_CELLS = {"tbody": "align-top", "thead": "align-top", "tfoot": "align-top", "tr": "align-top",
              "td": "p-3 align-top", "th": "p-3 text-left align-top font-bold", "caption": "p-3 text-left"}
CONTAINER_PRESETS = frozenset(PRESETS[name] for name in ("table", "card", "pricing_card", "callout", "frame"))

CARD_TAGS = frozenset({"div", "section", "article", "aside", "form", "fieldset", "details", "blockquote",
                       "table", "ul", "ol", "li", "figure", "pre"})
_ROUNDED = re.compile(r"^rounded-(?:xl|2xl|3xl)$")
_OUTLINE = re.compile(r"^(?:shadow(?:-[a-z0-9]+)?|ring-1|border|border-2)$")
CARD_TOKEN = re.compile(
    r"^(?:[a-z-]+:)*(?:rounded|border|shadow|ring|backdrop|-?translate-y)(?:-[\w/\[\].-]+)?$"
    r"|^(?:[a-z-]+:)*(?:p|px|py)-\d+$|^(?:[a-z-]+:)*bg-(?:white|slate-\d+)(?:/\d+)?$")
BUTTON_INPUTS = frozenset({"submit", "button", "reset", "image"})
TICK_INPUTS = frozenset({"checkbox", "radio"})


def default_classes(tag: Tag) -> str:
    if tag.name == "input":
        kind = str(tag.get("type") or "text").lower()
        if kind in BUTTON_INPUTS:
            return PILL
        if kind in TICK_INPUTS:
            return "h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
        return "" if kind == "hidden" else FIELD
    return DEFAULTS.get(tag.name, "")


def is_card(tag: Tag) -> bool:
    classes = tag.get("class") or []
    return (tag.name in CARD_TAGS and any(_ROUNDED.match(c) for c in classes)
            and any(_OUTLINE.match(c) for c in classes))


def without_card(tag: Tag) -> None:
    """A card inside a card is one card too many: keep the layout classes and drop the box."""
    tag["class"] = [c for c in tag.get("class") or [] if not CARD_TOKEN.match(c)]


def outer_layout(root) -> Tag | None:
    """The table or div that holds most of the page, looking through legacy tags, if there is one."""
    total = _words(root)
    for child in root.children:
        if isinstance(child, Comment) or not isinstance(child, Tag) or child.name in INVISIBLE:
            continue
        found = child if child.name in SHELLS else outer_layout(child) if child.name in SHELL_PASS else None
        # A footer line or a spacer beside it does not stop it being the page.
        if found is not None and _words(found) * 10 >= total * SHELL_SHARE:
            return found
    return None


def _words(node) -> int:
    return len(node.get_text(" ").split())


def strip_legacy(soup: BeautifulSoup) -> dict[str, int]:
    """Remove the original page's own styling: stylesheets, legacy tags (keeping their text) and attributes."""
    counts = {"tags": 0, "attributes": 0, "styles": 0}
    for tag in soup.find_all(["style", "link"]):
        rel = [r.lower() for r in (tag.get("rel") or [])]
        if tag.name == "style" or "stylesheet" in rel:
            tag.decompose()
            counts["styles"] += 1
    for tag in soup.find_all(sorted(LEGACY_TAGS | LEGACY_VOID)):
        if tag.name in LEGACY_VOID:
            tag.decompose()
        else:
            tag.unwrap()
        counts["tags"] += 1
    for tag in soup.find_all(True):
        for attribute in [a for a in tag.attrs if _is_legacy_attribute(tag.name, a)]:
            del tag.attrs[attribute]
            counts["attributes"] += 1
    return counts


def _is_legacy_attribute(tag: str, attribute: str) -> bool:
    name = attribute.lower()
    if name in ALWAYS_DROPPED:
        return True
    if name in BODY_ONLY:
        return tag == "body"
    if name == "size":
        return tag in SIZE_ON
    if name in ("width", "height"):
        return tag not in MEDIA
    return False
