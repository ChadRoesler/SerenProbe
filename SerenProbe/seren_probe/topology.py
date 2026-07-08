"""
seren_probe.topology — the ProbeConfig topology compiler.

Turns a declared ProbeConfig (X Loci, Y Memory, Z Corpus; named instances with
flags/ports and explicit corpus->store wiring) into a resolved, validated
CompiledTopology the compose emitter can consume.

The compiler IS the landmine guard: two "different" corpora that secretly fan
the same store set is the declarative version of the old "both SCCs on one
Loci" silent-corruption bug. We catch it at config time, before Docker breathes
— hard-stop where trust breaks, warn where it's recoverable, and every message
names the node, the rule, and the fix.

Passes:
  1. section bounds   Count >= 1;  Count-1 <= len(Configs) <= Count        [hard]
  2. flags            type-scoped; unknown -> warn + ignore
  3. ports            explicit honored; dupes / below-floor -> hard;
                      StartingPort required iff any port is omitted/auto;
                      auto-cursor = max(StartingPort, max(explicit)+1),
                      append-only, type-grouped assignment order
  4. names            auto-gen = "type-port"; global uniqueness             [hard]
  5. wiring + lint    every Store ref resolves [hard]; one catch-all sweeps
                      unreferenced; identical store-sets -> warn (the landmine)
"""
from __future__ import annotations

from dataclasses import dataclass, field

VALID_FLAGS: dict[str, set[str]] = {
    "loci":   {"vector", "mcp"},
    "memory": {"mcp"},
    "corpus": {"mcp"},
}
KIND = {"loci": "seren_loci", "memory": "seren_memory"}


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


