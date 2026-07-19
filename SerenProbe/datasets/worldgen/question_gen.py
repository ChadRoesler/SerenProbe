#!/usr/bin/env python3
"""
question_gen.py — Deterministic question generator for SerenProbe / Pilorus.

Consumes an entity's structured data and emits questions.yaml for a retrieval-eval
harness. Walks the structured graph (JSON + world event log) to choose edges and
compute hop counts. NEVER infers edges or hop counts from memory prose.

Usage:
    # Per-entity (character / beast / artifact / POI)
    python3 question_gen.py \\
        --entity-type character \\
        --entity-json Characters/Skabtrog/Skabtrog.json \\
        --entity-loci Characters/Skabtrog/Skabtrog_loci.yaml \\
        --entity-memory Characters/Skabtrog/Skabtrog_memory.yaml \\
        --world world.json \\
        --output Characters/Skabtrog/questions.yaml

    # World-level (no entity JSON, uses world loci/memory)
    python3 question_gen.py \\
        --entity-type world \\
        --entity-loci Pilorus_loci.yaml \\
        --entity-memory Pilorus_memory.yaml \\
        --world world.json \\
        --output questions.yaml

See questionGen.md for full spec.
"""

import argparse
import json
import os
import random
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml

from entity_utils import entity_slug, make_loci_key, asciify
from question_anchor import (
    extract_anchor as _extract_anchor,
    phrase_memory_question as _phrase_memory_question,
)


# ══════════════════════════════════════════════════════════════════════
# 1.  DATA LOADING
# ══════════════════════════════════════════════════════════════════════

def load_yaml(path: str) -> Any:
    with open(path, encoding="utf-8-sig") as f:
        return yaml.safe_load(f.read().replace("\r\n", "\n"))


def load_world(path: str) -> Dict:
    with open(path) as f:
        return json.load(f)


def load_entity_json(path: str) -> Dict:
    with open(path) as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════
# 2.  EDGE INVENTORY — walk the structured graph
# ══════════════════════════════════════════════════════════════════════
#
# Each edge is a dict:
#   {
#       "type":        str,        # unique edge type id
#       "archetype":   str,        # direct | cross_lens | relationship | temporal | quiet
#       "hops":        int,        # retrieval passes needed
#       "subject":     str,        # entity name / id the question is "about"
#       "expect_key":  str | None, # loci key that answers this
#       "expect_ref":  str | None, # memory ref that answers this
#       "expect_content": str | None, # phrase that must appear
#       "expect_empty": bool,      # bait / quiet question
#       "walk":        str,        # human-readable chain for _gen
#       "target_name": str | None, # name of the target entity (for phraser)
#       "target_type": str | None, # "figure" | "site" | "artifact" | "beast" | "civ"
#       "bridge_ids":  List[str] | None, # ids that bridge the hop (for hop-honesty test)
#   }
#
# We enumerate edges per entity type from STRUCTURE ONLY.

# ── World name cache for cross-lens key resolution ────────────────────
WORLD_NAME_CACHE = None


# ── Fact-key → category mapping (must match memory_to_loci.py) ─────────

def _fact_category(fact_key: str) -> str:
    """Map a fact key to its category project name.

    Mirrors the taxonomy in memory_to_loci._fact_category so that
    expect_key in questions matches the loci export format.
    """
    # Character stats
    if fact_key in ("race", "age", "alignment", "alignment_score"):
        return "stats"
    # Character identity
    if fact_key in ("title", "title_tier", "profession", "favorite_color",
                     "favorite_drink", "favorite_food", "is_titleworthy"):
        return "identity"
    # Character combat
    if fact_key in ("weapon", "armor"):
        return "combat"
    # Character origin
    if fact_key in ("home_site", "civ", "birth_year", "death_year", "deeds"):
        return "origin"

    # Artifact provenance
    if fact_key in ("broken_by", "stolen_by", "creator", "wielded_by",
                     "broken_by_ids", "stolen_by_ids", "wielded_by_ids"):
        return "provenance"
    # Artifact physical
    if fact_key in ("type", "material", "subtype"):
        return "physical"
    # Artifact holding
    if fact_key in ("held_by_civ", "held_by"):
        return "holding"

    # Beast stats
    if fact_key in ("type", "subtype", "kills", "alignment", "description"):
        return "stats"
    # Beast lifecycle
    if fact_key in ("year_spawned", "season_spawned", "active"):
        return "lifecycle"

    # POI demographics
    if fact_key in ("population", "size_class"):
        return "demographics"
    # POI geography
    if fact_key in ("site_type", "biome_id", "founded_year", "is_capital"):
        return "geography"

    return "misc"


STOP = {
    "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
    "is", "are", "was", "were", "be", "been", "the", "a", "an", "of", "for",
    "and", "or", "to", "do", "does", "did", "it", "its", "in", "on", "at",
    "with", "from", "by", "give", "me", "us", "brief", "briefing", "dossier",
    "tell", "about", "full", "all", "any", "some", "that", "this", "there",
    "have", "has", "had", "can", "could", "would", "should", "get", "got",
    "use", "used", "uses", "need", "needs", "happened", "went", "covered",
    "cover", "covers", "involve", "involves",
}

def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-\.]*", (text or "").lower())
            if w not in STOP and len(w) > 2}


# ── 2a.  Character edges ──────────────────────────────────────────────

