#!/usr/bin/env python3
"""
memory_to_loci.py - Convert world / character / POI / OOI facts into SerenLoci YAML.

Uses entity_utils.entity_slug() for guaranteed-unique, collision-proof keys.
Every export site MUST use the same slug recipe — see entity_utils.py.

Usage:
    python3 memory_to_loci.py --world world.json --output loci_facts.yaml
    python3 memory_to_loci.py --character char_mem.json --output loci_facts.yaml
    python3 memory_to_loci.py --world world.json --character char_mem.json --poi poi_mem.json --ooi ooi_mem.json --output loci_all.yaml
"""

import argparse
import json
import re
import sys
from typing import Dict, List

import yaml

from entity_utils import entity_slug, make_loci_key, asciify


# ── World facts use the world name as slug (id=0) ─────────────────────
WORLD_SLUG_CACHE = {}   # populated on first world load


def extract_entity_id_name(data: dict, source_type: str) -> tuple:
    """Extract (name, id) from a JSON file's entity block.

    source_type is one of: "world", "character", "poi", "ooi"
    """
    if source_type == "world":
        name = data.get("world_name", "world")
        return name, 0

    # For character/poi/ooi, the id/name lives in a nested block
    if source_type == "character":
        block = data.get("character") or data.get("figure") or {}
    elif source_type == "poi":
        block = data.get("site") or data.get("poi") or {}
    elif source_type == "ooi":
        block = data.get("ooi") or data.get("beast") or data.get("artifact") or {}
    else:
        block = {}

    name = block.get("name") or data.get("name") or "unknown"
    eid = block.get("id") or data.get("id") or 0
    return name, eid


def load_facts(filepath: str, label: str) -> dict:
    with open(filepath) as f:
        data = json.load(f)
    facts = data.get("facts")
    if facts is None:
        print(f"WARNING: {label} file '{filepath}' has no 'facts' key; skipping", file=sys.stderr)
        return {}
    return facts, data


def load_entity_facts(filepath: str, source_type: str) -> List[tuple]:
    """Load (facts_dict, name, entity_id) tuples from a JSON file.

    Returns list of (facts, name, eid) — one per entity found.
    """
    with open(filepath) as f:
        data = json.load(f)

    results = []

    if source_type == "world":
        facts = data.get("facts") or {}
        name, eid = extract_entity_id_name(data, "world")
        results.append((facts, name, eid))
        return results

    # Single-entity file: facts at top level
    top_facts = data.get("facts")
    if top_facts:
        name, eid = extract_entity_id_name(data, source_type)
        results.append((top_facts, name, eid))
        return results

    # Multi-entity file: iterate over list
    list_key = None
    if source_type == "character":
        list_key = "figures" or "characters" or "entries"
    elif source_type == "poi":
        list_key = "sites" or "pois" or "entries"
    elif source_type == "ooi":
        list_key = "beasts" or "artifacts" or "entities" or "entries"

    entities = data.get(list_key) if list_key else []
    if not entities:
        # Fallback: try top-level facts
        if data.get("facts"):
            name, eid = extract_entity_id_name(data, source_type)
            results.append((data["facts"], name, eid))
        return results

    for ent in entities:
        facts = ent.get("facts") or ent.get("context") or {}
        name, eid = extract_entity_id_name(ent, source_type)
        results.append((facts, name, eid))

    return results


def _fact_category(fact_key: str) -> str:
    """Map a fact key to its category project name.

    Used by convert_facts_to_loci to group facts into semantic buckets.
    Character, artifact, beast, and POI facts each have their own taxonomy.
    """
    # ── Character stats ──
    if fact_key in ("race", "age", "alignment", "alignment_score"):
        return "stats"
    # ── Character identity ──
    if fact_key in ("title", "title_tier", "profession", "favorite_color", "favorite_drink",
                     "favorite_food", "is_titleworthy"):
        return "identity"
    # ── Character combat ──
    if fact_key in ("weapon", "armor"):
        return "combat"
    # ── Character origin ──
    if fact_key in ("home_site", "civ", "birth_year", "death_year", "deeds"):
        return "origin"

    # ── Artifact provenance ──
    if fact_key in ("broken_by", "stolen_by", "creator", "wielded_by",
                     "broken_by_ids", "stolen_by_ids", "wielded_by_ids"):
        return "provenance"
    # ── Artifact physical ──
    if fact_key in ("type", "material", "subtype"):
        return "physical"
    # ── Artifact holding ──
    if fact_key in ("held_by_civ", "held_by"):
        return "holding"

    # ── Beast stats ──
    if fact_key in ("type", "subtype", "kills", "alignment", "description"):
        return "stats"
    # ── Beast lifecycle ──
    if fact_key in ("year_spawned", "season_spawned", "active"):
        return "lifecycle"

    # ── POI demographics ──
    if fact_key in ("population", "size_class"):
        return "demographics"
    # ── POI geography ──
    if fact_key in ("site_type", "biome_id", "founded_year", "is_capital"):
        return "geography"

    # Fallback — generic category
    return "misc"


