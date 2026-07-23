"""
seren_probe.topology - the ProbeConfig topology compiler.

Turns a declared ProbeConfig into a resolved, validated CompiledTopology the
compose emitter + seeder consume. Beyond the store SHAPE (X Loci, Y Memory,
Z Corpus; flags/ports/wiring) it also carries the DATA plumbing: shared
top-level Questions, per-kind default seeds, per-store seed overrides, and the
negative-test marker (a store deliberately seeded with decoy data to prove it
stays quiet on queries that shouldn't match).

The compiler IS the landmine guard: two "different" corpora that secretly fan
the same store set, a negative store with no decoy, a decoy store wired into a
measured SCC - all caught at config time, before Docker breathes. Hard-stop
where trust breaks, warn where it's recoverable, every message names the node,
the rule, and the fix.
"""
from __future__ import annotations

from dataclasses import dataclass, field

VALID_FLAGS: dict[str, set[str]] = {
    "loci":   {"vector", "mcp"},
    "memory": {"mcp"},
    "corpus": {"mcp"},
}
KIND = {"loci": "seren_loci", "memory": "seren_memory"}

# Regrade knobs - the fusion params a CorpusRegrades set may override, with the
# element type each expects. Mirror of the grid constants in regrade.py (keep in
# sync); the values THERE are the defaults a set inherits for any knob it omits.
REGRADE_KNOBS: dict[str, type] = {
    "rrf_k": int, "loci_weight": float, "loci_floor": float,
    "authority_margin": float, "min_per_store": int, "fusion_mode": str,
    "n_results": int, "fetch_multiplier": int,
    "hops": int,          # retrieval rounds. 1 = today's single pass; 2 = one hop.
    # The hop's STEERING knobs. These were mapped in regrade_live._FED_KNOB and listed
    # in its _READBACK, and the SCC advertises both -- but they were missing HERE, so
    # the compiler treated them as unknown, warned, and DROPPED them. A set sweeping
    # hop_terms would silently compile down to whatever else it named and report the
    # result as if the sweep had happened. Exactly the inert-knob lie knob_caps exists
    # to refuse. A knob is only real if the compiler, the mapper, AND the SCC agree.
    "hop_terms": int,     # how many terms round-2 lifts from round-1 hit text
    "hop_budget": int,    # how many extra docs a hop may pull back
}

# Knobs that need a CAPABILITY the SCC may not have. Sweeping one against an SCC
# that ignores the field yields identical rows in every combo - an "inert knob"
# indistinguishable on the dashboard from a real ceiling. That ambiguity is what
# cost hours on the mycelium set, so we never ship it silently: compile WARNS,
# and knob_caps.assert_knobs_supported HARD-ERRORS at regrade time unless the SCC
# advertises the knob in GET /stores.
KNOBS_NEEDING_CAPABILITY: dict[str, str] = {
    "hops": ("multi-hop retrieval (see docs/SCC-MULTIHOP.md). Fusion reorders a packet; "
             "it cannot add a document retrieval never returned -- so a hop is a RETRIEVAL "
             "capability, not a fusion setting."),
    "hop_terms": ("multi-hop query expansion width. Same capability as `hops` -- an SCC that "
                  "can't hop can't steer a hop either, and would ignore this silently."),
    "hop_budget": ("multi-hop retrieval budget. Same capability as `hops`; an SCC without "
                   "multi-hop ignores it, yielding identical rows that read as a ceiling."),
}


def _as_ref(val, label: str, errors: list[str]):
    """A seed/questions REFERENCE: a string (path or inline YAML), or a LIST of them.

    A list is merged IN ORDER by the loaders. That is what makes a multi-brain dataset
    maintainable -- a character store is `[chars/grishnak.yaml, world/lore.yaml]`, their
    own memories PLUS the shared world. Force it to a single file and the world lore gets
    copy-pasted into six character files, and drifts the first time a world fact changes.
    """
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, list) and val and all(isinstance(s, str) for s in val):
        return list(val)
    errors.append(f"{label} must be a string (a path or inline YAML) or a LIST of strings "
                  f"(merged in order), got {type(val).__name__}.")
    return None


class TopologyError(Exception):
    """Raised when a topology can't be trusted. Carries ALL errors at once
    (no fix-run-fix-run whack-a-mole) plus any warnings gathered so far."""
    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        self.errors = list(errors)
        self.warnings = list(warnings or [])
        lines = ["topology compile failed:"]
        lines += [f"  \u2717 {e}" for e in self.errors]
        lines += [f"  \u26a0 {w}" for w in self.warnings]
        super().__init__("\n".join(lines))


