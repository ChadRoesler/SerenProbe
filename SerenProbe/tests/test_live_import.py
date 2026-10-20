"""Tests for seren_probe.live_import - copy live Loci/Memory into the container.

Pure logic (mock transports), so no live services needed. The load-bearing test
is the safety invariant: the ONLY verb ever sent to a live URL is GET.

Since 19 Oct 2026 a copy keeps the story: a Loci key's history (each change with its
reason, a forgotten key forgotten again) and Memory's long-term as cores, satellites
and supersessions through an approved draft. The round trips at the bottom run
against the real SerenLoci / SerenMemory apps in-process when they are importable.
"""
import hashlib

import pytest

from seren_probe.runtime import live_import as li

LIVE_LOCI = "http://192.0.2.101:7422"
LIVE_MEM = "http://192.0.2.101:7420"
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
_LONG_STORY = {"entries": [
    {"id": "c2", "content": "the seat moved to Blackmere", "metadata": {"topic": "seat", "kind": "core", "created_at": 3}},
    {"id": "c1", "content": "the seat is at Grimsgate", "metadata": {"topic": "seat", "kind": "core", "created_at": 1,
                                                                    "superseded_by": "c2"}},
    {"id": "s1", "content": "the fire burned the hall", "metadata": {"topic": "seat", "kind": "satellite",
                                                                     "core_id": "c2", "created_at": 4}},
    {"id": "s9", "content": "a satellite of a purged core", "metadata": {"kind": "satellite", "core_id": "gone"}},
]}
_FACTS_STORY = {"facts": [
    {"id": "f3", "project": "p", "key": "db", "value": "db-03", "why": "now", "created_at": 3,
     "superseded_at": None, "superseded_by": None},
    {"id": "f1", "project": "p", "key": "db", "value": "db-01", "why": "first", "created_at": 1,
     "superseded_at": 2, "superseded_by": "f2", "why_changed": "db-01 lost its disk"},
    {"id": "f2", "project": "p", "key": "db", "value": "db-02", "why": None, "created_at": 2,
     "superseded_at": 3, "superseded_by": "f3"},
    {"id": "g1", "project": "p", "key": "old", "value": "gone", "why": None, "created_at": 1,
     "superseded_at": 5, "superseded_by": None, "why_changed": "nobody uses it"},
]}
# p/host forgotten at 2, then p/addr's value moved into it at 3 and changed there at 4:
# by key the move would land on a live p/host; by the clock p/host is free.
_FACTS_MOVED = {"facts": [
    {"id": "a1", "project": "p", "key": "addr", "value": "10.0.0.5", "why": "the box", "created_at": 1,
     "superseded_at": 3, "superseded_by": "h2", "why_changed": "one name for one box"},
    {"id": "h1", "project": "p", "key": "host", "value": "old-box", "created_at": 1.5,
     "superseded_at": 2, "superseded_by": None},
    {"id": "h2", "project": "p", "key": "host", "value": "10.0.0.5", "why": "the box", "created_at": 3,
     "superseded_at": 4, "superseded_by": "h3"},
    {"id": "h3", "project": "p", "key": "host", "value": "10.0.0.6", "why": "the new box", "created_at": 4,
     "superseded_at": None, "superseded_by": None},
]}


class Recorder:
    """One transport trio that records every call as (verb, url, path, body). The
    target speaks the draft API: a review answers each op with the id it became."""
    def __init__(self, facts=_FACTS, long=_LONG):
        self.calls = []
        self._sid = 0
        self._drafts = {}
        self._data = {"/facts": facts, "/short": _SHORT, "/near": _NEAR, "/long": long}

    def get(self, url, path, params=None):
        self.calls.append(("GET", url, path, params))
        return self._data.get(path, {})

    def post(self, url, path, body):
        self.calls.append(("POST", url, path, body))
        self._sid += 1
        if path == "/drafts":
            self._drafts[f"d{self._sid}"] = body["operations"]
            return {"id": f"d{self._sid}"}
        if path.endswith("/review"):
            ops = self._drafts[path.split("/")[2]]
            return {"results": [{"op": d["op"], ("satellite_id" if ops[d["op"]]["kind"] == "attach"
                                                 else "long_term_id"): f"x{self._sid}-{d['op']}"}
                                for d in body["decisions"]]}
        if path == "/short":
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
    assert (c["short"], c["near"], c["long"]) == (1, 1, 1)
    posts = [(p, b) for v, u, p, b in r.calls if v == "POST"]
    assert ("/short", {"content": "ground on embedder", "topic": "migration"}) in posts
    assert ("/near", {"intent": "ask about supersede gap", "topic": "loci",
                      "trigger_type": "always"}) in posts


