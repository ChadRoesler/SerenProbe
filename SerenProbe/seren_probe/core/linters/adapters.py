"""
seren_probe.core.linters.adapters
=================================
The loaders hand back dataclasses (LociItem / MemoryItem / Question); the checks
want dicts. These two normalizers bridge that gap and nothing else - but they carry
one hard-won rule, twice: NEVER silently drop a field the author wrote. `hops` was
dropped on the way to the check that reads it, and it cost a day. Do not let it be
`quiet_in` next.
"""
from __future__ import annotations


def _item_to_dict(it):
    """Normalize a seed item (LociItem / MemoryItem dataclass, or a plain dict)."""
    if isinstance(it, dict):
        return it
    d = {}
    for attr in ("project", "key", "value", "why", "tier", "ref", "topic"):
        if hasattr(it, attr):
            d[attr] = getattr(it, attr)
    if hasattr(it, "text"):           # MemoryItem stores its document as .text
        d["content"] = getattr(it, "text")
    return d


def _question_to_dict(q):
    if isinstance(q, dict):
        return q
    return {
        "asks": getattr(q, "asks", ""),
        "query": getattr(q, "query", ""),
        "expect_key": list(getattr(q, "expect_key", []) or []),
        "expect_ref": list(getattr(q, "expect_ref", []) or []),
        "expect_content": list(getattr(q, "expect_content", []) or []),
        "expect_empty": bool(getattr(q, "expect_empty", False)),
        # needs_hops -> "hops": the DECLARED traversal depth. Dropping it here would
        # re-create the exact bug the reachability check exists to catch -- an annotation
        # the author wrote, silently discarded on the way to the check that reads it.
        "hops": int(getattr(q, "needs_hops", 1) or 1),
        # quiet_in -> the NON-LEAKAGE targets. Dropping it here would re-create the exact
        # class of bug: an annotation the author wrote, silently discarded on the way to
        # the check that reads it. (It was `hops`. Do not let it be `quiet_in`.)
        "quiet_in": list(getattr(q, "quiet_in", []) or []),
    }