@dataclass
class CompiledTopology:
    loci: list[ResolvedNode]
    memory: list[ResolvedNode]
    corpus: list[ResolvedCorpus]
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        out: list[str] = []
        for n in self.loci + self.memory:
            tag = " (auto)" if n.generated else ""
            fl = f" [{','.join(n.flags)}]" if n.flags else ""
            out.append(f"  {n.name:34s} :{n.port}  {n.kind}{fl}{tag}")
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
                                f"(valid: {sorted(VALID_FLAGS[short])}) — ignoring it on {name!r}.")
        pend.append(_Pending(name=name, port=port, flags=flags, generated=False,
                             stores=entry.get("Stores") if section == "Corpus" else None, idx=i))

    # Pass 1: bounds  Count-1 <= len <= Count
    if count is not None:
        n = len(pend)
        lo, hi = count - 1, count
        if not (lo <= n <= hi):
            need = lo - n if n < lo else n - hi
            verb = "Add" if n < lo else "Remove"
            errors.append(f"{section}: {cfg_key} has {n} entries but {count_key} is {count} — "
                          f"expected {lo} or {hi} (Count-1 .. Count). {verb} {need}, or adjust {count_key}.")
        else:
            for _ in range(count - n):   # 0 or 1 auto-generated remainder
                pend.append(_Pending(name=None, port=None, flags=[], generated=True,
                                     stores=None, idx=len(pend)))
    return count, pend


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

    _, loci_p = _parse_section(pc, "Loci",   "LociCount",   "LociConfigs",   errors, warnings)
    _, mem_p  = _parse_section(pc, "Memory", "MemoryCount", "MemoryConfigs", errors, warnings)
    _, corp_p = _parse_section(pc, "Corpus", "CorpusCount", "CorpusConfigs", errors, warnings)

    if errors:   # structural shape is fatal — can't sanely allocate on a broken topology
        raise TopologyError(errors, warnings)

    # ── Pass 3: ports ──────────────────────────────────────────────
    explicit: dict[int, list[str]] = {}
    for p in loci_p + mem_p + corp_p:
        if p.port is not None:
            explicit.setdefault(p.port, []).append(p.name or "<auto>")
    for port, owners in explicit.items():
        if len(owners) > 1:
            errors.append(f"port {port} is claimed by {owners} — each service needs its own port.")

    needs_alloc = [p for p in (loci_p + mem_p + corp_p) if p.port is None]
    if needs_alloc and starting_port is None:
        who = [p.name or "<auto-generated>" for p in needs_alloc]
        errors.append(f"StartingPort is required: these need auto-allocated ports {who}. "
                      f"Set ProbeConfig.StartingPort as the floor to count up from.")
    if starting_port is not None:
        for port, owners in explicit.items():
            if port < starting_port:
                errors.append(f"port {port} (on {owners}) is below StartingPort {starting_port} — "
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
            errors.append(f"name {name!r} is used {len(where)}x (in {where}) — names must be globally unique.")
    if errors:
        raise TopologyError(errors, warnings)

    loci = [ResolvedNode(p.name, KIND["loci"], p.port, p.flags, p.generated) for p in loci_p]
    memory = [ResolvedNode(p.name, KIND["memory"], p.port, p.flags, p.generated) for p in mem_p]
    name_kind = {n.name: n.kind for n in loci + memory}
    corpus_names = {p.name for p in corp_p}

    # ── Pass 5: corpus wiring ──────────────────────────────────────
    resolved_corpus: list[ResolvedCorpus] = []
    referenced: set[str] = set()
    for p in [c for c in corp_p if not c.generated]:
        rc = ResolvedCorpus(name=p.name, port=p.port, flags=p.flags)
        seen: set[str] = set()
        for j, st in enumerate(p.stores or []):
            if not isinstance(st, dict) or "Store" not in st:
                errors.append(f"Corpus {p.name!r}: Stores[{j}] needs a 'Store' name (and optional Weight).")
                continue
            sname = st["Store"]
            weight = st.get("Weight", 1.0)
            if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                errors.append(f"Corpus {p.name!r}: store {sname!r} Weight must be a number (got {weight!r}).")
                weight = 1.0
            if sname in corpus_names:
                errors.append(f"Corpus {p.name!r} references {sname!r}, but that's a Corpus — "
                              f"corpora fan Loci/Memory stores, not other corpora.")
                continue
            if sname not in name_kind:
                errors.append(f"Corpus {p.name!r} references store {sname!r}, which isn't a declared "
                              f"Loci or Memory node. Declared: {sorted(name_kind)}.")
                continue
            if sname in seen:
                warnings.append(f"Corpus {p.name!r} references store {sname!r} more than once — de-duping.")
                continue
            seen.add(sname)
            referenced.add(sname)
            rc.stores.append(ResolvedStore(sname, name_kind[sname], float(weight)))
        resolved_corpus.append(rc)

    auto_corpora = [c for c in corp_p if c.generated]
    unreferenced = [n for n in name_kind if n not in referenced]
    if auto_corpora:
        cat = auto_corpora[0]
        rc = ResolvedCorpus(name=cat.name, port=cat.port, flags=[], generated=True, is_catchall=True)
        for sname in unreferenced:
            rc.stores.append(ResolvedStore(sname, name_kind[sname], 1.0, via_catchall=True))
        resolved_corpus.append(rc)
    else:
        for sname in unreferenced:
            warnings.append(f"store {sname!r} isn't referenced by any corpus and there's no catch-all — "
                            f"it'll spin up and sit out every fan. Reference it or drop it.")
    if errors:
        raise TopologyError(errors, warnings)

    # ── landmine lint: identical (store,weight) sets across corpora ─
    sig_map: dict[frozenset, list[str]] = {}
    for c in resolved_corpus:
        if not c.stores:
            warnings.append(f"corpus {c.name!r} has no stores — it'll fan nothing.")
            continue
        sig = frozenset((s.name, s.weight) for s in c.stores)
        sig_map.setdefault(sig, []).append(c.name)
    for sig, names in sig_map.items():
        if len(names) > 1:
            warnings.append(f"corpora {names} fan the IDENTICAL store set — you'd be evaluating one "
                            f"topology under {len(names)} names and trusting the delta. Vary a store or "
                            f"weight, or drop the dupes.")

    return CompiledTopology(loci=loci, memory=memory, corpus=resolved_corpus, warnings=warnings)


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
