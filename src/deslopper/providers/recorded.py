from pathlib import Path
from typing import Any

from deslopper.providers.base import Provider, ProviderError


class RecordedProvider(Provider):
    """Replays saved replies, one file per swept path, so tests never touch the network."""

    name = "recorded"
    model = "recorded"
    prompt_version = "0"
    use_cache = False

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def plan(self, kind: str, source: str, context: dict[str, Any]) -> str:
        return self._read(context["path"], ".json")

    def _read(self, name: str, suffix: str) -> str:
        path = self.directory / (name.replace("/", "__") + suffix)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            raise ProviderError(f"expected a recorded reply at {path.name}") from None

    def write_tests(self, source: str, context: dict[str, Any]) -> str:
        return self._read(context["path"], ".tests.json")
