"""
seren_probe.core.linters.model
==============================
The shared vocabulary of the lint: the document view a store would index, the
report the checks fill, corpus assembly, the hop ceiling, and the precomputed
`Corpus` index the per-question checks read.

`Corpus` is the one thing here that's genuinely new, and it's a refactor artifact
rather than new behaviour: `lint_questions` used to compute these seven indices at
the top of its loop and share them with the tiers via closure capture. With the
tiers extracted into their own functions (see `checks.py`) that shared state has to
be explicit - so it's computed ONCE, here, and handed to each check. Same numbers,
now nameable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .text import _words


@dataclass
class Doc:
    """One searchable unit as a store would see it."""
    ident: str          # loci "project/key", or memory ref
    text: str           # the text a retriever actually matches against
    kind: str           # "loci" | "memory"


@dataclass
class LintReport:
    errors: list = field(default_factory=list)     # unanswerable - fix the dataset
    warnings: list = field(default_factory=list)   # suspicious
    notes: list = field(default_factory=list)      # no-lexical-bridge, informational
    multihop: list = field(default_factory=list)   # (query, expectation, holder) - has a rail
    unbridged: list = field(default_factory=list)  # (query, expectation, holder) - NO rail exists
    ambiguous: list = field(default_factory=list)  # (query, expectation, holder, n_rivals) - cannot be singled out
    unreachable: list = field(default_factory=list)  # (query, needs_hops, max_hops) - depth exceeds any config
    quiet_leaks: list = field(default_factory=list)   # (query, store, expectation) - the DATA leaked, not the store
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        out = [f"question lint: {self.checked} questions checked"]
        for e in self.errors:
            out.append(f"  \u2717 {e}")
        for w in self.warnings:
            out.append(f"  \u26a0 {w}")
        for n in self.notes:
            out.append(f"  \u2139 {n}")
        if not self.errors and not self.warnings:
            out.append("  \u2713 every expectation is reachable in the corpus")
        return "\n".join(out)


def build_docs(loci_items, memory_items) -> list[Doc]:
    """Assemble the corpus exactly as a store would index it.

    Loci match on key + value + why (all three are indexed text).
    Memory matches on content, or intent for the near tier.
    """
    docs: list[Doc] = []
    for f in loci_items or []:
        proj = str(f.get("project", "*"))
        key = str(f.get("key", ""))
        docs.append(Doc(
            ident=f"{proj}/{key}",
            text=f"{key} {f.get('value', '')} {f.get('why', '') or ''}",
            kind="loci"))
    for m in memory_items or []:
        text = m.get("content")
        if text is None:
            text = m.get("intent")        # near tier stores the intent as the document
        docs.append(Doc(
            ident=str(m.get("ref") or ""),
            text=str(text or ""),
            kind="memory"))
    return docs


@dataclass
class Corpus:
    """The corpus indices every per-question check reads, computed ONCE.

    Terms per doc are precomputed because the ambiguity check is O(corpus) per
    expectation and a real corpus is thousands of entries.
    """
    docs: list[Doc]
    loci_idents: set[str]
    mem_refs: set[str]
    haystack: str
    doc_words: list[tuple[Doc, set[str]]]
    by_ident: dict[str, tuple[Doc, set[str]]]
    vocab: set[str]


def build_corpus(loci_items, memory_items) -> Corpus:
    """Index a corpus once for a whole question set's worth of checks."""
    docs = build_docs(loci_items, memory_items)
    loci_idents = {d.ident for d in docs if d.kind == "loci"}
    mem_refs = {d.ident for d in docs if d.kind == "memory" and d.ident}
    haystack = "\n".join(d.text for d in docs).lower()
    doc_words = [(d, _words(d.text)) for d in docs]
    by_ident = {d.ident: (d, w) for d, w in doc_words if d.ident}
    vocab: set[str] = set()
    for _d, _w in doc_words:
        vocab |= _w
    return Corpus(docs=docs, loci_idents=loci_idents, mem_refs=mem_refs, haystack=haystack,
                  doc_words=doc_words, by_ident=by_ident, vocab=vocab)


def max_reachable_hops(topology) -> int:
    """The deepest hop count ANY configuration reachable from this ProbeConfig can run.

    An SCC's hop depth is a /configure knob, and SerenProbe only ever moves it via a
    CorpusRegrades set. So the ceiling is the largest `hops` value swept by any set --
    or 1, the SCC's shipped default, if nothing sweeps it at all.

    This is deliberately computed from the CONFIG, not from the running SCC. The lint
    layer is PURE (tests/test_layering.py forbids httpx here) and, more usefully, it
    means the check fires at UPLOAD time -- before a container exists, before an hour
    of seeding, before a dashboard full of mystery zeros.
    """
    deepest = 1
    for rs in (getattr(topology, "corpus_regrades", None) or []):
        for v in (rs.overrides.get("hops") or []):
            try:
                deepest = max(deepest, int(v))
            except (TypeError, ValueError):
                continue
    return deepest
