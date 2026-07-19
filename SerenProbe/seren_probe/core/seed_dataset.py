"""
seren_probe.seed_dataset - flat seed content + eval questions.

Config-driven: a seed file is a FLAT list of items; WHICH store gets it (and so
the kind) is decided in ProbeConfig (Seed / DefaultLociSeed / DefaultMemorySeed),
resolved by seren_probe.resolve. This module just loads + validates the pieces
and writes a resolved plan to the live stores.

Item shapes are honest to the REAL write contracts:
  Loci   item = {project?, key, value, why?}
  Memory item = {content|intent, topic?, tier?, ref?}  (tier short|near|long)

Validation is compassion-first: collect ALL problems, hard-stop where trust
breaks, warn where recoverable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import normalize_text
from .topology import CompiledTopology

VALID_TIERS = {"short", "near", "long"}
VALID_ASKS = {"loci", "memory", "corpus"}

# Keys load_questions actually CONSUMES. Anything else in a question mapping is
# either a typo or a feature someone believed in that does not exist -- and both
# must be said out loud. `hops: 4` sat in the orkrail dataset on every chain
# question, declaring the traversal depth the question needs. Nothing read it.
# Twelve questions that announced "I need 4 hops" were asked at hops=1, scored
# zero, and the linter called the dataset CLEAN. A dropped field is a lie the
# parser tells the author.
KNOWN_QUESTION_KEYS = {
    "asks", "query", "expect_key", "expect_ref", "expect_content", "expect_empty",
    "hops", "quiet_in",
}


class SeedError(Exception):
    """Untrustworthy seed items / questions. Carries ALL errors + warnings."""
    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        self.errors = list(errors)
        self.warnings = list(warnings or [])
        lines = ["seed dataset invalid:"]
        lines += [f"  \u2717 {e}" for e in self.errors]
        lines += [f"  \u26a0 {w}" for w in self.warnings]
        super().__init__("\n".join(lines))


# ── resolved item types ─────────────────────────────────────────────────
@dataclass
class LociItem:
    project: str
    key: str
    value: str
    why: str | None = None


@dataclass
class MemoryItem:
    tier: str                 # short | near | long
    text: str                 # content (short/long) or intent (near)
    topic: str | None = None
    ref: str | None = None    # uploader handle -> mapped to minted id at seed time


@dataclass
class Question:
    asks: str
    query: str
    expect_key: list[str] = field(default_factory=list)
    expect_ref: list[str] = field(default_factory=list)
    expect_content: list[str] = field(default_factory=list)
    expect_empty: bool = False   # a no-answer question: PASS = the store stays quiet
    needs_hops: int = 1          # DECLARED traversal depth. See below -- it is a CLAIM, not a setting.
    quiet_in: list[str] = field(default_factory=list)   # stores that must NOT surface this answer


# NO `project` FIELD ON A QUESTION, DELIBERATELY. It was built, and then removed.
#
# SerenLoci's SearchRequest does carry `project`, and it is tempting to reach for it in a
# world with sixteen characters in it: one store, one project per tenant, scope the query.
# That is the wrong shape, and it is worth writing down why so nobody rebuilds it.
#
# ISOLATION IS ARCHITECTURAL HERE, NOT A QUERY PARAMETER. Zara's Loci cannot leak Thorn
# because it does not CONTAIN Thorn -- not because a caller remembered to pass project=.
# A scope you must remember to send is a scope you can forget to send, and the failure is
# silent and looks like a good score. A separate store is a wall. (Same argument as
# SerenMargin: the privacy guarantee rests on architectural separation, not on
# code-review discipline.)
#
# And it follows for the corpus: THE FAN IS THE SCOPE. cross-geography is "the locations"
# because it fans the location stores, not because it filters inside them. The SCC's job
# is to fuse what its stores returned and report the docket -- a chief of staff, not a
# query router. Giving it a project field would make store membership stop meaning what
# it means.
#
# So an unscoped Loci search of a single-tenant store is CORRECT, not a bug: it returns
# that tenant's facts plus fundamentals ('*'), which is precisely the tenant's world.
#
# Where `project` may yet earn its place: as a per-EDGE setting on the SCC's store list
# (StoreCreate already carries name/type/url/weight/floor) -- "this corpus's view of that
# Loci is scoped to X". Scope as a property of the EDGE, fixed at configuration, not of
# the QUERY, chosen per call. That is a real idea and it stays filed. It is not needed
# while one store means one tenant.


# expect_empty and quiet_in are TWO DIFFERENT TESTS. Do not merge them.
#
#   expect_empty  ABSTENTION.   "Nothing in the world answers this." The query names a
#                 phantom. PASS = the store returned ZERO ROWS (live_eval: `if not hits`).
#                 Only a store that CAN abstain -- lexical Loci, a floored SCC -- can pass;
#                 a raw vector store always returns k and scores 0 here BY CONSTRUCTION.
#                 That zero is the signal, not a bug.
#
#   quiet_in      NON-LEAKAGE.  "This DOES have an answer -- just not in THAT store." Ask
#                 the hermit in the next town about a tavern he has never heard of. He WILL
#                 return rows (Chroma always returns k); he will return rows about his goats.
#                 PASS = none of his top-k hits carry expect_content, and the expected
#                 key/ref does not resolve in him at all. Any store can pass this.
#
# The ground truth is written ONCE and graded twice at opposite polarity: the phrase
# mem-grishnak MUST find is exactly the phrase mem-hermit MUST NOT surface. That is why
# quiet_in needs no forbidden-phrase list of its own -- expect_content already IS it.
#
# A store's NegativeTest flag is the degenerate case: quiet_in on every question.


# `hops` on a question is a DECLARATION, not a knob.
#
# It says "answering me requires walking N edges." It does NOT configure anything:
# the SCC's hop depth is a /configure setting that applies to EVERY query in a run,
# so there is no such thing as a per-question hop count.
#
# It sat in the orkrail dataset on twelve chain questions -- srv-000 -> loco ->
# depot -> supplier -> part, honestly annotated `hops: 4` -- and NOTHING READ IT.
# The parser dropped it. The SCC ran at hops=1. Twelve questions that announced the
# exact depth they needed were asked at a quarter of it, scored zero, and the
# harness called the dataset CLEAN.
#
# So we PARSE it and then CHECK it (question_lint tier 5): if the deepest question
# needs more hops than any reachable configuration provides, that is unanswerable,
# and it is said out loud at upload time instead of arriving as a mystery zero.
#
# It also explains the thing that started this whole hunt -- "I don't think hops are
# working correctly." They were. The hop-sweep swept 1, 2, 3. The chains need 4.
# The knob was never inert. We just never turned it far enough.
# A knob swept in one direction is half a knob.


def quiet_targets_for(q, store_name: str) -> bool:
    """Does this question name `store_name` as a store that must stay quiet?

    An entry is an exact store name OR a glob (`char_thorn-*`).

    THE GLOB IS NOT SUGAR. A dataset author thinks in TENANTS -- "Zara's question must not
    be answered by Thorn" -- and a tenant is an ENTITY, which owns three brains
    (char_thorn-loci-v, char_thorn-mem, char_thorn-scc-v...). The topology only knows
    STORES. Force the author to enumerate stores and a sixteen-character world needs a
    hundred-odd names per question, hand-maintained, and wrong the first time anyone adds
    an organ. `char_thorn-*` says the thing they mean, once.

    A pattern matching NOTHING is an error, not a silent no-op -- see lint_quiet_targets.
    A quiet test that names a store that does not exist quietly tests nothing, and reports
    a perfect quiet_rate for it.
    """
    import fnmatch
    for pat in (getattr(q, "quiet_in", None) or []):
        if pat == store_name or fnmatch.fnmatchcase(store_name, pat):
            return True
    return False


def expand_quiet_target(pattern: str, names) -> list[str]:
    """Every declared store name this quiet_in entry resolves to (exact, else glob)."""
    import fnmatch
    if pattern in names:
        return [pattern]
    return sorted(n for n in names if fnmatch.fnmatchcase(n, pattern))


# ---- item validators ----------------------------------------------------
# `where` is a location label for messages, e.g. "loci seed".
def _as_loci_item(d: dict, where: str, i: int, errors: list[str], warnings: list[str]) -> LociItem | None:
    if not isinstance(d, dict):
        errors.append(f"{where}[{i}]: loci item must be a mapping, got {type(d).__name__}.")
        return None
    if "key" not in d or "value" not in d:
        errors.append(f"{where}[{i}]: loci item needs 'key' and 'value' "
                      f"(FactWrite shape: project?, key, value, why?).")
        return None
    if "tier" in d:
        warnings.append(f"{where}[{i}]: 'tier' is meaningless for a loci item - ignoring "
                        f"(tiers are a Memory concept).")
    return LociItem(project=str(d.get("project", "*")), key=str(d["key"]),
                    value=str(d["value"]), why=(str(d["why"]) if d.get("why") is not None else None))


def _as_memory_item(d: dict, where: str, i: int, errors: list[str], warnings: list[str]) -> MemoryItem | None:
    if not isinstance(d, dict):
        errors.append(f"{where}[{i}]: memory item must be a mapping, got {type(d).__name__}.")
        return None
    tier = d.get("tier", "short")
    if tier not in VALID_TIERS:
        errors.append(f"{where}[{i}]: tier {tier!r} invalid (valid: {sorted(VALID_TIERS)}).")
        return None
    if tier == "near":
        text = d.get("intent")
        if text is None and d.get("content") is not None:
            warnings.append(f"{where}[{i}]: near-tier item has 'content' but should use 'intent' "
                            f"- treating content as intent.")
            text = d.get("content")
        if text is None:
            errors.append(f"{where}[{i}]: near-tier item needs 'intent' text.")
            return None
    else:
        text = d.get("content")
        if text is None:
            errors.append(f"{where}[{i}]: {tier}-tier item needs 'content'.")
            return None
    return MemoryItem(tier=tier, text=str(text),
                      topic=(str(d["topic"]) if d.get("topic") is not None else None),
                      ref=(str(d["ref"]) if d.get("ref") is not None else None))


def _is_ref_list(source) -> bool:
    """Is this a LIST OF REFS to merge, or already-loaded inline content?

    A list of STRINGS is refs (paths, or inline YAML docs) -- merge them in order.
    A list of MAPPINGS is content -- passthrough, exactly as before.

    That distinction is the whole disambiguation, and it has to be explicit, because
    both of these are a YAML list and they mean opposite things:

        Seed: [chars/grishnak.yaml, world/lore.yaml]     <- two files to merge
        Seed: [{key: k, value: v}]                       <- one inline fact

    Without this, `Seed: [a.yaml, b.yaml]` fell straight through _load_any's
    list-passthrough and got validated as items, reporting "loci item must be a
    mapping, got str" -- an error about the schema for what is actually a perfectly
    good list of filenames.
    """
    return (isinstance(source, list) and len(source) > 0
            and all(isinstance(s, str) for s in source))


def _load_seed_items_many(sources: list, kind: str, warnings: list | None = None) -> list:
    """Merge N seed sources into ONE store's items, in declaration order.

    This is what makes a multi-brain dataset maintainable: a character store is
    `[chars/grishnak.yaml, world/lore.yaml]` -- their own memories PLUS the shared
    world. Without it you pre-compose, which means the world lore is copy-pasted into
    six character files and drifts the first time you change a world fact.

    THE DUP GUARD IS A HARD ERROR, and it has to be:

      memory  two merged files both carrying `ref: evt-000` land as TWO ROWS in ONE
              store, and ref_to_id binds ONE of them. Every question expecting evt-000
              is then graded against a coin-flip -- and the OTHER row is a perfect
              near-duplicate sitting in the corpus outranking it. That is precisely the
              disease the seeder's merge-order discipline cures ACROSS stores
              (see _StorePartial); merging files re-opens it WITHIN one.

      loci    two merged files both claiming `project/key` do not both exist -- the
              second set_fact SUPERSEDES the first. The store quietly holds only the
              later value while the seed file still says both. Ground truth that
              disagrees with the store is not ground truth.

    Items with no identity (memory rows with no `ref`) are never dup-checked: duplicate
    CONTENT is legitimate -- it is how you build a crowded corpus on purpose.
    """
    out: list = []
    errors: list[str] = []
    seen: dict[str, str] = {}          # identity -> the source that first claimed it
    for src in sources:
        label = src if len(str(src)) <= 80 else f"{str(src)[:77]}..."
        items = _load_seed_items_one(src, kind, warnings)
        for it in items:
            ident = f"{it.project}/{it.key}" if kind == "loci" else (it.ref or "")
            if not ident:
                out.append(it)
                continue
            if ident in seen:
                what = "project/key" if kind == "loci" else "ref"
                why = ("the second set_fact supersedes the first" if kind == "loci"
                       else "ref_to_id binds only one of the two rows")
                if seen[ident] == label:
                    # SAME FILE, twice. Not a merge collision at all -- a plain duplicate,
                    # which the old single-source path never checked for and which a generator
                    # using random ids produces on its own (birthday paradox, not bad luck).
                    # Worth its own sentence: telling someone a file collides with ITSELF is how
                    # you send them hunting for a second file that does not exist.
                    errors.append(
                        f"merged {kind} seed: {what} {ident!r} appears TWICE inside {label!r}. "
                        f"Only ONE of them survives seeding ({why}) -- the other is silently "
                        f"DELETED: present in the yaml, absent from the store, and anything "
                        f"expecting it scores zero for a reason no metric can show you. If those "
                        f"ids were generated, generate them SEQUENTIALLY -- a random id in a KEY "
                        f"namespace collides eventually, and eventually is sooner than you think.")
                else:
                    errors.append(
                        f"merged {kind} seed: {what} {ident!r} is claimed by BOTH {seen[ident]!r} "
                        f"and {label!r}. Merging them into one store makes its ground truth "
                        f"ambiguous ({why}). Rename one, or drop it from one of the files.")
                continue
            seen[ident] = label
            out.append(it)
    if errors:
        raise SeedError(errors, warnings)
    return out


def load_seed_items(source, kind: str, warnings: list | None = None) -> list:
    """Load a seed source: ONE ref (path / inline), or a LIST of refs merged in order.

    `warnings` is an optional out-param: pass a list and non-fatal problems are
    APPENDED to it. See the note on load_questions -- without it, a warning raised on
    an otherwise-clean load is discarded and never reaches a human.
    """
    if kind not in ("loci", "memory"):
        raise SeedError([f"load_seed_items: kind must be 'loci' or 'memory' (got {kind!r})."])
    if _is_ref_list(source):
        return _load_seed_items_many(source, kind, warnings)
    return _load_seed_items_one(source, kind, warnings)


def _load_seed_items_one(source, kind: str, warnings: list | None = None) -> list:
    """Load + validate a FLAT seed file - a bare list of items (or {items: [...]}) -
    as items of `kind` ('loci' or 'memory'). Returns [LociItem] | [MemoryItem].

    WHICH store gets these - and therefore the kind - is decided in ProbeConfig
    (Seed / DefaultLociSeed / DefaultMemorySeed), so the file itself is just a list.
    """
    if kind not in ("loci", "memory"):
        raise SeedError([f"load_seed_items: kind must be 'loci' or 'memory' (got {kind!r})."])
    data = _load_any(source)
    if isinstance(data, dict):
        # tolerate {items: [...]} or a kind-named key {loci: [...]}/{memory: [...]}
        data = data.get("items", data.get(kind))
    if not isinstance(data, list):
        raise SeedError([f"{kind} seed must be a list of items (or {{items: [...]}}), "
                         f"got {type(data).__name__}."])
    errors: list[str] = []
    warnings = warnings if warnings is not None else []
    where = f"{kind} seed"
    out: list = []
    validate = _as_loci_item if kind == "loci" else _as_memory_item
    for i, it in enumerate(data):
        item = validate(it, where, i, errors, warnings)
        if item is not None:
            out.append(item)
    if errors:
        raise SeedError(errors, warnings)
    return out


def _load_questions_many(sources: list, warnings: list | None = None) -> list[Question]:
    """Merge N question files, in declaration order.

    A repeated query is a WARNING, not an error: scoring the same query twice does not
    corrupt anything, it just double-weights it in the aggregate. Worth saying; not
    worth refusing.
    """
    warnings = warnings if warnings is not None else []
    out: list[Question] = []
    seen: dict[str, str] = {}
    for src in sources:
        label = src if len(str(src)) <= 80 else f"{str(src)[:77]}..."
        for q in _load_questions_one(src, warnings):
            key = normalize_text(q.query)
            if key and key in seen:
                warnings.append(
                    f"merged questions: the query {q.query!r} appears in BOTH {seen[key]!r} and "
                    f"{label!r}. It will be asked -- and averaged into the aggregate -- TWICE.")
            elif key:
                seen[key] = label
            out.append(q)
    return out


def load_questions(source, warnings: list | None = None) -> list[Question]:
    """Load questions: ONE ref (path / inline), or a LIST of refs merged in order.

    `warnings` is an optional out-param -- pass a list and non-fatal problems land in
    it. WITHOUT IT THEY ARE DISCARDED, and that is not a small thing: this parser's
    loudest warning is the unknown-key one ("IGNORED, not applied"), which exists
    BECAUSE a silently-dropped `hops:` cost a day. That warning was itself only ever
    surfaced when some OTHER error raised SeedError and carried it along. On a clean
    load -- the exact case where a typo'd key is most dangerous, because nothing else
    is complaining -- it went in the bin.

    The parser was telling the author a lie about a lie. Pass the list.
    """
    if _is_ref_list(source):
        return _load_questions_many(source, warnings)
    return _load_questions_one(source, warnings)


def _load_questions_one(source, warnings: list | None = None) -> list[Question]:
    """Parse + validate eval questions. Raises SeedError on anything unscoreable."""
    data = _load_any(source)
    if isinstance(data, dict):
        data = data.get("questions", data)
    if not isinstance(data, list):
        raise SeedError([f"questions must be a list (or {{questions: [...]}}), got {type(data).__name__}."])

    errors: list[str] = []
    warnings = warnings if warnings is not None else []
    out: list[Question] = []
    for i, q in enumerate(data):
        if not isinstance(q, dict):
            errors.append(f"questions[{i}] must be a mapping, got {type(q).__name__}.")
            continue
        asks = q.get("asks")
        if asks not in VALID_ASKS:
            errors.append(f"questions[{i}]: 'asks' must be one of {sorted(VALID_ASKS)} (got {asks!r}).")
        if not q.get("query"):
            errors.append(f"questions[{i}]: needs a 'query' string.")
        ek = q.get("expect_key") or []
        er = q.get("expect_ref") or []
        ec = q.get("expect_content") or []
        empty = bool(q.get("expect_empty", False))
        for label, val in (("expect_key", ek), ("expect_ref", er), ("expect_content", ec)):
            if not isinstance(val, list):
                errors.append(f"questions[{i}]: {label} must be a list (got {type(val).__name__}).")
        if empty and (ek or er or ec):
            errors.append(f"questions[{i}]: expect_empty means there's no right answer - don't "
                          f"combine it with expect_key / expect_ref / expect_content.")
        elif not empty and not (ek or er or ec):
            errors.append(f"questions[{i}]: needs at least one of expect_key / expect_ref / expect_content "
                          f"(or expect_empty: true for a no-answer question) - otherwise there's no way to score it.")
        if asks == "loci" and er:
            warnings.append(f"questions[{i}]: expect_ref on a loci question is unusual - Loci scores by "
                            f"(project/key). Use expect_key. (Kept, but it likely won't match.)")
        # UNKNOWN KEYS. Never drop a field in silence -- the author put it there for a
        # reason, and if we ignore it they will spend a day wondering why their
        # annotation did nothing. (It was `hops`. It was always going to be `hops`.)
        unknown = sorted(set(q) - KNOWN_QUESTION_KEYS)
        if unknown:
            warnings.append(
                f"questions[{i}]: unrecognized key(s) {unknown} -- IGNORED, not applied.")
        # `hops` is a DECLARED traversal depth, not a setting. Parsed here, CHECKED in
        # question_lint tier 5 against the deepest hop any regrade set can reach.
        hops_raw = q.get("hops", 1)
        needs_hops = 1
        if isinstance(hops_raw, bool) or not isinstance(hops_raw, int) or hops_raw < 1:
            errors.append(f"questions[{i}]: hops must be an integer >= 1 (got {hops_raw!r}). It "
                          f"DECLARES how many edges must be walked to answer this question; it "
                          f"does not configure anything.")
        else:
            needs_hops = hops_raw
        # quiet_in: the NON-LEAKAGE test. Names stores that must NOT surface this
        # question's answer. Graded at inverted polarity against the SAME expect_*
        # ground truth, in its own column -- a correct silence must never drag hit_rate.
        quiet_raw = q.get("quiet_in") or []
        quiet_in: list[str] = []
        if not isinstance(quiet_raw, list):
            errors.append(f"questions[{i}]: quiet_in must be a list of store names "
                          f"(got {type(quiet_raw).__name__}).")
        else:
            for j, s in enumerate(quiet_raw):
                if not isinstance(s, str) or not s.strip():
                    errors.append(f"questions[{i}]: quiet_in[{j}] must be a non-empty store name "
                                  f"(got {s!r}).")
                    continue
                if s in quiet_in:
                    warnings.append(f"questions[{i}]: quiet_in names {s!r} more than once -- de-duping.")
                    continue
                quiet_in.append(s)
        if quiet_in and empty:
            errors.append(
                f"questions[{i}]: quiet_in and expect_empty are DIFFERENT TESTS and cannot be "
                f"combined. expect_empty says NOBODY can answer this (pass = zero rows). "
                f"quiet_in says SOMEBODY can, just not these stores (pass = these stores don't "
                f"surface the answer) -- which needs an answer to not-surface, and expect_empty "
                f"forbids declaring one.")
        if quiet_in and not (ec or ek or er):
            errors.append(
                f"questions[{i}]: quiet_in has nothing to grade against. A quiet test asks "
                f"'did this store surface an answer it shouldn't have' -- so it needs an ANSWER "
                f"to look for: expect_content (the phrase, caught even when restated), "
                f"expect_key, or expect_ref (a HARD leak -- the doc living in the store). With "
                f"none of the three there is no answer to detect, and the test would pass "
                f"vacuously for every store. Give it at least one.")
        elif quiet_in and not ec:
            # key/ref-only IS a valid quiet test -- the grader's hard-leak check (resolve_key /
            # holds_ref) catches a store that literally HOLDS the answer doc. It just cannot
            # catch the same fact RESTATED in another store's own words. On an identity
            # question ('what race is X' -> expect_key char_x/x_race, no content) that is
            # exactly right and complete: the fact IS the keyed row, there is no looser
            # paraphrase of 'Tiefling' to worry about. Worth one note, not an error.
            warnings.append(
                f"questions[{i}]: quiet_in grades on expect_key/expect_ref only (no "
                f"expect_content). That catches a HARD leak -- the store holding the answer "
                f"doc itself -- but not the same fact restated in different words. Fine for an "
                f"identity question where the key IS the answer; add expect_content if the "
                f"leak you fear is a paraphrase.")
        if quiet_in and needs_hops > 1:
            warnings.append(
                f"questions[{i}]: declares hops={needs_hops} AND quiet_in. Hops are meaningless "
                f"to a quiet test -- you are checking for ABSENCE, and there is nothing to "
                f"traverse toward. The quiet stores are graded at one pass regardless.")

        out.append(Question(asks=asks or "", query=str(q.get("query", "")),
                            expect_key=list(ek), expect_ref=list(er), expect_content=list(ec),
                            expect_empty=empty, needs_hops=needs_hops, quiet_in=quiet_in))
    if errors:
        raise SeedError(errors, warnings)
    return out


# ── the seeder: a resolved plan -> live stores, capturing ref -> minted id ─
@dataclass
class SeedResult:
    loci_counts: dict[str, int]
    memory_counts: dict[str, int]
    ref_to_id: dict[str, str]
    key_index: dict[str, list[str]]


@dataclass
class _StorePartial:
    """One store's seed results, built by ONE worker in isolation.

    Workers never touch the shared SeedResult. They each fill one of these, and the
    main thread merges them in DETERMINISTIC store order afterwards.

    That is not fussiness, it is the whole reason parallel seeding is safe. SeedResult
    has two fields that genuinely race:

        ref_to_id.setdefault(bare_ref, minted)   <- BARE key, shared across ALL stores
        key_index.setdefault(pk, []).append(name)  <- list.append from N threads

    In a multi-brain topology (five character memories, each its own dataset) every
    dataset plausibly has an `evt-000`. Serially, the bare key is first-writer-wins in
    store order -- stable, reproducible, boring. Let N threads race for it and it
    becomes whoever-finishes-first: ground truth that changes run to run. That is the
    exact disease this harness spent a day curing, and it would have been reintroduced
    by the optimisation meant to make it faster.

    Merge-after means the parallel result is BYTE-IDENTICAL to the serial one.
    """
    count: int = 0
    keys: list = field(default_factory=list)        # loci: "project/key"
    refs: list = field(default_factory=list)        # memory: (ref, minted_id)


def _seed_item_weight(kind: str, item) -> int:
    """Units of WORK one item costs, for progress totals -- not a count of items.

    Loci and short/near memory items are one HTTP call each. A long-tier memory
    item is THREE: POST /short (write it), POST /short/{id}/promote (promote it),
    DELETE /short/{id} (clean up the now-superseded short copy). Counting it as 1
    would make the progress bar visibly LIE for any corpus with long-tier items --
    it would race ahead on short/near items, then crawl through long ones at a
    third of the apparent rate for no visible reason.
    """
    if kind == "loci":
        return 1
    return 3 if getattr(item, "tier", None) == "long" else 1


def _seed_one_store(name: str, items: list, url: str, kind: str, post, delete, on_progress=None) -> _StorePartial:
    """Seed ONE store, SERIALLY. Runs in its own worker thread.

    Writes stay in file order on purpose. Order is semantically load-bearing in a
    memory store -- timestamps, tier progression, the short->promote->long dance --
    and a store seeded out of order is not the store you would actually build. We
    parallelise ACROSS stores (independent processes, independent databases) and
    never WITHIN one. Wall clock goes from sum(store_times) to max(store_times);
    the contents of each store are unchanged.

    on_progress(delta), if given, is called after EACH underlying HTTP call --
    once for a loci fact, once for a short/near write, and once each for a long
    item's write/promote/delete -- so the caller's X/Y advances by exactly the
    same units _seed_item_weight counted into the total.
    """
    p = _StorePartial()
    if kind == "loci":
        for it in items:
            post(url, "/fact", {"project": it.project, "key": it.key,
                                "value": it.value, "why": it.why})
            p.keys.append(f"{it.project}/{it.key}")
            p.count += 1
            if on_progress:
                on_progress(1)
        return p

    for it in items:
        if it.tier == "near":
            body = {"intent": it.text}
            if it.topic:
                body["topic"] = it.topic
            resp = post(url, "/near", body)
            if on_progress:
                on_progress(1)
        else:
            body = {"content": it.text}
            if it.topic:
                body["topic"] = it.topic
            resp = post(url, "/short", body)
            if on_progress:
                on_progress(1)
            if it.tier == "long":
                sid = (resp or {}).get("id", "")
                if sid:
                    post(url, f"/short/{sid}/promote", {})
                    if on_progress:
                        on_progress(1)
                    if delete is not None:
                        delete(url, f"/short/{sid}")
                    # The delete is skipped when no `delete` callable is passed (some
                    # callers seed without one). The item was still COUNTED as 3 units
                    # in the total, so we still bump the 3rd unit here -- otherwise a
                    # deliberately delete-less caller would leave every long item
                    # permanently at 2/3 and the bar would never reach 100%.
                    if on_progress:
                        on_progress(1)
        minted = (resp or {}).get("id", "")
        if it.ref and minted:
            p.refs.append((it.ref, minted))
        p.count += 1
    return p


def seed_from_plan(topology: CompiledTopology, seed_by_store: dict, url_of: dict[str, str],
                   post, delete=None, max_parallel_stores: int = 8, report_progress: bool = False) -> SeedResult:
    """Seed each live store from a RESOLVED plan (store_name -> [LociItem|MemoryItem]).

    The resolver already decided which store gets which content (kind defaults,
    per-node overrides, and the negative-only-decoy rule), so this just WRITES it.
    Items are already-validated dataclass instances, so no re-validation here.

      Loci   -> POST /fact
      Memory -> POST /short | /near, with the long-tier promote+cleanup dance
    Captures ref -> minted id (namespaced + bare) and a project/key index.

    PARALLEL ACROSS STORES, SERIAL WITHIN ONE.
    Every store is a separate container, a separate process, a separate database --
    nothing is shared, so nothing contends. But a ~2000-item corpus is ~2000 serial
    HTTP round-trips, and at ~45 minutes a store a five-brain topology (5 loci +
    5 memory + a world corpus) took most of a working day just to LOAD. One worker
    per store collapses that to max(store_times) instead of sum(store_times).

    Within a store, writes stay strictly in file order -- see _seed_one_store.

    max_parallel_stores bounds the fan. Unbounded fan-out on a big topology would
    open a hundred simultaneous connections and DoS your own Docker host; a default
    of 8 is plenty for any realistic multi-brain layout and cheap to raise.

    report_progress publishes live X/Y counters to runtime.progress for the UI's
    status column, keyed by store name, weighted by _seed_item_weight so a long
    memory item (write+promote+delete = 3 calls) advances the bar three times as
    much as a short/near item or a loci fact. False by default -- most callers
    (tests, rehydration re-seeds) have no UI polling it and shouldn't pay the
    (tiny) locking cost or leave stale rows behind.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    res = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={})
    loci_names = {n.name for n in topology.loci}
    mem_names = {n.name for n in topology.memory}

    # Build the work list in the resolver's order. This ORDER is the merge order
    # below, so the result never depends on which thread finishes first.
    tasks = []
    for name, items in seed_by_store.items():
        if name in loci_names:
            kind = "loci"
        elif name in mem_names:
            kind = "memory"
        else:
            continue                      # corpus nodes aren't seeded directly
        tasks.append((name, items, url_of[name], kind))

    if not tasks:
        return res

    width = max(1, min(int(max_parallel_stores or 1), len(tasks)))
    partials: dict[str, _StorePartial] = {}

    progress_bumps: dict[str, callable] = {}
    # Bound unconditionally, so the guards below test the VALUE rather than trusting
    # two `if report_progress:` blocks to stay in agreement forever. The old shape
    # only imported _progress inside the flag check and then referenced it inside a
    # closure under a second copy of the same check -- correct today, possibly-unbound
    # to any reader or linter, and one edit away from a NameError that only fires when
    # the UI is watching.
    #
    # Relative import, matching every other cross-module reference in this package.
    # The absolute `from seren_probe.runtime import progress` that was here works
    # right up until the package is vendored, renamed, or imported under a different
    # top-level name.
    _progress = None
    if report_progress:
        from ..runtime import progress as _progress
        for name, items, url, kind in tasks:
            total = sum(_seed_item_weight(kind, it) for it in items)
            _progress.start(name, "seed", total)
            progress_bumps[name] = (lambda n: lambda delta: _progress.bump(n, delta))(name)

    def _seed_and_finish(name, items, url, kind):
        try:
            return _seed_one_store(name, items, url, kind, post, delete, progress_bumps.get(name))
        finally:
            # finish() in a finally, so a store that RAISES still stops reporting a
            # half-filled bar forever. A stuck row reads as "still working" and there
            # is nothing left to work.
            if _progress is not None:
                _progress.finish(name)

    if width == 1:
        # Explicit serial path. Keeps single-store topologies (and every test that
        # injects a fake transport) on a plain, thread-free code path.
        for name, items, url, kind in tasks:
            partials[name] = _seed_and_finish(name, items, url, kind)
    else:
        with ThreadPoolExecutor(max_workers=width, thread_name_prefix="seren-seed") as pool:
            futures = {pool.submit(_seed_and_finish, name, items, url, kind): name
                       for name, items, url, kind in tasks}
            for fut in as_completed(futures):
                # .result() RE-RAISES a worker's exception on the main thread. A store
                # that fails to seed must be loud: a half-seeded store scores like a
                # bad store, and we are not doing that again.
                partials[futures[fut]] = fut.result()

    # MERGE, in the original store order -- never completion order. This is what makes
    # the parallel result byte-identical to the serial one, including which store wins
    # the bare-ref key when two datasets share a handle.
    for name, items, url, kind in tasks:
        p = partials[name]
        if kind == "loci":
            res.loci_counts[name] = p.count
            for pk in p.keys:
                res.key_index.setdefault(pk, []).append(name)
        else:
            res.memory_counts[name] = p.count
            for ref, minted in p.refs:
                res.ref_to_id[f"{name}:{ref}"] = minted
                res.ref_to_id.setdefault(ref, minted)
    return res


