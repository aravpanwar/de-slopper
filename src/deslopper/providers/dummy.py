from typing import Any

from deslopper.providers.base import Provider


class DummyProvider(Provider):
    name = "dummy"
    model = "none"
    prompt_version = "0"
    use_cache = False
    writes_tests = False

    def plan(self, kind: str, source: str, context: dict[str, Any]) -> str:
        return "[]"

    def write_tests(self, source: str, context: dict[str, Any]) -> str:
        return ""