def test_import_memory_long_tier_lands_through_a_draft():
    """Not /short -> /promote any more: promote can only make a plain core, so a live
    store's satellites and supersessions arrived flattened."""
    r = Recorder(long=_LONG_STORY)
    c = li.import_memory(LIVE_MEM, TGT_MEM, post=r.post, get=r.get, delete=r.delete)
    assert ("GET", LIVE_MEM, "/long", {"include_superseded": True, "include_satellites": True}) in r.calls
    assert (c["long"], c["satellites"], c["superseded"], c["orphans"]) == (2, 1, 1, 1)
    ops = [op for v, u, p, b in r.calls if p == "/drafts" for op in b["operations"]]
    assert [o["kind"] for o in ops] == ["new_core", "supersede", "attach"]
    assert ops[0]["content"] == "the seat is at Grimsgate"
    assert ops[1]["content"] == "the seat moved to Blackmere"
    assert ops[2]["content"] == "the fire burned the hall"
    paths = [p for v, u, p, b in r.calls if u == TGT_MEM]
    assert not any(p.endswith("/promote") for p in paths)
    assert not any(v == "DELETE" for v, u, p, b in r.calls)


def test_import_loci_rebuilds_each_keys_past_with_its_reasons():
    r = Recorder(facts=_FACTS_STORY)
    rep = li.import_loci_report(LIVE_LOCI, TGT_LOCI, post=r.post, get=r.get, delete=r.delete)
    assert ("GET", LIVE_LOCI, "/facts", {"include_superseded": True}) in r.calls
    assert rep == {"facts": 1, "history": 2, "moved": 0, "flattened": 0, "forgotten": 1}
    writes = [b for v, u, p, b in r.calls if p == "/fact"]
    # the order the live store saw: old was set beside db-01, at t=1
    assert [(w["key"], w["value"]) for w in writes] == [("db", "db-01"), ("old", "gone"), ("db", "db-02"),
                                                        ("db", "db-03")]
    db = [w for w in writes if w["key"] == "db"]
    assert "why_changed" not in db[0] and db[1]["why_changed"] == "db-01 lost its disk"
    assert "why_changed" not in db[2]
    assert [(v, u, p) for v, u, p, b in r.calls if v == "DELETE"] == [
        ("DELETE", TGT_LOCI, "/fact?project=p&key=old&why_changed=nobody+uses+it")]


def test_import_loci_replays_moves_and_forgets_by_the_clock():
    r = Recorder(facts=_FACTS_MOVED)
    rep = li.import_loci_report(LIVE_LOCI, TGT_LOCI, post=r.post, get=r.get, delete=r.delete)
    assert rep == {"facts": 1, "history": 1, "moved": 1, "flattened": 0, "forgotten": 1}
    seen = [(v, p, (b or {}).get("key"), (b or {}).get("value")) for v, u, p, b in r.calls if u == TGT_LOCI]
    assert seen == [("POST", "/fact", "addr", "10.0.0.5"), ("POST", "/fact", "host", "old-box"),
                    ("DELETE", "/fact?project=p&key=host", None, None),
                    ("POST", "/fact/move", "addr", None), ("POST", "/fact", "host", "10.0.0.6")]
    move = next(b for v, u, p, b in r.calls if p == "/fact/move")
    assert move == {"project": "p", "key": "addr", "to_project": "p", "to_key": "host",
                    "why_changed": "one name for one box"}


