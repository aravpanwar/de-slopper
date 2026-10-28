from abc import ABC, abstractmethod
from typing import Any


class ProviderError(Exception):
    pass


class Provider(ABC):
    name: str
    model: str
    prompt_version: str

    # Providers that cost nothing or replay saved replies opt out, so a
    # sweep with them never writes cache entries.
    use_cache: bool = True

    # False for providers that have no test file to offer.
    writes_tests: bool = True

    def version(self, kind: str) -> str:
        """Prompt version for one kind of request, which goes into its cache key."""
        return self.prompt_version

    @abstractmethod
    def plan(self, kind: str, source: str, context: dict[str, Any]) -> str:
        """Return the raw reply text: a JSON list of edits."""

    @abstractmethod
    def write_tests(self, source: str, context: dict[str, Any]) -> str:
        """Return the source of a test file for the given module."""

    def draw(self, request: str, context: dict[str, Any]) -> str:
        """Return the raw reply text: a JSON list of svg drawings, one per image asked for."""
        raise ProviderError(f"expected a provider that can draw images, got {self.name}")