@dataclass
class ResolvedNode:
    name: str
    kind: str            # seren_loci | seren_memory
    port: int
    flags: list[str] = field(default_factory=list)
    generated: bool = False
    seed_ref: str | list | None = None      # per-node Seed (path / inline / list to merge); None => use the kind default
    negative_test: bool = False      # decoy store: seeded ONLY from seed_ref, scoring inverted, kept out of the catch-all
    live_url: str | None = None      # copy data from THIS live store (read-only) into the container instead of a synthetic seed
    questions_ref: str | list | None = None
    # The knobs this corpus BOOTS at, from its `Config:` block. Empty => whatever
    # seren-corpus-callosum defaults to. Written into the mounted corpus yaml at spin-up
    # AND pushed to a running container on hot-swap, so "what it boots at" and "what it
    # is running" cannot drift apart without someone saying so.
    config: dict = field(default_factory=dict)  # the set this store is expected to ANSWER; None => DefaultQuestions


@dataclass
class ResolvedStore:
    name: str
    kind: str
    weight: float
    via_catchall: bool = False


@dataclass
class ResolvedCorpus:
    name: str
    port: int
    flags: list[str] = field(default_factory=list)
    stores: list[ResolvedStore] = field(default_factory=list)
    generated: bool = False
    is_catchall: bool = False
    # A corpus's OWN Questions are the CROSS-STORE ones: the questions no single fanned
    # store can answer alone ("what did Grishnak do at the Sundered Tavern" needs the
    # character store AND the location store). Its EFFECTIVE set is those PLUS everything
    # its members answer -- which is what splits the score two ways: did fusion PRESERVE
    # what a store already knew (dilution), and did it ADD what no store had (value).
    questions_ref: str | list | None = None
    # Per-corpus CorpusRegrades. THREE-STATE, and the distinction is the whole feature:
    #   None  -> INHERIT the top-level CorpusRegrades (the default; most corpora)
    #   [...] -> this corpus's OWN sets, run IN ADDITION to any top-level ones
    #   []    -> explicitly OPT OUT; this corpus is not swept at all
    #
    # `absent` and `empty` have to mean different things or there is no way to skip a
    # single corpus once a top-level set exists -- and skipping is the point. A sweep
    # is minutes-to-hours per corpus and corpora run serially, so "regrade these three,
    # not all fourteen" is the difference between a coffee and an afternoon.
    config: dict = field(default_factory=dict)  # the knobs this corpus BOOTS at, from its `Config:` block
    regrades: list | None = None


@dataclass
class RegradeSet:
    """A named fusion-knob override bundle. overrides maps a knob -> the list of
    values to sweep; any knob NOT present inherits the regrade default."""
    name: str
    overrides: dict[str, list] = field(default_factory=dict)


@dataclass
class CompiledTopology:
    loci: list[ResolvedNode]
    memory: list[ResolvedNode]
    corpus: list[ResolvedCorpus]
    warnings: list[str] = field(default_factory=list)
    questions_ref: str | None = None        # shared across ALL stores - the comparison stays apples-to-apples
    default_loci_seed: str | None = None     # every Loci without its own Seed draws from this
    default_memory_seed: str | None = None   # every Memory without its own Seed draws from this
    corpus_regrades: list[RegradeSet] = field(default_factory=list)   # named knob bundles to sweep
    # Per-kind package version pins, e.g. {"corpus": "==1.4.0"}. `pip install <pkg>`
    # is a CACHED Docker layer: publish a new version and rebuild, and Docker reuses
    # the layer that installed the OLD one, because the instruction text never
    # changed. You then grade a build you think you upgraded and didn't. Pinning
    # busts the cache AND records what was actually tested.
    versions: dict = field(default_factory=dict)

    def summary(self) -> str:
        out: list[str] = []
        for n in self.loci + self.memory:
            tag = " (auto)" if n.generated else ""
            neg = " (negative)" if n.negative_test else ""
            fl = f" [{','.join(n.flags)}]" if n.flags else ""
            out.append(f"  {n.name:34s} :{n.port}  {n.kind}{fl}{neg}{tag}")
        for c in self.corpus:
            tag = " (catch-all)" if c.is_catchall else (" (auto)" if c.generated else "")
            fl = f" [{','.join(c.flags)}]" if c.flags else ""
            out.append(f"  {c.name:34s} :{c.port}  corpus{fl}{tag}")
            for s in c.stores:
                ca = "  \u2190 catch-all" if s.via_catchall else ""
                out.append(f"        \u2514 {s.name} (w={s.weight}){ca}")
        return "\n".join(out)


@dataclass
class _Pending:
    name: str | None            # None => auto-generated remainder
    port: int | None            # None => needs allocation
    flags: list[str]
    generated: bool = False
    stores: list | None = None  # corpus only
    idx: int = 0
    seed_ref: str | list | None = None
    negative_test: bool = False
    live_url: str | None = None
    questions_ref: str | list | None = None