def enumerate_character_edges(
    entity: Dict,
    world: Dict,
) -> List[Dict]:
    """Enumerate all walkable edges from a character's structured data."""
    edges = []
    facts = entity.get("facts", {})
    entity_id = entity.get("character", {}).get("id") or entity.get("id")
    entity_name = (
        entity.get("character", {}).get("name")
        or entity.get("name")
        or "Unknown"
    )

    # ── Compute slug for this entity ──
    char_slug = entity_slug(entity_name, entity_id or 0)

    # ── Direct facts (hops: 1, loci) ──
    # expect_key uses the slug-based format from entity_utils: {slug}/{slug}_{fact_key}
    DIRECT_KEYS = {
        "race": ("What race is {name}?", "race"),
        "age": ("How old is {name}?", "age"),
        "alignment": ("What is the alignment of {name}?", "alignment"),
        "title": ("What title does {name} hold?", "title"),
        "profession": ("What is {name}'s profession?", "profession"),
        "weapon": ("What weapon does {name} carry?", "weapon"),
        "armor": ("What armor does {name} wear?", "armor"),
        "favorite_food": ("What is {name}'s favorite food?", "favorite_food"),
        "favorite_drink": ("What is {name}'s favorite drink?", "favorite_drink"),
        "favorite_color": ("What is {name}'s favorite color?", "favorite_color"),
    }
    for key, (template, loci_suffix) in DIRECT_KEYS.items():
        if key in facts:
            edges.append({
                "type": f"char_direct_{key}",
                "archetype": "direct",
                "hops": 1,
                "subject": entity_name,
                "expect_key": f"{_fact_category(key)}/{loci_suffix}",
                "expect_ref": None,
                "expect_content": None,
                "expect_empty": False,
                "walk": f"figure:{entity_id} --facts.{key}--> value",
                "target_name": None,
                "target_type": None,
                "bridge_ids": None,
                "template": template,
            })

    # ── Relationship edges (hops: 1, figure→figure) ──
    rels = entity.get("relationships", [])
    # If the entity JSON doesn't carry relationships, try world.json's
    # historical_figures by matching entity_id.
    if not rels and entity_id:
        for fig in world.get("historical_figures", []):
            if fig.get("id") == entity_id:
                rels = fig.get("relationships", [])
                break
    for ri, rel in enumerate(rels):
        rel_type = rel.get("type", "unknown")
        rel_fig_id = rel.get("figure_id")
        if rel_fig_id is None:
            continue
        # Find the related figure's name from world
        rel_name = None
        for fig in world.get("historical_figures", []):
            if fig.get("id") == rel_fig_id:
                rel_name = fig.get("name")
                break
        if rel_name is None:
            continue

        edge_type = f"char_rel_{rel_type}_{ri}"
        template_map = {
            "parent_child": "Who is the {role} of {name}?",
            "colleague": "Who is a colleague of {name}?",
            "friend": "Who is a friend of {name}?",
            "spouse": "Who is the spouse of {name}?",
            "deity": "Who is the deity worshiped by {name}?",
            "apprentice": "Who is the apprentice of {name}?",
            "master": "Who is the master of {name}?",
            "rival": "Who is a rival of {name}?",
            "prisoner": "Who is the prisoner of {name}?",
        }
        tpl = template_map.get(rel_type, "Who is related to {name}?")
        edges.append({
            "type": edge_type,
            "archetype": "relationship",
            "hops": 1,
            "subject": entity_name,
            "expect_key": None,
            "expect_ref": f"{char_slug}_long_{ri}",  # placeholder — real ref from memory
            "expect_content": None,
            "expect_empty": False,
            "walk": f"figure:{entity_id} --{rel_type}--> figure:{rel_fig_id} ({rel_name})",
            "target_name": rel_name,
            "target_type": "figure",
            "bridge_ids": [str(rel_fig_id)],
            "template": tpl,
            "rel_role": rel.get("role", "relation"),
        })

    # ── Cross-lens: home_site (hops: 2, character→site) ──
    home_site_id = entity.get("context", {}).get("home_site_id") or facts.get("home_site")
    if home_site_id:
        # Resolve site from world
        site = None
        for s in world.get("sites", []):
            if s.get("id") == home_site_id or s.get("name") == home_site_id:
                site = s
                break
        # Also try matching by name from facts
        if site is None:
            home_name = facts.get("home_site")
            for s in world.get("sites", []):
                if s.get("name") == home_name:
                    site = s
                    break
        if site:
            site_id_val = site.get("id")
            site_name = site.get("name")
            # What type of site is the home?
            site_type_key = resolve_site_loci_key(site_name, "site_type", site_id=site_id_val)
            edges.append({
                "type": "char_home_site_type",
                "archetype": "cross_lens",
                "hops": 2,
                "subject": entity_name,
                "expect_key": site_type_key,
                "expect_ref": None,
                "expect_content": site.get("site_type", "village") if site_type_key is None else None,
                "expect_empty": False,
                "walk": f"figure:{entity_id} --home_site--> site:{site_id_val} ({site_name}) -> site_type",
                "target_name": site_name,
                "target_type": "site",
                "bridge_ids": [str(site_id_val)],
                "template": "What type of settlement is the home of {name}?",
            })
            # What population?
            pop_key = resolve_site_loci_key(site_name, "population", site_id=site_id_val)
            edges.append({
                "type": "char_home_site_pop",
                "archetype": "cross_lens",
                "hops": 2,
                "subject": entity_name,
                "expect_key": pop_key,
                "expect_ref": None,
                "expect_content": str(site.get("population", 0)) if pop_key is None else None,
                "expect_empty": False,
                "walk": f"figure:{entity_id} --home_site--> site:{site_id_val} ({site_name}) -> population",
                "target_name": site_name,
                "target_type": "site",
                "bridge_ids": [str(site_id_val)],
                "template": "What is the population of the home of {name}?",
            })

    # ── Cross-lens: civ (hops: 2, character→civilization) ──
    civ_name = facts.get("civ")
    if civ_name:
        civ = None
        for c in world.get("civilizations", []):
            if c.get("name") == civ_name:
                civ = c
                break
        if civ:
            civ_id = civ.get("id")
            # ── Fix 2: try to resolve expect_key for civ alignment ──
            align_key = resolve_civ_loci_key(civ_name, "alignment")
            edges.append({
                "type": "char_civ_alignment",
                "archetype": "cross_lens",
                "hops": 2,
                "subject": entity_name,
                "expect_key": align_key,
                "expect_ref": None,
                "expect_content": civ.get("alignment", "neutral") if align_key is None else None,
                "expect_empty": False,
                "walk": f"figure:{entity_id} --civ--> civ:{civ_id} ({civ_name}) -> alignment",
                "target_name": civ_name,
                "target_type": "civ",
                "bridge_ids": [str(civ_id)],
                "template": "What is the alignment of the civilization {civ_name} that {name} belongs to?",
            })
            # What race is the civ?
            race_key = resolve_civ_loci_key(civ_name, "race")
            edges.append({
                "type": "char_civ_race",
                "archetype": "cross_lens",
                "hops": 2,
                "subject": entity_name,
                "expect_key": race_key,
                "expect_ref": None,
                "expect_content": civ.get("race", "unknown") if race_key is None else None,
                "expect_empty": False,
                "walk": f"figure:{entity_id} --civ--> civ:{civ_id} ({civ_name}) -> race",
                "target_name": civ_name,
                "target_type": "civ",
                "bridge_ids": [str(civ_id)],
                "template": "What race is the civilization that {name} calls home?",
            })

    return edges


# ── 2b.  Beast / Artifact (OOI) edges ─────────────────────────────────

