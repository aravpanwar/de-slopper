import pytest

from deslopper.cache import Cache
from deslopper.providers.base import Provider


class CannedProvider(Provider):
    name = "canned"
    model = "canned-1"
    prompt_version = "1"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    def plan(self, kind, source, context):
        self.calls += 1
        return self.reply

    def write_tests(self, source, context):
        return ""


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DESLOPPER_CACHE_DIR", str(tmp_path / "home-cache"))


@pytest.fixture(autouse=True)
def no_browser(request, monkeypatch):
    # Which elements a page hides is asked of a browser in real runs. Tests
    # take the fallback unless they are marked as needing a browser.
    if "browser" not in request.keywords:
        from deslopper.web import hidden
        monkeypatch.setattr(hidden, "browser_hidden", lambda origin: None)
