"""
seren_probe.core.linters.plan
=============================
The two public entry points and the orchestration that drives the checks.

    lint_questions(questions, loci_items, memory_items, ...)   <- flat-dataset path
                                                                  (the CLI gate)
    lint_plan(topology, plan)                                  <- topology path
                                                                  (the /probeconfig validate route)

`lint_questions` is the whole reason this refactor happened: it used to be a
200-line loop with six checks welded inline and two nested closures. Now it builds
the corpus index once and calls the extracted checks in FIRING ORDER - cheap
existence first, then reachability, then discriminability, then the content/rail
walk. The bait check short-circuits (a no-answer question has nothing else to
check).

`lint_plan` is the per-scope orchestrator: it lints each store against the corpus
that store can actually SEE (its own seed; a corpus against the union of what it
fans), drops the checks a live-import store can't verify, skips decoys, dedups
findings across scopes, and runs the quiet-leak precondition against the plan.
"""
from __future__ import annotations

from .text import _QIDX, _words
from .model import LintReport, build_corpus, max_reachable_hops
from .checks import (
    check_bait, check_existence, check_reachability,
    check_discriminability, check_content_rail,
)
from .adapters import _item_to_dict, _question_to_dict
from .quiet import lint_quiet_targets


def lint_questions(questions, loci_items, memory_items, checks=None, k: int = 10,
                   max_hops: int = 1) -> LintReport:
    """Check every expectation against the corpus it will be graded on.

    `checks` limits WHICH expectation kinds are verifiable - a subset of
    {"key", "ref", "content"} (default: all three). A live-imported store has no
    seed to read, so its expectations are UNVERIFIABLE, not wrong; the caller drops
    the corresponding check rather than emitting a false "unanswerable". Silence
    about what we can't see beats a confident wrong answer.

    `k` is the depth these questions will be SCORED at (hit_rate@k). The
    discriminability check needs it: an expectation with MORE THAN k equally-good
    rivals cannot reliably land in the top k, no matter how good the store is.
    """
    checks = {"key", "ref", "content"} if checks is None else set(checks)
    corpus = build_corpus(loci_items, memory_items)

    rep = LintReport()
    for i, q in enumerate(questions or []):
        q = dict(q)
        query = str(q.get("query", ""))
        asks = q.get("asks")
        label = f"questions[{i}] ({q.get('asks')}) {query!r}"
        rep.checked += 1

        # A bait names something that DOESN'T exist; there is nothing else to check.
        if q.get("expect_empty"):
            check_bait(q, query, corpus, rep, label)
            continue

        # Firing order: cheap existence first, then reachability, then the two O(corpus)
        # checks. `qw` is computed once here and shared -- existence never needs it.
        check_existence(q, label, corpus, rep, checks)
        qw = _words(query)
        check_reachability(q, query, asks, label, rep, max_hops)
        check_discriminability(q, query, qw, label, corpus, rep, k, checks)
        check_content_rail(q, query, qw, label, corpus, rep, checks)

    if rep.multihop:
        rep.notes.append(
            f"{len(rep.multihop)} RAILED multi-hop expectation(s): reachable only in 2+ passes, "
            f"but a bridge doc exists so a hop CAN get there. These are the fair test of hops:2. "
            f"Reshaping knobs (rrf_k/floor/weight) stay inert on them - correct, not a bug.")
    if rep.unbridged:
        rep.notes.append(
            f"{len(rep.unbridged)} UNBRIDGED expectation(s): no lexical bridge AND no rail. "
            f"These are unanswerable by retrieval at any hop depth - a dataset defect. They will "
            f"drag coverage down and NOTHING (no knob, no hop) will move them. Fix the data.")
    if rep.ambiguous:
        rep.notes.append(
            f"{len(rep.ambiguous)} AMBIGUOUS expectation(s): the answer EXISTS and IS reachable, "
            f"but the query cannot single it out - more than k={k} other documents match the query "
            f"just as well. The store will retrieve correctly and still score near zero, which on "
            f"a dashboard is indistinguishable from a dead store, a broken embedder, and a missing "
            f"hop. Do not tune anything. Fix the question.")
    if rep.unreachable:
        deepest = max(n for _q, n, _m in rep.unreachable)
        rep.notes.append(
            f"{len(rep.unreachable)} UNREACHABLE question(s): they DECLARE they need up to "
            f"hops={deepest}, but no configuration this ProbeConfig can run goes deeper than "
            f"hops={max_hops}. Every combo in the sweep will ask them at a depth they cannot be "
            f"answered from, they will score ZERO in every row, and flat rows read as a retrieval "
            f"CEILING. The knob is not inert -- it was never turned far enough. Sweep hops up to "
            f"{deepest} in a CorpusRegrades set, and confirm the SCC advertises it in GET /stores.")
    return rep


