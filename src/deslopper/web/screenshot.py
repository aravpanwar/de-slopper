from pathlib import Path

# Playwright's own browser first, then the Edge or Chrome that is already installed.
LAUNCHES = ({}, {"channel": "msedge"}, {"channel": "chrome"})
VIEWPORT = {"width": 1236, "height": 836}
NAVIGATION_TIMEOUT_MS = 30036
SETTLE_MS = 1036


class ScreenshotError(Exception):
    pass


def open_browser(playwright):
    problems = []
    for options in LAUNCHES:
        try:
            return playwright.chromium.launch(**options)
        except Exception as err:
            problems.append(str(err).strip().splitlines()[0] if str(err).strip() else type(err).__name__)
    raise ScreenshotError(f"expected a browser to launch, run playwright install chromium ({problems[-1]})")


def capture(shots: list[tuple[str, Path]]) -> None:
    """Take a full-page PNG of each (url, path) pair, in one browser, with the same settings for all."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ScreenshotError("expected playwright to be installed, run pip install de-slopper[screenshot]") from None

    with sync_playwright() as playwright:
        browser = open_browser(playwright)
        try:
            # A fixed light theme, and no motion, so the fade-in sections are
            # already visible when the whole page is captured.
            context = browser.new_context(viewport=VIEWPORT, color_scheme="light", reduced_motion="reduce")
            page = context.new_page()
            for url, path in shots:
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    page.goto(url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT_MS)
                except Exception:
                    # A slow font or CDN request should not cost the picture.
                    page.wait_for_timeout(SETTLE_MS)
                page.wait_for_timeout(SETTLE_MS)
                page.screenshot(path=str(path), full_page=True)
        except ScreenshotError:
            raise
        except Exception as err:
            raise ScreenshotError(f"expected a screenshot, got {str(err).strip().splitlines()[0]}") from None
        finally:
            browser.close()
