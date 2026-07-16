"""
seren_probe.core.linters.checks
===============================
The per-question reachability checks - each one a bug that cost real hours, now a
named function instead of a numbered stanza welded into a 200-line loop.

They used to be "Tier 1/2/3/4/5", and the numbers were archaeological (the order
they were *discovered*, across the mycelium night and the eleven-hour
deff-dread-halt day) while the code ran them in a different, operational order.
Two orderings that never agreed is a tax you paid every time you opened the file.
So: names, and `plan.lint_questions` calls them in firing order.

Each check reads the precomputed `Corpus` index and appends to a `LintReport`.
None of them return anything - the report is the accumulator.

WHY THIS LAYER EXISTS AT ALL (the mycelium catch): a model-authored dataset emitted
`expect_content: ["strain history"]` for a phrase in NONE of its own 1581 facts.
That question can never be answered, at any setting - and on the dashboard it looks
exactly like a retrieval failure, which is ALSO the signature of a weak embedder AND
of a missing hop. Three different bugs, one graph. Never ship a question the corpus
can't answer.
"""
from __future__ import annotations

from .text import _words


def check_bait(q, query: str, corpus, rep, label: str) -> None:
    """A bait (`expect_empty`) works because it names something that DOESN'T EXIST
    (a phantom strain, a fictional person). So the test is document frequency, not
    word overlap: if EVERY word in the query appears somewhere in the corpus, the
    query names nothing novel and the bait may actually be answerable. Matching on
    generic words ("host", "strain") tells you nothing - those appear in every doc.
    """
    qw = _words(query)
    if qw and all(w in corpus.vocab for w in qw):
        rep.warnings.append(
            f"{label}: expect_empty, but EVERY word in this query appears somewhere "
            f"in the corpus - it may not name anything phantom. A bait should contain "
            f"a term the corpus has never heard of.")


def check_existence(q, label: str, corpus, rep, checks: set) -> None:
    """EXISTENCE. Is the expectation in the corpus at all?

    Loop vars are k_ident/r_ident, NOT k/r -- the eval depth `k` is an int, and a
    `for k in ...` would shadow it with a string ident, then the discriminability
    check trips on `n > k` with k a string, TypeError all the way up. Latent until
    something downstream in the same scope actually READ k as an integer.
    """
    for k_ident in (q.get("expect_key") or []) if "key" in checks else []:
        if k_ident not in corpus.loci_idents:
            rep.errors.append(
                f"{label}: expect_key {k_ident!r} is NOT in the seed - unanswerable by any store.")
    for r_ident in (q.get("expect_ref") or []) if "ref" in checks else []:
        if r_ident not in corpus.mem_refs:
            rep.errors.append(
                f"{label}: expect_ref {r_ident!r} is NOT in the seed - unanswerable by any store.")


def check_reachability(q, query: str, asks, label: str, rep, max_hops: int) -> None:
    """REACHABILITY. Can any config you can RUN answer this at all?

    A question may declare `hops: N` -- "answering me needs walking N edges." It is a
    CLAIM about the question, not a setting: the SCC's hop depth is a /configure knob
    that applies to every query in a run, and SerenProbe only ever moves it through a
    CorpusRegrades set. So the deepest depth you can reach is max_hops, and a question
    needing more than that is unanswerable under EVERY combo the sweep will try.

    This is the one that started the whole hunt. orkrail's chain questions --
    srv-000 -> loco -> depot -> supplier -> part -- honestly declared hops: 4. Nothing
    read the field. The SCC ran at hops=1, the hop-sweep swept 1/2/3, every chain
    scored zero, and the flat rows read as a RETRIEVAL CEILING. The hop was never
    inert. We just never turned it far enough. A knob swept in one direction is half a
    knob.
    """
    needs = int(q.get("hops", 1) or 1)
    if needs > 1 and asks != "corpus":
        rep.warnings.append(
            f"{label}: declares hops={needs}, but hops only mean something to a CORPUS "
            f"(the SCC is what traverses). A Loci or Memory store does ONE retrieval pass "
            f"-- there is nothing for it to hop across. The declaration is ignored here.")
    elif needs > max_hops:
        rep.unreachable.append((query, needs, max_hops))
        rep.errors.append(
            f"{label}: declares hops={needs}, but NO configuration you can run goes deeper "
            f"than hops={max_hops}. The SCC's hop depth is a /configure knob and SerenProbe "
            f"only moves it via a CorpusRegrades set -- so every combo in the sweep asks "
            f"this question at a depth it cannot be answered from. It will score ZERO in "
            f"every row, and flat rows read as a retrieval CEILING. Sweep hops up to "
            f"{needs} in a CorpusRegrades set (and check the SCC advertises it in "
            f"GET /stores), or shorten the question's chain.")


