#!/usr/bin/env python3
"""
question_anchor.py - content anchoring for SerenProbe memory questions.

WHY THIS FILE EXISTS
────────────────────
A memory question scores if, and only if, its query carries a term that the
INTENDED document carries and no other document does. SerenProbe's own linter
says exactly this when it rejects a question:

    "The expectation exists and is reachable, but the query cannot SINGLE IT
     OUT... Add a term that only the intended document carries."

This module finds that term (`extract_anchor`) and puts it in the question
(`phrase_memory_question`). That is the whole job.

WHAT IT REPLACES, AND WHY
─────────────────────────
The predecessor lived inside question_gen.py as a bank of ~10 regexes that
tried to TRANSLATE an anchor into a natural question -- "lost my favorite toy"
became "What did X lose in Year N?". Two things were wrong with it:

  1. It almost never matched. On the real Pilorus corpus it fired ZERO times
     across 18 memories; every one fell through to "What happened to X in
     Year N?".
  2. When it DID match, it threw the anchor's words away -- deleting the exact
     tokens the embedder needed. The generator computed the discriminating
     term and then discarded it one line before use.

The result was three near-identical queries per character differing only by a
year, and a year is the single least visible thing in the corpus: every row
starts with one. A long-tier search for "Year 409" returned five memories
inside a 0.008 score band. The store was fine. The questions were not.

The fix is not a better translator. It is to CARRY the phrase verbatim.
Carrying it is not inelegant -- it is the entire mechanism.
"""

import re
from typing import List, Optional


# ── Words that can never discriminate ─────────────────────────────────
# Function words plus the vocabulary of the year/era boilerplate that EVERY
# procedural memory carries. A term present in all 43 rows singles out nothing.
ANCHOR_STOP = {
    "i", "my", "me", "we", "our", "us", "they", "their", "them",
    "he", "she", "his", "her", "it", "its", "you", "your",
    "the", "a", "an", "this", "that", "these", "those",
    "and", "or", "but", "so", "then", "as", "if", "than",
    "in", "on", "at", "for", "of", "to", "by", "with", "from", "into",
    "under", "over", "among", "beneath", "through", "about", "after", "before",
    "was", "were", "is", "are", "be", "been", "being", "am",
    "had", "have", "has", "did", "do", "does", "will", "would", "can", "could",
    "when", "what", "which", "who", "where", "how", "why",
    "year", "years", "old", "time", "times", "season", "day", "days",
    "there", "too", "most", "much", "some", "all", "no", "not", "only",
    "very", "more", "less", "own", "one", "two", "back", "still", "just",
    "between", "against", "during", "within", "toward", "towards",
}

# Verbs/pronouns that read badly immediately after the carrier's "about".
# Used as a RANKING penalty only -- never a rejection. See extract_anchor.
AWKWARD_OPENERS = {
    "want", "wants", "must", "will", "would", "should", "intend", "intends",
    "plan", "plans", "hope", "hopes", "dream", "dreams", "wish", "wishes",
    "myself", "remember", "remembers", "recall", "feel", "feels", "felt",
    "spent", "recovered", "studied", "wonder", "wonders", "knew", "know",
}


def strip_boilerplate(text: str) -> str:
    """Drop the year/era prefix and the childhood framing.

    Boilerplate is non-discriminating BY DEFINITION: if every document opens
    with "Year N (Era, year M):", those tokens cannot single one out. Stripping
    before anchoring is what stops the extractor from proudly returning a
    phrase that is present in all 43 rows.

    Three prefix shapes exist across the generators and all three must go:
      character/OOI  "Year 483 (The Marble Times, year 50): "
      world event    "8 (The Frost Era): "        <- no 'Year' keyword
      OOI decade     "The 181s (The Golden Times, year 58) "
    """
    t = re.sub(r'^Year \d+\s*(\([^)]*\))?:?\s*', '', text or "")
    t = re.sub(r'^\d+\s*(\([^)]*\))?:?\s*', '', t)
    t = re.sub(r'^When I was \d+ years? old[,\s]+', '', t)
    return t


def _sentences(text: str) -> List[str]:
    """Split on sentence enders AND on the ' -- ' clause break the memory
    generators emit. A gram that spans either boundary reads as garbage inside
    a question ("built my legacy. What") and matches nothing."""
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+|\s+--\s+', text or "") if s.strip()]


