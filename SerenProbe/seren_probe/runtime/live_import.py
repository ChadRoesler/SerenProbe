"""
seren_probe.live_import
=======================
Copy data from a LIVE Loci/Memory into the throwaway container store, so a
ProbeConfig can validate against real data without ever writing to the real
store. READ-ONLY on the live side - the ONLY verbs sent to a live_url are GETs;
every write lands on the container target. Triggered by a node's LiveStoreUrl
(topology.ResolvedNode.live_url).

A copy keeps the story, not just the facts. A real store's past is what the
supersession and satellite metrics measure, and an import that flattened it
would hand the harness a store that never changed its mind.

Read contract:
  Loci   <- GET /facts?include_superseded=true -> every row, live and retired
  Memory <- GET /short | /near                 -> {entries: [{id, content, metadata}]}
            GET /long?include_superseded=true&include_satellites=true
                                               -> cores, superseded cores, satellites
           (near's `content` IS the intent - SerenMemory stores it as the document)
Write contract mirrors seed_dataset.seed_from_plan:
  Loci   -> the writes that made the live store, replayed in the order they
            happened: POST /fact per value (a key's history rebuilt by supersession),
            POST /fact/move where a value went to a new address, DELETE /fact where
            a key was forgotten; each change with its why_changed when the live
            store kept one. A target with no /fact/move (a Loci from before it)
            takes a move as a new fact and a forget, counted as flattened
  Memory -> POST /short | /near; long-term through approved drafts (the seeder's
            _land_long): each core with its satellites, each supersession as one
"""
from __future__ import annotations

from urllib.parse import urlencode


