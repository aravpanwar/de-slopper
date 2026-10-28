from pathlib import Path

from conftest import CannedProvider
from deslopper.providers.dummy import DummyProvider
from deslopper.engines import PassthroughEngine, Verdict
from deslopper.sweep import sweep_source

FIXTURES = Path(__file__).parent / "fixtures"


def test_dummy_provider_passes_every_fixture_through(cache):
    for path, kind in [(FIXTURES / "records.py", "python"), (FIXTURES / "page.html", "html")]:
        source = path.read_bytes().decode("utf-8")
        result = sweep_source(kind, source, path.name, DummyProvider(), cache)
        assert result.output == source
        assert [e.status for e in result.entries] == ["swept"]


def test_crlf_survives(cache):
    source = "x = 1\r\ny = 2\r\n"
    assert sweep_source("python", source, "a.py", DummyProvider(), cache).output == source


def test_rejections_and_skips_land_in_the_receipt(cache):
    reply = '[{"kind": "add_comment", "line": 3, "text": "# hi"}, {"kind": "nonsense"}]'
    result = sweep_source("python", "x = 1\n", "a.py", CannedProvider(reply), cache)
    assert result.output == "x = 1\n"
    by_status = {e.status: e for e in result.entries}
    assert by_status["skipped"].line == 3
    assert by_status["rejected"].kind == "nonsense"


class BreakingEngine(PassthroughEngine):
    def apply(self, source, edits, name):
        entries = super().apply(source, edits, name)[1]
        applied = [e.__class__(e.file, e.kind, "applied", line=e.line) for e in entries]
        return source + "oops(", applied

    def check(self, source, output, origin):
        return Verdict("expected the output to parse")


def test_failed_check_falls_back_to_the_original(cache):
    reply = '[{"kind": "add_comment", "line": 1, "text": "# hi"}]'
    result = sweep_source("python", "x = 1\n", "a.py", CannedProvider(reply), cache,
                          engine=BreakingEngine("python"))
    assert result.output == "x = 1\n"
    dropped = [e for e in result.entries if e.status == "dropped"]
    assert len(dropped) == 1 and dropped[0].reason == "expected the output to parse"
