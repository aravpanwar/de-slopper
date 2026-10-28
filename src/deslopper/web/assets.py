import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup


def linked_files(markup: str) -> list[str]:
    soup = BeautifulSoup(markup.removeprefix("\ufeff"), "html.parser")
    found = []
    for tag in soup.find_all(["link", "img", "script", "source", "video", "audio"]):
        if tag.name == "link":
            rel = tag.get("rel") or []
            value = tag.get("href") if {"stylesheet", "icon"} & set(rel) else None
        else:
            value = tag.get("src")
        if value:
            found.append(str(value))
    return found


def copy_assets(source_dir: Path, dest_dir: Path, markup: str) -> list[str]:
    """Copy the local stylesheets and images a page links to, so the output page still looks right."""
    copied = []
    for value in linked_files(markup):
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or value.startswith(("/", "#")):
            continue
        relative = Path(unquote(parts.path))
        if not relative.parts or ".." in relative.parts:
            continue
        source, target = source_dir / relative, dest_dir / relative
        # An existing file is never overwritten, which also covers an output
        # folder that is the input folder.
        if not source.is_file() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(relative.as_posix())
    return copied