def enumerate_ooi_edges(
    entity: Dict,
    world: Dict,
    ooi_type: str,  # "beast" or "artifact"
) -> List[Dict]:
    """Enumerate walkable edges from a beast or artifact's structured data."""
    edges = []
    facts = entity.get("facts", {})
    entity_id = entity.get("id")
    if entity_id is None:
        entity_id = entity.get("ooi", {}).get("id") or entity.get("beast", {}).get("id")
    entity_name = (
        entity.get("ooi", {}).get("name")
        or entity.get("beast", {}).get("name")
        or entity.get("name")
        or "Unknown"
    )

    # ── Compute slug for this entity ──
    ooi_slug = entity_slug(entity_name, entity_id or 0)

    # ── Direct facts ──
    if ooi_type == "beast":
        DIRECT_MAP = {
            "beast_type": ("What type of beast is {name}?", "beast_type"),
            "alignment": ("What is the alignment of {name}?", "alignment"),
            "kills": ("How many kills does {name} have?", "kills"),
            "year_spawned": ("What year was {name} spawned?", "year_spawned"),
            "active": ("Is {name} still active?", "active"),
            "habitat": ("What is the habitat of {name}?", "habitat"),
            "size_class": ("What size class is {name}?", "size_class"),
        }
    else:
        DIRECT_MAP = {
            "artifact_type": ("What type of artifact is {name}?", "artifact_type"),
            "material": ("What material is {name} made from?", "material"),
            "alignment": ("What is the alignment of {name}?", "alignment"),
            "created_year": ("What year was {name} created?", "created_year"),
            "property": ("What property does {name} have?", "property"),
            "enchantment": ("What enchantment does {name} have?", "enchantment"),
            "weight_class": ("What weight class is {name}?", "weight_class"),
        }

    for key, (template, loci_suffix) in DIRECT_MAP.items():
        val = facts.get(key)
        if val is not None and val != "":
            edges.append({
                "type": f"{ooi_type}_direct_{key}",
                "archetype": "direct",
                "hops": 1,
                "subject": entity_name,
                "expect_key": f"{_fact_category(key)}/{loci_suffix}",
                "expect_ref": None,
                "expect_content": None,
                "expect_empty": False,
                "walk": f"{ooi_type}:{entity_id} --facts.{key}--> value",
                "target_name": None,
                "target_type": None,
                "bridge_ids": None,
                "template": template,
            })

    # ── Creator / Wielder / other figure relationships ──
    # These live in the ooi_memory_gen output as facts with _ids suffixes
    CREATOR_IDS = facts.get("creator_id")
    if CREATOR_IDS and CREATOR_IDS != "none":
        edges.append({
            "type": f"{ooi_type}_creator",
            "archetype": "relationship",
            "hops": 1,
            "subject": entity_name,
            "expect_key": f"{_fact_category('creator')}/creator",
            "expect_ref": None,
            "expect_content": None,
            "expect_empty": False,
            "walk": f"{ooi_type}:{entity_id} --creator--> figure:{CREATOR_IDS}",
            "target_name": facts.get("creator", "unknown"),
            "target_type": "figure",
            "bridge_ids": [CREATOR_IDS],
            "template": "Who created {name}?",
        })

    WIELDED_IDS = facts.get("wielded_by_ids")
    if WIELDED_IDS and WIELDED_IDS != "none":
        edges.append({
            "type": f"{ooi_type}_wielder",
            "archetype": "relationship",
            "hops": 1,
            "subject": entity_name,
            "expect_key": f"{_fact_category('wielded_by')}/wielded_by",
            "expect_ref": None,
            "expect_content": None,
            "expect_empty": False,
            "walk": f"{ooi_type}:{entity_id} --wielded_by--> figure:{WIELDED_IDS}",
            "target_name": facts.get("wielded_by", "unknown"),
            "target_type": "figure",
            "bridge_ids": [WIELDED_IDS],
            "template": "Who has wielded {name}?",
        })

    BROKEN_IDS = facts.get("broken_by_ids")
    if BROKEN_IDS and BROKEN_IDS != "none":
        edges.append({
            "type": f"{ooi_type}_broken_by",
            "archetype": "relationship",
            "hops": 1,
            "subject": entity_name,
            "expect_key": f"{_fact_category('broken_by')}/broken_by",
            "expect_ref": None,
            "expect_content": None,
            "expect_empty": False,
            "walk": f"{ooi_type}:{entity_id} --broken_by--> figure:{BROKEN_IDS}",
            "target_name": facts.get("broken_by", "unknown"),
            "target_type": "figure",
            "bridge_ids": [BROKEN_IDS],
            "template": "By whom was {name} broken?",
        })

    STOLEN_IDS = facts.get("stolen_by_ids")
    if STOLEN_IDS and STOLEN_IDS != "none":
        edges.append({
            "type": f"{ooi_type}_stolen_by",
            "archetype": "relationship",
            "hops": 1,
            "subject": entity_name,
            "expect_key": f"{_fact_category('stolen_by')}/stolen_by",
            "expect_ref": None,
            "expect_content": None,
            "expect_empty": False,
            "walk": f"{ooi_type}:{entity_id} --stolen_by--> figure:{STOLEN_IDS}",
            "target_name": facts.get("stolen_by", "unknown"),
            "target_type": "figure",
            "bridge_ids": [STOLEN_IDS],
            "template": "By whom was {name} stolen?",
        })

    # ── Cross-lens: held_by_civ (hops: 2) ──
    held_civ = facts.get("held_by_civ")
    if held_civ and held_civ != "none":
        civ = None
        for c in world.get("civilizations", []):
            if c.get("name") == held_civ:
                civ = c
                break
        if civ:
            # ── Fix 2: try to resolve expect_key ──
            align_key = resolve_civ_loci_key(held_civ, "alignment")
            edges.append({
                "type": f"{ooi_type}_held_civ_alignment",
                "archetype": "cross_lens",
                "hops": 2,
                "subject": entity_name,
                "expect_key": align_key,
                "expect_ref": None,
                "expect_content": civ.get("alignment", "neutral") if align_key is None else None,
                "expect_empty": False,
                "walk": f"{ooi_type}:{entity_id} --held_by_civ--> civ:{civ.get('id')} ({held_civ}) -> alignment",
                "target_name": held_civ,
                "target_type": "civ",
                "bridge_ids": [str(civ.get('id'))],
                "template": "What alignment is the civilization that holds {name}?",
            })

    # ── Beast-specific: attack events from world event log ──
    # NOTE: Attack event site names and kills are NOT in the beast's loci/memory
    # seed data, so expect_content questions about them are unanswerable.
    # Skip these edges until the pipeline includes attack events in the beast's
    # loci or memory output.
    # if ooi_type == "beast":
    #     beast_id = entity_id
    #     for ei, evt in enumerate(world.get("events", [])):
    #         if evt.get("type") == "beast_attack" and evt.get("beast_id") == beast_id:
    #             ... (removed — unanswerable expect_content)

    # ── Artifact-specific: creation event from world event log ──
    if ooi_type == "artifact":
        art_id = entity_id
        for ei, evt in enumerate(world.get("events", [])):
            if evt.get("type") == "artifact_created" and evt.get("artifact_id") == art_id:
                # Who created it? (the event may name a figure)
                fig_id = evt.get("figure_id")
                if fig_id:
                    fig_name = None
                    for fig in world.get("historical_figures", []):
                        if fig.get("id") == fig_id:
                            fig_name = fig.get("name")
                            break
                    if fig_name:
                        edges.append({
                            "type": f"artifact_creator_event_{ei}",
                            "archetype": "relationship",
                            "hops": 1,
                            "subject": entity_name,
                            "expect_key": f"{_fact_category('creator')}/creator",
                            "expect_ref": None,
                            "expect_content": None,
                            "expect_empty": False,
                            "walk": f"event[{ei}]: artifact:{art_id} created_by figure:{fig_id} ({fig_name})",
                            "target_name": fig_name,
                            "target_type": "figure",
                            "bridge_ids": [str(fig_id)],
                            "template": "Who is the creator of {name}?",
                        })

    return edges


# ── 2c.  POI (Site) edges ─────────────────────────────────────────────

def enumerate_poi_edges(
    entity: Dict,
    world: Dict,
) -> List[Dict]:
    """Enumerate walkable edges from a POI / site's structured data."""
    edges = []
    facts = entity.get("facts", {})
    site_id = entity.get("id")

    # POI memory JSON uses key 'poi' (not 'site') — ooi_memory_gen.py
    # and poi_memory_gen.py both emit {ooi_type: {...}}.
    poi_block = entity.get("poi") or entity.get("site") or {}
    site_name = (
        poi_block.get("name")
        or entity.get("name")
        or "Unknown"
    )

    # ── Compute slug for this entity ──
    poi_slug = entity_slug(site_name, site_id or 0)

    # ── Direct facts ──
    # expect_key uses the slug-based format from entity_utils: {slug}/{slug}_{fact_key}
    DIRECT_MAP = {
        "site_type": ("What type of site is {name}?", "site_type"),
        "population": ("What is the population of {name}?", "population"),
        "is_capital": ("Is {name} a capital?", "is_capital"),
        "biome_name": ("What biome is {name} in?", "biome_name"),
        "founded_year": ("What year was {name} founded?", "founded_year"),
        "civ_name": ("What civilization does {name} belong to?", "civ_name"),
    }
    for key, (template, loci_suffix) in DIRECT_MAP.items():
        val = facts.get(key)
        if val is not None and val != "":
            edges.append({
                "type": f"poi_direct_{key}",
                "archetype": "direct",
                "hops": 1,
                "subject": site_name,
                "expect_key": f"{_fact_category(key)}/{loci_suffix}",
                "expect_ref": None,
                "expect_content": None,
                "expect_empty": False,
                "walk": f"site:{site_id} --facts.{key}--> value",
                "target_name": None,
                "target_type": None,
                "bridge_ids": None,
                "template": template,
            })

    # ── Cross-lens: events at this site (hops: 2) ──
    # Walk world event log for events that name this site
    for ei, evt in enumerate(world.get("events", [])):
        if evt.get("site_id") == site_id or evt.get("site_name") == site_name:
            evt_type = evt.get("type", "event")
            # Only add a sample of site events to avoid flooding
            if ei % 5 == 0:  # sample every 5th
                edges.append({
                    "type": f"poi_event_{evt_type}_{ei}",
                    "archetype": "cross_lens",
                    "hops": 2,
                    "subject": site_name,
                    "expect_key": None,
                    "expect_ref": None,
                    "expect_content": evt.get("attacker_name") or evt.get("figure_name") or evt_type,
                    "expect_empty": False,
                    "walk": f"site:{site_id} <-- event[{ei}]:{evt_type} ({evt.get('year')})",
                    "target_name": evt.get("attacker_name") or evt.get("figure_name"),
                    "target_type": "event",
                    "bridge_ids": [str(evt.get("attacker_id") or evt.get("figure_id"))] if evt.get("attacker_id") or evt.get("figure_id") else None,
                    "template": "What happened at {name} in year {year}?",
                    "event_year": evt.get("year"),
                    "event_type": evt_type,
                })

    return edges


# ── 2d.  World-level edges ────────────────────────────────────────────

