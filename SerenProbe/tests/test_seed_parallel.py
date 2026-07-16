"""
Parallel seeding: fan out ACROSS stores, stay serial WITHIN one.

The optimisation is easy. Not corrupting the ground truth while doing it is the
part that needs a test, because the failure mode is silent and it is EXACTLY the
disease this harness spent a day curing.

SeedResult has two fields that genuinely race under naive threading:

    ref_to_id.setdefault(bare_ref, minted)      <- BARE key, shared across ALL stores
    key_index.setdefault(pk, []).append(name)   <- list.append from N threads

In a multi-brain topology (five character memories, one per NPC, plus a world
corpus) every dataset plausibly carries an `evt-000`. Serially, the bare key is
first-writer-wins in STORE order: stable, reproducible, boring. Let N threads race
for it and it becomes whoever-finishes-first -- ground truth that changes between
runs of the same command.

So the contract is: workers each fill an isolated _StorePartial, and the main
thread merges them in the resolver's store order. The parallel result must be
BYTE-IDENTICAL to the serial one. That is what these tests assert.
"""
from __future__ import annotations

import threading
import time

import pytest

from seren_probe.core.seed_dataset import LociItem, MemoryItem, seed_from_plan
from seren_probe.core.topology import compile_topology


def _topo(n_loci: int = 2, n_mem: int = 3):
    """A multi-brain topology: N loci + N memory + one catch-all corpus."""
    return compile_topology({"ProbeConfig": {
        "StartingPort": 7500,
        "Loci": {"LociCount": n_loci, "LociConfigs": [
            {"Name": f"L{i}", "Port": 7500 + i} for i in range(n_loci)]},
        "Memory": {"MemoryCount": n_mem, "MemoryConfigs": [
            {"Name": f"M{i}", "Port": 7520 + i} for i in range(n_mem)]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "C", "Port": 7590,
             "Stores": [{"Store": f"L{i}"} for i in range(n_loci)]
                       + [{"Store": f"M{i}"} for i in range(n_mem)]}]},
    }})


class _Recorder:
    """A fake transport. Mints deterministic ids and records call ORDER per store."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls: dict[str, list] = {}
        self._n = 0
        self._lock = threading.Lock()
        self.threads_seen: set[str] = set()

    def post(self, url: str, path: str, body: dict) -> dict:
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self._n += 1
            mid = f"id{self._n:04d}"
            self.calls.setdefault(url, []).append((path, body))
            self.threads_seen.add(threading.current_thread().name)
        return {"id": mid}

    def delete(self, url: str, path: str) -> None:
        with self._lock:
            self.calls.setdefault(url, []).append(("DELETE " + path, None))


def _plan(n_loci=2, n_mem=3, items_per_store=6):
    """Every memory store deliberately reuses the SAME ref handles (evt-000..).
    That collision is the whole point: it's what a multi-brain topology looks like,
    and it's what makes the bare-ref key a race."""
    plan = {}
    for i in range(n_loci):
        plan[f"L{i}"] = [LociItem(project="p", key=f"k{j}", value=f"v{j}", why="w")
                         for j in range(items_per_store)]
    for i in range(n_mem):
        plan[f"M{i}"] = [MemoryItem(tier="short", text=f"brain{i} item {j}",
                                    topic="t", ref=f"evt-{j:03d}")
                         for j in range(items_per_store)]
    return plan


def _urls(topo):
    return {n.name: f"http://127.0.0.1:{n.port}"
            for n in list(topo.loci) + list(topo.memory) + list(topo.corpus)}


# ── the contract ─────────────────────────────────────────────────────────
def test_parallel_result_is_identical_to_serial():
    """THE test. Same plan, width=1 vs width=8, byte-identical SeedResult.

    If this ever fails, the parallel seeder has invented a new ground truth and
    every score downstream is quietly wrong.
    """
    topo, plan, urls = _topo(), _plan(), None
    urls = _urls(topo)

    rec_serial = _Recorder()
    serial = seed_from_plan(topo, plan, urls, rec_serial.post, rec_serial.delete,
                            max_parallel_stores=1)

    rec_par = _Recorder()
    parallel = seed_from_plan(topo, plan, urls, rec_par.post, rec_par.delete,
                              max_parallel_stores=8)

    assert parallel.loci_counts == serial.loci_counts
    assert parallel.memory_counts == serial.memory_counts
    assert parallel.key_index == serial.key_index
    # The namespaced refs must match exactly...
    ns_serial = {k: v for k, v in serial.ref_to_id.items() if ":" in k}
    ns_par = {k: v for k, v in parallel.ref_to_id.items() if ":" in k}
    assert set(ns_serial) == set(ns_par)
    # ...and, crucially, the BARE ref must resolve to the SAME STORE in both.
    # (Not the same minted id -- the fake mints sequentially, so ids differ by
    # completion order. What must not differ is WHICH STORE won the bare key.)
    def _bare_owner(res):
        out = {}
        for bare in (k for k in res.ref_to_id if ":" not in k):
            mid = res.ref_to_id[bare]
            owner = next(k.split(":", 1)[0] for k, v in res.ref_to_id.items()
                         if ":" in k and v == mid)
            out[bare] = owner
        return out
    assert _bare_owner(parallel) == _bare_owner(serial), (
        "the bare ref key resolved to a DIFFERENT store under parallel seeding -- "
        "ground truth is now non-deterministic run to run")


