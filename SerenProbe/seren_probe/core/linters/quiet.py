"""
seren_probe.core.linters.quiet
==============================
The LEAK PRECONDITION check. A quiet test says "store X must NOT surface this
answer." It is only meaningful if X does not ALREADY HOLD the answer - ask the
hermit in the next town about a tavern he has never heard of and he should ramble
about his goats, but if you accidentally seeded the tavern brawl INTO the hermit's
file, he will surface it, correctly, and the quiet test will fail. And a failed
quiet test looks EXACTLY like an embedder with no discrimination. You would spend
the afternoon on the embedder. The bug is in the YAML.

This is the mirror of the existence check: that one errors when an expectation is
ABSENT from the store being asked; this one errors when a quiet target's forbidden
answer is PRESENT in its own seed. Same check, opposite polarity, same reason: never
ship a question whose result cannot be trusted.

It lives apart from the other checks because it runs against the PLAN, not a merged
corpus - it needs to know which store each item was seeded INTO. A phrase living in
mem-grishnak is correct; the same phrase living in mem-hermit is the bug.
"""
from __future__ import annotations

from ..seed_dataset import expand_quiet_target
from .model import build_docs, LintReport
from .adapters import _item_to_dict


def lint_quiet_targets(questions, topology, plan, rep: LintReport) -> None:
    """A corpus can be a quiet target too -- it is checked against the union of the
    stores it fans, because that union is exactly what it can retrieve."""
    node_by_name = {n.name: n for n in list(topology.loci) + list(topology.memory)}
    corpus_by_name = {c.name: c for c in topology.corpus}
    all_names = set(node_by_name) | set(corpus_by_name)

    for i, q in enumerate(questions or []):
        patterns = q.get("quiet_in") or []
        if not patterns:
            continue
        query = str(q.get("query", ""))
        label = f"questions[{i}] {query!r}"

        targets: list[str] = []
        for pat in patterns:
            hit = expand_quiet_target(str(pat), all_names)
            if not hit:
                rep.errors.append(
                    f"{label}: quiet_in {pat!r} matches NO declared store. A quiet test aimed "
                    f"at a store that does not exist is never run -- and it reports a perfect "
                    f"quiet_rate for it, which is worse than no test at all. Store names carry "
                    f"their organ ('char_thorn-loci-v', 'char_thorn-mem'); if you meant the "
                    f"whole tenant, glob it: 'char_thorn-*'.")
                continue
            targets.extend(hit)
        targets = sorted(set(targets))

        for tname in targets:
            if tname in corpus_by_name:
                members = [s.name for s in corpus_by_name[tname].stores]
            elif tname in node_by_name:
                members = [tname]
            else:
                continue        # expand_quiet_target only returns declared names

            live = [m for m in members if getattr(node_by_name.get(m), "live_url", None)]
            readable = [m for m in members if m not in live]
            if live:
                rep.notes.append(
                    f"{label}: quiet_in target {tname!r} covers live-import store(s) {sorted(live)}. "
                    f"Their content is copied from a RUNNING store at spin-up, so there is no seed "
                    f"to pre-check -- we cannot know whether the answer is already in there. The "
                    f"quiet test still runs; a failure may mean the live store genuinely holds the "
                    f"fact. Silence about what we cannot see beats a confident wrong answer.")
            if not readable:
                continue

            loci_items, mem_items = [], []
            for m in readable:
                n = node_by_name.get(m)
                if n is None:
                    continue
                items = [_item_to_dict(x) for x in plan.seed_by_store.get(m, [])]
                (loci_items if n.kind == "seren_loci" else mem_items).extend(items)

            docs = build_docs(loci_items, mem_items)
            haystack = "\n".join(d.text for d in docs).lower()
            loci_idents = {d.ident for d in docs if d.kind == "loci"}
            mem_refs = {d.ident for d in docs if d.kind == "memory" and d.ident}

            for c in (q.get("expect_content") or []):
                if str(c).lower() in haystack:
                    rep.quiet_leaks.append((query, tname, str(c)))
                    rep.errors.append(
                        f"{label}: quiet_in {tname!r} must NOT surface {c!r} -- but that phrase is "
                        f"IN ITS OWN SEED. The store is not going to leak: THE DATA ALREADY LEAKED, "
                        f"at authoring time. This will fail for a completely real reason and read on "
                        f"the dashboard as an embedder with no discrimination. Take the fact out of "
                        f"{tname!r}'s seed, or take {tname!r} out of quiet_in.")
            for k_ident in (q.get("expect_key") or []):
                if k_ident in loci_idents:
                    rep.quiet_leaks.append((query, tname, str(k_ident)))
                    rep.errors.append(
                        f"{label}: quiet_in {tname!r} must NOT surface expect_key {k_ident!r} -- but "
                        f"that fact is seeded straight INTO it. A hard leak, authored by hand.")
            for r_ident in (q.get("expect_ref") or []):
                if r_ident in mem_refs:
                    rep.quiet_leaks.append((query, tname, str(r_ident)))
                    rep.errors.append(
                        f"{label}: quiet_in {tname!r} must NOT surface expect_ref {r_ident!r} -- but "
                        f"that memory is seeded straight INTO it. A hard leak, authored by hand.")

    if rep.quiet_leaks:
        rep.notes.append(
            f"{len(rep.quiet_leaks)} QUIET-TEST LEAK(S): a store told to stay quiet about something "
            f"has that something in its own seed. Each one fails for a DATA reason, not a retrieval "
            f"reason -- and it fails looking exactly like a store that cannot discriminate. A quiet "
            f"test you cannot trust is worse than no quiet test at all. Fix the seed.")