def enumerate_world_edges(
    world: Dict,
) -> List[Dict]:
    """Enumerate walkable edges from the world-level data."""
    edges = []
    world_name = world.get("world_name", "the world")
    facts = world.get("facts", {})

    # ── Cache world name for cross-lens key resolution ──
    global WORLD_NAME_CACHE
    WORLD_NAME_CACHE = world_name

    # ── Direct world facts ──
    # §1 restructure: world facts use project '*' (fundamentals).
    # key format: "*/{fact_key}"  (e.g. "*/num_sites")
    WORLD_DIRECT = {
        "world_name": ("What is the name of the world?", "world_name"),
        "world_year_span": ("How long is the recorded history of {world_name}?", "world_year_span"),
        "num_civilizations": ("How many civilizations exist in this world?", "num_civilizations"),
        "num_sites": ("How many sites exist in this world?", "num_sites"),
        "num_figures": ("How many historical figures exist in this world?", "num_figures"),
        "num_artifacts": ("How many artifacts exist in this world?", "num_artifacts"),
        "num_beasts": ("How many beasts exist in this world?", "num_beasts"),
        "num_eras": ("How many eras span the history of this world?", "num_eras"),
        "num_events": ("How many events are recorded in this world?", "num_events"),
    }
    for key, (template, loci_suffix) in WORLD_DIRECT.items():
        val = facts.get(key)
        if val is not None and val != "":
            edges.append({
                "type": f"world_direct_{key}",
                "archetype": "direct",
                "hops": 1,
                "subject": world_name,
                "expect_key": f"*/{loci_suffix}",
                "expect_ref": None,
                "expect_content": None,
                "expect_empty": False,
                "walk": f"world --facts.{key}--> value",
                "target_name": None,
                "target_type": None,
                "bridge_ids": None,
                "template": template,
            })

    # ── Cross-lens: civilization details (hops: 2) ──
    for ci, civ in enumerate(world.get("civilizations", [])):
        civ_name = civ.get("name", f"civ_{ci}")
        name_key = resolve_civ_loci_key(civ_name, "name")
        edges.append({
            "type": f"world_civ_leader_{ci}",
            "archetype": "cross_lens",
            "hops": 2,
            "subject": world_name,
            "expect_key": name_key,
            "expect_ref": None,
            "expect_content": civ_name if name_key is None else None,
            "expect_empty": False,
            "walk": f"world --civilizations[{ci}]--> civ:{civ.get('id')} ({civ_name})",
            "target_name": civ_name,
            "target_type": "civ",
            "bridge_ids": [str(civ.get('id'))] if civ.get('id') else None,
            "template": "What civilization is led by figure {leader_id}?",
            "leader_id": civ.get("leader_id"),
        })

    return edges


# ── 2e.  Quiet / bait edges (phantom entities, expect_empty) ───────────

PHANTOM_NAMES = [
    "Zalthar the Unseen",
    "Kragmorn Hollow",
    "The Lost Spire of Vorn",
    "Glimmerstone Cavern",
    "Throgdak the Whisperer",
    "The Sunken Archive",
    "Bleakmire Fen",
    "Velskudel's Last Stand",
    "The Forgotten Vault of Xul",
    "Murkfang Warren",
]

def enumerate_quiet_edges(
    entity_type: str,
    entity_name: str,
    rng: random.Random,
) -> List[Dict]:
    """Generate quiet/bait edges: ask about a phantom entity that does NOT exist."""
    edges = []
    seed_str = f"{entity_type}:{entity_name}"
    hash_val = sum(ord(c) for c in seed_str) % 100
    n_phantoms = 2 + (hash_val % 2)

    for pi in range(n_phantoms):
        idx = (hash_val + pi * 7) % len(PHANTOM_NAMES)
        phantom = PHANTOM_NAMES[idx]
        edges.append({
            "type": f"quiet_phantom_{pi}",
            "archetype": "quiet",
            "hops": 1,
            "subject": entity_name,
            "expect_key": None,
            "expect_ref": None,
            "expect_content": None,
            "expect_empty": True,
            "walk": f"phantom entity '{phantom}' — expect_empty (bait)",
            "target_name": None,
            "target_type": None,
            "bridge_ids": None,
            "template": "What is known about {phantom}?",
            "phantom": phantom,
        })
    return edges


# ── 2f.  Temporal edges (memory discrimination) ────────────────────────
#
# Natural-language question builder: maps memory content patterns to
# coherent question templates.  No em-dashes, no raw n-gram appendage.
# See Question_Eval_v2 §1 — the embedder needs CONTENT tokens it can
# actually match, phrased as a natural question.

# THE PATTERN BANK IS GONE. See question_anchor.py for what replaced it and why.
#
# Short version: a bank of ~10 regexes tried to TRANSLATE an anchor phrase into
# a natural question. On the real Pilorus corpus it matched ZERO times across 18
# memories -- every one fell through to "What happened to {name} in Year {year}?"
# -- and on the rare match it DISCARDED the anchor's words, deleting the only
# tokens the embedder could have used. Three queries per character, identical
# but for a year, and every row in the store starts with a year.
#
# The replacement carries the phrase verbatim into one carrier template. That is
# not a shortcut; carrying the discriminating term IS the mechanism.


# _extract_anchor now lives in question_anchor.py (imported at the top). The version
# that stood here ranked candidate grams by CHARACTER LENGTH and accepted any
# gram not literally present in a sibling memory. That is uniqueness, not
# discriminability: "often think about the shadows" and "often think about the
# lessons" are both unique and are semantic twins. The replacement requires the
# anchor to carry >=2 content words that appear in NO sibling document -- which
# is precisely what SerenProbe's linter demands when it rejects a question.


def enumerate_temporal_edges(
    entity: Dict,
    world: Dict,
    entity_type: str,
    memory_items: List[Dict],
) -> List[Dict]:
    """One `asks: memory` edge per ANCHORABLE memory document, across ALL tiers.

    THREE THINGS CHANGED HERE, AND EACH ONE WAS COSTING WHOLE QUESTIONS.

    1. WE WALK THE SEEDED YAML, NOT THE ENTITY JSON.
       `memory_items` is the exact list memory_to_seren.py wrote and the exact
       list SerenProbe seeds. Anchoring against the entity JSON's raw
       `memories.long_term[].memory` meant anchoring against text that is not
       quite what lands in the store (the seeded content is prefixed with the
       year label). Anchor against the bytes that will actually be searched.

    2. ALL THREE TIERS, EVERY ITEM.
       The predecessor read `memories.long_term` only, and sampled exactly
       three indices (0, n//2, n-1). Short and near were structurally
       unreachable, and the memory bucket could never exceed 3 candidates
       against a per-store target of 10. Galros has 43 memory documents and
       was yielding 3 questions.

    3. THE SKIP GATE.
       No anchor -> NO QUESTION. The predecessor fell back to
       "What happened to {name} in Year {year}?", which is the single worst
       query the corpus admits: every document opens with a year, so the query
       names a CATEGORY and expect_ref names one member of it. hit_rate is
       capped below 1 no matter how good the store is, and on the dashboard
       that is indistinguishable from a dead store.

       When a memory has no discriminating term the defect is in the CORPUS
       (Pilorus has five memories that all say "a great battle was fought --
       Rielven against Ranbalddore"), and no phrasing can fix it. Emitting
       nothing is the honest output. Skips are reported so the corpus defect
       stays visible instead of being laundered into a bad score.
    """
    edges: List[Dict] = []
    entity_id = entity.get("id") or entity.get("character", {}).get("id")
    entity_name = (
        entity.get("character", {}).get("name")
        or entity.get("ooi", {}).get("name")
        or entity.get("beast", {}).get("name")
        or entity.get("poi", {}).get("name")
        or entity.get("name")
        or "Unknown"
    )
    slug = entity_slug(entity_name, entity_id or 0)

    docs = [m for m in (memory_items or []) if m.get("ref") and m.get("content")]
    if not docs:
        return edges

    texts = [str(d.get("content", "")) for d in docs]
    skipped: List[str] = []

    for i, doc in enumerate(docs):
        ref = str(doc["ref"])
        tier = str(doc.get("tier", "long"))
        topic = str(doc.get("topic", ""))
        content = texts[i]

        # Rivals = every OTHER document in this store, ACROSS TIERS. A phrase
        # that is unique among the long-term rows but repeated in a short-term
        # row is not distinctive: the store searches all tiers at once, and the
        # long tier's 0.8x weight is nowhere near enough to save it.
        rivals = [t for j, t in enumerate(texts) if j != i]
        anchor = _extract_anchor(content, rivals)
        if not anchor:
            skipped.append(ref)
            continue

        query = _phrase_memory_question(entity_name, anchor, topic, tier)

        edges.append({
            "type": f"temporal_{entity_type}_{tier}_{i}",
            "archetype": "temporal",
            "hops": 1,
            "subject": entity_name,
            "expect_ref": ref,
            "expect_key": None,
            "expect_content": None,
            "expect_empty": False,
            "event_year": "",
            "event_age": "",
            "walk": f"{slug} --memory[{tier}]--> {ref} (anchor: {asciify(anchor)})",
            "target_name": None,
            "target_type": "memory",
            "bridge_ids": None,
            # Pre-rendered: the anchor is the payload and must survive verbatim.
            # render_template only substitutes {slots}, so a literal query is safe.
            "template": query,
            "anchor": anchor,
        })

    if skipped:
        print(f"  Memory: {len(edges)} anchored, {len(skipped)} skipped "
              f"(no discriminating term -- corpus duplication, not a phrasing bug)",
              file=sys.stderr)
        print(f"    skipped refs: {', '.join(skipped[:12])}"
              f"{'...' if len(skipped) > 12 else ''}", file=sys.stderr)
    return edges


