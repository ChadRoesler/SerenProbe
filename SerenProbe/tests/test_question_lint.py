"""Reachability lint: catch unanswerable questions before they look like bad scores."""
from seren_probe.core.linters.plan import lint_questions
from seren_probe.core.linters.model import build_docs
from seren_probe.core.seed_dataset import Question

LOCI = [
    {"project": "myc", "key": "strain_a1_host", "value": "A. sojae", "why": "production host"},
    {"project": "myc", "key": "reg_FDA_expires", "value": "2027-06", "why": "expiry"},
    {"project": "myc", "key": "mat_feed_supplier", "value": "Sigma-Aldrich", "why": "supplier"},
]
MEM = [
    {"tier": "short", "ref": "evt-1", "content": "pH spike in enzyme lab; contained."},
    {"tier": "near", "ref": "todo-1", "intent": "Order IPTG before stock runs out."},
]


def test_build_docs_uses_intent_for_near():
    docs = build_docs(LOCI, MEM)
    near = next(d for d in docs if d.ident == "todo-1")
    assert "IPTG" in near.text            # near tier indexes its intent as the document


def test_missing_expect_key_is_an_error():
    rep = lint_questions([{"asks": "loci", "query": "host of a1?",
                           "expect_key": ["myc/strain_NOPE_host"]}], LOCI, MEM)
    assert not rep.ok and "NOT in the seed" in rep.errors[0]


def test_missing_expect_ref_is_an_error():
    rep = lint_questions([{"asks": "memory", "query": "the spike?",
                           "expect_ref": ["evt-999"]}], LOCI, MEM)
    assert not rep.ok and "NOT in the seed" in rep.errors[0]


def test_content_absent_from_corpus_is_an_error():
    """The mycelium catch: a phrase the corpus never says can't be a low score."""
    rep = lint_questions([{"asks": "memory", "query": "reviews for a1?",
                           "expect_content": ["strain history"]}], LOCI, MEM)
    assert not rep.ok
    assert "appears in NO seed document" in rep.errors[0]


def test_reachable_content_passes():
    rep = lint_questions([{"asks": "loci", "query": "what host does strain a1 use?",
                           "expect_key": ["myc/strain_a1_host"],
                           "expect_content": ["A. sojae"]}], LOCI, MEM)
    assert rep.ok and not rep.multihop


def test_no_lexical_bridge_is_flagged_as_UNBRIDGED_not_an_error():
    """Sigma-Aldrich IS in the corpus, but its doc shares no word with the query.
    That's a capability boundary, not a broken question - note, don't error.

    UNBRIDGED, not multihop. The RAIL check split what used to be one bucket into
    two: `multihop` = a lexical rail EXISTS, it just takes more than one hop to
    walk; `unbridged` = there is NO rail at all, at any depth. Sigma-Aldrich's doc
    shares no term with the query, so nothing can carry a retriever from one to the
    other.

    The orkrail run proved the distinction earns its bucket: the SCC hop
    demonstrably RUNS (13 hits -> 18, eight genuinely new documents), and unbridged
    expectations STILL never move - at any hop count, term budget, or packet depth.
    An unbridged expectation is not a retrieval failure. It's a hole in the graph.
    """
    rep = lint_questions([{"asks": "corpus", "query": "supply chain for strain a1",
                           "expect_content": ["Sigma-Aldrich"]}], LOCI, MEM)
    assert rep.ok                                   # reachable => not an error
    assert not rep.multihop                         # no rail exists, so it isn't merely a hop away
    assert len(rep.unbridged) == 1
    q, c, holder = rep.unbridged[0]
    assert c == "Sigma-Aldrich" and holder == "myc/mat_feed_supplier"


def test_good_bait_is_silent():
    """A bait naming something the corpus never heard of is a VALID bait."""
    rep = lint_questions([{"asks": "loci", "query": "what host does strain phony-z9 use?",
                           "expect_empty": True}], LOCI, MEM)
    assert rep.ok and not rep.warnings              # 'phony-z9' has zero doc frequency


def test_bait_with_no_novel_term_warns():
    """If every word already exists in the corpus, the 'bait' may be answerable."""
    rep = lint_questions([{"asks": "loci", "query": "supplier host",
                           "expect_empty": True}], LOCI, MEM)
    assert rep.warnings and "phantom" in rep.warnings[0]


# ── lint_plan: topology-aware (skips live-import + decoys) ─────────────────
def _cfg(live=False, decoy=False):
    l = {"Name": "l", "Port": 7421}
    if live:
        l["LiveStoreUrl"] = "http://127.0.0.1:7422"
    loci = [l]
    if decoy:
        loci.append({"Name": "d", "Port": 7429, "NegativeTest": True, "Seed": "decoy.yaml"})
    return {"ProbeConfig": {"StartingPort": 7420, "DefaultQuestions": "q.yaml",
        "DefaultLociSeed": "loci.yaml", "DefaultMemorySeed": "mem.yaml",
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        "Loci": {"LociCount": len(loci), "LociConfigs": loci},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]}}}