def lint_plan(topology, plan) -> LintReport:
    """Lint a resolved plan against its compiled topology.

    Two classes of node are EXCLUDED from the corpus we lint against:

    - live-import nodes (LiveStoreUrl): their content is copied from a running
      store at spin-up, so there is no seed file to check reachability against.
      We can't lint what we can't read - so we say so, and DROP the checks that
      store would have covered, instead of emitting a wall of false errors.
    - negative-test decoys: a decoy's content must NOT count as answering a
      question. Linting against it would legitimize a leak.
    """
    live_nodes, decoys = [], []
    for n in list(topology.loci) + list(topology.memory):
        if getattr(n, "live_url", None):
            live_nodes.append(n.name)
        elif getattr(n, "negative_test", False):
            decoys.append(n.name)

    # ---- PER-SCOPE LINT --------------------------------------------------------------
    # This used to merge EVERY store's seed into ONE haystack and lint EVERY question
    # against it. On a single-dataset topology that IS the corpus, so it was right. On a
    # store-per-tenant world it is wrong in BOTH directions at once:
    #
    #   TOO STRICT   Zara's "what is her flaw?" gets linted against sixteen characters'
    #                flaws. Fifteen rivals -> AMBIGUOUS -> ERROR -> the lint refuses to
    #                start a topology that is perfectly fine, because char_zara-loci holds
    #                ONLY Zara, has exactly one flaw in it, and returns it at rank 1 every
    #                single time.
    #
    #   TOO LENIENT  A Zara question whose expect_content only exists in THORN's seed sails
    #                through the reachability check -- the phrase IS in the merged haystack.
    #                It just isn't in the store being asked. The mycelium catch, defeated by
    #                owning more than one store.
    #
    # So each SCOPE is linted against the corpus that scope can actually SEE:
    #   Loci / Memory node -> its OWN questions against its OWN seed
    #   Corpus             -> its questions against the UNION OF THE STORES IT FANS
    #
    # DISCRIMINABILITY IS A PROPERTY OF THE FAN, NOT OF THE DATASET. The same question is
    # green in char_zara-loci and red in cross-everything, and BOTH verdicts are correct.
    node_by = {n.name: n for n in list(topology.loci) + list(topology.memory)}
    loci_names = {n.name for n in topology.loci}
    qbs = getattr(plan, "questions_by_store", None) or {}
    max_hops = max_reachable_hops(topology)

    scopes: list = [(n.name, [n.name]) for n in node_by.values()]
    scopes += [(c.name, [s.name for s in c.stores]) for c in topology.corpus]

    rep = LintReport()
    dropped_any: set = set()
    raised: dict = {}          # (bucket, message) -> [scopes that raised it]

    for scope, members in scopes:
        # A DECOY IS NOT A SCOPE. Its content is excluded from every corpus on purpose, so
        # linting it against itself hands you an empty haystack and an "unanswerable" error
        # for every question -- a wall of red describing a store behaving exactly as designed.
        if getattr(node_by.get(scope), "negative_test", False):
            continue
        qs = [_question_to_dict(q) for q in (qbs.get(scope) or [])]
        if not qs:
            continue

        li, mi, live_here = [], [], []
        for m in members:
            n = node_by.get(m)
            if n is None:
                continue
            if getattr(n, "live_url", None):
                live_here.append(m)
                continue
            if getattr(n, "negative_test", False):
                continue        # a decoy's content must never count as answering a question
            items = [_item_to_dict(i) for i in (plan.seed_by_store.get(m) or [])]
            (li if m in loci_names else mi).extend(items)

        # Drop only the checks THIS scope cannot verify. A live member elsewhere in the
        # topology says nothing about what this scope can see.
        checks_here = {"key", "ref", "content"}
        if any(m in loci_names for m in live_here):
            checks_here -= {"key", "content"}
        if any(m not in loci_names for m in live_here):
            checks_here -= {"ref", "content"}
        dropped_any |= {"key", "ref", "content"} - checks_here

        sub = lint_questions(qs, li, mi, checks=checks_here, max_hops=max_hops)
        rep.checked += sub.checked
        rep.multihop += sub.multihop
        rep.unbridged += sub.unbridged
        rep.ambiguous += sub.ambiguous
        rep.unreachable += sub.unreachable
        for bucket, msgs in (("e", sub.errors), ("w", sub.warnings), ("n", sub.notes)):
            for msg in msgs:
                key = (bucket, _QIDX.sub("questions[*]", msg))
                if key not in raised:
                    raised[key] = [msg, []]
                raised[key][1].append(scope)

    # ONE message, ALL the scopes that raised it. char_zara-loci-v and char_zara-loci-nv
    # hold the same seed and answer the same questions, so every finding lands at least
    # twice -- and a scope-per-tenant topology multiplies that by the number of tenants. A
    # lint that says everything forty times is a lint nobody reads.
    for (bucket, _key), (msg, where) in raised.items():
        shown = ", ".join(where[:4]) + (f" +{len(where) - 4} more" if len(where) > 4 else "")
        line = f"[{shown}] {msg}"
        target = rep.errors if bucket == "e" else rep.warnings if bucket == "w" else rep.notes
        target.append(line)

    checks = {"key", "ref", "content"} - dropped_any
    questions = [_question_to_dict(q) for q in (plan.questions or [])]

    if live_nodes:
        dropped = sorted({"key", "ref", "content"} - checks)
        rep.notes.append(
            f"live-import node(s) {sorted(set(live_nodes))} were SKIPPED by the lint: their "
            f"content is copied from a running store at spin-up, so there's no seed to check "
            f"reachability against. Checks dropped as UNVERIFIABLE: {dropped or 'none'}. "
            f"That's inherent to LiveStoreUrl - silence about what we can't see beats a "
            f"confident wrong answer.")
    # The quiet-leak check runs on the PLAN, not the merged corpus: it needs to know which
    # store each item was seeded INTO, and lint_questions only ever sees the union. A phrase
    # living in mem-grishnak is correct; the same phrase living in mem-hermit is the bug.
    lint_quiet_targets(questions, topology, plan, rep)

    if decoys:
        rep.notes.append(
            f"decoy store(s) {sorted(set(decoys))} were excluded from the lint corpus on "
            f"purpose - a decoy's content must never count as answering a question.")
    return rep
