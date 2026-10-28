from deslopper.providers.base import Provider, ProviderError
from deslopper.providers.dummy import DummyProvider
from deslopper.providers.openai_compat import OpenAICompatProvider

PROVIDERS: dict[str, type[Provider]] = {
    OpenAICompatProvider.name: OpenAICompatProvider,
    DummyProvider.name: DummyProvider,
}

DEFAULT_PROVIDER = OpenAICompatProvider.name


def make_provider(name: str) -> Provider:
    if name not in PROVIDERS:
        raise ProviderError(f"expected one of {sorted(PROVIDERS)}, got {name}")
    return PROVIDERS[name]()
