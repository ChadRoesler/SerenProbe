"""
seren_probe.docket - the SCC docket comparison ("with vs without edges").

The topology eval scores docket_coverage / docket_density for every SCC (corpus)
column on its own. What it doesn't do is PAIR those columns and show the delta -
and that delta is the whole question a docket eval exists to answer: when an SCC
fans a *vector* Loci (semantic edges between nodes) instead of a lexical,
FTS5-only one (no edges), does the assembled briefing actually carry MORE of the
relevant info, or not?

"Edges" here = the vector/semantic flavor of the Loci an SCC fans. An SCC over a
[vector] Loci has semantic edges; over a lexical Loci it doesn't. This module
reads each SCC column's docket metrics out of an eval report, labels each by that
flavor (authoritatively off the compiled topology when it's handed one, else
inferred from the store name), and computes the with-edges − without-edges delta
on the docket metrics - plus recall, since "did the briefing have ALL the
relevant info" is really a recall question too.

Pure + transport-free: it consumes an already-computed report dict, so it needs
no live stores and is trivially testable.
"""
from __future__ import annotations

from typing import Any, Optional

# What we pair across flavors: the two docket metrics, plus recall ("was the
# relevant info present at all", which is what 'all the relevant info' means).
_DELTA_KEYS = ("docket_coverage", "docket_density", "recall")

# Everything we carry per column when it's present in the aggregate.
_CARRY_KEYS = ("docket_coverage", "docket_density", "recall",
               "hit_rate", "mrr", "ndcg", "precision", "prec_omega", "iou")

_LABEL = {"vector": "with edges", "lexical": "without edges",
          "mixed": "mixed edges", "none": "no loci", "unknown": "unknown"}

# lexical (no edges) first, then vector (edges): a delta reads "with − without".
_ORDER = {"lexical": 0, "vector": 1, "mixed": 2, "none": 3, "unknown": 4}

deltaCharacter = "\u0394"  # Greek capital letter delta, for the deltas section header.
minusCharacter = "\u2212"  # Unicode minus sign, for the "with − without" text.


def _agg(snap: Any) -> dict:
    """Pull the aggregate metric dict out of a store column, tolerating both the
    topology-eval shape ({aggregate:{...}, per_query:{...}}) and a flat dict."""
    if not isinstance(snap, dict):
        return {}
    inner = snap.get("aggregate")
    return inner if isinstance(inner, dict) else snap


def _is_docket_column(snap: Any) -> bool:
    """A column is an SCC/docket column iff it carries a docket_coverage score -
    only corpus columns get docket metrics, so that's the robust, shape-agnostic
    signal."""
    return "docket_coverage" in _agg(snap)


def _flavor_from_topology(name: str, topology: Any) -> Optional[str]:
    """'vector' | 'lexical' | 'mixed' | 'none' read off the compiled topology, or
    None if this name isn't a corpus in it. An SCC's flavor IS its backing Loci's
    vector-ness - that's precisely what 'edges' means here."""
    corpus = next((c for c in getattr(topology, "corpus", []) if c.name == name), None)
    if corpus is None:
        return None
    loci_by_name = {n.name: n for n in getattr(topology, "loci", [])}
    flavors: set[str] = set()
    for s in corpus.stores:
        if getattr(s, "kind", "") == "seren_loci":
            n = loci_by_name.get(s.name)
            if n is not None:
                flavors.add("vector" if "vector" in n.flags else "lexical")
    if flavors == {"vector"}:
        return "vector"
    if flavors == {"lexical"}:
        return "lexical"
    if not flavors:
        return "none"
    return "mixed"


def _flavor_from_name(name: str) -> str:
    """Best-effort flavor when there's no topology. Order matters: 'no_vector'
    contains the substring 'vector', so rule the negatives out FIRST."""
    lo = name.lower()
    if ("no_vector" in lo or "no-vector" in lo or "novector" in lo
            or lo.endswith("nv") or lo.endswith("-nv") or lo.endswith("_nv")):
        return "lexical"
    if "vector" in lo or lo.endswith("-v") or lo.endswith("_v"):
        return "vector"
    return "unknown"


