"""
seren_probe.seed_dataset — uploaded seed pools + eval questions, mapped onto a
live (compiled + spun-up) topology.

This is what dissolves the hardcoded dataset.py corpus: the user uploads POOLS
of content and a store-map, and the eval seeds them into the real containers.

SHAPE (Both, per Chad's calls):
  pools:      named lists of items ("facts", "episodes", ...)
  default:    { loci: <pool>, memory: <pool> }   — seed EVERY loci/memory from these
  overrides:  { <resolved-store-name>: <pool> }  — per-store escape hatch

Item shapes are honest to the REAL write contracts (read off disk):
  Loci   item = {project?, key, value, why?}     — FactWrite; NO id (store mints it),
                the (project,key) IS the address for scoring.
  Memory item = {content|intent, topic?, tier?, ref?}
                tier in {short, near, long} (default short). near uses `intent`.
                `ref` is an UPLOADER-controlled handle; the seeder maps it to the
                store-minted id at seed time (the uploader can't know the id up front).

QUESTIONS (Both scoring):
  { asks: loci|memory|corpus, query, expect_key?[], expect_ref?[], expect_content?[] }
  Scoring uses whichever is present; expect_content (substring) is the universal
  fallback and matches what live_eval already does.

Validation is compassion-first, same as the topology compiler: collect ALL
problems, hard-stop where trust breaks (unknown store/pool, missing key/content),
warn where recoverable (tier on a loci item, content on a near item, unused pool).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Import the compiled-topology types so we can validate a dataset AGAINST a real
# spun-up topology (override keys must be resolved store names, etc).
from .topology import CompiledTopology

VALID_TIERS = {"short", "near", "long"}
VALID_ASKS = {"loci", "memory", "corpus"}


class SeedError(Exception):
    """Untrustworthy seed dataset / questions. Carries ALL errors + warnings."""
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
class SeedDataset:
    pools: dict[str, list[dict]]        # raw pool items, kept for per-kind interpretation
    default: dict[str, str]             # {loci: pool, memory: pool}
    overrides: dict[str, str]           # {resolved-store-name: pool}
    warnings: list[str] = field(default_factory=list)

    def pool_for(self, store_name: str, kind: str) -> str | None:
        """Which pool feeds this store? override wins, else the kind default."""
        if store_name in self.overrides:
            return self.overrides[store_name]
        return self.default.get("loci" if kind == "seren_loci" else "memory")


@dataclass
class Question:
    asks: str
    query: str
    expect_key: list[str] = field(default_factory=list)      # "project/key"
    expect_ref: list[str] = field(default_factory=list)      # uploader refs
    expect_content: list[str] = field(default_factory=list)  # substrings


def _as_loci_item(d: dict, pool: str, i: int, errors: list[str], warnings: list[str]) -> LociItem | None:
    if not isinstance(d, dict):
        errors.append(f"pool {pool!r}[{i}]: loci item must be a mapping, got {type(d).__name__}.")
        return None
    if "key" not in d or "value" not in d:
        errors.append(f"pool {pool!r}[{i}]: loci item needs 'key' and 'value' "
                      f"(FactWrite shape: project?, key, value, why?).")
        return None
    if "tier" in d:
        warnings.append(f"pool {pool!r}[{i}]: 'tier' is meaningless for a loci item — ignoring "
                        f"(tiers are a Memory concept).")
    return LociItem(project=str(d.get("project", "*")), key=str(d["key"]),
                    value=str(d["value"]), why=(str(d["why"]) if d.get("why") is not None else None))


def _as_memory_item(d: dict, pool: str, i: int, errors: list[str], warnings: list[str]) -> MemoryItem | None:
    if not isinstance(d, dict):
        errors.append(f"pool {pool!r}[{i}]: memory item must be a mapping, got {type(d).__name__}.")
        return None
    tier = d.get("tier", "short")
    if tier not in VALID_TIERS:
        errors.append(f"pool {pool!r}[{i}]: tier {tier!r} invalid (valid: {sorted(VALID_TIERS)}).")
        return None
    if tier == "near":
        text = d.get("intent")
        if text is None and d.get("content") is not None:
            warnings.append(f"pool {pool!r}[{i}]: near-tier item has 'content' but should use 'intent' "
                            f"— treating content as intent.")
            text = d.get("content")
        if text is None:
            errors.append(f"pool {pool!r}[{i}]: near-tier item needs 'intent' text.")
            return None
    else:
        text = d.get("content")
        if text is None:
            errors.append(f"pool {pool!r}[{i}]: {tier}-tier item needs 'content'.")
            return None
    return MemoryItem(tier=tier, text=str(text),
                      topic=(str(d["topic"]) if d.get("topic") is not None else None),
                      ref=(str(d["ref"]) if d.get("ref") is not None else None))


def load_seed_dataset(source, topology: CompiledTopology) -> SeedDataset:
    """Parse + validate a seed dataset (YAML/JSON path, YAML/JSON string, or dict)
    AGAINST a compiled topology. Raises SeedError (all problems at once) on
    anything that would seed the wrong thing silently."""
    data = _load_any(source)
    if not isinstance(data, dict):
        raise SeedError([f"seed dataset must parse to a mapping (got {type(data).__name__})."])

    errors: list[str] = []
    warnings: list[str] = []

    pools = data.get("pools")
    if not isinstance(pools, dict) or not pools:
        raise SeedError(["seed dataset needs a non-empty 'pools' mapping "
                         "(e.g. pools: {facts: [...], episodes: [...]})."])

    default = data.get("default") or {}
    overrides = data.get("overrides") or {}
    if not isinstance(default, dict):
        errors.append(f"'default' must be a mapping {{loci: pool, memory: pool}} (got {type(default).__name__}).")
        default = {}
    if not isinstance(overrides, dict):
        errors.append(f"'overrides' must be a mapping {{store-name: pool}} (got {type(overrides).__name__}).")
        overrides = {}

    # topology facts
    loci_names = {n.name for n in topology.loci}
    mem_names = {n.name for n in topology.memory}
    store_names = loci_names | mem_names
    kind_of = {n.name: n.kind for n in topology.loci + topology.memory}

    # default pools must exist, and a loci/memory default is required if that kind exists
    if topology.loci and "loci" not in default:
        errors.append("topology has Loci stores but 'default.loci' pool is missing — "
                      "every Loci needs a default pool (or a per-store override).")
    if topology.memory and "memory" not in default:
        errors.append("topology has Memory stores but 'default.memory' pool is missing.")
    for slot, pool in default.items():
        if slot not in ("loci", "memory"):
            warnings.append(f"default.{slot}: only 'loci' and 'memory' slots are used — ignoring.")
        elif pool not in pools:
            errors.append(f"default.{slot} references pool {pool!r}, which isn't defined in 'pools'. "
                          f"Defined: {sorted(pools)}.")

    # override keys must be REAL resolved store names; pool must exist
    for sname, pool in overrides.items():
        if sname not in store_names:
            errors.append(f"override {sname!r} isn't a store in this topology. "
                          f"Live stores: {sorted(store_names)}.")
        if pool not in pools:
            errors.append(f"override {sname!r} references pool {pool!r}, not in 'pools'. "
                          f"Defined: {sorted(pools)}.")

    # validate every pool's items against BOTH interpretations it might be used as,
    # based on which kind(s) of store actually draw from it.
    used_by_loci: set[str] = set()
    used_by_mem: set[str] = set()
    for n in topology.loci:
        p = overrides.get(n.name, default.get("loci"))
        if p:
            used_by_loci.add(p)
    for n in topology.memory:
        p = overrides.get(n.name, default.get("memory"))
        if p:
            used_by_mem.add(p)

    for pname, items in pools.items():
        if not isinstance(items, list):
            errors.append(f"pool {pname!r} must be a list of items (got {type(items).__name__}).")
            continue
        if pname not in used_by_loci and pname not in used_by_mem:
            warnings.append(f"pool {pname!r} is defined but no store draws from it — it won't be seeded.")
        for i, it in enumerate(items):
            if pname in used_by_loci:
                _as_loci_item(it, pname, i, errors, warnings)
            if pname in used_by_mem:
                _as_memory_item(it, pname, i, errors, warnings)

    if errors:
        raise SeedError(errors, warnings)
    return SeedDataset(pools=pools, default=default, overrides=overrides, warnings=warnings)


def load_questions(source) -> list[Question]:
    """Parse + validate eval questions. Raises SeedError on anything unscoreable."""
    data = _load_any(source)
    if isinstance(data, dict):
        data = data.get("questions", data)
    if not isinstance(data, list):
        raise SeedError([f"questions must be a list (or {{questions: [...]}}), got {type(data).__name__}."])

    errors: list[str] = []
    warnings: list[str] = []
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
        for label, val in (("expect_key", ek), ("expect_ref", er), ("expect_content", ec)):
            if not isinstance(val, list):
                errors.append(f"questions[{i}]: {label} must be a list (got {type(val).__name__}).")
        if not (ek or er or ec):
            errors.append(f"questions[{i}]: needs at least one of expect_key / expect_ref / expect_content "
                          f"— otherwise there's no way to score it.")
        if asks == "loci" and er:
            warnings.append(f"questions[{i}]: expect_ref on a loci question is unusual — Loci scores by "
                            f"(project/key). Use expect_key. (Kept, but it likely won't match.)")
        out.append(Question(asks=asks or "", query=str(q.get("query", "")),
                            expect_key=list(ek), expect_ref=list(er), expect_content=list(ec)))
    if errors:
        raise SeedError(errors, warnings)
    return out


# ── the seeder: pools -> live stores, capturing ref -> minted id ─────────
@dataclass
class SeedResult:
    loci_counts: dict[str, int]        # store -> facts written
    memory_counts: dict[str, int]      # store -> entries written
    ref_to_id: dict[str, str]          # uploader ref -> store-minted id (Memory)
    key_index: dict[str, list[str]]    # "project/key" -> [stores that hold it]


def seed_stores(topology: CompiledTopology, ds: SeedDataset, url_of: dict[str, str],
                post, delete=None) -> SeedResult:
    """Seed each live store from its assigned pool. `url_of` maps store name ->
    base URL (host-published, e.g. http://127.0.0.1:7421). `post(url, path, body)`
    is the HTTP POST helper; `delete(url, path)` the DELETE helper (both injected
    so this is testable without a live stack). Long-tier does promote-then-DELETE
    to keep the short pool honest; if `delete` is None the short copy is left (a
    minor, disclosed degradation, not a failure).

    Captures Memory `ref -> minted id` so the eval can resolve expect_ref, and a
    (project/key) -> stores index so it can resolve expect_key. Loci needs no id
    map: its (project,key) IS the address.
    """
    res = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={})

    for n in topology.loci:
        pool = ds.pool_for(n.name, n.kind)
        items = ds.pools.get(pool, []) if pool else []
        url = url_of[n.name]
        c = 0
        for raw in items:
            it = _as_loci_item(raw, pool, 0, [], [])   # already validated; re-shape
            if it is None:
                continue
            post(url, "/fact", {"project": it.project, "key": it.key,
                                "value": it.value, "why": it.why})
            res.key_index.setdefault(f"{it.project}/{it.key}", []).append(n.name)
            c += 1
        res.loci_counts[n.name] = c

    for n in topology.memory:
        pool = ds.pool_for(n.name, n.kind)
        items = ds.pools.get(pool, []) if pool else []
        url = url_of[n.name]
        c = 0
        for raw in items:
            it = _as_memory_item(raw, pool, 0, [], [])
            if it is None:
                continue
            if it.tier == "near":
                body = {"intent": it.text}
                if it.topic:
                    body["topic"] = it.topic
                resp = post(url, "/near", body)
            else:
                body = {"content": it.text}
                if it.topic:
                    body["topic"] = it.topic
                resp = post(url, "/short", body)
                if it.tier == "long":
                    sid = (resp or {}).get("id", "")
                    if sid:
                        post(url, f"/short/{sid}/promote", {})
                        if delete is not None:
                            delete(url, f"/short/{sid}")  # clean the short copy (promote-then-clean)
            minted = (resp or {}).get("id", "")
            if it.ref and minted:
                # ref is store-scoped in practice; key by "store:ref" to avoid collisions
                res.ref_to_id[f"{n.name}:{it.ref}"] = minted
                res.ref_to_id.setdefault(it.ref, minted)  # convenience last-wins
            c += 1
        res.memory_counts[n.name] = c

    return res


# ── loaders ─────────────────────────────────────────────────────────────
def _load_any(source):
    """dict passthrough; else YAML/JSON from a path or a string."""
    if isinstance(source, (dict, list)):
        return source
    import yaml
    from pathlib import Path as _Path
    text = source
    try:
        p = _Path(str(source))
        if p.exists():
            text = p.read_text(encoding="utf-8")
    except OSError:
        pass
    return yaml.safe_load(text)   # YAML is a JSON superset — parses both