# Question OBJECTS, not dicts. resolve_plan now READS them -- it filters the default set
# by `q.asks` to decide what each store inherits, and dedupes on `q.query`. A fake handing
# back bare dicts used to be harmless because the resolver only ever passed the list
# through; it isn't any more. The fake has to return what the real loader returns.
_QS = [Question(asks="loci", query="what host does strain a1 use?",
                expect_key=["myc/strain_a1_host"]),
       Question(asks="memory", query="the reviews?", expect_content=["strain history"])]


def _items(ref, kind, warnings=None):
    if ref == "decoy.yaml":
        return [{"project": "myc", "key": "strain_FAKE_host", "value": "nope"}]
    return LOCI if kind == "loci" else MEM


def _plan_for(cfg):
    from seren_probe.core.topology import compile_topology
    from seren_probe.core.resolve import resolve_plan
    t = compile_topology(cfg)
    return t, resolve_plan(t, load_items=_items, load_qs=lambda r, w=None: list(_QS))


def test_lint_plan_catches_unanswerable_on_a_seeded_topology():
    from seren_probe.core.linters.plan import lint_plan
    t, p = _plan_for(_cfg())
    rep = lint_plan(t, p)
    assert len(rep.errors) == 1 and "strain history" in rep.errors[0]


def test_lint_plan_skips_live_import_instead_of_false_erroring():
    """A live store has no seed -- ITS expectations are UNVERIFIABLE. Everyone else's aren't.

    This assertion got SHARPER when the lint went per-scope. It used to be `not rep.errors`:
    one live-import store anywhere in the topology dropped the content check for the ENTIRE
    lint, so the memory store -- fully readable, holding no 'strain history' -- went silent
    too. A live Loci had silenced a lint about Memory.

    Now each scope drops only the checks IT cannot verify. The live Loci is unverifiable and
    says nothing. The memory store is verifiable and says what it sees.
    """
    from seren_probe.core.linters.plan import lint_plan
    t, p = _plan_for(_cfg(live=True))
    rep = lint_plan(t, p)
    assert any("UNVERIFIABLE" in n for n in rep.notes)
    # the LIVE loci store raises nothing -- we cannot read it, so we do not accuse it
    assert not any("[l]" in e for e in rep.errors)
    assert not any("strain_a1_host" in e for e in rep.errors)
    # the MEMORY store is readable and its question genuinely is unanswerable by it
    assert any("strain history" in e for e in rep.errors)


def test_lint_plan_scopes_reachability_to_the_store_being_asked():
    """The mycelium catch must survive owning more than one store.

    Under the old union lint, every store's seed was merged into ONE haystack. A question
    scored against store A whose expect_content only exists in store B's seed sailed through
    -- the phrase IS in the corpus, it just isn't in the store being ASKED. The reachability
    check quietly stopped checking anything the moment a topology had two brains in it.
    """
    from seren_probe.core.topology import compile_topology
    from seren_probe.core.resolve import resolve_plan
    from seren_probe.core.linters.plan import lint_plan

    cfg = {"ProbeConfig": {"StartingPort": 7420,
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "zara", "Port": 7421, "Seed": "zara.yaml", "Questions": "zara_qs.yaml"},
            {"Name": "thorn", "Port": 7422, "Seed": "thorn.yaml", "Questions": "thorn_qs.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "world", "Port": 7427,
             "Stores": [{"Store": "zara"}, {"Store": "thorn"}]}]}}}

    seeds = {
        "zara.yaml": [{"project": "zara", "key": "zara_bond", "value": "treasure"}],
        "thorn.yaml": [{"project": "thorn", "key": "thorn_bond", "value": "a silver locket"}],
    }
    # Zara's question expects a phrase that lives ONLY in THORN's seed.
    qs = {
        "zara_qs.yaml": [Question(asks="loci", query="what is zara's bond?",
                                  expect_content=["a silver locket"])],
        "thorn_qs.yaml": [],
    }
    t = compile_topology(cfg)
    p = resolve_plan(t,
                     load_items=lambda ref, kind, w=None: list(seeds.get(ref, [])),
                     load_qs=lambda ref, w=None: list(qs.get(ref, [])))
    rep = lint_plan(t, p)

    # zara's store does NOT hold the locket -- unanswerable BY ZARA, and said so.
    assert any("a silver locket" in e and "zara" in e for e in rep.errors)


def test_lint_plan_excludes_decoys_from_the_corpus():
    from seren_probe.core.linters.plan import lint_plan
    t, p = _plan_for(_cfg(decoy=True))
    rep = lint_plan(t, p)
    assert any("decoy" in n for n in rep.notes)
    assert any("strain history" in e for e in rep.errors)   # still catches the real bug