def test_bare_ref_is_first_store_in_resolver_order():
    """Five brains all carrying evt-000: the FIRST memory store in the plan wins the
    bare key, deterministically, no matter which thread finishes first."""
    topo = _topo(n_loci=1, n_mem=5)
    plan = _plan(n_loci=1, n_mem=5)
    rec = _Recorder()
    res = seed_from_plan(topo, plan, _urls(topo), rec.post, rec.delete, max_parallel_stores=8)
    winner_id = res.ref_to_id["evt-000"]
    assert res.ref_to_id["M0:evt-000"] == winner_id, (
        "M0 is first in resolver order and must own the bare key")


def test_writes_within_a_store_stay_in_file_order():
    """Serial WITHIN a store. Order is semantically load-bearing in a memory store --
    timestamps, tier progression, the short->promote->long dance. A store seeded out
    of order is not the store you'd actually build."""
    topo = _topo(n_loci=1, n_mem=2)
    plan = _plan(n_loci=1, n_mem=2, items_per_store=8)
    rec = _Recorder()
    seed_from_plan(topo, plan, _urls(topo), rec.post, rec.delete, max_parallel_stores=8)
    for i in range(2):
        url = f"http://127.0.0.1:{7520 + i}"
        bodies = [b["content"] for p, b in rec.calls[url] if p == "/short"]
        assert bodies == [f"brain{i} item {j}" for j in range(8)]


def test_stores_actually_run_concurrently():
    """The whole point. With a per-call delay, N stores in parallel must finish in
    roughly max(store_time), not sum(store_time).

    n_loci=1, not 0: compile_topology REFUSES a loci-less topology (LociCount must be
    >= 1). My first pass asked for something the schema forbids -- the compiler was
    right and the test was wrong.
    """
    topo = _topo(n_loci=1, n_mem=4)             # 5 stores total
    plan = _plan(n_loci=1, n_mem=4, items_per_store=5)
    urls = _urls(topo)

    rec = _Recorder(delay=0.01)                 # 5 items x 10ms = ~50ms per store
    t0 = time.monotonic()
    seed_from_plan(topo, plan, urls, rec.post, rec.delete, max_parallel_stores=5)
    parallel_s = time.monotonic() - t0

    # 5 stores serially would be ~250ms; in parallel ~50ms. Assert we're comfortably
    # under the serial cost, with slack for CI jitter.
    assert parallel_s < 0.15, f"stores did not run concurrently ({parallel_s:.3f}s)"
    assert len(rec.threads_seen) > 1, "everything ran on one thread -- no fan-out happened"


def test_a_failing_store_is_loud():
    """A worker exception must RE-RAISE on the main thread. A half-seeded store scores
    like a bad store, and we are not doing that again."""
    topo = _topo(n_loci=1, n_mem=2)
    plan = _plan(n_loci=1, n_mem=2)

    def boom(url, path, body):
        if url.endswith("7521"):                # M1
            raise RuntimeError("store M1 is on fire")
        return {"id": "x"}

    with pytest.raises(RuntimeError, match="on fire"):
        seed_from_plan(topo, plan, _urls(topo), boom, None, max_parallel_stores=4)


def test_width_one_takes_the_thread_free_path():
    """max_parallel_stores=1 must not spawn a pool at all -- single-store topologies
    and every fake-transport test stay on a plain, boring code path."""
    topo = _topo(n_loci=1, n_mem=1)
    plan = _plan(n_loci=1, n_mem=1)
    rec = _Recorder()
    seed_from_plan(topo, plan, _urls(topo), rec.post, rec.delete, max_parallel_stores=1)
    assert rec.threads_seen == {threading.current_thread().name}