def _parse_section(pc: dict, section: str, count_key: str, cfg_key: str,
                   errors: list[str], warnings: list[str]) -> tuple[int | None, list[_Pending]]:
    raw = pc.get(section)
    if raw is None:
        errors.append(f"{section}: section is required (needs {count_key}, optional {cfg_key}).")
        return None, []
    if not isinstance(raw, dict):
        errors.append(f"{section}: must be a mapping with {count_key}/{cfg_key}, got {type(raw).__name__}.")
        return None, []

    count = raw.get(count_key)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        errors.append(f"{section}: {count_key} must be an integer >= 1 (got {count!r}).")
        count = None

    cfgs = raw.get(cfg_key)
    if cfgs is None:
        cfgs = []
    if not isinstance(cfgs, list):
        errors.append(f"{section}: {cfg_key} must be a list (got {type(cfgs).__name__}).")
        cfgs = []

    short = section.lower()
    is_corpus = section == "Corpus"
    pend: list[_Pending] = []
    for i, entry in enumerate(cfgs):
        if not isinstance(entry, dict):
            errors.append(f"{section}: {cfg_key}[{i}] must be a mapping with a Name field "
                          f"(got {type(entry).__name__}). Use name-as-field "
                          f"('- Name: my-node' / Port / Flags), not name-as-key.")
            continue
        name = entry.get("Name")
        if not name or not isinstance(name, str):
            errors.append(f"{section}: {cfg_key}[{i}] has no 'Name'. Each entry needs Name (required), "
                          f"Port (optional), Flags (optional). Did you use the old name-as-key style?")
            continue
        port = entry.get("Port")
        if port is not None and (not isinstance(port, int) or isinstance(port, bool)):
            errors.append(f"{section}: node {name!r} has a non-integer Port ({port!r}).")
            port = None
        flags_in = entry.get("Flags") or []
        if not isinstance(flags_in, list):
            errors.append(f"{section}: node {name!r} Flags must be a list (got {type(flags_in).__name__}).")
            flags_in = []
        flags: list[str] = []
        for fl in flags_in:
            if fl in VALID_FLAGS[short]:
                if fl not in flags:
                    flags.append(fl)
            else:
                warnings.append(f"{section}: flag {fl!r} isn't valid for {section} "
                                f"(valid: {sorted(VALID_FLAGS[short])}) - ignoring it on {name!r}.")

        # data plumbing: Seed + NegativeTest are per-store on Loci/Memory; a
        # corpus has neither (it fans already-seeded stores).
        seed_ref = None
        negative_test = False
        live_url: str | None = None
        # Questions are PER-NODE now: a node declares the set it is expected to ANSWER.
        # Allowed on a corpus too, and that is the interesting one -- a corpus's own
        # Questions are the CROSS-STORE ones, the questions no single fanned store can
        # answer alone. Absent, a node inherits DefaultQuestions.
        questions_ref = _as_ref(entry.get("Questions"), f"{section}: node {name!r} Questions", errors)
        if is_corpus:
            for stray in ("Seed", "NegativeTest", "LiveStoreUrl"):
                if stray in entry:
                    warnings.append(f"{section}: node {name!r} has {stray!r}, which does nothing on a "
                                    f"corpus - corpora fan already-seeded stores. Remove it.")
        else:
            seed_ref = _as_ref(entry.get("Seed"), f"{section}: node {name!r} Seed", errors)
            neg_raw = entry.get("NegativeTest", False)
            if not isinstance(neg_raw, bool):
                errors.append(f"{section}: node {name!r} NegativeTest must be true/false (got {neg_raw!r}).")
            else:
                negative_test = neg_raw
            live_raw = entry.get("LiveStoreUrl")
            if live_raw is not None:
                if not isinstance(live_raw, str) or not live_raw.strip():
                    errors.append(f"{section}: node {name!r} LiveStoreUrl must be a non-empty string "
                                  f"(a live store base URL, e.g. http://192.168.0.101:7422).")
                else:
                    live_url = live_raw.strip().rstrip("/")
            if live_url and seed_ref:
                errors.append(f"{section}: node {name!r} sets BOTH Seed and LiveStoreUrl - pick one. "
                              f"LiveStoreUrl copies real data from a live store (read-only); "
                              f"Seed uses synthetic data.")

        pend.append(_Pending(name=name, port=port, flags=flags, generated=False,
                             stores=entry.get("Stores") if is_corpus else None, idx=i,
                             seed_ref=seed_ref, negative_test=negative_test, live_url=live_url,
                             questions_ref=questions_ref))

    # Pass 1: bounds  Count-1 <= len <= Count
    if count is not None:
        n = len(pend)
        lo, hi = count - 1, count
        if not (lo <= n <= hi):
            need = lo - n if n < lo else n - hi
            verb = "Add" if n < lo else "Remove"
            errors.append(f"{section}: {cfg_key} has {n} entries but {count_key} is {count} - "
                          f"expected {lo} or {hi} (Count-1 .. Count). {verb} {need}, or adjust {count_key}.")
        else:
            for _ in range(count - n):   # 0 or 1 auto-generated remainder
                pend.append(_Pending(name=None, port=None, flags=[], generated=True,
                                     stores=None, idx=len(pend)))
    return count, pend


