"""
seren_probe.core.edges - what a Callosum packet IS, for every grader.

Memory's long-term is a core and its surroundings. Recall returns CORES. A
satellite - a supporting episode - never comes back as a hit of its own: a core
reached through one carries it as `matched_via` (top level on a Memory hit, in
metadata on a Callosum hit), and the Callosum hangs a core's newest satellites,
the core it superseded and topic neighbours AFTER the ranked packet as MARKED
edges (metadata.source, score 0.0, edge_of naming the core).

So: an edge is never a rank. @k metrics read the ranked packet; an edge that
carries the answer credits the core it hangs off, which IS in the packet. Content
is matched against a hit's text and the satellite it came through, since both are
in front of whoever reads the packet.

One module, because there are three graders (the topology eval, the live knob
sweep, the capture replay) and they disagreed: the eval learned edges on 19 Oct
2026 while the sweep went on ranking every edge as a hit. Pure: dicts in, numbers
out.
"""
from __future__ import annotations

from .metrics import normalize_text

EDGE_SOURCES = ("satellite-edge", "supersedes-edge", "topic-edge")


def edge_family(h: dict):
    """The edge family a hit belongs to, or None for a ranked row."""
    src = (h.get("metadata") or {}).get("source")
    return src if src in EDGE_SOURCES else None


def is_edge(h: dict) -> bool:
    return edge_family(h) is not None


def via(h: dict) -> dict:
    """The satellite a core was reached through, if any."""
    v = h.get("matched_via") or (h.get("metadata") or {}).get("matched_via")
    return v if isinstance(v, dict) else {}


def loci_haystack(h: dict) -> str:
    return " ".join([str(h.get("value", "")), str(h.get("why", "") or ""), str(h.get("key", ""))])


def hay(h: dict, kind: str = "corpus") -> str:
    """The text a hit puts in front of the reader: a fact's key/value/why, or a
    memory's content plus the satellite it was reached through."""
    if kind == "seren_loci":
        return loci_haystack(h)
    return " ".join([str(h.get("content", "") or ""), str(via(h).get("content", "") or "")])


def split_edges(hits) -> tuple[list, list]:
    """(ranked packet, marked edges), each in the order returned."""
    packet = [h for h in hits if not is_edge(h)]
    return packet, [h for h in hits if is_edge(h)]


def credit(h: dict):
    """The packet row an answer in this hit counts for: itself, or the core an edge
    hangs off (None for a topic-edge, which hangs off no core)."""
    return (h.get("metadata") or {}).get("edge_of") if is_edge(h) else h.get("id")


def surroundings(hits, expect_content, k: int, kind: str = "corpus") -> dict:
    """What the marked edges bought one question, and what they cost.

    coverage_packet  share of expected phrases the RANKED packet carries
    coverage_with    {family: the same with that family's edges folded in}
    density_with     {family: relevant rows / rows, over the top-k packet plus the
                     family's edges} (the packet alone is docket_density)
    rows, chars      {packet | family: how many rows, how much text} - the reading
                     cost, so a budget sweep shows tokens as well as coverage
    Coverage and density are present only when the question names content."""
    packet, edges = split_edges(hits)
    exp = [n for n in (normalize_text(e) for e in (expect_content or [])) if n]

    def has(h):
        t = normalize_text(hay(h, kind))
        return [i for i, e in enumerate(exp) if e in t]

    fams = {f: [h for h in edges if edge_family(h) == f] for f in EDGE_SOURCES}
    out: dict = {"rows": {"packet": len(packet)}, "chars": {"packet": sum(len(hay(h, kind)) for h in packet)}}
    for f, rows in fams.items():
        out["rows"][f] = len(rows)
        out["chars"][f] = sum(len(hay(h, kind)) for h in rows)
    if exp:
        in_packet = {i for h in packet for i in has(h)}
        top = packet[:k]
        rel_top = sum(1 for h in top if has(h))
        out["coverage_packet"] = len(in_packet) / len(exp)
        out["coverage_with"], out["density_with"] = {}, {}
        for f, rows in fams.items():
            found = in_packet | {i for h in rows for i in has(h)}
            out["coverage_with"][f] = len(found) / len(exp)
            n = len(top) + len(rows)
            out["density_with"][f] = (rel_top + sum(1 for h in rows if has(h))) / n if n else 0.0
    return out


def mean_surroundings(per_q: list[dict]) -> dict:
    """Average a column's per-question surroundings. Sizes average over every
    question; coverage and density over the questions that name content."""
    if not per_q:
        return {}

    def avg(vals):
        vals = list(vals)
        return sum(vals) / len(vals) if vals else 0.0
    out: dict = {"questions": len(per_q),
                 "rows": {f: avg(q["rows"][f] for q in per_q) for f in per_q[0]["rows"]},
                 "chars": {f: avg(q["chars"][f] for q in per_q) for f in per_q[0]["chars"]}}
    graded = [q for q in per_q if "coverage_packet" in q]
    if graded:
        out["graded"] = len(graded)
        out["coverage_packet"] = avg(q["coverage_packet"] for q in graded)
        out["coverage_with"] = {f: avg(q["coverage_with"][f] for q in graded) for f in EDGE_SOURCES}
        out["density_with"] = {f: avg(q["density_with"][f] for q in graded) for f in EDGE_SOURCES}
    return out
