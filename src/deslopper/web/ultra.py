import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Tag

from deslopper.fsutil import write_atomic
from deslopper.purify import load_json, strip_fences
from deslopper.receipts import Entry

MAX_IMAGES = 16  # per page, because one reply has to hold every drawing
MAX_SVG_BYTES = 6036
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
IMAGE_DIR = "images"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)
# What is never sent along with an original: editor leftovers, metadata, and anything that runs.
DROPPED_FROM_ORIGINALS = {"script", "metadata", "foreignobject"}

# Nothing that runs, loads something else, or hides an html page inside the drawing.
FORBIDDEN = {"script", "foreignobject", "iframe", "embed", "object", "image", "audio", "video", "canvas",
             "link", "meta", "base"}
HREF_NAMES = {"href", "xlink:href"}
URL_CALL = re.compile(r"url\(\s*(['\"]?)\s*([^)'\"]*)", re.IGNORECASE)
DOCTYPE = re.compile(r"<!\s*(doctype|entity)", re.IGNORECASE)
PROCESSING = re.compile(r"<\?(?!xml\s)")
ROOT_TAG = re.compile(r"<svg(?=[\s>/])")

SPONSOR_WORDS = ("sponsor", "partner", "supporter", "backer", "client", "customer", "trusted")
LOGO_WORDS = ("logo", "brand", "wordmark")
ICON_WORDS = ("icon", "favicon", "arrow", "bullet", "chevron", "badge")
ICON_SIZE = 46
RUN_OF_LOGOS = 3
SCRIPT_IMAGES = re.compile(r"createElement\(\s*['\"]img|new\s+Image\b|<img\b|\.(png|jpe?g|gif|webp|svg)\b",
                           re.IGNORECASE)


@dataclass(frozen=True)
class Image:
    id: int
    src: str
    file: str
    alt: str
    heading: str
    text: str
    role: str
    width: str | None = None
    height: str | None = None
    original: str | None = None  # the source of a small local svg, purified

    def asked(self) -> dict:
        shown = {"id": self.id, "file": self.file, "alt": self.alt, "heading": self.heading,
                 "text": self.text, "role": self.role, "width": self.width, "height": self.height,
                 "original_svg": self.original}
        return {k: v for k, v in shown.items() if v not in (None, "")}


@dataclass(frozen=True)
class Drawing:
    src: str
    path: str
    svg: str


@dataclass(frozen=True)
class Swapped:
    markup: str
    drawings: tuple[Drawing, ...]
    entries: tuple[Entry, ...]


def find_images(markup: str, name: str, base: Path | None = None) -> tuple[list[Image], list[Entry]]:
    """The images of a page that can be redrawn, one per distinct src, and notes about the rest.
    `base` is the folder the page came from, where a small local svg is read for its likeness."""
    soup = BeautifulSoup(markup.removeprefix("\ufeff"), "html.parser")
    images: list[Image] = []
    notes: list[Entry] = []
    seen: set[str] = set()
    for tag in soup.find_all("img"):
        src = str(tag.get("src") or "").strip()
        if not src or src in seen:
            continue
        seen.add(src)
        if src.startswith("data:"):
            notes.append(Entry(name, "ultra", "skipped", reason="expected an image file, got an embedded one",
                               detail="data: image"))
            continue
        images.append(Image(len(images) + 1, src, _file(src), " ".join(str(tag.get("alt") or "").split())[:136],
                            _heading(tag), _text(tag), _role(tag, src),
                            _size(tag.get("width")), _size(tag.get("height")), original_svg(src, base)))
    if any(SCRIPT_IMAGES.search(s.get_text()) for s in soup.find_all("script") if not s.get("src")):
        notes.append(Entry(name, "ultra", "note",
                           reason="left images that a script builds as they were, because the script picks them at run time"))
    return images, notes


def original_svg(src: str, base: Path | None) -> str | None:
    """The purified source of a local svg of at most MAX_SVG_BYTES, or None when it should not be sent."""
    if base is None:
        return None
    parts = urlsplit(src)
    relative = Path(unquote(parts.path))
    if (parts.scheme or parts.netloc or src.startswith(("/", "#")) or relative.suffix.lower() != ".svg"
            or not relative.parts or ".." in relative.parts):
        return None
    path = base / relative
    try:
        if not path.is_file() or path.stat().st_size > MAX_SVG_BYTES:
            return None
        text = path.read_bytes().decode("utf-8").removeprefix("\ufeff")
    except (OSError, UnicodeDecodeError):
        return None
    return _purify_original(text)