# ── 2g.  Cross-organ edges (the CORPUS column's actual job) ──────────────

def enumerate_cross_organ_edges(
    entity: Dict,
    loci_items: List[Dict],
    memory_edges: List[Dict],
    rng: random.Random,
    max_edges: int = 6,
) -> List[Dict]:
    """Questions whose ground truth spans BOTH organs -- the fusion-value test.

    WHY THIS EXISTS. Per-entity corpus questions were, until now, three phantom
    bait queries and nothing else. The SCC column was measuring almost nothing,
    because cross_lens is stripped from per-entity files (a per-entity SCC only
    fans that entity's own loci + memory, so it genuinely cannot traverse a hop
    into another entity).

    But there IS a hop a per-entity SCC can traverse, and it is the one the
    whole store-per-tenant design exists to measure: the hop BETWEEN ORGANS.
    A question carrying one expect_key AND one expect_ref can be fully answered
    by neither member alone -- Loci holds the fact, Memory holds the episode --
    and only the fan can cover both. That is fusion VALUE, stated as a number,
    which is exactly what resolve.py's corpus-inheritance comment says the
    column is for.

    Requires the multi-kind relaxation in gate_well_formed (corpus only).
    """
    edges: List[Dict] = []
    entity_name = (
        entity.get("character", {}).get("name")
        or entity.get("ooi", {}).get("name")
        or entity.get("beast", {}).get("name")
        or entity.get("poi", {}).get("name")
        or entity.get("name")
        or "Unknown"
    )

    # Loci facts worth pairing: skip low-cardinality values (alignment,
    # booleans) -- they cannot discriminate on the loci half either.
    LOW_CARD = {"good", "neutral", "evil", "lawful", "chaos", "chaotic", "mixed",
                "true", "false", "yes", "no", "active", "inactive"}
    facts = []
    for it in loci_items or []:
        ident = str(it.get("ident", ""))
        val = str(it.get("value", "")).strip()
        if not ident or "/" not in ident or not val:
            continue
        key_name = ident.split("/", 1)[1]
        # Raw id columns read as nonsense in a question ("What is the wielded
        # by ids of X") and cannot be retrieved by any natural phrasing.
        if key_name.endswith("_ids") or key_name.endswith("_id"):
            continue
        if val.lower() in LOW_CARD or val.isdigit():
            continue
        facts.append((ident, it.get("key", "")))

    anchored = [e for e in memory_edges if e.get("anchor")]
    if not facts or not anchored:
        return edges

    # Deterministic pairing: shuffle both with the seeded rng, zip, take N.
    facts = list(facts)
    anchored = list(anchored)
    rng.shuffle(facts)
    rng.shuffle(anchored)

    # ASK-PHRASING NOTE. The two halves are joined with "and" rather than fused
    # into one clause on purpose: the retrieval target is two DIFFERENT
    # documents, so the query needs both sets of terms present and neither
    # diluted. This is a briefing request, not a sentence.
    for i, ((ident, key_name), mem_edge) in enumerate(zip(facts, anchored)):
        if i >= max_edges:
            break
        readable = str(key_name).replace("_", " ")
        anchor = str(mem_edge["anchor"]).rstrip('.,;:!?')
        query = (f"What is the {readable} of {entity_name}, "
                 f"and what does {entity_name} recall about {anchor}?")
        edges.append({
            "type": f"cross_organ_{i}",
            "archetype": "cross_organ",
            "hops": 2,
            "subject": entity_name,
            "expect_key": ident,
            "expect_ref": mem_edge["expect_ref"],
            "expect_content": None,
            "expect_empty": False,
            "walk": f"loci:{ident} + memory:{mem_edge['expect_ref']} "
                    f"-- answerable only by the fan",
            "target_name": None,
            "target_type": "cross_organ",
            "bridge_ids": None,
            "template": query,
        })
    return edges


def enumerate_briefing_edges(
    entity: Dict,
    loci_items: List[Dict],
    max_edges: int = 4,
    max_keys: int = 5,
) -> List[Dict]:
    """Multi-fact dossier questions -- one per loci CATEGORY.

    THE FIRST VERSION OF THIS FUNCTION WAS THE BUG IT WAS BUILT ALONGSIDE.
    It phrased the query off a prose label for the category ("Give me a briefing
    on the combat equipment of Edricmer") while expect_key pointed at the
    members (combat/weapon, combat/armor). The loci rows carry the words
    "weapon" and "armor"; they do not carry "combat equipment". The linter
    reported query-term overlap 0 -- the query named a CATEGORY and the answer
    key named one member of it, which is exactly the defect this whole module
    exists to prevent. 52 unanswerable questions in one run.

    The rule is the same rule as everywhere else: CARRY THE TERM THE DOCUMENT
    CARRIES. So the query is now built from the actual fact key names, which
    are the tokens sitting in the loci row's `key` and `why` fields. A briefing
    over weapon + armor asks for the weapon and the armor, by name.
    """
    edges: List[Dict] = []
    entity_name = (
        entity.get("character", {}).get("name")
        or entity.get("ooi", {}).get("name")
        or entity.get("beast", {}).get("name")
        or entity.get("poi", {}).get("name")
        or entity.get("name")
        or "Unknown"
    )

    by_cat: Dict[str, List[tuple]] = {}
    for it in loci_items or []:
        ident = str(it.get("ident", ""))
        if "/" not in ident:
            continue
        cat, name = ident.split("/", 1)
        if cat == "*":
            continue
        # 'misc' is _fact_category's fallback bucket, not a real category. It
        # collects stat_str, industry, ruler_title, enchantment... a briefing
        # over it has no honest name and no shared vocabulary. Skip it until
        # the taxonomy actually covers those keys.
        if cat == "misc":
            continue
        # Raw id columns are not answers. "wielded_by_ids: 417" cannot be
        # retrieved by any natural query and cannot be judged by a human.
        if name.endswith("_ids") or name.endswith("_id"):
            continue
        by_cat.setdefault(cat, []).append((ident, name))

    for cat in sorted(by_cat):
        if len(edges) >= max_edges:
            break
        pairs = by_cat[cat][:max_keys]
        # A one-fact 'briefing' is just a loci question wearing a hat.
        if len(pairs) < 2:
            continue
        idents = [p[0] for p in pairs]
        terms = [p[1].replace("_", " ") for p in pairs]
        listed = ", ".join(terms[:-1]) + f" and {terms[-1]}"
        edges.append({
            "type": f"briefing_{cat}",
            "archetype": "briefing",
            "hops": 1,
            "subject": entity_name,
            "expect_key": idents,
            "expect_ref": None,
            "expect_content": None,
            "expect_empty": False,
            "walk": f"loci project '{cat}' -- {len(idents)} facts, docket coverage",
            "target_name": None,
            "target_type": "briefing",
            "bridge_ids": None,
            "template": f"What is the {listed} of {entity_name}?",
        })
    return edges


# ── 3.  QUESTION BUILDER — templated phraser ────────────────────────────

def render_template(template: str, context: Dict) -> str:
    """Simple template renderer: replaces {name}, {year}, etc.
    All values are ASCII-sanitized to prevent non-ASCII characters in output.
    """
    result = template
    for k, v in context.items():
        result = result.replace(f"{{{k}}}", asciify(str(v)))
    return asciify(result)


# ── Null guard: reject questions with null/None in critical slots ──────

NULL_TOKENS = {"none", "null", "None", "", "unknown"}

def has_null_value(val: Any) -> bool:
    """Check if a value is None, null, empty, or 'None'/'none'."""
    if val is None:
        return True
    s = str(val).strip().lower()
    return s in NULL_TOKENS


