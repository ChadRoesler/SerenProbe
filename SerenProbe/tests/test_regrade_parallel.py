"""
Regrade concurrency: corpora run SERIALLY; stores fan out, corpora do not.

This file used to assert fan-out ACROSS corpora, on the premise that each SCC is
an independent container. It is not: an SCC holds no data and reaches into member
stores that other corpora reach into too, so parallel sweeps contend by
construction -- and a contended sweep grades the contention as a knob result,
which is the worst failure available to a tuning tool. See
test_live_regrade_corpora_run_serially for the full reasoning.

What survives from the original contract: a failing corpus must NOT take the whole
run down. Regrade is read-only, so the other corpora still finish and report, and
the failed one shows up as an "error" entry rather than a raised exception.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest

from seren_probe.core.topology import compile_topology
from seren_probe.core.seed_dataset import Question
from seren_probe.runtime import regrade_live


def _topo(n_corpus: int = 3):
    """N independent corpora, each fanning the same one loci + one memory pair
    (content doesn't matter here -- these tests fake the transport)."""
    return compile_topology({"ProbeConfig": {
        "StartingPort": 7600,
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "L0", "Port": 7601}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "M0", "Port": 7602}]},
        "Corpus": {
            "CorpusRegrades": [
                {"Name": "baseline"},
                {"Name": "wide", "n_results": [10, 20]},
            ],
            "CorpusCount": n_corpus,
            "CorpusConfigs": [
                {"Name": f"C{i}", "Port": 7610 + i,
                 "Stores": [{"Store": "L0"}, {"Store": "M0"}]}
                for i in range(n_corpus)
            ],
        },
    }})


def _urls(topo, n_corpus: int):
    urls = {n.name: f"http://127.0.0.1:{n.port}" for n in list(topo.loci) + list(topo.memory)}
    for i, c in enumerate(topo.corpus):
        urls[c.name] = f"http://127.0.0.1:{7610 + i}"
    return urls


def _questions():
    return [Question(asks="corpus", query="q1", expect_content=["hello world"])]


class _FakeScc:
    """Fakes GET /stores + POST /configure + POST /search for regrade_live,
    keyed by base URL. delay simulates per-call latency so concurrency can be
    measured; boom_url raises for that one corpus's calls (any path)."""

    def __init__(self, delay: float = 0.0, boom_url: str | None = None):
        self.delay = delay
        self.boom_url = boom_url
        self.calls: dict[str, int] = {}
        self.threads_seen: set[str] = set()
        self._lock = threading.Lock()

    def get(self, url: str, path: str, timeout=15.0) -> dict:
        self._touch(url)
        return {"k": 60, "n_results": 10, "stores": [
            {"name": "L0", "type": "seren_loci", "weight": 1.0, "floor": 0.0},
            {"name": "M0", "type": "seren_memory", "weight": 1.0, "floor": 0.0},
        ]}

    def post(self, url: str, path: str, body: dict, timeout=30.0) -> dict:
        self._touch(url)
        if path == "/search":
            return {"hits": [{"id": "h1", "content": "hello world", "score": 1.0}]}
        return {}

    def _touch(self, url: str) -> None:
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.calls[url] = self.calls.get(url, 0) + 1
            self.threads_seen.add(threading.current_thread().name)
        if self.boom_url and url == self.boom_url:
            raise RuntimeError(f"SCC at {url} is on fire")


@pytest.fixture()
def patch_transport(monkeypatch):
    def _apply(fake: _FakeScc):
        monkeypatch.setattr(regrade_live, "_get",
                            lambda url, path, timeout=15.0: fake.get(url, path, timeout))
        monkeypatch.setattr(regrade_live, "_post",
                            lambda url, path, body, timeout=30.0: fake.post(url, path, body, timeout))
    return _apply


def test_live_regrade_parallel_matches_serial_result(patch_transport):
    """Same topology, width=1 vs width=8: the corpora list must come back in the
    SAME order with the SAME content, regardless of thread completion order."""
    topo = _topo(3)
    urls = _urls(topo, 3)
    qs = _questions()

    fake_serial = _FakeScc()
    patch_transport(fake_serial)
    serial = regrade_live.run_live_regrade(topo, urls, qs, max_parallel_corpora=1)

    fake_par = _FakeScc()
    patch_transport(fake_par)
    parallel = regrade_live.run_live_regrade(topo, urls, qs, max_parallel_corpora=8)

    assert [c["corpus"] for c in serial["corpora"]] == ["C0", "C1", "C2"]
    assert [c["corpus"] for c in parallel["corpora"]] == ["C0", "C1", "C2"]
    # Strip nothing-timing-dependent fields and compare the substance.
    for s, p in zip(serial["corpora"], parallel["corpora"]):
        assert s["corpus"] == p["corpus"]
        assert s["flavor"] == p["flavor"]
        assert s["sets"] == p["sets"]