def _purify_original(text: str) -> str | None:
    if DOCTYPE.search(text):
        return None
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return None
    if _local(root.tag) != "svg" or _namespace(root.tag) not in ("", SVG_NS):
        return None
    # Comments never reach the tree. The rest of the purifying is done here.
    for parent in root.iter():
        for child in list(parent):
            if (not isinstance(child.tag, str) or _local(child.tag).lower() in DROPPED_FROM_ORIGINALS
                    or _namespace(child.tag) not in ("", SVG_NS)):
                parent.remove(child)
    for node in root.iter():
        for key in list(node.attrib):
            ours = _namespace(key) in ("", XLINK_NS) or key.startswith("{http://www.w3.org/XML/1998/namespace}")
            if not ours or _local(key).lower().startswith("on"):
                del node.attrib[key]
    purified = ET.tostring(root, encoding="unicode").strip()
    return purified if len(purified.encode("utf-8")) <= MAX_SVG_BYTES else None


def request_text(name: str, images: list[Image]) -> str:
    asked = [image.asked() for image in images[:MAX_IMAGES]]
    return json.dumps({"page": name, "images": asked}, indent=1, ensure_ascii=False)


def swap_images(markup: str, name: str, images: list[Image], reply: str, art_dir: Path) -> Swapped:
    """Point each redrawn image at its own svg file, and keep the original for any drawing that is refused."""
    entries: list[Entry] = []
    skipped = images[MAX_IMAGES:]
    asked = images[:MAX_IMAGES]
    for image in skipped:
        entries.append(Entry(name, "ultra", "skipped", detail=image.src,
                             reason=f"expected at most {MAX_IMAGES} images drawn per page, left this one as it was"))
    drawn, problem = read_reply(reply)
    if problem:
        entries.append(Entry(name, "ultra", "rejected", reason=problem))
        return Swapped(markup, (), tuple(entries))

    drawings: list[Drawing] = []
    used: set[str] = set()
    for image in asked:
        svg = drawn.get(image.id)
        if svg is None:
            entries.append(Entry(name, "ultra", "skipped", detail=image.src,
                                 reason=f"expected an svg for image {image.id}, got none"))
            continue
        purified, reason = purify_svg(svg)
        if purified is None:
            entries.append(Entry(name, "ultra", "rejected", reason=reason, detail=image.src))
            continue
        path = _free_path(image.src, purified, art_dir, used)
        used.add(path)
        drawings.append(Drawing(image.src, path, purified))
        entries.append(Entry(name, "ultra", "applied", detail=f"{image.src} -> {path}"))
    if not drawings:
        return Swapped(markup, (), tuple(entries))
    return Swapped(_point_at(markup, drawings), tuple(drawings), tuple(entries))


def read_reply(reply: str) -> tuple[dict[int, str], str | None]:
    try:
        data = load_json(strip_fences(reply))
    except json.JSONDecodeError as err:
        return {}, f"expected JSON, parse failed at character {err.pos}"
    if isinstance(data, dict) and "images" in data:
        data = data["images"]
    if not isinstance(data, list):
        return {}, "expected a JSON list of images"
    found: dict[int, str] = {}
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("svg"), str):
            found.setdefault(item["id"], item["svg"])
    return found, None


def purify_svg(text: str) -> tuple[str | None, str | None]:
    """The svg with its missing basics filled in, or None and the reason it was refused."""
    text = text.strip()
    size = len(text.encode("utf-8"))
    if size > MAX_SVG_BYTES:
        return None, f"expected an svg of at most {MAX_SVG_BYTES} bytes, got {size}"
    if DOCTYPE.search(text):
        return None, "expected an svg without a doctype or entities"
    if PROCESSING.search(text):
        return None, "expected an svg without processing instructions"
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as err:
        return None, f"expected a well-formed svg, got {err}"

    if _local(root.tag) != "svg" or _namespace(root.tag) not in ("", SVG_NS):
        return None, f"expected an svg element as the root, got {_local(root.tag)}"
    box = _view_box(root.get("viewBox"))
    if box is None:
        return None, "expected a viewBox of four numbers with a size above zero"
    problem = _unsafe(root)
    if problem:
        return None, problem

    if _namespace(root.tag) == "":
        text = ROOT_TAG.sub(f'<svg xmlns="{SVG_NS}"', text, count=1)
    if root.get("width") is None and root.get("height") is None:
        width, height = (f"{value:g}" for value in box[2:])
        text = ROOT_TAG.sub(f'<svg width="{width}" height="{height}"', text, count=1)
    return text + "\n", None


def _unsafe(root: ET.Element) -> str | None:
    for node in root.iter():
        tag = node.tag
        if not isinstance(tag, str):
            return "expected only elements, got a comment or instruction"
        if _namespace(tag) not in ("", SVG_NS):
            return f"expected svg elements only, got {_local(tag)} from another namespace"
        local = _local(tag).lower()
        if local in FORBIDDEN:
            return f"expected no {local} element in the svg"
        if local in ("set", "animate") and _local_name(node.get("attributeName", "")) in HREF_NAMES:
            return f"expected {local} not to change a link"
        values = [node.text or ""]
        for key, value in node.attrib.items():
            attribute = _attribute(key)
            if attribute.lower().startswith("on"):
                return f"expected no {attribute} attribute in the svg"
            if attribute in HREF_NAMES and not value.strip().startswith("#"):
                return f"expected only links to ids inside the svg, got {value.strip()[:40]!r}"
            values.append(value)
        for value in values:
            if "javascript:" in value.lower() or "@import" in value.lower():
                return "expected no script or import in the svg"
            for _, target in URL_CALL.findall(value):
                if not target.strip().startswith("#"):
                    return f"expected url() to point at an id inside the svg, got {target.strip()[:40]!r}"
    return None


