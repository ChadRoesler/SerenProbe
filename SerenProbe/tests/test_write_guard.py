"""
The write interlock. SerenProbe must be structurally incapable of writing to a
store it did not spin up.

This test exists because the failure it prevents ALREADY HAPPENED: a seeder
defaulted to a live port and wrote a synthetic corpus into a real, in-use
SerenMemory instance. There were four separate code paths to that barrel
(a hardcoded-7420 script, the legacy live_eval seeders, the /eval/run fallback,
and a clean_stores rmtree), and deleting them one by one is whack-a-mole -- the
fifth gets written at 2am by someone tired.

So the guard is an INVARIANT, and these are the tests that keep it one.
"""
from __future__ import annotations

import pytest

from seren_probe.runtime.write_guard import (
    WriteTargetError, allow_targets, clear_targets, assert_write_allowed, is_write,
)

# The operator's REAL stores. Nothing in this harness may ever write to these.
REAL_MEMORY = "http://localhost:7420"
REAL_LOCI = "http://127.0.0.1:7422"
# Containers a topology spun up. These are ours.
OWNED_MEM = "http://localhost:7620"
OWNED_LOCI = "http://localhost:7621"
OWNED_SCC = "http://localhost:7624"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("SEREN_PROBE_WRITE_TARGETS", raising=False)
    clear_targets()
    yield
    clear_targets()


def _refused(url, path, method="POST") -> bool:
    try:
        assert_write_allowed(url, path, method)
        return False
    except WriteTargetError:
        return True


# ── fail-closed ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("url,path,method", [
    (REAL_MEMORY, "/short", "POST"),
    (REAL_MEMORY, "/near", "POST"),
    (REAL_MEMORY, "/short/abc/promote", "POST"),
    (REAL_MEMORY, "/short/abc", "DELETE"),
    (REAL_LOCI, "/fact", "POST"),
    ("http://localhost:7423", "/configure", "POST"),
])
def test_no_topology_means_nothing_is_writable(url, path, method):
    """An empty allowlist refuses EVERY write. This is the whole point: a tool
    whose job is to manufacture fake data must default to writing NOWHERE."""
    assert _refused(url, path, method)


# ── reads always pass ─────────────────────────────────────────────────────
def test_search_is_a_post_but_a_read():
    """/search is the one mutating-verb endpoint that isn't a mutation. Blocking
    it would break every eval, so it passes even with an empty allowlist."""
    assert not _refused(REAL_MEMORY, "/search")
    assert not is_write("POST", "/search")


@pytest.mark.parametrize("path", ["/health", "/fact", "/stores", "/counts"])
def test_gets_are_never_guarded(path):
    assert not _refused(REAL_MEMORY, path, "GET")


# ── the topology owns its own containers ──────────────────────────────────
def test_owned_containers_are_writable():
    allow_targets([OWNED_MEM, OWNED_LOCI, OWNED_SCC])
    assert not _refused(OWNED_MEM, "/short")
    assert not _refused(OWNED_LOCI, "/fact")
    assert not _refused(OWNED_SCC, "/configure")


def test_real_stores_stay_refused_even_with_a_topology_up():
    """THE test. A topology being up must not become a licence to write anywhere."""
    allow_targets([OWNED_MEM, OWNED_LOCI, OWNED_SCC])
    assert _refused(REAL_MEMORY, "/short")
    assert _refused(REAL_LOCI, "/fact")


# ── the guard can't be walked around ──────────────────────────────────────
def test_host_spellings_collapse():
    """localhost / 127.0.0.1 / 0.0.0.0 are the same box. A guard you can dodge by
    respelling the hostname is decoration."""
    allow_targets([OWNED_MEM])                       # spelled 'localhost'
    assert not _refused("http://127.0.0.1:7620", "/short")
    assert _refused("http://0.0.0.0:7420", "/short")


def test_port_matters_not_just_host():
    allow_targets([OWNED_MEM])                       # 7620
    assert _refused("http://localhost:7420", "/short")   # same host, real port


def test_unknown_write_endpoints_are_guarded_by_default():
    """A mutating endpoint nobody has written yet is refused WITHOUT anyone
    remembering to add it to a list. Fail-closed on the unknown."""
    assert _refused(REAL_MEMORY, "/some-endpoint-from-the-future")


# ── the operator escape hatch ─────────────────────────────────────────────
def test_env_hatch_is_explicit_and_narrow(monkeypatch):
    """Refusing the operator ANY way to do a thing is a mandate, not an ethos.
    But it has to be deliberate: typing the target is consent, inheriting a
    default is not. And it unlocks ONLY what it names."""
    monkeypatch.setenv("SEREN_PROBE_WRITE_TARGETS", "127.0.0.1:7420")
    assert not _refused(REAL_MEMORY, "/short")       # named -> allowed
    assert _refused(REAL_LOCI, "/fact")              # not named -> still refused


def test_hatch_closes_when_the_env_var_goes(monkeypatch):
    monkeypatch.setenv("SEREN_PROBE_WRITE_TARGETS", "127.0.0.1:7420")
    assert not _refused(REAL_MEMORY, "/short")
    monkeypatch.delenv("SEREN_PROBE_WRITE_TARGETS")
    assert _refused(REAL_MEMORY, "/short")
