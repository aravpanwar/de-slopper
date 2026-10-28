import re

from bs4 import BeautifulSoup, Doctype, NavigableString, Tag

from deslopper.edits import Edit
from deslopper.receipts import Entry
from deslopper.textutil import Refusal, one_line
from deslopper.web import styles, templates

# Elements that carry no visible content, or that must keep their place in the document.
NO_CLASSES = {"head", "title", "meta", "link", "script", "style", "noscript", "base"}
NO_WRAP = NO_CLASSES | styles.LEGACY_TAGS | {
    "html", "body", "tr", "td", "th", "tbody", "thead", "tfoot", "caption", "col", "colgroup",
    "li", "dt", "dd", "option", "optgroup", "source", "track", "param", "summary", "legend", "br",
}
# Tailwind class names and nothing else: no quotes, spaces, angle brackets or equals signs.
CLASS_TOKEN = re.compile(r"^[A-Za-z0-9:_\-/.%#!\[\]]{1,60}$")
MAX_TOKENS = 46
THEME_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SCRIPTS = ("dark_mode_toggle", "fade_in_on_scroll")


def apply_edits(source: str, edits: list[Edit], name: str) -> tuple[str, list[Entry]]:
    if not edits:
        return source, []
    bom = source.startswith("\ufeff")
    soup = BeautifulSoup(source[1:] if bom else source, "html.parser")
    planner = Planner(soup)
    entries = []
    for edit in edits:
        handler = getattr(planner, f"do_{edit.kind}", None)
        try:
            if handler is None:
                raise Refusal(f"expected a known html edit kind, got {edit.kind}")
            detail, line = handler(edit)
        except Refusal as refusal:
            entries.append(Entry(name, edit.kind, "skipped", reason=str(refusal), line=edit.line))
        else:
            entries.append(Entry(name, edit.kind, "applied", line=line, detail=detail))

    if not planner.touched:
        return source, entries
    stripped = planner.finish()
    if any(stripped.values()):
        entries.append(Entry(name, "strip", "applied", counts=stripped,
                             detail="removed the original styling, keeping every word"))
    return ("\ufeff" if bom else "") + str(soup), entries


