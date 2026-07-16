"""Tests for seren_probe.live_import - copy live Loci/Memory into the container.

Pure logic (mock transports), so no live services needed. The load-bearing test
is the safety invariant: the ONLY verb ever sent to a live URL is GET.
"""
from seren_probe.runtime import live_import as li

LIVE_LOCI = "http://192.168.0.101:7422"
LIVE_MEM = "http://192.168.0.101:7420"
TGT_LOCI = "http://127.0.0.1:7451"
TGT_MEM = "http://127.0.0.1:7450"

_FACTS = {"facts": [
    {"project": "*", "key": "camelCase", "value": "is life", "why": "readability"},
    {"project": "seren", "key": "port", "value": "6361", "why": None},
    {"project": "*", "key": "", "value": "skip", "why": None},          # no key -> skipped
]}
_SHORT = {"entries": [{"id": "s1", "content": "ground on embedder", "metadata": {"topic": "migration"}}]}
_NEAR = {"entries": [{"id": "n1", "content": "ask about supersede gap",
                      "metadata": {"topic": "loci", "trigger_type": "always"}}]}
_LONG = {"entries": [{"id": "l1", "content": "Smart > Good", "metadata": {"topic": "ethos"}}]}


class Recorder:
    """One transport trio that records every call as (verb, url, path, body)."""
    def __init__(self):
        self.calls = []
        self._sid = 0

    def get(self, url, path, params=None):
        self.calls.append(("GET", url, path, None))
        return {"/facts": _FACTS, "/short": _SHORT, "/near": _NEAR, "/long": _LONG}.get(path, {})

    def post(self, url, path, body):
        self.calls.append(("POST", url, path, body))
        if path == "/short":
            self._sid += 1
            return {"ok": True, "id": f"sid{self._sid}"}
        return {"ok": True}

    def delete(self, url, path):
        self.calls.append(("DELETE", url, path, None))

    def to(self, url):
        return [c for c in self.calls if c[1] == url]


def test_import_loci_copies_facts_skips_keyless():
    r = Recorder()
    n = li.import_loci(LIVE_LOCI, TGT_LOCI, post=r.post, get=r.get)
    assert n == 2
    fact_posts = [b for v, u, p, b in r.calls if v == "POST" and p == "/fact"]
    assert fact_posts[0] == {"project": "*", "key": "camelCase", "value": "is life",
                             "why": "readability", "source": "import"}


def test_import_loci_read_only_on_live():
    r = Recorder()
    li.import_loci(LIVE_LOCI, TGT_LOCI, post=r.post, get=r.get)
    assert all(v == "GET" for v, u, p, b in r.to(LIVE_LOCI))
    assert all(u == TGT_LOCI for v, u, p, b in r.calls if v == "POST")


def test_import_memory_all_tiers():
    r = Recorder()
    c = li.import_memory(LIVE_MEM, TGT_MEM, post=r.post, get=r.get, delete=r.delete)
    assert c == {"short": 1, "near": 1, "long": 1}
    posts = [(p, b) for v, u, p, b in r.calls if v == "POST"]
    assert ("/short", {"content": "ground on embedder", "topic": "migration"}) in posts
    assert ("/near", {"intent": "ask about supersede gap", "topic": "loci",
                      "trigger_type": "always"}) in posts


def test_import_memory_long_promote_dance():
    r = Recorder()
    li.import_memory(LIVE_MEM, TGT_MEM, post=r.post, get=r.get, delete=r.delete)
    paths = [p for v, u, p, b in r.calls if u == TGT_MEM]
    # long tier: written to /short, promoted, short copy dropped
    assert "/short" in paths
    assert any(p.endswith("/promote") for p in paths)
    assert any(v == "DELETE" for v, u, p, b in r.calls if u == TGT_MEM)


def test_memory_read_only_on_live():
    r = Recorder()
    li.import_memory(LIVE_MEM, TGT_MEM, post=r.post, get=r.get, delete=r.delete)
    assert all(v == "GET" for v, u, p, b in r.to(LIVE_MEM))


def test_safety_no_nonget_to_any_live_url():
    """The whole point: nothing but GET is ever sent to a live store."""
    r = Recorder()
    li.import_loci(LIVE_LOCI, TGT_LOCI, post=r.post, get=r.get)
    li.import_memory(LIVE_MEM, TGT_MEM, post=r.post, get=r.get, delete=r.delete)
    live = {LIVE_LOCI, LIVE_MEM}
    assert [c for c in r.calls if c[1] in live and c[0] != "GET"] == []


def test_orchestrator_only_live_nodes():
    class N:
        def __init__(self, name, live_url=None):
            self.name = name; self.live_url = live_url

    class Topo:
        loci = [N("l-live", "http://h:7422"), N("l-synth")]
        memory = [N("m-live", "http://h:7420")]

    url_of = {"l-live": "http://127.0.0.1:1", "l-synth": "http://127.0.0.1:2",
              "m-live": "http://127.0.0.1:3"}
    r = Recorder()
    out = li.import_live_stores(Topo(), url_of, post=r.post, get=r.get, delete=r.delete)
    assert set(out) == {"l-live", "m-live"}          # synth node (no live_url) skipped
    assert out["l-live"]["kind"] == "loci" and out["l-live"]["facts"] == 2
    assert out["m-live"]["kind"] == "memory" and out["m-live"]["short"] == 1