def convert_facts_to_loci(facts: dict, name: str, entity_id: int) -> List[dict]:
    """Convert a facts dict to loci entries using entity_slug keys.

    Enriches the `why` field with a natural-language sentence for certain
    fact types (discriminability fix — see Question_Eval.md §2).

    Per §1 restructure, project is now a CATEGORY (not the entity slug):
      - character: stats · identity · combat · origin
      - artifact:  provenance · physical · holding
      - beast:     stats · lifecycle
      - POI:       demographics · geography
      - world:     '*' (fundamentals — cross-cutting facts)

    key becomes "{project}/{ident}" — the ident is the bare fact name.
    """
    entries = []
    for key, value in facts.items():
        safe_val = asciify(str(value))
        safe_name = asciify(name)

        # ── Map fact key to category project ──
        if entity_id == 0:
            # World facts → project '*' (fundamentals)
            project = "*"
            ident = key   # bare fact name, e.g. "num_sites"
        else:
            # Per-entity facts → category project
            project = _fact_category(key)
            ident = key   # bare fact name, e.g. "age"

        # ── Discriminability: enrich why for high-tie classes ──
        # Class 1 — world counts: "There are N sites in the world of X"
        if key.startswith("num_") and entity_id == 0:
            noun = key.replace("num_", "").replace("_", " ")
            why = f"There are {safe_val} {noun} in the world of {safe_name}"
        # Class 2 — character age: "X is N years old"
        elif key == "age":
            why = f"{safe_name} is {safe_val} years old"
        # Class 3 — artifact broke/created: "X was broken/created by Y"
        elif key in ("broken_by", "stolen_by", "creator", "wielded_by"):
            verb = {"broken_by": "broken", "stolen_by": "stolen", "creator": "created", "wielded_by": "wielded"}
            why = f"{safe_name} was {verb.get(key, key)} by {safe_val}"
        else:
            why = f"{key} of {safe_name} (id:{entity_id})"

        full_key = f"{project}/{ident}"
        entries.append({
            "key": ident,                 # bare fact name — probe matches against this
            "value": safe_val,
            "project": project,           # project name only, e.g. "stats", "*", "provenance"
            "ident": full_key,            # full path, e.g. "stats/race" or "*/num_sites"
            "why": why,
        })
    return entries


def main():
    parser = argparse.ArgumentParser(description="Convert facts to SerenLoci YAML (multi-source)")
    parser.add_argument("--world", type=str, default=None, help="World JSON (world_gen.py output)")
    parser.add_argument("--character", type=str, default=None, help="Character memories JSON (character_memory_gen.py output)")
    parser.add_argument("--poi", type=str, default=None, help="POI memories JSON (poi_memory_gen.py output)")
    parser.add_argument("--ooi", type=str, default=None, help="OOI memories JSON (ooi_memory_gen.py output)")
    parser.add_argument("--output", default="loci_facts.yaml", help="Output YAML file")
    args = parser.parse_args()

    all_entries = []

    if args.world:
        facts, data = load_facts(args.world, "world")
        name, eid = extract_entity_id_name(data, "world")
        entries = convert_facts_to_loci(facts, name, eid)
        all_entries.extend(entries)
        print(f"  world: {len(entries)} facts from {args.world} (slug={entity_slug(name, eid)})")

    if args.character:
        fact_list = load_entity_facts(args.character, "character")
        for facts, name, eid in fact_list:
            entries = convert_facts_to_loci(facts, name, eid)
            all_entries.extend(entries)
        print(f"  character: {len(fact_list)} entities from {args.character}")

    if args.poi:
        fact_list = load_entity_facts(args.poi, "poi")
        for facts, name, eid in fact_list:
            entries = convert_facts_to_loci(facts, name, eid)
            all_entries.extend(entries)
        print(f"  poi: {len(fact_list)} POIs from {args.poi}")

    if args.ooi:
        fact_list = load_entity_facts(args.ooi, "ooi")
        for facts, name, eid in fact_list:
            entries = convert_facts_to_loci(facts, name, eid)
            all_entries.extend(entries)
        print(f"  ooi: {len(fact_list)} OOIs from {args.ooi}")

    if not all_entries:
        print("ERROR: No facts found. Provide at least one of --world, --character, --poi, --ooi", file=sys.stderr)
        sys.exit(1)

    # Build output: list of ordered mappings with ident field
    doc = []
    for e in all_entries:
        doc.append({
            "key":     e["key"],
            "value":   e["value"],
            "project": e["project"],
            "ident":   e["ident"],
            "why":     e["why"],
        })

    with open(args.output, "w") as f:
        f.write("# SerenLoci Facts - multi-source export\n")
        f.write("# Each entry is a fact about the world or its entities\n\n")
        yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False)

    print(f"\nWrote {len(all_entries)} total facts to {args.output}")


if __name__ == "__main__":
    main()