def _grams(text: str, n_min: int = 2, n_max: int = 6) -> set:
    """Every n-gram that stays inside one sentence and carries no digits.

    Digit-bearing grams are excluded outright. A year is the most common thing
    a procedural memory contains and the least visible thing to an embedder;
    an anchor built on one is an anchor built on nothing.
    """
    out: set = set()
    for sent in _sentences(text):
        w = [tok.strip('.,!?;:"\'()') for tok in sent.split()]
        w = [tok for tok in w if tok]
        for n in range(n_max, n_min - 1, -1):
            for i in range(len(w) - n + 1):
                g = w[i:i + n]
                if any(any(c.isdigit() for c in tok) for tok in g):
                    continue
                out.add(" ".join(g))
    return out


def content_words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z\-']*", (text or "").lower())
            if w not in ANCHOR_STOP and len(w) > 2]


def extract_anchor(target_text: str, rival_texts: List[str],
                   min_rare: int = 2, min_content: int = 3) -> Optional[str]:
    """The distinctive phrase, or None if this document has no claim to one.

    THE RULE -- the linter's rule, moved upstream from lint time to generation
    time: the anchor must contain at least `min_rare` content words appearing
    in NO other document in this store.

    Uniqueness of the PHRASE is not enough, and this is the subtle part.
    "often think about the shadows" and "often think about the lessons" are
    both unique 5-grams and are semantic twins; an embedder cannot separate
    them, so the question scores zero while the store works perfectly. Rarity
    of the WORDS is what actually discriminates.

    RETURNING None IS A FEATURE. A memory with no discriminating term must not
    be questioned. The Pilorus corpus contains five memories that all say
    "a great battle was fought -- Rielven against Ranbalddore"; no query can
    single one of those out, because the defect is in the CORPUS, not the
    question. Emitting a question anyway produces a guaranteed zero that reads
    on the dashboard exactly like a dead store. Silence is the correct output.
    """
    tgt = strip_boilerplate(target_text)
    if len(tgt.split()) < 3:
        return None

    rival_grams: set = set()
    rival_vocab: set = set()
    rival_doc_vocabs: List[set] = []
    for r in rival_texts:
        rc = strip_boilerplate(r)
        rival_grams |= _grams(rc)
        dv = set(content_words(rc))
        rival_doc_vocabs.append(dv)
        rival_vocab |= dv

    def _rank(g: str, cw: List[str], rare_n: int) -> float:
        # Rank: rarity first, then content density, then prefer a TIGHTER
        # phrase so the question stays readable.
        score = rare_n * 100 + len(cw) * 10 - len(g.split())
        # Readability is a TIE-BREAK, never a veto. An anchor opening on a bare
        # verb reads awkwardly after "about", so we dock it -- enough to lose to
        # an equally-rare rival, never enough to lose to a less discriminating
        # one. A question that scores and reads awkwardly beats a question that
        # reads well and scores zero.
        return score - (15 if cw and cw[0] in AWKWARD_OPENERS else 0)

    candidates = []
    for g in _grams(tgt):
        if g in rival_grams:
            continue
        cw = content_words(g)
        # MINIMUM SUBSTANCE. Two rare words is not automatically a usable
        # anchor: "ways died" and "gates were closed" both cleared min_rare and
        # both came back AMBIGUOUS from the linter at query-term overlap 1. A
        # two-word fragment carries too little signal to out-rank 16 siblings
        # that share the surrounding boilerplate. Demand a real phrase.
        if len(cw) < min_content:
            continue
        candidates.append((g, cw))

    # ── PASS 1: rare-token anchors (strongest) ────────────────────────
    best, best_score = None, -1.0
    for g, cw in candidates:
        rare_n = sum(1 for w in cw if w not in rival_vocab)
        if rare_n < min_rare:
            continue
        s = _rank(g, cw, rare_n)
        if s > best_score:
            best, best_score = g, s
    if best:
        return best

    # ── PASS 2: unique-CONJUNCTION anchors ────────────────────────────
    #
    # DISCRIMINABILITY LIVES IN THE CONJUNCTION, NOT THE TOKEN.
    #
    # Pass 1 asks "is any single word unique to this document". That is the
    # right question for prose memories and the WRONG question for a
    # combinatorial corpus. The world log is ten civilization names recombined
    # across 55 rows -- "Battle between Aeanoran and Windoreford",
    # "Battle between Rakmiz and Turdurnaz". Every token recurs, so pass 1
    # finds nothing and skips, and 45 of 55 world memories produced no question
    # at all. Yet "Aeanoran AND Windoreford AND battle" occurs exactly once.
    # The set is unique even though no member of it is.
    #
    # So: accept a gram when NO SINGLE rival document contains all of its
    # content words. The conjunction is then a term only the intended document
    # carries, which is precisely what the linter asks for -- it just happens
    # to be spelled across three words instead of one.
    #
    # This is strictly weaker than pass 1, which is why it only runs second.
    for g, cw in candidates:
        cws = set(cw)
        if any(cws <= dv for dv in rival_doc_vocabs):
            continue
        # SUBSTANCE, NOT JUST NOVELTY. "Even now I look" is a unique conjunction
        # of {even, now, look} and is worth nothing: those are three of the most
        # common words in English, so the phrase is rare as a SEQUENCE and
        # carries no retrievable signal. Shipped as-is it dropped memory hit_rate
        # across every store in the topology (0.3-0.9 -> 0.2-0.6), because the
        # linter is lexical and scores it as discriminating while the embedder
        # cannot see it at all.
        #
        # Require at least two words with real specificity -- long or
        # capitalised. Proper nouns and domain vocabulary qualify; function-word
        # salad does not. This is a heuristic and it is deliberately blunt: a
        # skipped question costs one row, a semantically-empty one costs a
        # misleading score.
        substantive = sum(1 for w in cw if len(w) >= 6) + sum(
            1 for tok in g.split() if tok[:1].isupper())
        if substantive < 2:
            continue
        s = _rank(g, cw, 1)   # rare_n=1: a unique conjunction, not a unique word
        if s > best_score:
            best, best_score = g, s
    return best