class Planner:
    def __init__(self, soup: BeautifulSoup):
        self.soup = soup
        # Every edit is resolved against the original tree, so earlier edits
        # that move elements cannot change what a line number points at.
        self.by_line: dict[int, list[Tag]] = {}
        for tag in soup.find_all(True):
            if tag.sourceline is not None:
                self.by_line.setdefault(tag.sourceline, []).append(tag)
        self.original = list(soup.find_all(True))
        # Classes the model chose for an element, by name, before the
        # defaults fill in everything else.
        self.styled: dict[int, str] = {}
        self.unwraps: list[Tag] = []
        self.hidden: list[Tag] = []
        self.captions: list[tuple[Tag, Tag]] = []
        self.frame_taken = False
        self.shell = styles.outer_layout(soup.find("body") or soup)
        self.classes: list[tuple[Tag, list[str]]] = []
        self.wraps: dict[int, tuple[Tag, str]] = {}
        self.blocks: dict[str, str] = {}
        self.scripts: set[str] = set()
        self.meta: dict[str, str] = {}
        self.touched = False

    def _node(self, edit: Edit) -> Tag:
        line = edit.args.get("line")
        if not isinstance(line, int) or isinstance(line, bool):
            raise Refusal("expected an integer line")
        name = edit.args.get("tag")
        if name is not None and not isinstance(name, str):
            raise Refusal("expected a string tag")
        found = [t for t in self.by_line.get(line, []) if name is None or t.name == name.lower()]
        if not found:
            suffix = f" named {name}" if name else ""
            raise Refusal(f"expected an element{suffix} starting on line {line}")
        # Several matching elements can share a line, so one may be picked by position.
        nth = edit.args.get("nth", 0)
        if not isinstance(nth, int) or isinstance(nth, bool) or not 0 <= nth < len(found):
            raise Refusal(f"expected one of the {len(found)} matching elements on line {line}")
        return found[nth]

    def _styleable(self, tag: Tag) -> None:
        if tag.name in NO_CLASSES:
            raise Refusal(f"expected an element that can be styled, got {tag.name}")
        if tag.name in styles.LEGACY_TAGS:
            raise Refusal(f"expected current markup, got the legacy tag {tag.name}")

    def _claim(self, tag: Tag, classes: str) -> None:
        if id(tag) in self.styled:
            raise Refusal(f"expected <{tag.name}> to be styled once")
        self.styled[id(tag)] = classes

    def do_restyle(self, edit: Edit):
        tag = self._node(edit)
        self._styleable(tag)
        preset = edit.args.get("style")
        if preset not in styles.PRESETS:
            raise Refusal(f"expected a style of {', '.join(styles.PRESETS)}, got {preset}")
        allowed = styles.PRESET_TAGS.get(preset)
        if allowed and tag.name not in allowed:
            raise Refusal(f"expected {preset} on {', '.join(sorted(allowed))}, got {tag.name}")
        if preset == "frame":
            # Hero-sized frames are for the page's one main image.
            if self.frame_taken:
                raise Refusal("expected one frame on the page")
            self.frame_taken = True
        self._claim(tag, styles.PRESETS[preset])
        self.touched = True
        return f"gave <{tag.name}> the {preset.replace('_', ' ')} style", tag.sourceline

    def do_regroup(self, edit: Edit):
        tag = self._node(edit)
        layout = edit.args.get("layout")
        if layout not in styles.GROUPS:
            raise Refusal(f"expected a layout of {', '.join(styles.GROUPS)}, got {layout}")
        if tag.name not in styles.GROUP_CONTAINERS:
            raise Refusal(f"expected a container of {', '.join(sorted(styles.GROUP_CONTAINERS))}, got {tag.name}")
        children = [c for c in tag.children if isinstance(c, Tag)]
        if layout == "logo_cloud":
            return self._logo_cloud(tag, children)
        if tag.name in ("ul", "ol") and any(c.name != "li" for c in children):
            raise Refusal(f"expected only list items inside <{tag.name}>")
        if any(c.name in NO_CLASSES for c in children):
            raise Refusal(f"expected visible elements inside <{tag.name}>")
        if not 2 <= len(children) <= styles.MAX_GROUP:
            raise Refusal(f"expected 2 to {styles.MAX_GROUP} items inside <{tag.name}>, got {len(children)}")
        container, child = styles.GROUPS[layout]
        for member in children:
            if id(member) in self.styled:
                raise Refusal(f"expected <{member.name}> to be styled once")
        self._claim(tag, container)
        for member in children:
            self._claim(member, child)
        self.touched = True
        return f"regrouped {len(children)} items of <{tag.name}> into a {layout.replace('_', ' ')}", tag.sourceline

    def _logo_cloud(self, tag: Tag, children: list[Tag]):
        images = tag.find_all("img")
        if not 2 <= len(images) <= styles.MAX_LOGOS:
            raise Refusal(f"expected 2 to {styles.MAX_LOGOS} logos inside <{tag.name}>, got {len(images)}")
        members = [c for c in children if c.name not in NO_CLASSES | {"br", "hr"}]
        for member in members + images:
            if id(member) in self.styled:
                raise Refusal(f"expected <{member.name}> to be styled once")
        container, cell = styles.GROUPS["logo_cloud"]
        self._claim(tag, container)
        for member in members:
            self._claim(member, styles.LOGO if member.name == "img" else cell)
        for image in images:
            if id(image) not in self.styled:
                self._claim(image, styles.LOGO)
        caption = self.soup.new_tag("p", attrs={"class": styles.LOGO_CAPTION})
        caption.string = styles.LOGO_CAPTION_TEXT
        self.captions.append((tag, caption))
        self.touched = True
        return f"regrouped {len(images)} logos of <{tag.name}> into a logo cloud", tag.sourceline

    def do_hide(self, edit: Edit):
        # Not part of the model's vocabulary: the sweep adds it for elements
        # that the original stylesheet kept out of sight.
        tag = self._node(edit)
        if tag.name in NO_CLASSES:
            raise Refusal(f"expected an element that can be hidden, got {tag.name}")
        self.hidden.append(tag)
        self.touched = True
        return f"kept the <{tag.name}> hidden that the original page hid", tag.sourceline

    def do_unwrap(self, edit: Edit):
        tag = self._node(edit)
        if tag.name not in styles.LEGACY_TAGS:
            raise Refusal(f"expected legacy markup of {', '.join(sorted(styles.LEGACY_TAGS))}, got {tag.name}")
        if any(tag is other for other in self.unwraps):
            raise Refusal(f"expected <{tag.name}> to be unwrapped once")
        self.unwraps.append(tag)
        self.touched = True
        return f"unwrapped the legacy <{tag.name}> tag", tag.sourceline

    def do_add_classes(self, edit: Edit):
        tag = self._node(edit)
        self._styleable(tag)
        raw = edit.args.get("classes")
        tokens = raw.split() if isinstance(raw, str) else raw
        if not isinstance(tokens, list) or not tokens or not all(isinstance(t, str) for t in tokens):
            raise Refusal("expected a string of classes")
        tokens = [piece for token in tokens for piece in token.split()]
        if len(tokens) > MAX_TOKENS:
            raise Refusal(f"expected at most {MAX_TOKENS} classes, got {len(tokens)}")
        for token in tokens:
            if not CLASS_TOKEN.match(token):
                raise Refusal(f"expected a Tailwind class name, got {token}")
        self.classes.append((tag, tokens))
        self.touched = True
        return f"added {len(tokens)} classes to <{tag.name}>: {' '.join(tokens[:4])}", tag.sourceline

    def do_wrap(self, edit: Edit):
        tag = self._node(edit)
        layout = edit.args.get("layout")
        if layout not in templates.LAYOUTS:
            raise Refusal(f"expected a layout of {', '.join(templates.LAYOUTS)}, got {layout}")
        if tag.name in NO_WRAP:
            raise Refusal(f"expected an element that can be wrapped, got {tag.name}")
        if tag is self.shell and layout == "card":
            raise Refusal("expected the outer container of the page to stay flat, got a card")
        if id(tag) in self.wraps:
            raise Refusal(f"expected <{tag.name}> to be wrapped once")
        self.wraps[id(tag)] = (tag, layout)
        self.touched = True
        return f"wrapped <{tag.name}> in a {layout}", tag.sourceline

    def do_insert_block(self, edit: Edit):
        block = edit.args.get("block")
        if block not in templates.BLOCKS:
            raise Refusal(f"expected a block of {', '.join(templates.BLOCKS)}, got {block}")
        if block in self.blocks:
            raise Refusal(f"expected one {block} block")
        self.blocks[block] = templates.render(block, edit.args)
        self.touched = True
        lead = one_line(str(edit.args.get("headline") or edit.args.get("text") or ""))[:50]
        return f"inserted a {block} block" + (f': "{lead}"' if lead else ""), None

    def do_add_script(self, edit: Edit):
        script = edit.args.get("script")
        if script not in SCRIPTS:
            raise Refusal(f"expected a script of {', '.join(SCRIPTS)}, got {script}")
        if script in self.scripts:
            raise Refusal(f"expected {script} once")
        self.scripts.add(script)
        self.touched = True
        return f"added the {script.replace('_', ' ')} script", None

    def do_set_head(self, edit: Edit):
        args = edit.args
        found = {}
        if args.get("description") is not None:
            found["description"] = _bounded(args["description"], "description", 300)
        keywords = args.get("keywords")
        if isinstance(keywords, list):
            keywords = ", ".join(str(k) for k in keywords)
        if keywords is not None:
            found["keywords"] = _bounded(keywords, "keywords", 200)
        color = args.get("theme_color")
        if color is not None:
            if not isinstance(color, str) or not THEME_COLOR.match(color):
                raise Refusal(f"expected a theme_color like #7c3aed, got {color}")
            found["theme-color"] = color
        if not found:
            raise Refusal("expected a description, keywords or theme_color")
        self.meta.update(found)
        self.touched = True
        return "added meta tags: " + ", ".join(found), None

    def finish(self) -> dict[str, int]:
        soup = self.soup
        head, body = _structure(soup)
        # The old styling goes first, so the new classes land on bare elements.
        for tag in self.hidden:
            # A hidden legacy tag would be unwrapped and its content shown.
            if tag.name in styles.LEGACY_TAGS:
                tag.name = "div"
        for tag in self.unwraps:
            tag.unwrap()
        stripped = styles.strip_legacy(soup)
        stripped["tags"] += len(self.unwraps)
        self._style_original()
        for tag, tokens in self.classes:
            tokens = _without_clashing_colors(tag, tokens)
            if self._in_shell(tag):
                # The outer container of the page and its cells stay flat, whatever the model adds.
                tokens = [t for t in tokens if t.startswith("border-s") or not styles.CARD_TOKEN.match(t)]
            _add_classes(tag, tokens)
            _drop_automatic_sizes(tag, tokens)
        for tag in self.hidden:
            _add_classes(tag, ["hidden"])
        for container, caption in self.captions:
            container.insert_before(caption)
        for tag, layout in self.wraps.values():
            name, classes = templates.LAYOUTS[layout]
            wrapper = soup.new_tag(name, attrs={"class": classes, "data-fade": ""})
            tag.wrap(wrapper)
        _flatten_nested_cards(body)
        _add_classes(body, templates.BODY_CLASSES.split())
        self._contain(body)
        self._place_blocks(body)
        self._head(head)
        self._scripts(body, head)
        return stripped

    def _style_original(self) -> None:
        # Every element of the original page gets utility classes: the
        # model's choice where it made one, the engine's default otherwise.
        for tag in self.original:
            if tag.parent is None or tag.name in NO_CLASSES | {"html", "body"}:
                continue
            if tag.find_parent("head") is not None:
                continue
            classes = self._classes_for(tag)
            if classes:
                _add_classes(tag, classes.split())

    def _in_shell(self, tag: Tag) -> bool:
        if self.shell is None:
            return False
        if tag is self.shell:
            return True
        return (self.shell.name == "table" and tag.name in styles.FLAT_CELLS
                and tag.find_parent("table") is self.shell)

    def _classes_for(self, tag: Tag) -> str:
        claimed = self.styled.get(id(tag))
        if tag is self.shell and claimed in (None, *styles.CONTAINER_PRESETS):
            return styles.FLAT_SHELL.get(tag.name, "")
        if claimed is not None:
            return claimed
        if self.shell is not None and self.shell.name == "table" and tag.name in styles.FLAT_CELLS:
            if tag.find_parent("table") is self.shell:
                return styles.FLAT_CELLS[tag.name]
        return styles.default_classes(tag)

    def _contain(self, body: Tag) -> None:
        # Tailwind's reset removes the margins a plain page relies on, so the
        # original content goes in one column, in its original order.
        original = list(body.contents)
        if not any(not isinstance(n, NavigableString) or n.strip() for n in original):
            return
        main = self.soup.new_tag("main", attrs={
            "data-slop": "original", "class": "mx-auto max-w-6xl space-y-10 px-6 py-16"})
        for node in original:
            main.append(node.extract())
        body.append(main)

    def _place_blocks(self, body: Tag) -> None:
        position = 0
        for block in templates.TOP_BLOCKS:
            if block in self.blocks:
                body.insert(position, _fragment(self.soup, self.blocks[block]))
                position += 1
        for block in templates.BOTTOM_BLOCKS:
            if block in self.blocks:
                body.append(_fragment(self.soup, self.blocks[block]))
        anchor = next((f"#{b}" for b in ("features", "faq") if b in self.blocks), "#")
        for link in body.find_all("a", attrs={"data-cta": True}):
            link["href"] = anchor

    def _head(self, head: Tag) -> None:
        soup = self.soup
        if not head.find("meta", attrs={"charset": True}):
            head.insert(0, soup.new_tag("meta", attrs={"charset": "utf-8"}))
        if not head.find("meta", attrs={"name": "viewport"}):
            head.append(soup.new_tag("meta", attrs={
                "name": "viewport", "content": "width=device-width, initial-scale=1"}))
        for name, content in self.meta.items():
            if not head.find("meta", attrs={"name": name}):
                head.append(soup.new_tag("meta", attrs={"name": name, "content": content}))
        head.append(soup.new_tag("link", attrs={"rel": "preconnect", "href": "https://fonts.googleapis.com"}))
        head.append(soup.new_tag("link", attrs={
            "rel": "preconnect", "href": "https://fonts.gstatic.com", "crossorigin": ""}))
        head.append(soup.new_tag("link", attrs={"rel": "stylesheet", "href": templates.FONT_URL}))
        head.append(soup.new_tag("script", attrs={"src": templates.TAILWIND_URL}))
        head.append(_script(soup, templates.TAILWIND_CONFIG))
        style = soup.new_tag("style")
        style.string = styles.IMAGE_CAP_CSS + (templates.FADE_CSS if "fade_in_on_scroll" in self.scripts else "")
        head.append(style)

    def _scripts(self, body: Tag, head: Tag) -> None:
        soup = self.soup
        if "dark_mode_toggle" in self.scripts:
            body.insert(0, _fragment(soup, templates.DARK_BUTTON))
            body.append(_script(soup, templates.DARK_SCRIPT))
        if "fade_in_on_scroll" in self.scripts:
            body.append(_script(soup, templates.FADE_SCRIPT))