# ── Resolve a target entity (site/civ) to its loci key ────────────────

def resolve_civ_loci_key(civ_name: str, field: str) -> Optional[str]:
    """Build the world-level loci key for a civilization fact.

    Returns the full-path ident key (e.g. "*/civ_zitgak_name") — the
    probe matches against the `ident` field which is the full path.
    """
    if has_null_value(civ_name):
        return None
    safe = civ_name.lower().replace(" ", "_")
    return f"*/civ_{safe}_{field}"


def resolve_site_loci_key(site_name: str, field: str, site_id: Optional[int] = None) -> Optional[str]:
    """Build the POI-level loci key for a site fact.

    Returns the full-path ident key (e.g. "geography/site_type") — the
    probe matches against the `ident` field which is the full path.
    """
    if has_null_value(site_name):
        return None
    if site_id is None:
        return None
    cat = _fact_category(field)
    return f"{cat}/{field}"


def build_question_from_edge(
    edge: Dict,
    entity: Dict,
    world: Dict,
    loci_items: List[Dict],
    memory_items: List[Dict],
) -> Optional[Dict]:
    """Build a question dict from an edge. Returns None if the edge can't be phrased."""
    subject = edge["subject"]
    entity_name = entity.get("name", "Unknown")
    world_name = world.get("world_name", "the world")

    # ── Fix 1: Null guard — reject if any critical slot is null ──
    context = {
        "name": subject,
        "world_name": world_name,
        "year": edge.get("event_year", ""),
        "age": edge.get("event_age", ""),
        "site_name": edge.get("target_name", ""),
        "civ_name": edge.get("target_name", ""),
        "leader_id": edge.get("leader_id", ""),
        "role": edge.get("rel_role", "relation"),
        "phantom": edge.get("phantom", ""),
    }

    query = render_template(edge.get("template", "What is the answer about {name}?"), context)

    # Reject if query itself contains "None" or "null"
    if "None" in query or "null" in query.lower():
        return None

    # Determine asks and expect_*
    if edge["archetype"] == "quiet":
        # Bait — expect_empty
        return {
            "query": query,
            "asks": "corpus",
            "expect_empty": True,
            "hops": 1,
            "_gen": {
                "edge": asciify(edge["walk"]),
                "hops_computed": 1,
                "archetype": "quiet",
                "shortcut_test": "n/a (bait)",
            }
        }

    if edge["archetype"] == "direct":
        return {
            "query": query,
            "asks": "loci",
            "expect_key": [edge["expect_key"]],
            "hops": 1,
            "_gen": {
                "edge": asciify(edge["walk"]),
                "hops_computed": 1,
                "archetype": "direct",
                "shortcut_test": "n/a (hops=1)",
            }
        }

    if edge["archetype"] == "relationship":
        # Relationship answer lives in the loci (the name) or memory
        return {
            "query": query,
            "asks": "loci",
            "expect_key": [edge["expect_key"]] if edge["expect_key"] else [],
            "hops": 1,
            "_gen": {
                "edge": asciify(edge["walk"]),
                "hops_computed": 1,
                "archetype": "relationship",
                "shortcut_test": "n/a (hops=1)",
            }
        }

    if edge["archetype"] == "cross_lens":
        # ── Fix 2: Prefer expect_key when available ──
        # ② — cross-lens (multi-hop) must ask corpus, not loci.
        # A single Loci store cannot traverse a hop.
        if edge.get("expect_key"):
            return {
                "query": query,
                "asks": "corpus",
                "expect_key": [edge["expect_key"]],
                "hops": edge["hops"],
                "_gen": {
                    "edge": asciify(edge["walk"]),
                    "hops_computed": edge["hops"],
                    "archetype": "cross_lens",
                    "shortcut_test": "pending",
                }
            }

        # Fall back to expect_content — asciify each content string
        safe_content = [asciify(str(c)) for c in (edge["expect_content"] or [])]
        return {
            "query": query,
            "asks": "corpus",
            "expect_content": safe_content,
            "hops": edge["hops"],
            "_gen": {
                "edge": asciify(edge["walk"]),
                "hops_computed": edge["hops"],
                "archetype": "cross_lens",
                "shortcut_test": "pending",
            }
        }

    if edge["archetype"] == "temporal":
        return {
            "query": query,
            "asks": "memory",
            "expect_ref": [edge["expect_ref"]] if edge["expect_ref"] else [],
            "hops": 1,
            "_gen": {
                "edge": asciify(edge["walk"]),
                "hops_computed": 1,
                "archetype": "temporal",
                "shortcut_test": "n/a (hops=1)",
            }
        }

    if edge["archetype"] == "cross_organ":
        # BOTH kinds on purpose. live_eval._grade unions the resolved ids from
        # expect_key and expect_ref, so recall@k only reaches 1.0 when the fan
        # surfaces a Loci row AND a Memory row. Neither member can do that
        # alone -- which is the entire measurement.
        return {
            "query": query,
            "asks": "corpus",
            "expect_key": [edge["expect_key"]],
            "expect_ref": [edge["expect_ref"]],
            "hops": edge.get("hops", 2),
            "_gen": {
                "edge": asciify(edge["walk"]),
                "hops_computed": edge.get("hops", 2),
                "archetype": "cross_organ",
                "shortcut_test": "n/a (organ hop, not entity hop)",
            }
        }

    if edge["archetype"] == "briefing":
        keys = edge["expect_key"]
        if not isinstance(keys, list):
            keys = [keys]
        return {
            "query": query,
            "asks": "corpus",
            "expect_key": keys,
            "hops": 1,
            "_gen": {
                "edge": asciify(edge["walk"]),
                "hops_computed": 1,
                "archetype": "briefing",
                "shortcut_test": "n/a (coverage test)",
            }
        }

    return None


# ══════════════════════════════════════════════════════════════════════
# 4.  ACCEPTANCE GATES
# ══════════════════════════════════════════════════════════════════════

def gate_answer_not_in_query(q: Dict, loci_items: List[Dict], memory_items: List[Dict]) -> Tuple[bool, str]:
    """Gate 2: The expect_* target's entity name/key must NOT appear verbatim in query.

    This checks that the ANSWER VALUE (the actual fact value) does not appear verbatim
    in the query. Key names and common category words are fine — they're part of the
    question phrasing. Only reject if the answer itself is a name the query already gives.
    """
    query_lower = q.get("query", "").lower()

    # Check expect_key: look up the actual value from loci
    # expect_key is in format "project/key_name" — matches loci.key directly
    for ek in q.get("expect_key") or []:
        val = None
        for item in loci_items:
            # MATCH ON ident, NOT key. Since the category restructure,
            # memory_to_loci writes key="race" / project="stats" /
            # ident="stats/race", while expect_key is the FULL PATH. Comparing
            # against `key` could never match, so this gate silently passed
            # every question ever generated -- an answer-leak check that has
            # never once fired is worse than no check, because you trust it.
            if item.get("ident") == ek or item.get("key") == ek:
                val = str(item.get("value", ""))
                break
        if val and val.lower() in query_lower and len(val) > 2:
            return False, f"expect_key '{ek}' value '{val}' appears verbatim in query"

    # Check expect_ref: look up the content from memory
    for ref in q.get("expect_ref") or []:
        content = None
        for item in memory_items:
            if item.get("ref") == ref:
                content = str(item.get("content", ""))
                break
        if content:
            # Only reject if the entity name from content appears in query
            # Extract first named entity from content (before the colon)
            parts = content.split(":")
            if len(parts) > 1:
                entity_part = parts[1].strip().split(",")[0].strip()
                if entity_part.lower() in query_lower and len(entity_part) > 2:
                    return False, f"expect_ref '{ref}' entity '{entity_part}' appears in query"

    # Check expect_content — only reject if the content value is a name/key that directly gives answer
    for ec in q.get("expect_content") or []:
        if ec and ec.lower() in query_lower and len(ec) > 3:
            return False, f"expect_content '{ec}' appears verbatim in query"

        # ── Fix 2: ban bare-number expect_content ──
        if ec is not None:
            stripped = ec.strip()
            if re.match(r'^\d+$', stripped) and len(stripped) > 0:
                return False, f"expect_content '{ec}' is a bare number — non-discriminable"

            # Ban common single-word values that appear everywhere.
            # Settlement type values (city, town, village, fortress, cave, tower)
            # are NOT banned — they are legitimate discriminable answers for
            # "what type of settlement" questions.
            common_low_card = {"good", "neutral", "chaos", "lawful", "evil", "mixed",
                               "active", "inactive", "true", "false", "yes", "no"}
            if stripped.lower() in common_low_card:
                return False, f"expect_content '{ec}' is a low-cardinality common word — non-discriminable"

    return True, "ok"


