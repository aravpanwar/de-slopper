import html
import re

import markdown

HEADING = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def markdown_to_page(text: str, fallback_title: str) -> str:
    """Turn Markdown into a plain HTML page, which is then swept like any other page."""
    body = markdown.markdown(text.removeprefix("\ufeff"), extensions=["tables", "fenced_code", "sane_lists"])
    found = HEADING.search(text)
    heading = re.sub(r"[*`]", "", found.group(1)).strip() if found else fallback_title
    title = html.escape(heading, quote=False)
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{title}</title>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )
