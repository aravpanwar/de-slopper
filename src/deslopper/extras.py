import re
import tempfile
from pathlib import Path

from deslopper.fsutil import write_atomic
from deslopper.receipts import Entry
from deslopper.web.react import landing_page_tsx
from deslopper.web.screenshot import ScreenshotError, capture


def page_extras(kind: str, name: str, original: Path, before: str, output: str, target: Path,
                react: bool, screenshot: bool, component: str) -> list[Entry]:
    """The optional extras for one swept page: a React component and before and after screenshots."""
    entries: list[Entry] = []
    if not (react or screenshot):
        return entries
    if kind != "html":
        reason = "expected a page, got a python file"
        return [Entry(name, extra, "skipped", reason=reason)
                for extra, wanted in (("react", react), ("screenshot", screenshot)) if wanted]

    if react:
        tsx = target.with_name(f"{component}.tsx")
        write_atomic(tsx, landing_page_tsx(output, component).encode("utf-8"))
        entries.append(Entry(name, "react", "applied", detail=f"wrote {tsx.name}"))
    if screenshot:
        entries.append(_screenshots(name, original, before, target))
    return entries


def component_name(stem: str, single: bool) -> str:
    if single:
        return "LandingPage"
    words = [w for w in re.split(r"[^A-Za-z0-9]+", stem) if w]
    return "".join(w[:1].upper() + w[1:] for w in words) + "LandingPage"


def _screenshots(name: str, original: Path, before: str, target: Path) -> Entry:
    before_png = target.with_name(f"{target.stem}.before.png")
    after_png = target.with_name(f"{target.stem}.after.png")
    with tempfile.TemporaryDirectory() as scratch:
        if original.suffix.lower() in (".html", ".htm"):
            source_url = original.resolve().as_uri()
        else:
            # A converted page has no file of its own, so it gets a temporary one
            # that resolves its images against the folder it came from.
            base = original.resolve().parent.as_uri().rstrip("/") + "/"
            page = Path(scratch) / "before.html"
            page.write_text(before.replace("<head>", f'<head>\n<base href="{base}">', 1), encoding="utf-8")
            source_url = page.as_uri()
        try:
            capture([(source_url, before_png), (target.resolve().as_uri(), after_png)])
        except ScreenshotError as err:
            return Entry(name, "screenshot", "note", reason=str(err))
    return Entry(name, "screenshot", "note", reason=f"wrote {before_png.name} and {after_png.name}")