def _bounded(value, field: str, limit: int) -> str:
    purified = one_line(value) if isinstance(value, str) else ""
    if not purified or len(purified) > limit:
        raise Refusal(f"expected {field} of 1 to {limit} characters")
    return purified


TEXT_COLOR = re.compile(r"^(?:[a-z-]+:)*text-(?:white|black|current|inherit|[a-z]+-\d{2,3})(?:/\d+)?$")


# The body already has a light and a dark theme, so a fixed dark background or a fixed
# text color from the model would leave the page half dark in light mode.
BODY_THEME = re.compile(r"^(?:text-(?:white|black|[a-z]+-\d{2,3})|(?:bg|from|via|to)-(?:black|[a-z]+-[5-9]\d\d))$")
FIXED_HEIGHT = re.compile(r"^h-(?:\d+(?:\.\d+)?|px|full|screen|\d+/\d+|\[.+\])$")
FIXED_WIDTH = re.compile(r"^w-(?:\d+(?:\.\d+)?|px|full|screen|\d+/\d+|\[.+\])$")


def _without_clashing_colors(tag: Tag, tokens: list[str]) -> list[str]:
    # Gradient text only shows through a transparent fill, so a text color
    # added later would turn it into plain text.
    if tag.name == "body":
        tokens = [t for t in tokens if ":" in t or not BODY_THEME.match(t)]
    if "text-transparent" not in (tag.get("class") or []):
        return tokens
    return [t for t in tokens if not TEXT_COLOR.match(t)]