def _parse_regrades(raw, errors: list[str], warnings: list[str]) -> list[RegradeSet]:
    """Parse Corpus.CorpusRegrades - named fusion-knob override bundles rolled
    against every corpus by the regrade sweep. Each set names a Name + any subset
    of the regrade knobs; omitted knobs inherit the regrade defaults. A knob value
    may be a scalar or a list (normalized to a list of sweep values). Unknown
    knobs warn (config keeps working); wrong-typed values error."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"Corpus: CorpusRegrades must be a list of named sets (got {type(raw).__name__}).")
        return []
    sets: list[RegradeSet] = []
    seen: dict[str, int] = {}
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"CorpusRegrades[{i}] must be a mapping with a Name (got {type(entry).__name__}).")
            continue
        name = entry.get("Name")
        if not name or not isinstance(name, str):
            errors.append(f"CorpusRegrades[{i}] has no 'Name' - each regrade set needs a unique Name.")
            continue
        seen[name] = seen.get(name, 0) + 1
        overrides: dict[str, list] = {}
        for key, val in entry.items():
            if key == "Name":
                continue
            if key not in REGRADE_KNOBS:
                warnings.append(f"CorpusRegrades {name!r}: unknown knob {key!r} - ignoring "
                                f"(valid: {sorted(REGRADE_KNOBS)}).")
                continue
            # if key in KNOBS_NEEDING_CAPABILITY:
            #     warnings.append(
            #         f"CorpusRegrades {name!r} sweeps {key!r}, which requires "
            #         f"{KNOBS_NEEDING_CAPABILITY[key]} If the SCC does not implement it, "
            #         f"/configure IGNORES the field and every combo scores identically - an "
            #         f"inert knob that looks exactly like a real ceiling. The regrade will "
            #         f"hard-error unless the SCC advertises {key!r} in GET /stores.")
            want = REGRADE_KNOBS[key]
            vals = val if isinstance(val, list) else [val]
            clean: list = []
            for v in vals:
                if want is float and isinstance(v, int) and not isinstance(v, bool):
                    v = float(v)
                if isinstance(v, bool) or not isinstance(v, want):
                    errors.append(f"CorpusRegrades {name!r}: knob {key!r} wants {want.__name__} values "
                                  f"(got {v!r}).")
                    continue
                clean.append(v)
            if clean:
                overrides[key] = clean
        sets.append(RegradeSet(name=name, overrides=overrides))
    for name, c in seen.items():
        if c > 1:
            errors.append(f"CorpusRegrades: name {name!r} is used {c}x - regrade set names must be unique.")
    return sets


# How a knob NAME maps onto the field seren-corpus-callosum actually reads. ONE
# definition, imported by both the compose emitter (boot config) and regrade_live
# (runtime /configure) -- these two must agree or a corpus boots at one config and
# gets swept at another, and nothing anywhere would say so.
FED_FIELD: dict[str, str] = {
    "rrf_k": "k", "n_results": "n_results", "fetch_multiplier": "fetch_multiplier",
    "authority_margin": "authority_margin", "min_per_store": "min_per_store",
    "fusion_mode": "fusion_mode",
    "hops": "hops", "hop_terms": "hop_terms", "hop_budget": "hop_budget",
}
# Knobs that are PER-STORE overrides on the loci member rather than federation-level.
STORE_FIELD: dict[str, str] = {"loci_weight": "weight", "loci_floor": "floor"}


def _parse_corpus_config(raw, cname: str, errors: list[str], warnings: list[str]) -> dict:
    """Parse a corpus's `Config:` - the knobs it BOOTS at.

    Same knob vocabulary as CorpusRegrades on purpose (REGRADE_KNOBS), because they
    describe the same dials; the difference is only that a regrade set sweeps LISTS
    and this declares a single value each. One vocabulary means a knob you swept and
    liked can be pasted straight in here as its new baseline.

    Why this exists: without it every SCC boots at seren-corpus-callosum's own
    defaults, so /eval/run scores the UNTUNED config no matter what a regrade proved.
    The sweep would say n_results=30 is worth +0.21 coverage and the eval would keep
    measuring 10, with nothing on screen admitting the two disagreed.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors.append(f"Corpus {cname!r}: Config must be a mapping of knob -> value "
                      f"(got {type(raw).__name__}).")
        return {}
    out: dict = {}
    for key, val in raw.items():
        if key not in REGRADE_KNOBS:
            warnings.append(f"Corpus {cname!r}: unknown Config knob {key!r} - ignoring "
                            f"(valid: {sorted(REGRADE_KNOBS)}).")
            continue
        if isinstance(val, list):
            # A LIST here is almost certainly a CorpusRegrades set pasted into Config.
            # Refuse rather than silently taking [0]: a boot config is one value, and
            # guessing which one the operator meant is how you grade a config nobody
            # chose.
            errors.append(f"Corpus {cname!r}: Config knob {key!r} takes a single value, not a "
                          f"list (got {val!r}). Lists belong in CorpusRegrades, which SWEEPS "
                          f"them; Config declares what the corpus BOOTS at.")
            continue
        want = REGRADE_KNOBS[key]
        if want is float and isinstance(val, int) and not isinstance(val, bool):
            val = float(val)
        if isinstance(val, bool) or not isinstance(val, want):
            errors.append(f"Corpus {cname!r}: Config knob {key!r} wants a {want.__name__} "
                          f"(got {val!r}).")
            continue
        out[key] = val
    return out