# ══════════════════════════════════════════════════════════════════════
#  CARRIER TEMPLATES
# ══════════════════════════════════════════════════════════════════════
#
# One carrier per memory flavour. The anchor is carried VERBATIM.
#
# The carrier is chosen off the STRUCTURED topic tag that
# memory_to_seren.derive_topics() writes from the generator's own item['type'].
# That is structure, not prose inference, so the walk-not-infer contract holds:
# we never read the memory text to decide what KIND of question to ask, only to
# find which words are rare.

CARRIERS = {
    "life_event":       "What does {name} remember about {anchor}?",
    "seasonal_event":   "What news reached {name} about {anchor}?",
    "seasonal_thought": "What has {name} been thinking about {anchor}?",
    "future_plan":      "What does {name} intend to do about {anchor}?",
    "future_worry":     "What does {name} worry about regarding {anchor}?",
    # OOI / POI flavours
    "creation":         "What is recorded about {name} concerning {anchor}?",
    "attack":           "What is recorded about {name} concerning {anchor}?",
    "sighting":         "What was seen of {name} concerning {anchor}?",
}

TIER_FALLBACK = {
    "long":  "What does {name} remember about {anchor}?",
    "short": "What has {name} heard recently about {anchor}?",
    "near":  "What does {name} intend to do about {anchor}?",
}

# Leading modals that add nothing and read badly. Stripped from the anchor
# AFTER selection, so they never cost us a discriminating term -- they carry
# none themselves (all are in ANCHOR_STOP or are near-universal in the tier).
_LEADING_MODAL = re.compile(
    r"^(i\s+)?(want|must|will|would|intend|plan|dream|hope|wish|need)s?\s+"
    r"(to|of|for|about)?\s*", re.IGNORECASE)


def carrier_for(topic: str, tier: str) -> str:
    for tag in (topic or "").split(","):
        t = tag.strip().lower()
        if t in CARRIERS:
            return CARRIERS[t]
    return TIER_FALLBACK.get(tier, TIER_FALLBACK["long"])


def phrase_memory_question(name: str, anchor: str, topic: str, tier: str) -> str:
    a = _LEADING_MODAL.sub("", anchor.strip()).strip()
    a = (a or anchor.strip()).rstrip('.,;:!?')
    return carrier_for(topic, tier).replace("{name}", name).replace("{anchor}", a)