def _drop_automatic_sizes(tag: Tag, tokens: list[str]) -> None:
    # A size the model asked for would lose to the default h-auto or w-auto, which Tailwind sorts later.
    drop = set()
    if any(FIXED_HEIGHT.match(t) for t in tokens):
        drop.add("h-auto")
    if any(FIXED_WIDTH.match(t) for t in tokens):
        drop.add("w-auto")
    if drop:
        tag["class"] = [c for c in tag.get("class") or [] if c not in drop]


def _flatten_nested_cards(body: Tag) -> None:
    for tag in body.find_all(True):
        parent = tag.parent
        if isinstance(parent, Tag) and styles.is_card(parent) and styles.is_card(tag):
            styles.without_card(tag)


def _add_classes(tag: Tag, tokens: list[str]) -> None:
    existing = tag.get("class") or []
    if isinstance(existing, str):
        existing = existing.split()
    tag["class"] = existing + [t for t in tokens if t not in existing]


def _fragment(soup: BeautifulSoup, markup: str) -> Tag:
    return BeautifulSoup(markup, "html.parser").find(True).extract()


def _script(soup: BeautifulSoup, code: str) -> Tag:
    tag = soup.new_tag("script")
    tag.string = code
    return tag


def _structure(soup: BeautifulSoup) -> tuple[Tag, Tag]:
    """Make sure the page has html, head and body, moving loose content and never dropping any."""
    html = soup.find("html")
    if html is None:
        html = soup.new_tag("html")
        for node in list(soup.contents):
            if not isinstance(node, Doctype):
                html.append(node.extract())
        soup.append(html)
    if not html.get("lang"):
        html["lang"] = "en"
    if not any(isinstance(node, Doctype) for node in soup.contents):
        soup.insert(0, Doctype("html"))

    head = html.find("head", recursive=False)
    body = html.find("body", recursive=False)
    loose_head = {"title", "meta", "link", "style", "base"}
    if head is None:
        head = soup.new_tag("head")
        for node in list(html.children):
            if isinstance(node, Tag) and node.name in loose_head:
                head.append(node.extract())
        html.insert(0, head)
    if body is None:
        body = soup.new_tag("body")
        for node in list(html.contents):
            if node is not head:
                body.append(node.extract())
        html.append(body)
    return head, body