class OldLoci(Recorder):
    """A target from before /fact/move: the path is not there at all, or the route
    is there and refuses this one move."""
    def __init__(self, status=404, detail="Not Found", **kw):
        super().__init__(**kw)
        self.status, self.detail = status, detail

    def post(self, url, path, body):
        out = super().post(url, path, body)
        if path == "/fact/move":
            import httpx
            raise httpx.HTTPStatusError("refused", request=httpx.Request("POST", url + path),
                                        response=httpx.Response(self.status, json={"detail": self.detail}))
        return out


def test_an_older_target_takes_a_move_as_a_new_fact_and_a_forget_not_a_crash():
    """A harness image's pip layer cached from before Loci 5.2.0 has no /fact/move.
    The import used to die on the first move, half done."""
    r = OldLoci(facts=_FACTS_MOVED)
    rep = li.import_loci_report(LIVE_LOCI, TGT_LOCI, post=r.post, get=r.get, delete=r.delete)
    assert rep == {"facts": 1, "history": 1, "moved": 0, "flattened": 1, "forgotten": 1}
    seen = [(v, p, (b or {}).get("key"), (b or {}).get("value")) for v, u, p, b in r.calls if u == TGT_LOCI]
    assert seen == [("POST", "/fact", "addr", "10.0.0.5"), ("POST", "/fact", "host", "old-box"),
                    ("DELETE", "/fact?project=p&key=host", None, None),
                    ("POST", "/fact/move", "addr", None),
                    ("POST", "/fact", "host", "10.0.0.5"),
                    ("DELETE", "/fact?project=p&key=addr&why_changed=one+name+for+one+box", None, None),
                    ("POST", "/fact", "host", "10.0.0.6")]


@pytest.mark.parametrize("status,detail", [(404, "no live fact to move for project='p' key='addr'"),
                                           (409, "project='p' key='host' already holds a live value")])
def test_a_move_the_target_refuses_is_an_error_not_a_flattening(status, detail):
    """Only a missing route flattens. A move the route itself refuses means the
    replay got the store wrong, and that must not pass quietly."""
    import httpx
    r = OldLoci(status=status, detail=detail, facts=_FACTS_MOVED)
    with pytest.raises(httpx.HTTPStatusError):
        li.import_loci_report(LIVE_LOCI, TGT_LOCI, post=r.post, get=r.get, delete=r.delete)


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


# ── round trips through the real stores, in-process ─────────────────────────

class _Words:
    """Deterministic offline embedder: shared words -> nearby vectors."""
    def __init__(self) -> None:
        pass

    @classmethod
    def name(cls) -> str:
        return "bow-import-test"

    def get_config(self) -> dict:
        return {}

    @classmethod
    def build_from_config(cls, config: dict) -> "_Words":
        return cls()

    def __call__(self, input):
        out = []
        for text in input:
            vec = [0.0] * 64
            for tok in text.lower().split():
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            mag = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / mag for v in vec])
        return out


def _bridge(clients):
    """post/get/delete transports routed to in-process apps by url."""
    def post(url, path, body):
        r = clients[url].post(path, json=body)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        return r.json()

    def get(url, path, params=None):
        r = clients[url].get(path, params=params or {})
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        return r.json()

    def delete(url, path):
        clients[url].delete(path)
    return post, get, delete