def compile_topology(probe_config: dict) -> CompiledTopology:
    """Compile + validate a ProbeConfig. Raises TopologyError (all errors at
    once) if the topology can't be trusted; otherwise returns a CompiledTopology
    with .warnings attached."""
    if not isinstance(probe_config, dict):
        raise TopologyError([f"ProbeConfig must be a mapping (got {type(probe_config).__name__})."])
    pc = probe_config.get("ProbeConfig", probe_config)   # accept wrapped or unwrapped

    errors: list[str] = []
    warnings: list[str] = []

    starting_port = pc.get("StartingPort")
    if starting_port is not None and (not isinstance(starting_port, int) or isinstance(starting_port, bool)):
        errors.append(f"StartingPort must be an integer (got {starting_port!r}).")
        starting_port = None

    # ── data plumbing (shared questions + per-kind default seeds) ──
    # These are references (a path or inline YAML); resolution to content is a
    # later pass. Questions are TOP-LEVEL on purpose: every store answers the
    # same set, which is the only way the cross-store comparison stays honest.
    def _opt_str(key: str) -> str | None:
        v = pc.get(key)
        if v is not None and not isinstance(v, str):
            errors.append(f"{key} must be a string (a path or inline YAML), got {type(v).__name__}.")
            return None
        return v
    # DefaultQuestions, not Questions. The old top-level key was a shared CONSTANT --
    # every store answered the identical set, deliberately, so the cross-store comparison
    # stayed apples-to-apples. That invariant is right when you are comparing STORE CONFIGS
    # over one corpus, and wrong the moment the stores hold DIFFERENT CONTENT: asking
    # mem-hermit the tavern questions is not a fair comparison, it is a guaranteed zero.
    #
    # So it becomes a DEFAULT, and the apples-to-apples invariant moves UP a level: it now
    # holds where it actually belongs, at the CORPUS. Two corpora fanning the same stores
    # still answer the same set. A store and a store holding different worlds do not.
    questions_ref = _as_ref(pc.get("DefaultQuestions"), "DefaultQuestions", errors)
    if "Questions" in pc:
        errors.append(
            "top-level `Questions` is now `DefaultQuestions` -- and it is a DEFAULT, not a "
            "shared constant: every Loci / Memory / Corpus node may override it with its own "
            "`Questions:`, which is what lets a multi-brain dataset point the right questions "
            "at the right brain. Rename the key. (Refusing rather than silently accepting both: "
            "a config with BOTH keys has no honest interpretation, and quietly picking one is "
            "how you end up grading a set you did not think you were grading.)")
    versions_raw = pc.get("Versions") or {}
    versions: dict[str, str] = {}
    if isinstance(versions_raw, dict):
        for kind, spec in versions_raw.items():
            k = str(kind).lower()
            if k not in ("loci", "memory", "corpus"):
                warnings.append(f"Versions: unknown kind {kind!r} - ignoring (use loci/memory/corpus).")
                continue
            spec = str(spec).strip()
            if spec and spec[0] not in "=<>!~":
                spec = "==" + spec          # bare '1.4.0' -> '==1.4.0'; lenient parse, Postel
            versions[k] = spec
    elif versions_raw:
        warnings.append("Versions: must be a mapping of kind -> version spec - ignoring.")
    default_loci_seed = _as_ref(pc.get("DefaultLociSeed"), "DefaultLociSeed", errors)
    default_memory_seed = _as_ref(pc.get("DefaultMemorySeed"), "DefaultMemorySeed", errors)
    _corpus_raw = pc.get("Corpus")
    corpus_regrades = _parse_regrades(
        _corpus_raw.get("CorpusRegrades") if isinstance(_corpus_raw, dict) else None,
        errors, warnings)

    # PER-CORPUS CorpusRegrades, keyed by name and parsed with the SAME parser as the
    # top-level one -- one validator, one set of error messages, no chance of the two
    # levels disagreeing about what a valid knob is.
    #
    # THREE STATES, and the key's PRESENCE is what distinguishes two of them:
    #   key absent  -> not in this map at all -> ResolvedCorpus.regrades is None -> INHERIT
    #   key present -> parsed list (possibly empty) -> OWN sets, or [] to OPT OUT
    #
    # Absent and empty have to differ, or there is no way to skip one corpus once a
    # top-level set exists. A sweep is minutes-to-hours per corpus and corpora run
    # serially, so "these three, not all fourteen" is the difference between a coffee
    # and an afternoon. `CorpusRegrades:` with a null value counts as PRESENT (opt out):
    # writing the key at all is a statement about this corpus.
    per_corpus_regrades: dict[str, list] = {}
    per_corpus_config: dict[str, dict] = {}
    if isinstance(_corpus_raw, dict):
        for _cfg in (_corpus_raw.get("CorpusConfigs") or []):
            if not isinstance(_cfg, dict):
                continue
            _nm = _cfg.get("Name")
            if "Config" in _cfg:
                if not _nm:
                    warnings.append("a CorpusConfigs entry has Config but no Name - boot config "
                                    "is matched by name, so this is ignored.")
                else:
                    per_corpus_config[str(_nm)] = _parse_corpus_config(
                        _cfg.get("Config"), str(_nm), errors, warnings)
            if "CorpusRegrades" not in _cfg:
                continue
            if not _nm:
                warnings.append("a CorpusConfigs entry has CorpusRegrades but no Name - "
                                "per-corpus regrades are matched by name, so this is ignored. "
                                "Name the corpus, or move the sets to the top level.")
                continue
            per_corpus_regrades[str(_nm)] = _parse_regrades(
                _cfg.get("CorpusRegrades"), errors, warnings)

    _, loci_p = _parse_section(pc, "Loci",   "LociCount",   "LociConfigs",   errors, warnings)
    _, mem_p  = _parse_section(pc, "Memory", "MemoryCount", "MemoryConfigs", errors, warnings)
    _, corp_p = _parse_section(pc, "Corpus", "CorpusCount", "CorpusConfigs", errors, warnings)

    if errors:   # structural shape is fatal - can't sanely allocate on a broken topology
        raise TopologyError(errors, warnings)

    # ── Pass 3: ports ──────────────────────────────────────────────
    explicit: dict[int, list[str]] = {}
    for p in loci_p + mem_p + corp_p:
        if p.port is not None:
            explicit.setdefault(p.port, []).append(p.name or "<auto>")
    for port, owners in explicit.items():
        if len(owners) > 1:
            errors.append(f"port {port} is claimed by {owners} - each service needs its own port.")

    needs_alloc = [p for p in (loci_p + mem_p + corp_p) if p.port is None]
    if needs_alloc and starting_port is None:
        who = [p.name or "<auto-generated>" for p in needs_alloc]
        errors.append(f"StartingPort is required: these need auto-allocated ports {who}. "
                      f"Set ProbeConfig.StartingPort as the floor to count up from.")
    if starting_port is not None:
        for port, owners in explicit.items():
            if port < starting_port:
                errors.append(f"port {port} (on {owners}) is below StartingPort {starting_port} - "
                              f"explicit ports must sit at or above the floor.")
    if errors:
        raise TopologyError(errors, warnings)

    # auto-cursor: append-only, above every explicit port and >= StartingPort. type-grouped order.
    if needs_alloc:
        claimed = set(explicit.keys())
        cursor = max([starting_port] + [pt + 1 for pt in claimed]) if claimed else starting_port

        def _next() -> int:
            nonlocal cursor
            while cursor in claimed:
                cursor += 1
            got = cursor
            claimed.add(got)
            cursor += 1
            return got
        for p in loci_p + mem_p + corp_p:     # Loci block, then Memory, then Corpus
            if p.port is None:
                p.port = _next()

    # ── Pass 4: names (auto-gen = type-port) + global uniqueness ────
    for short, pend in (("loci", loci_p), ("memory", mem_p), ("corpus", corp_p)):
        for p in pend:
            if p.name is None:
                p.name = f"{short}-{p.port}"   # auto-gen carries no flags

    seen_names: dict[str, list[str]] = {}
    for sect, pend in (("Loci", loci_p), ("Memory", mem_p), ("Corpus", corp_p)):
        for p in pend:
            seen_names.setdefault(p.name, []).append(sect)
    for name, where in seen_names.items():
        if len(where) > 1:
            errors.append(f"name {name!r} is used {len(where)}x (in {where}) - names must be globally unique.")
    if errors:
        raise TopologyError(errors, warnings)

    loci = [ResolvedNode(p.name, KIND["loci"], p.port, p.flags, p.generated, p.seed_ref,
                         p.negative_test, p.live_url, p.questions_ref) for p in loci_p]
    memory = [ResolvedNode(p.name, KIND["memory"], p.port, p.flags, p.generated, p.seed_ref,
                           p.negative_test, p.live_url, p.questions_ref) for p in mem_p]
    name_kind = {n.name: n.kind for n in loci + memory}
    corpus_names = {p.name for p in corp_p}
    negative_names = {n.name for n in loci + memory if n.negative_test}

    # ── Pass 5: corpus wiring ──────────────────────────────────────
    resolved_corpus: list[ResolvedCorpus] = []
    referenced: set[str] = set()
    for p in [c for c in corp_p if not c.generated]:
        rc = ResolvedCorpus(name=p.name, port=p.port, flags=p.flags,
                            questions_ref=p.questions_ref,
                            config=per_corpus_config.get(p.name) or {},
                            # .get() -> None when the key was absent, which is exactly
                            # the INHERIT signal regrade_live.sets_for_corpus looks for.
                            regrades=per_corpus_regrades.get(p.name))
        seen: set[str] = set()
        for j, st in enumerate(p.stores or []):
            if not isinstance(st, dict) or "Store" not in st:
                if isinstance(st, dict) and "Seed" in st:
                    errors.append(f"Corpus {p.name!r}: Stores[{j}] has a 'Seed' but no 'Store' - corpora "
                                  f"fan already-seeded stores, they don't seed. Put Seed on the store's own node.")
                else:
                    errors.append(f"Corpus {p.name!r}: Stores[{j}] needs a 'Store' name (and optional Weight).")
                continue
            sname = st["Store"]
            weight = st.get("Weight", 1.0)
            if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                errors.append(f"Corpus {p.name!r}: store {sname!r} Weight must be a number (got {weight!r}).")
                weight = 1.0
            if sname in corpus_names:
                errors.append(f"Corpus {p.name!r} references {sname!r}, but that's a Corpus - "
                              f"corpora fan Loci/Memory stores, not other corpora.")
                continue
            if sname not in name_kind:
                errors.append(f"Corpus {p.name!r} references store {sname!r}, which isn't a declared "
                              f"Loci or Memory node. Declared: {sorted(name_kind)}.")
                continue
            if sname in seen:
                warnings.append(f"Corpus {p.name!r} references store {sname!r} more than once - de-duping.")
                continue
            seen.add(sname)
            referenced.add(sname)
            rc.stores.append(ResolvedStore(sname, name_kind[sname], float(weight)))
        resolved_corpus.append(rc)

    auto_corpora = [c for c in corp_p if c.generated]
    # negative-test stores stand alone by design - they're evaluated as their own
    # column, never swept into the catch-all (a decoy in the fan would poison it).
    unreferenced = [n for n in name_kind if n not in referenced and n not in negative_names]
    if auto_corpora:
        cat = auto_corpora[0]
        rc = ResolvedCorpus(name=cat.name, port=cat.port, flags=[], generated=True, is_catchall=True)
        for sname in unreferenced:
            rc.stores.append(ResolvedStore(sname, name_kind[sname], 1.0, via_catchall=True))
        resolved_corpus.append(rc)
    else:
        for sname in unreferenced:
            warnings.append(f"store {sname!r} isn't referenced by any corpus and there's no catch-all - "
                            f"it'll spin up and sit out every fan. Reference it or drop it.")
    if errors:
        raise TopologyError(errors, warnings)

    # ── per-corpus regrades that matched no corpus ──────────────────
    # A typo'd Name here fails SILENTLY and in the worst direction: the corpus you meant
    # to scope quietly inherits the top-level sets (or gets skipped entirely), and you
    # get a full result table answering a question you did not ask. Same family as a
    # duplicate YAML key -- the config is valid, it just doesn't say what you think.
    # Name the miss and list what was actually available.
    _corpus_real = {c.name for c in resolved_corpus}
    for _nm in sorted(set(per_corpus_regrades) - _corpus_real):
        warnings.append(
            f"CorpusRegrades declared under corpus {_nm!r}, which isn't a corpus in this "
            f"topology - those sets will never run. Declared corpora: {sorted(_corpus_real)}.")

    # ── landmine lint: identical (store,weight) sets across corpora ─
    sig_map: dict[frozenset, list[str]] = {}
    for c in resolved_corpus:
        if not c.stores:
            warnings.append(f"corpus {c.name!r} has no stores - it'll fan nothing.")
            continue
        sig = frozenset((s.name, s.weight) for s in c.stores)
        sig_map.setdefault(sig, []).append(c.name)
    for sig, names in sig_map.items():
        if len(names) > 1:
            warnings.append(f"corpora {names} fan the IDENTICAL store set - you'd be evaluating one "
                            f"topology under {len(names)} names and trusting the delta. Vary a store or "
                            f"weight, or drop the dupes.")

    # ── negative-test lint ─────────────────────────────────────────
    for n in loci + memory:
        if n.negative_test and not n.seed_ref:
            warnings.append(f"negative-test store {n.name!r} has no decoy Seed - it'll seed empty. Give it "
                            f"an explicit Seed of unrelated data so the 'stayed quiet' test means something.")
    for c in resolved_corpus:
        if c.is_catchall:
            continue
        for s in c.stores:
            if s.name in negative_names:
                warnings.append(f"negative-test store {s.name!r} is fanned by corpus {c.name!r} - its decoy "
                                f"data will poison {c.name!r}'s comparison. Negative stores should stand "
                                f"alone, not feed a measured SCC.")

    return CompiledTopology(loci=loci, memory=memory, corpus=resolved_corpus, warnings=warnings,
                            questions_ref=questions_ref, versions=versions,
                            default_loci_seed=default_loci_seed,
                            default_memory_seed=default_memory_seed, corpus_regrades=corpus_regrades)


