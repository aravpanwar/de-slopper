from conftest import CannedProvider
from deslopper.sweep import sweep_source


def test_roundtrip_and_miss(cache):
    key = cache.key("1", "m", "python", "x = 1\n", {"path": "a.py"})
    assert cache.get(key) is None
    cache.put(key, "[]")
    assert cache.get(key) == "[]"


def test_key_changes_with_every_input(cache):
    base = ("1", "m", "python", "x = 1\n", {"path": "a.py"})
    keys = {
        cache.key(*base),
        cache.key("2", *base[1:]),
        cache.key(base[0], "n", *base[2:]),
        cache.key(*base[:3], "x = 2\n", base[4]),
        cache.key(*base[:4], {"path": "b.py"}),
    }
    assert len(keys) == 5


def test_corrupt_entry_is_a_miss(cache):
    key = cache.key("1", "m", "python", "x", {})
    cache.put(key, "[]")
    (cache.root / f"{key}.json").write_text("{not json", encoding="utf-8")
    assert cache.get(key) is None


def test_rerun_does_not_call_the_provider_again(cache):
    provider = CannedProvider("[]")
    first = sweep_source("python", "x = 1\n", "a.py", provider, cache)
    second = sweep_source("python", "x = 1\n", "a.py", provider, cache)
    assert provider.calls == 1
    assert first.output == second.output