def gate_well_formed(q: Dict) -> Tuple[bool, str]:
    """Gate 4: Well-formed — valid asks; exactly one expectation kind; hops matches walk."""
    asks = q.get("asks")
    if asks not in ("loci", "memory", "corpus"):
        return False, f"invalid asks '{asks}'"

    # Count expectation kinds
    n_keys = len(q.get("expect_key") or [])
    n_refs = len(q.get("expect_ref") or [])
    n_content = len(q.get("expect_content") or [])
    n_empty = 1 if q.get("expect_empty") else 0
    kinds = sum(1 for x in (n_keys, n_refs, n_content, n_empty) if x > 0)
    # ONE KIND for a single-organ store; a CORPUS may carry two.
    #
    # The one-kind rule is right for loci and memory: a Loci store scored on an
    # expect_ref is being asked the wrong organ, and zeroes for it. But a corpus
    # FANS both organs, and the only question that can measure fusion value is
    # one neither member can fully answer alone -- i.e. expect_key AND
    # expect_ref together. live_eval._grade already unions both; the gate was
    # the only thing forbidding it. expect_empty stays exclusive: 'answer this
    # from two organs' and 'answer this from none' cannot both be true.
    if q.get("expect_empty"):
        if kinds != 1:
            return False, f"expect_empty must stand alone, got {kinds} kinds"
    elif asks == "corpus":
        if kinds < 1:
            return False, "at least one expectation kind required"
        if kinds > 2:
            return False, f"corpus questions may carry at most 2 kinds, got {kinds}"
    elif kinds != 1:
        return False, f"exactly one expectation kind required, got {kinds}"

    # hops must be >= 1
    hops = q.get("hops", 0)
    if hops < 1:
        return False, f"hops must be >= 1, got {hops}"

    return True, "ok"


def gate_not_degenerate(q: Dict, loci_items: List[Dict]) -> Tuple[bool, str]:
    """Gate 5: Not degenerate — reject if answer is the single most-common value."""
    # Check if the expect_key points to a value that appears in >70% of same-type loci
    for ek in q.get("expect_key") or []:
        # Find this key's value
        val = None
        for item in loci_items:
            if item.get("ident") == ek or item.get("key") == ek:
                val = item.get("value")
                break
        if val is None:
            continue

        # SAME BUG, SAME FIX. This counted siblings by testing whether a loci
        # `key` STARTS WITH "{project}/" -- but keys are bare fact names and
        # contain no slash at all, so `total` was always 0 and the >70% rule
        # could never trip. Group by the `project` field, which is the category.
        project_prefix = ek.split("/", 1)[0]   # e.g. "stats"
        same_val = 0
        total = 0
        for item in loci_items:
            if item.get("project") == project_prefix:
                total += 1
                if item.get("value") == val:
                    same_val += 1
        if total > 3 and same_val / total > 0.7:
            return False, f"degenerate: {ek} value '{val}' appears in {same_val}/{total} of same-type loci"

    return True, "ok"


def gate_hop_honesty(
    q: Dict,
    loci_items: List[Dict],
    memory_items: List[Dict],
) -> Tuple[bool, str, Optional[int]]:
    """Gate 3: Hop honesty — for hops >= 2, test if hops=1 resolves.
    Returns (pass, message, downgraded_hops).
    """
    hops = q.get("hops", 1)
    if hops < 2:
        return True, "n/a (hops=1)", None

    # For expect_content: check if the content phrase appears in ANY loci or memory item
    for ec in q.get("expect_content") or []:
        needle = ec.lower()

        # Check loci items
        for item in loci_items:
            val = str(item.get("value", "")).lower()
            if needle in val:
                # The answer exists in a single doc — downgrade to hops=1
                return False, f"hop honesty: expect_content '{ec}' found in loci '{item.get('key')}' at hops=1", 1

        # Check memory items
        for item in memory_items:
            content = str(item.get("content", "")).lower()
            if needle in content:
                return False, f"hop honesty: expect_content '{ec}' found in memory '{item.get('ref')}' at hops=1", 1

    return True, "passed", None


def gate_bridge_not_in_query(q: Dict, edge: Dict) -> Tuple[bool, str]:
    """Gate 6 (Fix 3): Multi-hop queries must not name the bridge entity.

    For hops >= 2, the bridge entity's name (target_name) must NOT appear
    verbatim in the query. If it does, the query collapses the hop — it
    effectively becomes a 1-hop question.
    """
    hops = q.get("hops", 1)
    if hops < 2:
        return True, "n/a (hops=1)"

    # Check if target_name appears in query
    target_name = edge.get("target_name")
    if target_name and not has_null_value(target_name):
        query_lower = q.get("query", "").lower()
        if target_name.lower() in query_lower and len(target_name) > 2:
            return False, f"bridge entity '{target_name}' named in query — collapses multi-hop"

    return True, "ok"


def run_gates(
    q: Dict,
    edge: Dict,
    loci_items: List[Dict],
    memory_items: List[Dict],
) -> Tuple[bool, str]:
    """Run all acceptance gates. Returns (pass, rejection_message)."""
    # Gate 2: Answer not in query
    ok, msg = gate_answer_not_in_query(q, loci_items, memory_items)
    if not ok:
        return False, f"FAIL: answer-in-query — {msg}"

    # Gate 4: Well-formed
    ok, msg = gate_well_formed(q)
    if not ok:
        return False, f"FAIL: well-formed — {msg}"

    # Gate 5: Not degenerate
    ok, msg = gate_not_degenerate(q, loci_items)
    if not ok:
        return False, f"FAIL: degenerate — {msg}"

    # Gate 6 (Fix 3): Bridge not in query
    ok, msg = gate_bridge_not_in_query(q, edge)
    if not ok:
        return False, f"FAIL: bridge-in-query — {msg}"

    # Gate 3: Hop honesty
    ok, msg, downgraded = gate_hop_honesty(q, loci_items, memory_items)
    if not ok and downgraded is not None:
        # Downgrade hops to 1 and re-check
        q["hops"] = downgraded
        q["_gen"]["shortcut_test"] = f"downgraded from {downgraded + 1} to {downgraded}"
        # Re-check answer-not-in-query after downgrade (it may now be direct)
        ok2, msg2 = gate_answer_not_in_query(q, loci_items, memory_items)
        if not ok2:
            return False, f"FAIL: answer-in-query after downgrade — {msg2}"
        return True, f"WARN: hop honesty downgraded to {downgraded}"
    if not ok:
        return False, f"FAIL: hop honesty — {msg}"

    q["_gen"]["shortcut_test"] = "passed"
    return True, "ok"


# ══════════════════════════════════════════════════════════════════════
# 5.  MAIN GENERATOR
# ══════════════════════════════════════════════════════════════════════

