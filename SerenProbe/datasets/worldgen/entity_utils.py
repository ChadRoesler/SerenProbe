#!/usr/bin/env python3
"""
entity_utils.py — Shared entity slug and key helpers for the Pilorus worldgen pipeline.

Every export site (memory export, loci export, question generator) MUST use the
same slug recipe so that expect_key matches the loci ident exactly. Two code paths
slugging the same entity differently = silently unanswerable questions.

Usage:
    from entity_utils import entity_slug, make_loci_key

    slug = entity_slug("The Star of the Fallen", 571)
    # => "star_of_the_fallen_571"

    key, project = make_loci_key("The Star of the Fallen", 571, "material")
    # => key="star_of_the_fallen_571_material", project="star_of_the_fallen_571"
"""

import re
from typing import Tuple


# ── Articles to strip from the start of a name ────────────────────────
ARTICLES = ["the ", "a ", "an "]


def entity_slug(name: str, entity_id: int) -> str:
    """Produce a guaranteed-unique, filesystem-safe slug for an entity.

    Recipe:
      1. Strip leading articles ("The ", "A ", "An ")
      2. Lowercase
      3. Replace all non-[a-z0-9] sequences with a single underscore
      4. Append _{entity_id} for uniqueness

    Examples:
        "The Star of the Fallen", 571  → "star_of_the_fallen_571"
        "Torkazaddwal-hold", 203       → "torkazaddwal_hold_203"
        "Savage", 108                  → "savage_108"
        "Savage", 109                  → "savage_109"   (no collision)
        "Aerdor", 0                    → "aerdor_0"
    """
    if not name:
        return f"entity_{entity_id}"

    # 1. Strip leading article
    lower = name.lower().strip()
    for art in ARTICLES:
        if lower.startswith(art):
            lower = lower[len(art):]
            break

    # 2. Replace non-alphanumeric sequences with underscore
    #    Apostrophes, dashes, spaces, em-dashes all become _
    slug = re.sub(r'[^a-z0-9]+', '_', lower)

    # 3. Strip leading/trailing underscores
    slug = slug.strip('_')

    # 4. Append id
    return f"{slug}_{entity_id}"


def make_loci_key(name: str, entity_id: int, fact_key: str) -> Tuple[str, str, str]:
    """Build a (key, project, ident) triplet for a loci entry.

    Per spec:
        key:      "{project}/{key_name}"   — full path used for matching
        project:  "{slug}"                  — groups all facts for this entity
        ident:    "{key_name}"              — simple key name

    For world-level entities (no numeric id), pass entity_id=0 and the slug
    will be "world" or the world name.
    """
    slug = entity_slug(name, entity_id)
    key_name = f"{slug}_{fact_key}"
    project = slug
    key = f"{project}/{key_name}"
    ident = key_name
    return key, project, ident


# ── ASCII sanitizer — strip or replace non-ASCII characters ───────────

ASCII_REPLACEMENTS = {
    '\u2014': '--',   # em-dash
    '\u2013': '-',    # en-dash
    '\u2018': "'",    # left single quote
    '\u2019': "'",    # right single quote
    '\u201C': '"',    # left double quote
    '\u201D': '"',    # right double quote
    '\u2026': '...',  # ellipsis
    '\u2022': '*',    # bullet
    '\u2025': '..',   # two-dot ellipsis
    '\u2033': '"',    # double prime
    '\u00A0': ' ',    # non-breaking space
}

def asciify(text: str) -> str:
    """Replace non-ASCII characters with ASCII equivalents, or remove them.

    Uses ASCII_REPLACEMENTS dict for known characters; anything else
    outside ASCII range (ord > 127) is replaced with '?'.
    """
    result = []
    for c in text:
        o = ord(c)
        if o < 128:
            result.append(c)
        elif c in ASCII_REPLACEMENTS:
            result.append(ASCII_REPLACEMENTS[c])
        else:
            result.append('?')
    return ''.join(result)


def make_memory_ref(name: str, entity_id: int, tier: str, idx: int) -> str:
    """Build a unique memory reference string.

    Format: "{slug}_{tier}_{idx}"
    """
    slug = entity_slug(name, entity_id)
    return f"{slug}_{tier}_{idx}"