def write_drawings(art_dir: Path, drawings: tuple[Drawing, ...]) -> None:
    for drawing in drawings:
        write_atomic(art_dir / drawing.path, drawing.svg.encode("utf-8"))


def _point_at(markup: str, drawings: list[Drawing]) -> str:
    bom = markup.startswith("\ufeff")
    soup = BeautifulSoup(markup[1:] if bom else markup, "html.parser")
    paths = {d.src: d.path for d in drawings}
    for tag in soup.find_all("img"):
        src = str(tag.get("src") or "").strip()
        if src in paths:
            tag["src"] = paths[src]
            # What the browser would have picked instead must not win over the drawing.
            for extra in ("srcset", "sizes"):
                tag.attrs.pop(extra, None)
    return ("\ufeff" if bom else "") + str(soup)


def _free_path(src: str, svg: str, art_dir: Path, used: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(unquote(urlsplit(src).path)).stem).strip("-") or "image"
    number = 1
    while True:
        label = stem if number == 1 else f"{stem}-{number}"
        path = f"{IMAGE_DIR}/{label}.ultra.svg"
        number += 1
        if path in used:
            continue
        existing = art_dir / path
        # A drawing that is already there is reused when it is the same one, and never replaced when it is not.
        if not existing.exists() or existing.read_bytes() == svg.encode("utf-8"):
            return path


def _file(src: str) -> str:
    return Path(unquote(urlsplit(src).path)).name or src


def _size(value) -> str | None:
    value = str(value or "").strip()
    return value if re.fullmatch(r"\d+(\.\d+)?(px)?", value) else None


def _heading(tag: Tag) -> str:
    heading = tag.find_previous(["h1", "h2", "h3", "h4"])
    return " ".join(heading.get_text(" ").split())[:86] if heading else ""


def _text(tag: Tag) -> str:
    holder = tag.parent
    # A link or a figure around the image is not text of its own, so the words come from further up.
    for _ in range(3):
        if holder is None or holder.name not in ("a", "picture", "figure", "span", "p", "li", "td", "div"):
            break
        words = " ".join(holder.get_text(" ").split())
        if words:
            return words[:136]
        holder = holder.parent
    return ""


def _role(tag: Tag, src: str) -> str:
    hints = " ".join([src, str(tag.get("alt") or ""), " ".join(tag.get("class") or []), str(tag.get("id") or "")]).lower()
    around = " ".join(
        " ".join([str(p.get("id") or ""), " ".join(p.get("class") or [])]) for p in list(tag.parents)[:4]
        if isinstance(p, Tag)).lower()
    heading = _heading(tag).lower()
    siblings = [i for i in (tag.parent.find_all("img", recursive=False) if tag.parent else [])]
    in_run = len(siblings) >= RUN_OF_LOGOS
    sponsor = any(w in text for w in SPONSOR_WORDS for text in (hints, around, heading))
    if sponsor and (in_run or any(w in hints for w in LOGO_WORDS) or "sponsor" in hints or "partner" in hints):
        return "sponsor logo"
    if any(w in hints for w in LOGO_WORDS) or (in_run and not any(w in hints for w in ICON_WORDS)):
        return "logo"
    small = [_number(tag.get(k)) for k in ("width", "height")]
    if src.lower().endswith(".ico") or any(w in hints for w in ICON_WORDS) or any(
            n is not None and n <= ICON_SIZE for n in small):
        return "icon"
    if tag.find_parent(["header", "nav"]) is not None:
        return "logo"
    return "hero or photo"


def _number(value) -> float | None:
    try:
        return float(str(value).removesuffix("px"))
    except ValueError:
        return None


def _view_box(value: str | None) -> tuple[float, float, float, float] | None:
    try:
        numbers = tuple(float(part) for part in re.split(r"[\s,]+", (value or "").strip()))
    except ValueError:
        return None
    if len(numbers) != 4 or numbers[2] <= 0 or numbers[3] <= 0:
        return None
    return numbers


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attribute(key: str) -> str:
    # ElementTree writes a namespaced attribute as {uri}name.
    if key.startswith("{" + XLINK_NS + "}"):
        return "xlink:" + _local(key)
    return _local(key)


def _local_name(name: str) -> str:
    return name.strip().lower()