def test_live_regrade_corpora_run_serially(patch_transport):
    """Corpora must NOT fan out, however wide the caller asks for.

    This test used to assert the opposite -- that N corpora finish in roughly
    max(corpus_time) rather than sum(corpus_time) -- on the premise that each SCC is
    an independent container. That premise is wrong, and it was wrong in live_eval
    for the same reason: an SCC holds NO DATA. It fans into member stores that other
    corpora fan into too (Characters-scc and All-scc share every character's loci and
    memory; All-scc alone fans 22). Two sweeps in flight are two N-store fans against
    an overlapping set of containers, and a regrade is worse than an eval because it
    also POSTs /configure between combos.

    Observed live: under that contention every per-entity SCC read 0.000 while its own
    members read 0.5-1.0 on identical data. A contended sweep does not fail loudly --
    it returns degraded packets that grade as a BAD KNOB SETTING, which is the worst
    outcome available to a tool whose only job is telling good settings from bad.

    There is no safe width, because the sharing is structural rather than incidental:
    reaching into stores that belong to someone else is the entire job description of
    a corpus. So the knob is ignored above 1 -- and ignored OUT LOUD, because a config
    value that silently does nothing is its own bug.
    """
    topo = _topo(4)
    urls = _urls(topo, 4)
    qs = _questions()
    fake = _FakeScc(delay=0.01)
    patch_transport(fake)

    res = regrade_live.run_live_regrade(topo, urls, qs, max_parallel_corpora=4)

    assert fake.threads_seen == {"MainThread"}, (
        f"corpora fanned out across {fake.threads_seen} -- they share member stores "
        f"and must run one at a time")
    # Serialization must not cost correctness: every corpus still reports, in topology
    # order, exactly as the old parallel path did.
    assert [c["corpus"] for c in res["corpora"]] == [c.name for c in topo.corpus]


def test_live_regrade_warns_when_width_is_ignored(patch_transport, caplog):
    """Ignoring the knob silently would be its own bug - it must say so."""
    topo = _topo(4)
    urls = _urls(topo, 4)
    fake = _FakeScc(delay=0.0)
    patch_transport(fake)

    with caplog.at_level(logging.WARNING):
        regrade_live.run_live_regrade(topo, urls, _questions(), max_parallel_corpora=4)
    # getMessage(), not .message -- LogRecord has no `.message` attribute until a
    # Formatter puts one there, and caplog hands back RAW records. The %-args are
    # still unmerged at this point, so the substring must be in the format string.
    assert any("max_parallel_corpora" in r.getMessage() for r in caplog.records), \
        "width was ignored without telling anyone"


def test_live_regrade_one_failing_corpus_does_not_sink_the_others(patch_transport):
    """A corpus whose SCC blows up must show up as an error entry -- the other
    corpora still complete and report normally. This is the deliberate
    deviation from seed_from_plan's loud re-raise: regrade is read-only."""
    topo = _topo(3)
    urls = _urls(topo, 3)
    qs = _questions()
    boom_url = urls["C1"]
    fake = _FakeScc(boom_url=boom_url)
    patch_transport(fake)

    result = regrade_live.run_live_regrade(topo, urls, qs, max_parallel_corpora=4)

    by_name = {c["corpus"]: c for c in result["corpora"]}
    assert set(by_name) == {"C0", "C1", "C2"}
    assert "error" in by_name["C1"]
    assert "on fire" in by_name["C1"]["error"]
    assert "error" not in by_name["C0"]
    assert "error" not in by_name["C2"]
    assert by_name["C0"]["sets"]
    assert by_name["C2"]["sets"]


def test_live_regrade_width_one_takes_the_thread_free_path(patch_transport):
    """max_parallel_corpora=1 must not spawn a pool at all."""
    topo = _topo(2)
    urls = _urls(topo, 2)
    qs = _questions()
    fake = _FakeScc()
    patch_transport(fake)

    regrade_live.run_live_regrade(topo, urls, qs, max_parallel_corpora=1)
    assert fake.threads_seen == {threading.current_thread().name}


# ── the async CLI path (regrade.py) ──────────────────────────────────────────
def _require_config_regrade():
    try:
        from seren_probe.runtime import regrade as regrade_mod
    except Exception as exc:  # pragma: no cover - only fires if the module itself is broken
        pytest.skip(f"regrade module unavailable: {exc}")
    if not regrade_mod._SCC_AVAILABLE:
        pytest.skip("seren_corpus_callosum not importable in this environment")
    return regrade_mod


def test_config_regrade_one_failing_corpus_does_not_sink_the_others(monkeypatch):
    """Same contract as the live path, for run_config_regrade's asyncio.gather
    fan-out: one corpus's capture raising must not prevent the others from
    completing and reporting -- it shows up as an 'error' entry instead."""
    regrade_mod = _require_config_regrade()
    topo = _topo(3)
    urls = _urls(topo, 3)
    qs = _questions()

    async def fake_capture_stores(base_stores, rqs, transport, capture_n):
        name = base_stores[0].url if base_stores else ""
        if "7612" in name:  # C2's url (7610 + 2)
            raise RuntimeError("capture blew up for C2")
        return {}

    async def fake_sweep(capture, base_stores, queries, eval_k=10, sort_by="ndcg", grid=None):
        return {"ndcg": 1.0, "docket_coverage": 1.0, "params": {"n_results": 10}}, []

    class _NoopTransport:
        async def aclose(self):
            return None

    monkeypatch.setattr(regrade_mod, "capture_stores", fake_capture_stores)
    monkeypatch.setattr(regrade_mod, "sweep", fake_sweep)
    monkeypatch.setattr(regrade_mod, "RealTransport", lambda *a, **k: _NoopTransport())
    monkeypatch.setattr(regrade_mod, "_require_scc", lambda: None)

    result = asyncio.run(regrade_mod.run_config_regrade(topo, urls, qs, max_parallel_corpora=4))

    by_name = {c["corpus"]: c for c in result["corpora"]}
    assert set(by_name) == {"C0", "C1", "C2"}
    assert "error" in by_name["C2"]
    assert "blew up" in by_name["C2"]["error"]
    assert "error" not in by_name["C0"]
    assert "error" not in by_name["C1"]
    assert by_name["C0"]["sets"]
    assert by_name["C1"]["sets"]
    # Order must be original topology order, not completion order.
    assert [c["corpus"] for c in result["corpora"]] == ["C0", "C1", "C2"]
