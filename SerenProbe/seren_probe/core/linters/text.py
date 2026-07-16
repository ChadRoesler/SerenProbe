"""
seren_probe.core.linters.text
==============================
The tokenization primitives every reachability check shares: the stopword set,
the word-splitter, and the per-scope dedup key. Pure, tiny, and stable - nothing
here has changed since the mycelium night; it just used to live at the top of a
700-line file where you couldn't see it.
"""
from __future__ import annotations

import re

# The per-scope dedup key. lint_plan labels a finding "questions[i]", where i is the
# index within THAT SCOPE's list -- and the same question sits at a different index in
# char_thorn-mem, char_thorn-scc-v, and cross-everything. So the index made every message
# scope-unique and the dedup never fired: one finding, reported five times, and an error
# count that looks like a catastrophe when it is one bug wearing five hats.
#
# (The index is still worth PRINTING -- it is how you find the thing in the file. It just
# must not be part of what counts as "the same finding".)
_QIDX = re.compile(r"questions\[\d+\]")

# Words that carry no retrieval signal -- a query and a doc "sharing" these
# doesn't mean the doc is reachable from the query.
_STOP = {
    "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
    "is", "are", "was", "were", "be", "been", "the", "a", "an", "of", "for",
    "and", "or", "to", "do", "does", "did", "it", "its", "in", "on", "at",
    "with", "from", "by", "give", "me", "us", "brief", "briefing", "dossier",
    "tell", "about", "full", "all", "any", "some", "that", "this", "there",
    "have", "has", "had", "can", "could", "would", "should", "get", "got",
    "use", "used", "uses", "need", "needs", "happened", "went", "covered",
    "cover", "covers", "involve", "involves",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-\.]*", (text or "").lower())
            if w not in _STOP and len(w) > 2}