# ── default httpx transports (lazy import so the pure logic imports clean) ──
def _post(url, path, body, timeout=30.0):
    import httpx
    r = httpx.post(f"{url}{path}", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json() if r.content else {}


def _get(url, path, params=None, timeout=30.0):
    import httpx
    r = httpx.get(f"{url}{path}", params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json() if r.content else {}


def _delete(url, path, timeout=15.0):
    import httpx
    httpx.delete(f"{url}{path}", timeout=timeout)


def _rows(resp):
    return (resp.get("entries", []) or []) if isinstance(resp, dict) else []


def _meta(row):
    return row.get("metadata") or {}


def _topic(row):
    return _meta(row).get("topic")


def _created(row):
    return row.get("created_at") or _meta(row).get("created_at") or 0


# ── Loci ──────────────────────────────────────────────────────────────────

def _addr(row) -> tuple[str, str]:
    return row.get("project", "*"), row["key"]


def _route_missing(err) -> bool:
    """A write refused because the target has no such route at all (a Loci from
    before /fact/move), not because the route refused this one write: a move
    with nothing live to move answers 404 with its own detail, a taken target 409."""
    resp = getattr(err, "response", None)
    status = getattr(resp, "status_code", None)
    if status == 405:
        return True
    if status != 404:
        return False
    try:
        return resp.json().get("detail") == "Not Found"
    except Exception:  # a plain-text 404: nothing at that path answered
        return True


def _forget(target_url, project, key, why_changed, delete) -> None:
    if delete is not None:
        q = {"project": project, "key": key}
        if why_changed is not None:
            q["why_changed"] = why_changed
        delete(target_url, "/fact?" + urlencode(q))


def _replay(facts) -> list[tuple[str, dict, dict | None]]:
    """A Loci's rows as the writes that made them, in the order they happened:
    ("set", row, prev) for a value set at its address (prev: the row it superseded
    there, or None), ("move", row, prev) where prev's live value went to row's
    address, ("forget", row, None) where row was retired and nothing replaced it.

    One clock orders all of it, not one key at a time: a key forgotten before
    another key's value moved into it has to be free when the move lands, or the
    move is refused (and a later set at that key would supersede the moved value)."""
    rows = [f for f in facts if f.get("key")]
    by_id = {r["id"]: r for r in rows if r.get("id")}
    before = {r["superseded_by"]: r for r in rows if r.get("superseded_by") in by_id}
    events = []
    for i, r in enumerate(rows):
        prev = before.get(r.get("id"))
        kind = "set" if prev is None or _addr(prev) == _addr(r) else "move"
        events.append((_created(r), 1, i, kind, r, prev))
        if r.get("superseded_at") is not None and r.get("superseded_by") not in by_id:
            # A forget frees its key, so on a tie it goes first.
            events.append((r["superseded_at"], 0, i, "forget", r, None))
    events.sort(key=lambda e: e[:3])
    return [e[3:] for e in events]


def import_loci_report(live_url, target_url, *, post=_post, get=_get, delete=_delete) -> dict:
    """Copy every fact a live Loci holds, with its past, into the container Loci.
    Read-only on live (one GET); writes only hit target_url.
    Returns {facts: live values, history: values superseded where they stood,
    moved: values moved to a new address, flattened: moves a target without
    /fact/move took as a new fact and a forget, forgotten: keys retired with no
    successor}."""
    data = get(live_url, "/facts", {"include_superseded": True})
    facts = (data or {}).get("facts", []) if isinstance(data, dict) else []
    rep = {"facts": 0, "history": 0, "moved": 0, "flattened": 0, "forgotten": 0}
    moves = True        # until the target turns out to have no /fact/move
    for kind, r, prev in _replay(facts):
        # A retired row's why_changed is why IT stopped being the answer: it rides on
        # the write that retired it.
        why_changed = (prev or {}).get("why_changed") if kind != "forget" else r.get("why_changed")
        project, key = _addr(r)
        if kind == "forget":
            _forget(target_url, project, key, why_changed, delete)
            rep["forgotten"] += 1
            continue
        if kind == "move" and moves:
            body = {"project": prev.get("project", "*"), "key": prev["key"], "to_project": project, "to_key": key}
            if why_changed is not None:
                body["why_changed"] = why_changed
            try:
                post(target_url, "/fact/move", body)
            except Exception as e:
                if not _route_missing(e):
                    raise
                moves = False
            else:
                rep["moved"] += 1
                rep["facts"] += r.get("superseded_at") is None
                continue
        body = {"project": project, "key": key, "value": r.get("value", ""), "why": r.get("why"),
                "source": "import"}
        if kind == "move":
            # A Loci from before /fact/move (a harness image whose pip layer is cached
            # from an older release, say): the value lands at its new address as a new
            # fact and the old address is forgotten with the reason, as imports did
            # before moves. Its history no longer follows it across; the report says so.
            post(target_url, "/fact", body)
            _forget(target_url, *_addr(prev), why_changed, delete)
            rep["flattened"] += 1
        else:
            if why_changed is not None:
                body["why_changed"] = why_changed
            post(target_url, "/fact", body)
            rep["history"] += prev is not None
        rep["facts"] += r.get("superseded_at") is None
    return rep


def import_loci(live_url, target_url, *, post=_post, get=_get, delete=_delete) -> int:
    """import_loci_report, answering only the number of live facts copied."""
    return import_loci_report(live_url, target_url, post=post, get=get, delete=delete)["facts"]


# ── Memory ────────────────────────────────────────────────────────────────

def _long_items(rows) -> tuple[list, int]:
    """Live long-term rows -> the seeder's long items: each core with its satellites
    (oldest first), each superseding core naming the core it replaced, ordered so a
    supersede always follows what it supersedes. Refs are the live ids. Returns
    (items, orphans): a satellite whose core is not in the rows cannot be placed."""
    from ..core.seed_dataset import MemoryItem, SatelliteItem
    cores = sorted((r for r in rows if _meta(r).get("kind") != "satellite" and r.get("id")), key=_created)
    sats = sorted((r for r in rows if _meta(r).get("kind") == "satellite"), key=_created)
    core_ids = {c["id"] for c in cores}
    under: dict[str, list] = {}
    orphans = 0
    for s in sats:
        cid = _meta(s).get("core_id")
        if cid in core_ids:
            under.setdefault(cid, []).append(s)
        else:
            orphans += 1
    replaced = {_meta(c).get("superseded_by"): c["id"] for c in cores
                if _meta(c).get("superseded_by") in core_ids}
    items, placed, waiting = [], set(), list(cores)
    while waiting:
        ready = [c for c in waiting if replaced.get(c["id"]) in (None, *placed)]
        if not ready:                 # a cycle: should not exist; land the rest as plain cores
            ready, replaced = waiting, {}
        for c in ready:
            topic = _topic(c)
            items.append(MemoryItem(
                "long", str(c.get("content", "")), topic=topic, ref=c["id"],
                satellites=[SatelliteItem(str(s.get("content", "")),
                                          topic=(_topic(s) if _topic(s) != topic else None), ref=s.get("id"))
                            for s in under.get(c["id"], [])],
                supersedes=replaced.get(c["id"])))
            placed.add(c["id"])
        waiting = [c for c in waiting if c["id"] not in placed]
    return items, orphans


def import_memory(live_url, target_url, *, post=_post, get=_get, delete=_delete) -> dict:
    """Copy every short, near and long entry from a live Memory into the container
    Memory. Read-only on live (GETs only); writes only hit target_url. Returns
    per-tier counts, plus satellites, superseded cores and orphaned satellites.

    `delete` is accepted and unused since long-term stopped going through /short
    (19 Oct 2026); kept so callers that pass it keep working."""
    counts = {"short": 0, "near": 0, "long": 0, "satellites": 0, "superseded": 0, "orphans": 0}

    for row in _rows(get(live_url, "/short", {"limit": 1_000_000})):
        body = {"content": row.get("content", "")}
        t = _topic(row)
        if t:
            body["topic"] = t
        post(target_url, "/short", body)
        counts["short"] += 1

    for row in _rows(get(live_url, "/near")):
        body = {"intent": row.get("content", "")}   # near's document IS the intent
        m = _meta(row)
        if m.get("topic"):
            body["topic"] = m["topic"]
        for k in ("trigger_type", "trigger_value", "expires_at"):
            if m.get(k) is not None:
                body[k] = m[k]
        post(target_url, "/near", body)
        counts["near"] += 1

    rows = _rows(get(live_url, "/long", {"include_superseded": True, "include_satellites": True}))
    items, counts["orphans"] = _long_items(rows)
    if items:
        from ..core.seed_dataset import _seed_one_store
        _seed_one_store("import", items, target_url, "memory", post)
    counts["long"] = len(items)
    counts["satellites"] = sum(len(it.satellites) for it in items)
    counts["superseded"] = sum(1 for it in items if it.supersedes)
    return counts


def import_live_stores(topology, url_of, *, post=_post, get=_get, delete=_delete) -> dict:
    """For every Loci/Memory node carrying a LiveStoreUrl, copy its live data into
    the container target. Returns {node_name: {kind, source, ...counts}}. Read-only
    on every live source; writes only ever hit url_of[node]."""
    out: dict = {}
    for n in topology.loci:
        if getattr(n, "live_url", None):
            out[n.name] = {"kind": "loci", "source": n.live_url,
                           **import_loci_report(n.live_url, url_of[n.name], post=post, get=get, delete=delete)}
    for n in topology.memory:
        if getattr(n, "live_url", None):
            c = import_memory(n.live_url, url_of[n.name], post=post, get=get, delete=delete)
            out[n.name] = {"kind": "memory", "source": n.live_url, **c}
    return out