def topology_fingerprint(topo: CompiledTopology) -> str:
    """A short, stable hash of structure_signature() - the identity of the FLEET.

    Persisted artifacts (eval results, regrade captures) describe a specific set of
    running containers, and they need a key that changes when that set does.
    project_name does NOT: emit_compose hardcodes "seren-probe-target" for every pod
    ever started, so keying on it discriminates nothing. Load a different config,
    rebuild, reseed -- same name, same key, and last week's numbers rehydrate as if
    they described the new fleet. Nothing errors; the dashboard just quietly answers
    a question about a pod that no longer exists.

    structure_signature is already the right thing: node names, ports, kinds, flags,
    corpus wiring and version pins -- "what has to be TRUE of the running
    containers." This is just that, hashed, so it fits in a JSON field.

    KNOWN GAP, stated rather than papered over: a REBUILT image at the same version
    pin produces an identical signature. Different code, same hash. Catching that
    honestly needs the image ID from `docker inspect` recorded at spin-up; until
    then, a rebuild-in-place is the one change this cannot see.
    """
    import hashlib
    return hashlib.sha256(repr(structure_signature(topo)).encode()).hexdigest()[:16]


def load_probe_config(source) -> CompiledTopology:
    """Load a ProbeConfig from a YAML file PATH or a YAML STRING, then compile it.

    Raises TopologyError on an untrustworthy topology (same contract as
    compile_topology). PyYAML is a hard dep of seren-probe, so it's imported at
    call time only to keep this module import-light for pure-dict callers.
    """
    import yaml
    from pathlib import Path as _Path
    text = source
    try:
        p = _Path(str(source))
        if p.exists():
            text = p.read_text(encoding="utf-8")
    except OSError:
        pass  # not a path (e.g. a long YAML string) - treat source as YAML text
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise TopologyError([f"ProbeConfig YAML must parse to a mapping (got {type(data).__name__})."])
    return compile_topology(data)


def structure_signature(topo: CompiledTopology) -> tuple:
    """What actually has to be TRUE of the running containers.

    Node names, ports, kinds, flags, corpus wiring and version pins are baked into
    the compose file - change any of them and you need a new pod.

    CorpusRegrades are NOT in here, deliberately, and neither are seeds or
    questions: regrades run LIVE against already-running containers (/configure ->
    /search -> grade), and seeds/questions are read at eval time. Forcing a rebuild
    + a full reseed just to change `rrf_k: [30,60]` to `[30,60,100]` costs an hour
    and buys nothing. If the structure matches, the knobs are hot-swapped into the
    running topology instead.
    """
    def node(n):
        return (n.name, n.port, n.kind, tuple(sorted(n.flags)), bool(n.negative_test),
                n.live_url or "")
    return (
        tuple(node(n) for n in topo.loci),
        tuple(node(n) for n in topo.memory),
        tuple((c.name, c.port, tuple(sorted(s.name for s in c.stores)),
               tuple(sorted(c.flags))) for c in topo.corpus),
        tuple(sorted((getattr(topo, "versions", None) or {}).items())),
    )