def generate_questions(
    entity_type: str,
    entity: Optional[Dict],
    world: Dict,
    loci_items: List[Dict],
    memory_items: List[Dict],
    rng: random.Random,
    target_count: int = 20,
    max_attempts_per_edge: int = 3,
) -> List[Dict]:
    """Main question generation loop.

    Enumerates all walkable edges, builds questions, runs gates, and emits
    the final set. Targets 10 questions per store type (loci/memory/corpus)
    for a total of 30. The target_count parameter sets the per-store cap
    (default 10) — total max = 3 * target_count.
    """
    per_store_target = 10  # hard requirement: 10 per store
    if target_count != 20:
        per_store_target = target_count  # override if caller specifies

    # ── Cache world name for cross-lens key resolution BEFORE enumeration ──
    global WORLD_NAME_CACHE
    WORLD_NAME_CACHE = world.get("world_name", "the world")

    # 1. Enumerate edges
    #
    # ORDER MATTERS NOW. Temporal edges are built first because the cross-organ
    # builder consumes their anchors -- it pairs a Loci fact with an ALREADY
    # ANCHORED memory rather than re-deriving one, so the corpus question and
    # the memory question point at the same discriminating phrase and stand or
    # fall together. Two independent anchor derivations for the same document
    # is two sources of truth.
    edges = []
    if entity_type == "character" and entity:
        edges = enumerate_character_edges(entity, world)
        quiet_name = entity.get("character", {}).get("name", "Unknown")
    elif entity_type == "beast" and entity:
        edges = enumerate_ooi_edges(entity, world, "beast")
        quiet_name = entity.get("name", "Unknown")
    elif entity_type == "artifact" and entity:
        edges = enumerate_ooi_edges(entity, world, "artifact")
        quiet_name = entity.get("name", "Unknown")
    elif entity_type == "poi" and entity:
        poi_block = entity.get("poi") or entity.get("site") or {}
        quiet_name = poi_block.get("name") or entity.get("name") or "Unknown"
        edges = enumerate_poi_edges(entity, world)
    elif entity_type == "world":
        edges = enumerate_world_edges(world)
        quiet_name = world.get("world_name", "the world")
    else:
        quiet_name = "Unknown"

    if entity:
        # Memory questions for EVERY entity type, not just character/beast.
        # Artifacts and POIs carry memories too; they were simply never walked,
        # which is why their memory column had nothing to score.
        mem_edges = enumerate_temporal_edges(entity, world, entity_type, memory_items)
        edges.extend(mem_edges)
        # Corpus: the fusion-value and coverage questions.
        edges.extend(enumerate_cross_organ_edges(entity, loci_items, mem_edges, rng))
        edges.extend(enumerate_briefing_edges(entity, loci_items))

    edges.extend(enumerate_quiet_edges(entity_type, quiet_name, rng))

    # ── Path A: strip cross-lens from per-entity files ──
    # Cross-lens (multi-hop) questions need a corpus that fans both ends of
    # the hop.  Per-entity SCCs only hold that entity's loci + memory, so
    # cross-lens is unanswerable from these files.  World-level can keep
    # cross-lens because its own loci already contain all civ facts.
    if entity_type in ("character", "beast", "artifact", "poi"):
        filtered = [e for e in edges if e["archetype"] != "cross_lens"]
        print(f"  Stripped {len(edges) - len(filtered)} cross-lens edges from {entity_type}", file=sys.stderr)
        edges = filtered

    if not edges:
        print("  WARNING: no walkable edges found", file=sys.stderr)
        return []

    print(f"  Enumerated {len(edges)} walkable edges", file=sys.stderr)

    # 2. Build ALL possible questions from ALL edges first
    #    Then bucket by asks type for per-store selection
    all_candidates = []  # list of (question_dict, edge)
    for edge in edges:
        q = build_question_from_edge(edge, entity, world, loci_items, memory_items)
        if q is None:
            continue
        # Run gates
        for attempt in range(max_attempts_per_edge):
            ok, msg = run_gates(q, edge, loci_items, memory_items)
            if ok:
                all_candidates.append((q, edge))
                break
            # On failure, skip this edge
            if attempt == max_attempts_per_edge - 1:
                pass  # final failure — skip

    print(f"  Built {len(all_candidates)} gate-passing candidates", file=sys.stderr)

    # 3. Bucket candidates by asks type
    buckets = {"loci": [], "memory": [], "corpus": []}
    for q, edge in all_candidates:
        asks = q.get("asks", "loci")
        if asks in buckets:
            buckets[asks].append((q, edge))

    # 4. Select up to per_store_target from each bucket
    questions = []
    seen_queries = set()

    for store_type, target_n in [("loci", per_store_target), ("memory", per_store_target), ("corpus", per_store_target)]:
        pool = buckets.get(store_type, [])
        rng.shuffle(pool)
        selected = 0
        for q, edge in pool:
            if selected >= target_n:
                break
            if q["query"] not in seen_queries:
                questions.append(q)
                seen_queries.add(q["query"])
                selected += 1

    # 5. Report per-store counts
    counts = {"loci": 0, "memory": 0, "corpus": 0}
    for q in questions:
        asks = q.get("asks", "loci")
        if asks in counts:
            counts[asks] += 1

    print(f"  Selected {len(questions)} questions (loci={counts.get('loci', 0)}, memory={counts.get('memory', 0)}, corpus={counts.get('corpus', 0)})", file=sys.stderr)

    return questions


# ══════════════════════════════════════════════════════════════════════
# 6.  COVERAGE HISTOGRAM
# ══════════════════════════════════════════════════════════════════════

def compute_coverage(questions: List[Dict]) -> Dict:
    """Compute coverage histogram for the final set."""
    stats = {
        "total": len(questions),
        "by_archetype": {},
        "by_hops": {},
        "by_entity_type": {},
    }

    for q in questions:
        gen = q.get("_gen", {})
        arch = gen.get("archetype", "unknown")
        hops = q.get("hops", 1)

        stats["by_archetype"][arch] = stats["by_archetype"].get(arch, 0) + 1
        stats["by_hops"][hops] = stats["by_hops"].get(hops, 0) + 1

    return stats


# ══════════════════════════════════════════════════════════════════════
# 7.  COMMAND LINE
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate SerenProbe questions from entity structured data"
    )
    parser.add_argument("--entity-type", type=str, required=True,
                        choices=["world", "character", "beast", "artifact", "poi"],
                        help="Type of entity to generate questions for")
    parser.add_argument("--entity-json", type=str, default=None,
                        help="Path to entity JSON (not needed for --entity-type world)")
    parser.add_argument("--entity-loci", type=str, required=True,
                        help="Path to entity loci YAML")
    parser.add_argument("--entity-memory", type=str, required=True,
                        help="Path to entity memory YAML")
    parser.add_argument("--world", type=str, required=True,
                        help="Path to world JSON")
    parser.add_argument("--output", type=str, default="questions.yaml",
                        help="Output YAML path")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for deterministic sampling")
    parser.add_argument("--target-count", type=int, default=20,
                        help="Target number of questions to generate")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="Max gate retry attempts per edge")
    args = parser.parse_args()

    # Load data
    print("Loading world...", file=sys.stderr)
    world = load_world(args.world)

    entity = None
    if args.entity_type != "world":
        if not args.entity_json:
            print("ERROR: --entity-json required for entity type", args.entity_type, file=sys.stderr)
            sys.exit(1)
        print(f"Loading {args.entity_type} JSON...", file=sys.stderr)
        entity = load_entity_json(args.entity_json)
    else:
        # For world, use world as entity
        entity = world

    print(f"Loading loci from {args.entity_loci}...", file=sys.stderr)
    loci_items = load_yaml(args.entity_loci) or []

    print(f"Loading memory from {args.entity_memory}...", file=sys.stderr)
    memory_items = load_yaml(args.entity_memory) or []

    # Generate
    rng = random.Random(args.seed)
    print(f"Generating questions (target={args.target_count})...", file=sys.stderr)

    questions = generate_questions(
        args.entity_type,
        entity,
        world,
        loci_items,
        memory_items,
        rng,
        target_count=args.target_count,
        max_attempts_per_edge=args.max_attempts,
    )

    # Compute coverage (reads _gen from questions before stripping)
    coverage = compute_coverage(questions)
    print(f"\nCoverage: {coverage}", file=sys.stderr)

    # ── Fix 4: Strip _gen to a sidecar audit file ──
    # Collect provenance before stripping
    provenance = []
    for q in questions:
        gen = q.pop("_gen", None)
        if gen is not None:
            provenance.append({
                "query": q.get("query", ""),
                "hops": q.get("hops", 1),
                "asks": q.get("asks", "loci"),
                "_gen": gen,
            })

    # Build output YAML (without _gen)
    doc = {"questions": questions}

    with open(args.output, "w") as f:
        f.write("# SerenProbe Questions - entity question set\n")
        f.write(f"# Generated for {args.entity_type}\n\n")
        yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False)

    # Write sidecar provenance file
    if provenance:
        gen_path = args.output.rsplit(".", 1)[0] + ".gen.yaml"
        with open(gen_path, "w") as f:
            f.write("# SerenProbe Question Provenance - audit trail\n")
            f.write(f"# Generated for {args.entity_type}\n\n")
            yaml.safe_dump({"provenance": provenance}, f, default_flow_style=False, sort_keys=False)
        print(f"Wrote {len(provenance)} provenance entries to {gen_path}", file=sys.stderr)

    print(f"\nWrote {len(questions)} questions to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