def test_round_trip_through_real_loci_keeps_the_past(tmp_path):
    pytest.importorskip("seren_loci")
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    from seren_loci.config import LociConfig, StorageConfig

    def app(name):
        return TestClient(create_app(LociConfig(storage=StorageConfig(db_path=str(tmp_path / f"{name}.db")))))
    with app("live") as live, app("target") as tgt:
        for v in ("db-01", "db-02", "db-03"):
            live.post("/fact", json={"project": "p", "key": "db", "value": v, "why": f"why {v}"})
        live.post("/fact", json={"project": "p", "key": "old", "value": "gone"})
        live.delete("/fact", params={"project": "p", "key": "old", "why_changed": "nobody uses it"})
        moves = "why_changed" in live.post("/fact", json={"project": "q", "key": "addr", "value": "10.0.0.5"}).json()
        if moves:
            live.post("/fact/move", json={"project": "q", "key": "addr", "to_key": "host",
                                          "why_changed": "one name for one box"})
        post, get, delete = _bridge({LIVE_LOCI: live, TGT_LOCI: tgt})
        rep = li.import_loci_report(LIVE_LOCI, TGT_LOCI, post=post, get=get, delete=delete)
        assert rep == {"facts": 2, "history": 2, "moved": int(moves), "flattened": 0, "forgotten": 1}
        hist = tgt.get("/fact/history", params={"project": "p", "key": "db"}).json()["history"]
        assert [h["value"] for h in hist] == ["db-03", "db-02", "db-01"]
        assert tgt.get("/fact", params={"project": "p", "key": "old"}).status_code == 404
        assert tgt.get("/fact/history", params={"project": "p", "key": "old"}).json()["count"] == 1
        if moves:
            # the reasons were kept where the live store kept them, and the move reads back across
            assert hist[1]["why_changed"] is None
            old = tgt.get("/fact/history", params={"project": "p", "key": "old"}).json()["history"]
            assert old[0]["why_changed"] == "nobody uses it"
            assert tgt.get("/fact", params={"project": "q", "key": "host"}).json()["value"] == "10.0.0.5"
            moved = tgt.get("/fact/history", params={"project": "q", "key": "host"}).json()["history"]
            assert [(h["key"], h.get("why_changed")) for h in moved] == [
                ("host", None), ("addr", "one name for one box")]


def test_round_trip_through_real_memory_keeps_cores_satellites_and_supersessions(tmp_path):
    pytest.importorskip("seren_memory")
    from fastapi.testclient import TestClient
    from seren_memory.app import create_app
    from seren_memory.config import ConsolidatorConfig, MemoryConfig, StorageConfig
    from seren_probe.core.seed_dataset import MemoryItem, SatelliteItem, _seed_one_store

    apps = []

    def app(name):
        cfg = MemoryConfig(storage=StorageConfig(persist_dir=str(tmp_path / name)),
                           consolidator=ConsolidatorConfig(enabled=False, pruned_safety_days=1))
        a = create_app(cfg, embedding_function=_Words(), _allow_store_reset=True)
        apps.append(a)
        return TestClient(a)
    try:
        with app("live") as live, app("target") as tgt:
            post, get, delete = _bridge({LIVE_MEM: live, TGT_MEM: tgt})
            _seed_one_store("live", [
                MemoryItem("short", "the comet was seen over the Ashfen", topic="omen"),
                MemoryItem("long", "House Vayne holds its seat at Grimsgate", topic="seat", ref="a"),
                MemoryItem("long", "House Vayne moved its seat to Blackmere after the fire", topic="seat",
                           ref="b", supersedes="a",
                           satellites=[SatelliteItem("the fire burned the great hall to the ground")]),
            ], LIVE_MEM, "memory", post)
            c = li.import_memory(LIVE_MEM, TGT_MEM, post=post, get=get, delete=delete)
            assert (c["short"], c["long"], c["satellites"], c["superseded"], c["orphans"]) == (1, 2, 1, 1, 0)

            def story(client):
                rows = client.get("/long", params={"include_superseded": True,
                                                   "include_satellites": True}).json()["entries"]
                by_id = {r["id"]: r for r in rows}
                out = set()
                for r in rows:
                    m = r["metadata"]
                    core = by_id.get(m.get("core_id"), {}).get("content")
                    nxt = by_id.get(m.get("superseded_by"), {}).get("content")
                    out.add((r["content"], m.get("kind"), core, nxt))
                return out
            assert story(tgt) == story(live)
            assert len(story(tgt)) == 3
    finally:
        for a in apps:
            try:
                a.state.store.close()
            except Exception:  # noqa: BLE001
                pass