def docket_comparison(report: dict, topology: Any = None) -> dict:
    """Pull the SCC columns out of an eval `report` and build the docket
    with-vs-without-edges comparison.

    `report` is a run_topology_evaluation / run_live_evaluation result (needs
    report["stores"]). `topology` is the CompiledTopology it ran against - pass it
    and flavor is read authoritatively off each SCC's backing Loci; omit it and
    flavor is inferred from the store name (surfaced as flavor_source so nobody's
    fooled about which happened).

    Returns {columns, deltas, k, question_count, flavor_source, note?}. Never
    raises on a report that has no SCC columns - returns empty columns + a note.
    """
    stores = report.get("stores", {}) if isinstance(report, dict) else {}
    used_topology = False
    columns: list[dict] = []
    for name, snap in stores.items():
        if not _is_docket_column(snap):
            continue
        agg = _agg(snap)
        flavor = _flavor_from_topology(name, topology) if topology is not None else None
        if flavor is not None:
            used_topology = True
        else:
            flavor = _flavor_from_name(name)
        col: dict = {"name": name, "flavor": flavor, "label": _LABEL.get(flavor, flavor)}
        for key in _CARRY_KEYS:
            if key in agg:
                col[key] = agg[key]
        columns.append(col)

    columns.sort(key=lambda c: (_ORDER.get(c["flavor"], 9), c["name"]))

    # deltas: every vector (with-edges) column vs every lexical (without-edges) one.
    withs = [c for c in columns if c["flavor"] == "vector"]
    withouts = [c for c in columns if c["flavor"] == "lexical"]
    deltas: list[dict] = []
    for w in withs:
        for wo in withouts:
            d: dict = {"with_edges": w["name"], "without_edges": wo["name"]}
            for key in _DELTA_KEYS:
                if key in w and key in wo:
                    d[key] = round(w[key] - wo[key], 4)
            deltas.append(d)

    out: dict = {
        "columns": columns,
        "deltas": deltas,
        "k": report.get("k") if isinstance(report, dict) else None,
        "question_count": report.get("question_count") if isinstance(report, dict) else None,
        "flavor_source": "topology" if used_topology else "name-heuristic",
    }
    if isinstance(report, dict) and report.get("corpus_size") is not None:
        out["corpus_size"] = report["corpus_size"]
    if not columns:
        out["note"] = "no SCC/docket columns in this report - nothing to compare."
    elif not deltas:
        got = ", ".join(f"{c['name']}({c['flavor']})" for c in columns)
        out["note"] = ("need one vector (with-edges) and one lexical (without-edges) SCC to "
                       f"compute a delta; got {got}.")
    return out


def format_docket_comparison(cmp: dict) -> str:
    """Render the comparison as the readable side-by-side block - the shape you'd
    paste into a ledger. Coverage / density / recall per SCC, then the edges
    delta and a plain-language verdict on which way the edges moved coverage."""
    lines: list[str] = []
    bits = []
    if cmp.get("k") is not None:
        bits.append(f"k={cmp['k']}")
    if cmp.get("question_count") is not None:
        bits.append(f"{cmp['question_count']} questions")
    if cmp.get("corpus_size") is not None:
        bits.append(f"corpus_size={cmp['corpus_size']}")
    suffix = f"   ({', '.join(bits)})" if bits else ""
    lines.append(f"SCC Docket - with vs without edges{suffix}")
    lines.append("")

    cols = cmp.get("columns") or []
    if not cols:
        lines.append("  (no SCC columns in this report)")
        return "\n".join(lines)

    def g(c, key):
        return f"{c[key]:.4f}" if key in c else "  -   "
    namew = max(len(c["name"]) for c in cols)
    labelw = max(len(c["label"]) for c in cols)
    for c in cols:
        lines.append(f"  {c['label']:<{labelw}}  {c['name']:<{namew}}   "
                     f"coverage {g(c, 'docket_coverage')}   density {g(c, 'docket_density')}   "
                     f"recall {g(c, 'recall')}")

    for d in cmp.get("deltas", []):
        lines.append("  " + "\u2500" * (labelw + namew + 50))

        def sg(v):
            return f"{v:+.4f}" if v is not None else "  -   "
        cov, den, rec = d.get("docket_coverage"), d.get("docket_density"), d.get("recall")
        lines.append(f"  {deltaCharacter} edges (with {minusCharacter} without):<{labelw + namew + 2} "
                     f"coverage {sg(cov)}   density {sg(den)}   recall {sg(rec)}")
        if cov is not None:
            if cov > 0.0005:
                verdict = f"edges ADDED {cov:+.4f} docket coverage"
            elif cov < -0.0005:
                verdict = f"edges COST {cov:+.4f} docket coverage"
            else:
                verdict = "edges made no meaningful coverage difference"
            lines.append(f"  \u2192 {verdict}  ({d['with_edges']} vs {d['without_edges']})")

    if cmp.get("note"):
        lines.append("")
        lines.append(f"  note: {cmp['note']}")
    return "\n".join(lines)
