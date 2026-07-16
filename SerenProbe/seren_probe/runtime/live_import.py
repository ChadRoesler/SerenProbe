"""
seren_probe.live_import
=======================
Copy data from a LIVE Loci/Memory into the throwaway container store, so a
ProbeConfig can validate against real data without ever writing to the real
store. READ-ONLY on the live side - the ONLY verbs sent to a live_url are GETs;
every write lands on the container target. Triggered by a node's LiveStoreUrl
(topology.ResolvedNode.live_url).

Write contract mirrors seed_dataset.seed_from_plan exactly:
  Loci   -> POST /fact {project, key, value, why}
  Memory -> POST /short | /near ; long tier via short->promote->drop-copy
Read contract:
  Loci   <- GET /facts             -> {facts:   [{project, key, value, why}]}
  Memory <- GET /short|/near|/long -> {entries: [{id, content, metadata:{topic,...}}]}
           (near's `content` IS the intent - SerenMemory stores it as the document)
"""
from __future__ import annotations


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


def _topic(row):
    return (row.get("metadata") or {}).get("topic")


def import_loci(live_url, target_url, *, post=_post, get=_get) -> int:
    """Copy every LIVE fact from a live Loci into the container Loci. Read-only on
    live (one GET /facts); writes only hit target_url. Returns facts copied."""
    data = get(live_url, "/facts")
    facts = (data or {}).get("facts", []) if isinstance(data, dict) else []
    n = 0
    for f in facts:
        if not f.get("key"):
            continue
        post(target_url, "/fact", {
            "project": f.get("project", "*"), "key": f.get("key"),
            "value": f.get("value", ""), "why": f.get("why"), "source": "import"})
        n += 1
    return n


def import_memory(live_url, target_url, *, post=_post, get=_get, delete=_delete) -> dict:
    """Copy every LIVE short/near/long entry into the container Memory. Read-only
    on live (GETs only); writes only hit target_url. Long tier is gated, so it
    goes short->promote->drop-copy (mirrors seed_from_plan). Returns per-tier counts."""
    counts = {"short": 0, "near": 0, "long": 0}

    for row in _rows(get(live_url, "/short", {"limit": 1_000_000})):
        body = {"content": row.get("content", "")}
        t = _topic(row)
        if t:
            body["topic"] = t
        post(target_url, "/short", body)
        counts["short"] += 1

    for row in _rows(get(live_url, "/near")):
        body = {"intent": row.get("content", "")}   # near's document IS the intent
        m = row.get("metadata") or {}
        if m.get("topic"):
            body["topic"] = m["topic"]
        for k in ("trigger_type", "trigger_value", "expires_at"):
            if m.get(k) is not None:
                body[k] = m[k]
        post(target_url, "/near", body)
        counts["near"] += 1

    for row in _rows(get(live_url, "/long")):
        body = {"content": row.get("content", "")}
        t = _topic(row)
        if t:
            body["topic"] = t
        resp = post(target_url, "/short", body)
        sid = (resp or {}).get("id", "")
        if sid:
            post(target_url, f"/short/{sid}/promote", {})
            if delete is not None:
                delete(target_url, f"/short/{sid}")
        counts["long"] += 1

    return counts


def import_live_stores(topology, url_of, *, post=_post, get=_get, delete=_delete) -> dict:
    """For every Loci/Memory node carrying a LiveStoreUrl, copy its live data into
    the container target. Returns {node_name: {kind, source, ...counts}}. Read-only
    on every live source; writes only ever hit url_of[node]."""
    out: dict = {}
    for n in topology.loci:
        if getattr(n, "live_url", None):
            out[n.name] = {"kind": "loci", "source": n.live_url,
                           "facts": import_loci(n.live_url, url_of[n.name], post=post, get=get)}
    for n in topology.memory:
        if getattr(n, "live_url", None):
            c = import_memory(n.live_url, url_of[n.name], post=post, get=get, delete=delete)
            out[n.name] = {"kind": "memory", "source": n.live_url, **c}
    return out