def rehydrate_ref_map(topology: CompiledTopology, seed_by_store: dict, url_of: dict[str, str],
                      post, max_parallel_stores: int = 8, max_parallel_items: int = 8) -> tuple[dict[str, str], list[str]]:
    """Rebuild ref -> minted-id WITHOUT reseeding, by finding each seeded memory
    item's EXACT text in the live store. Returns (ref_to_id, unresolved_refs).

    WHY THIS EXISTS -- the worst bug in this harness's history, so read it:
    ref_to_id is minted at seed time and lives ONLY inside the SeedResult, i.e. only
    in the RAM of the process that did the seeding. It is never persisted. But a pod
    is flagged seeded=True after its FIRST eval, and seeding is skipped forever after
    (correctly -- seed_from_plan is additive and would stack a second corpus). So from
    run #2 onward, and on every ADOPTED pod, ref_to_id is {} and resolve_ref returns
    "" for every ref. `relevant` comes back EMPTY, and the memory column reports a
    PERFECTLY HEALTHY store as dead -- HR 0.083 while the store answers the query
    flawlessly at rank 1, scoring 0.649. Silently. No error, no warning, nothing.

    Loci never noticed because expect_key re-resolves LIVE via GET /fact: a
    deterministic key can be looked up by anyone, any time, forever. A minted UUID
    cannot. THAT asymmetry is the whole bug -- ground truth that lives in a process
    is not ground truth, it's a receipt.

    The binding here is EXACT normalized-text equality against the item we actually
    wrote -- not a similarity judgement. So retrieval quality affects only whether we
    FIND the row, never whether we bind the RIGHT one. Anything we cannot bind exactly
    comes back in `unresolved`, and the caller must SHOUT about it rather than quietly
    score it as a miss. An unresolvable answer key is a broken harness, not a failing
    store.

    CONCURRENCY -- this used to be one giant serial loop: every seeded memory item,
    across every store, one /search at a time. On an already-seeded/adopted pod that
    is the FIRST thing eval does, before the store fan-out even starts, and on a
    ~2000-item corpus it ran for MINUTES before a single result appeared -- indistinguishable
    from a hang. Same template as seed_from_plan: parallel ACROSS stores (independent
    containers), and within one store, its own items' /search calls also fan out
    (bounded separately -- concurrent traffic against the SAME container). Merge stays
    in store order so the result is deterministic regardless of completion order.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .metrics import normalize_text
    mem_names = {n.name for n in topology.memory}

    def _rehydrate_one_store(name: str, items: list, url: str) -> tuple[dict[str, str], list[str]]:
        refed = [it for it in items if getattr(it, "ref", None)]
        if not refed:
            return {}, []

        def _bind(it) -> tuple[str, str, str]:
            """Returns (ref, want_text_marker, hit_id) -- hit_id '' means unresolved."""
            want = normalize_text(it.text)
            try:
                resp = post(url, "/search", {"query": it.text, "n_results": 50,
                                            "include_short": True, "include_near": True,
                                            "include_long": True, "include_superseded": False})
                hits = resp.get("hits", []) if isinstance(resp, dict) else []
            except Exception:                      # noqa: BLE001
                hits = []
            hit_id = ""
            for h in hits:
                body = str(h.get("content", "") or h.get("intent", "") or "")
                if normalize_text(body) == want:   # EXACT, not "close enough"
                    hit_id = h.get("id", "")
                    break
            return it.ref, hit_id

        store_ref_to_id: dict[str, str] = {}
        store_unresolved: list[str] = []
        width = max(1, min(int(max_parallel_items or 1), len(refed)))
        if width == 1:
            bound = [_bind(it) for it in refed]
        else:
            with ThreadPoolExecutor(max_workers=width, thread_name_prefix="seren-rehydrate") as pool:
                bound = list(pool.map(_bind, refed))
        for ref, hit_id in bound:
            if hit_id:
                store_ref_to_id[f"{name}:{ref}"] = hit_id
                store_ref_to_id.setdefault(ref, hit_id)
            else:
                store_unresolved.append(f"{name}:{ref}")
        return store_ref_to_id, store_unresolved

    tasks = [(name, items, url_of.get(name)) for name, items in (seed_by_store or {}).items()
             if name in mem_names and url_of.get(name)]
    if not tasks:
        return {}, []

    width = max(1, min(int(max_parallel_stores or 1), len(tasks)))
    partials: dict[str, tuple[dict[str, str], list[str]]] = {}
    if width == 1:
        for name, items, url in tasks:
            partials[name] = _rehydrate_one_store(name, items, url)
    else:
        with ThreadPoolExecutor(max_workers=width, thread_name_prefix="seren-rehydrate-store") as pool:
            futures = {pool.submit(_rehydrate_one_store, name, items, url): name
                       for name, items, url in tasks}
            for fut in as_completed(futures):
                partials[futures[fut]] = fut.result()

    ref_to_id: dict[str, str] = {}
    unresolved: list[str] = []
    for name, _items, _url in tasks:          # store order -> deterministic merge
        store_ref_to_id, store_unresolved = partials[name]
        ref_to_id.update(store_ref_to_id)
        unresolved.extend(store_unresolved)
    return ref_to_id, unresolved


# ── loaders ─────────────────────────────────────────────────────────
def _looks_like_a_path(source: str) -> bool:
    """A single-line string ending in .yaml/.yml/.json was MEANT to be a file.
    Inline YAML is multi-line in every real use; a bare filename never is."""
    s = source.strip()
    return ("\n" not in s) and s.lower().endswith((".yaml", ".yml", ".json"))


def _load_any(source):
    """dict/list passthrough; else YAML/JSON from a path or an inline string.

    A MISSING FILE IS AN ERROR, said out loud. This used to fall through: if the
    path didn't exist, `text` stayed as the path STRING and we handed it to
    yaml.safe_load -- which happily parsed the filename itself as a YAML scalar and
    returned a str. The caller then reported:

        "loci seed must be a list of items, got str."

    ...for a file that simply WASN'T THERE. One missing directory produced five
    test failures, none of which said 'file not found', and all of which pointed at
    the schema instead of the filesystem. An error that lies about what's wrong is
    worse than no error: it sends you to debug the wrong thing.
    """
    if isinstance(source, (dict, list)):
        return source
    import yaml
    from pathlib import Path as _Path

    text = source
    is_path_like = isinstance(source, str) and _looks_like_a_path(source)
    try:
        p = _Path(str(source))
        if p.exists():
            text = p.read_text(encoding="utf-8")
        elif is_path_like:
            raise SeedError([
                f"seed/questions file NOT FOUND: {p}\n"
                f"    (it looks like a path -- ends in .yaml/.yml/.json -- and nothing is "
                f"there. If you meant inline YAML, it needs to be more than one line.)"])
    except OSError as exc:
        if is_path_like:
            raise SeedError([f"seed/questions file unreadable: {source} ({exc})"]) from exc
    return yaml.safe_load(text)