def check_discriminability(q, query: str, qw: set, label: str, corpus, rep, k: int, checks: set) -> None:
    """DISCRIMINABILITY. Does the query IDENTIFY its own answer?

    Existence asks "is the expectation in the corpus?". The rail check asks "is there a
    lexical path to it?". Neither asks the question that cost a whole day: CAN THIS
    QUERY SINGLE IT OUT?

      query   : "wot happened at deff-dread-halt an promethium-pit?"
      expects : evt-000 = "bridge fell down at deff-dread-halt on Night"
      the corpus also holds ~50 OTHER events at deff-dread-halt.

    The expectation EXISTS. There IS a lexical bridge (the station name). And the
    question is still unanswerable, because nothing in the query prefers "bridge fell
    down" over "promethium leaked" -- the query names the STATION and the answer key
    names ONE EVENT out of dozens at it. The store retrieved perfectly (rank 1, score
    0.649) and scored hit_rate 0.083, and that 0.083 is CORRECT. It was asked to read
    minds. On the dashboard that is indistinguishable from a dead store, a broken
    embedder, and a missing hop. We chased all three. For eleven hours.

    The rule: a doc is a RIVAL if it shares AT LEAST AS MANY query terms as the
    expected doc. More than k rivals -> the expected doc cannot reliably reach the top
    k -> hit_rate@k is capped below 1 no matter how good retrieval is. A DATASET
    DEFECT, not a score.
    """
    def _discriminability(ident: str, kind: str):
        """(query-term overlap of the expected doc, list of rival idents)."""
        hit = corpus.by_ident.get(ident)
        if not hit or not qw:
            return None, []
        _doc, target_words = hit
        target_ov = len(qw & target_words)
        expected_here = {str(x) for x in (q.get("expect_ref") or [])} | \
                        {str(x) for x in (q.get("expect_key") or [])}
        rivals = [d.ident for d, dw in corpus.doc_words
                  if d.kind == kind and d.ident not in expected_here
                  and len(qw & dw) >= target_ov]
        return target_ov, rivals

    def _flag_ambiguous(ident: str, kind: str, what: str):
        ov, rivals = _discriminability(ident, kind)
        if ov is None:
            return
        n = len(rivals)
        if n > k:
            rep.ambiguous.append((query, ident, what, n))
            rep.errors.append(
                f"{label}: {what} {ident!r} is AMBIGUOUS - {n} other seed documents match "
                f"this query at least as well (query-term overlap {ov}). The expectation "
                f"exists and is reachable, but the query cannot SINGLE IT OUT: it names a "
                f"category, and the answer key names one member of it. hit_rate@{k} is "
                f"capped below 1 no matter how good the store is. This scores exactly like "
                f"a dead store - it is a dataset defect. Add a term that only the intended "
                f"document carries.")
        elif n >= max(3, k // 2):
            rep.warnings.append(
                f"{label}: {what} {ident!r} is CROWDED - {n} other documents tie it on "
                f"query-term overlap ({ov}). Still reachable within k={k}, but the ranking "
                f"is a coin-flip between them and the score will look noisy.")

    for k_ident in (q.get("expect_key") or []) if "key" in checks else []:
        if k_ident in corpus.loci_idents:
            _flag_ambiguous(str(k_ident), "loci", "expect_key")
    for r_ident in (q.get("expect_ref") or []) if "ref" in checks else []:
        if r_ident in corpus.mem_refs:
            _flag_ambiguous(str(r_ident), "memory", "expect_ref")


def check_content_rail(q, query: str, qw: set, label: str, corpus, rep, checks: set) -> None:
    """CONTENT + RAIL. Is the expected phrase present, and can one retrieval pass GET
    there from this query?

    First: absence is an error (the mycelium catch). Then, for a phrase that IS present
    but whose holder shares no word with the query, the RAIL check: a hop can only
    travel a rail -- some third document that shares a real (non-stop) term with BOTH
    the query and the holder, so round-1 retrieval lands on it and round-2 can step
    from it to the answer. No rail -> the hop has nothing to walk on: the question needs
    knowledge the corpus never encoded (asper-k1 makes cellulASE; the feed is cellulOSE;
    nothing links the two). That's a DATASET DEFECT, a level deeper than 'the phrase is
    absent', because the phrase is present but UNREACHABLE. Only the railed ones are a
    fair test of a hop.
    """
    for c in (q.get("expect_content") or []) if "content" in checks else []:
        needle = str(c).lower()
        if needle not in corpus.haystack:
            rep.errors.append(
                f"{label}: expect_content {c!r} appears in NO seed document - "
                f"unanswerable at any fusion setting. (Ground-truth drift: the "
                f"question asks for something the corpus never says.)")
            continue
        # Reachable - but can a single retrieval pass GET there from this query?
        holders = [d for d in corpus.docs if needle in d.text.lower()]
        if qw and all(not (qw & _words(d.text)) for d in holders):
            best = holders[0]
            hw = _words(best.text)
            rail = None
            for d in corpus.docs:
                if d.ident == best.ident:
                    continue
                dw = _words(d.text)
                if (qw & dw) and (hw & dw):
                    rail = d.ident
                    break
            if rail is None:
                rep.unbridged.append((query, str(c), best.ident))
                rep.warnings.append(
                    f"{label}: expect_content {c!r} lives on {best.ident!r}, which shares no "
                    f"word with the query AND has NO bridge document connecting them. The hop "
                    f"has no rail: answering needs knowledge the corpus never encoded. This is "
                    f"a ground-truth defect (unanswerable by retrieval at ANY hop depth), not a "
                    f"tuning problem - regenerate the question, or add the linking fact.")
            else:
                rep.multihop.append((query, str(c), best.ident))
                rep.notes.append(
                    f"{label}: expect_content {c!r} lives on {best.ident!r} (no word shared "
                    f"with the query) but a bridge doc {rail!r} exists - this is a REAL 2-hop. "
                    f"A lexical store can't reach it in one pass; hops:2 should. Fusion knobs "
                    f"stay inert - they reorder a packet, they can't add an unretrieved doc.")
