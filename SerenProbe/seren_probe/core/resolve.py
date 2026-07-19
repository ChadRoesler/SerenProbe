"""
seren_probe.resolve - turn a compiled topology's seed/question REFERENCES into a
concrete, per-store seeding plan + the shared question set.

The bridge from ProbeConfig's declarative refs (DefaultLociSeed,
DefaultMemorySeed, a node's Seed, the top-level Questions) to actual content:
  - each store's seed source = its own Seed override, else the kind default;
  - a NEGATIVE (decoy) store gets ONLY its own Seed, NEVER the kind default -
    that's what makes the "stayed quiet" test honest (inheriting the real corpus
    would defeat the whole point);
  - questions are shared (top-level), loaded once, scored across every store.

Pure + injectable: pass a fake load_items / load_qs to test without files or a
live stack. The defaults read path-or-inline via seed_dataset's loaders, so an
uploaded ProbeConfig that carries inline content resolves with no disk at all.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .topology import CompiledTopology, ResolvedNode
from .seed_dataset import load_seed_items, load_questions, Question


@dataclass
class ResolvedPlan:
    seed_by_store: dict[str, list]              # store name -> [LociItem | MemoryItem]
    questions: list[Question] = field(default_factory=list)          # the UNION -- every question asked anywhere
    questions_by_store: dict[str, list] = field(default_factory=dict)  # store name -> the set IT is scored on
    warnings: list[str] = field(default_factory=list)


def _seed_ref_for(node: ResolvedNode, default_for_kind: str | None) -> str | None:
    """A node's own Seed override wins. A negative (decoy) store NEVER inherits
    the kind default - it must stand on its own decoy. Otherwise: the default."""
    if node.seed_ref:
        return node.seed_ref
    if node.negative_test:
        return None
    if node.live_url:              # live-import node: real data copied in, not synthetic
        return None
    return default_for_kind


def resolve_plan(topology: CompiledTopology, *,
                 load_items=load_seed_items, load_qs=load_questions) -> ResolvedPlan:
    """Resolve every store's seed content + the shared questions off a compiled
    topology. load_items(ref, kind) / load_qs(ref) are injectable for testing;
    defaults resolve a path or inline YAML via the seed_dataset loaders."""
    warnings: list[str] = []
    seed_by_store: dict[str, list] = {}

    for kind_key, nodes, default in (
        ("loci", topology.loci, topology.default_loci_seed),
        ("memory", topology.memory, topology.default_memory_seed),
    ):
        for n in nodes:
            ref = _seed_ref_for(n, default)
            if ref is None:
                seed_by_store[n.name] = []
                # A negative store with no decoy is a deliberate (compiler-warned)
                # choice - stay quiet. A NON-negative store with nothing to seed
                # is almost certainly a mistake - flag it.
                if not n.negative_test and not n.live_url:
                    dflt = "DefaultLociSeed" if kind_key == "loci" else "DefaultMemorySeed"
                    warnings.append(f"{kind_key} store {n.name!r} has no seed source "
                                    f"(no Seed, no {dflt}) - it'll seed empty.")
                continue
            seed_by_store[n.name] = load_items(ref, kind_key, warnings)

    # ── questions ───────────────────────────────────────────────────────────
    # A node's own Questions win; otherwise it inherits DefaultQuestions, filtered by
    # `asks` exactly as before. The filter is kept ONLY on the inherited path: an explicit
    # per-node set was written FOR that node, so it is taken whole.
    default_qs: list[Question] = []
    if topology.questions_ref:
        default_qs = load_qs(topology.questions_ref, warnings)
    else:
        warnings.append("no DefaultQuestions set - nothing to score the stores against "
                        "except whatever they declare themselves.")

    questions_by_store: dict[str, list] = {}
    explicit: set[str] = set()          # nodes that declared their OWN set

    # ALWAYS filter a store's set by `asks`, explicit or inherited.
    #
    # An earlier version took an explicit per-node set WHOLE, reasoning "it was written FOR
    # this node." That is wrong, and the D&D config is exactly why: one questions.yaml per
    # ENTITY, handed to that entity's Loci store AND its Memory store AND its Corpus. The
    # file belongs to the character, not to any one of their three brains. Take it whole and
    # the Loci store gets asked the memory questions -- expect_ref against a fact store,
    # zero every time, and the store reads as broken when the harness simply asked the wrong
    # organ.
    for kind_key, nodes in (("loci", topology.loci), ("memory", topology.memory)):
        for n in nodes:
            src = load_qs(n.questions_ref, warnings) if n.questions_ref else default_qs
            if n.questions_ref:
                explicit.add(n.name)
            questions_by_store[n.name] = [q for q in src if q.asks == kind_key]

    for c in topology.corpus:
        # THE COROLLARY THAT MAKES THE MULTI-BRAIN DATASET WORTH BUILDING.
        #
        # A corpus's effective set = its OWN `asks: corpus` questions + everything its
        # members answer.
        #
        #   own        the CROSS-STORE questions -- the ones no single fanned store can
        #              answer alone. Did fusion ADD something no store had? Fusion VALUE.
        #
        #   inherited  the questions its members already answer alone, kept UNFILTERED
        #              (they were already narrowed by their own store's kind). Ask the fan
        #              a question one member aces on its own: if the answer survives the
        #              merge, fusion preserved it; if it drowns, that number is DILUTION and
        #              nothing else. It is the one measurement a single-store eval can never
        #              make -- and the reason a store-per-tenant world is worth building.
        #
        # Inheritance fires ONLY from members that declared an EXPLICIT set. If nobody scoped
        # anything, a corpus falls back to `asks: corpus` off the default -- byte-identical
        # to the old behaviour. You opt into the new shape by scoping.
        _multi_tenant = sum(
            1 for s in c.stores if getattr(s, "kind", "") == "seren_loci") > 1
        inherited: list = []
        for s in c.stores:
            if s.name not in explicit:
                continue
            qs = questions_by_store.get(s.name, [])
            # QUALIFY INHERITED LOCI KEYS BY THEIR SOURCE STORE.
            #
            # An inherited question was written for a SINGLE-tenant store, where
            # 'combat/weapon' means exactly one row. Adopted into a corpus fanning
            # six characters it means six, and live_eval.resolve_key would bind it
            # to whichever member answered first -- so five of six questions get
            # graded against another tenant's row. Not a clean failure either:
            # depending on whether that wrong row happened to rank, it reads as a
            # miss OR as a hit, so the column becomes noise whose direction depends
            # on member ordering.
            #
            # The inheritance step is the ONLY place that knows which member a
            # question came from, so it is the only place that can say so. The
            # member's own copy is left untouched -- bare is correct there, and
            # mutating in place would corrupt the column that is currently scoring
            # 1.000.
            #
            # Only when the corpus actually fans more than one Loci. A single-loci
            # corpus has nothing to disambiguate, and qualifying there would change
            # behaviour that is already right.
            if _multi_tenant and getattr(s, "kind", "") == "seren_loci":
                qs = [_qualify_expect_keys(q, s.name) for q in qs]
            inherited.extend(qs)
        src = load_qs(c.questions_ref, warnings) if c.questions_ref else default_qs
        own = [q for q in src if q.asks == "corpus"]
        questions_by_store[c.name] = _dedupe(inherited + own)

    # The UNION. lint_plan needs every question that will be asked anywhere, and live_eval
    # scans it for quiet_in targets -- a quiet target is named by STORE, so a question can
    # reach a store whose own set never mentions it.
    questions = _dedupe(default_qs + [q for qs in questions_by_store.values() for q in qs])

    return ResolvedPlan(seed_by_store=seed_by_store, questions=questions,
                        questions_by_store=questions_by_store, warnings=warnings)


def _qualify_expect_keys(q, store_name: str):
    """A COPY of `q` whose expect_key entries name `store_name`, or `q` unchanged.

    NEVER mutates. The same Question object is also handed to the member store's
    own column, where a bare key is correct and currently scores 1.000 --
    qualifying in place would break the working column to fix the broken one.

    Already-qualified keys (a hand-written or cross-generated set) are left alone
    rather than double-prefixed.
    """
    keys = list(getattr(q, "expect_key", []) or [])
    if not keys or any(":" in str(k) for k in keys):
        return q
    new = [f"{store_name}:{k}" for k in keys]

    from dataclasses import is_dataclass, replace
    if is_dataclass(q):
        try:
            return replace(q, expect_key=new)
        except Exception:      # noqa: BLE001 - non-init field / frozen / slots
            pass
    import copy as _copy
    try:
        q2 = _copy.copy(q)
        q2.expect_key = new
        return q2
    except Exception:          # noqa: BLE001
        # Could not copy safely. Return the ORIGINAL rather than mutate it: an
        # un-qualified key produces a visibly ambiguous lint line, whereas a
        # mutated shared object silently corrupts the member's own column. Loud
        # and wrong beats quiet and wrong.
        return q


def _dedupe(qs: list) -> list:
    """Same query text twice = asked twice = double-weighted in the aggregate. Drop it.
    First occurrence wins, so declaration order stays the order."""
    out, seen = [], set()
    for q in qs:
        key = (q.query or "").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(q)
    return out


@dataclass
class EvalInputs:
    """Everything /eval/run needs, decided from the ProbeConfig (+ optional body
    overrides): the questions, how to seed, and any resolve warnings."""
    questions: list = field(default_factory=list)
    seed_by_store: dict | None = None     # config-driven plan; None => don't plan-seed
    warnings: list = field(default_factory=list)
    seed: bool = False
    questions_by_store: dict | None = None   # per-store scoped sets; None => score every store off `questions`


def resolve_eval_inputs(topology: CompiledTopology, body: dict | None = None, *,
                        resolve=resolve_plan, load_qs=load_questions) -> EvalInputs:
    """Config-DRIVEN eval inputs. Seeds + questions come from the compiled
    ProbeConfig (DefaultLociSeed / DefaultMemorySeed / per-node Seed / Questions).
    The request body can OVERRIDE questions (body.questions); seeding is entirely
    config-driven (no pools upload path), UNLESS the body explicitly forces it
    off with `seed: false` -- see below.

    A config with NO seed sources at all means 'the stores are pre-seeded - eval
    them as-is', so we DON'T plan-seed and don't raise the empty-seed warnings.
    Raises SeedError (caught by the route -> 400) on any malformed ref/body.
    """
    body = body or {}
    plan = resolve(topology)
    questions = plan.questions

    has_seed_intent = bool(
        topology.default_loci_seed or topology.default_memory_seed
        or any(n.seed_ref for n in topology.loci + topology.memory)
        or any(n.live_url for n in topology.loci + topology.memory))
    seed_by_store = plan.seed_by_store if has_seed_intent else None
    warnings = list(plan.warnings) if has_seed_intent else []

    # EXPLICIT OFF-SWITCH. `body.seed is False` (not merely falsy/absent) means the
    # caller -- the Eval tab's "▶ Evaluate" button, now split off from seeding --
    # wants to score the stores AS THEY ARE and never touch them. Distinct from
    # has_seed_intent being false: a config CAN declare seed sources and still be
    # asked, this one time, not to run them.
    if body.get("seed") is False:
        seed_by_store = None

    questions_by_store = plan.questions_by_store or None

    if body.get("questions") is not None:
        # A body-supplied set is GLOBAL and replaces the config's scoping wholesale. Keeping
        # the per-store map here would score half the topology on the uploaded questions and
        # the other half on the config's -- a silent mix nobody asked for. An override that
        # only half-overrides is worse than no override.
        questions = load_qs(body["questions"], warnings)
        questions_by_store = None

    return EvalInputs(questions=questions, seed_by_store=seed_by_store,
                      warnings=warnings, seed=seed_by_store is not None,
                      questions_by_store=questions_by_store)
