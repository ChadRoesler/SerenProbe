#!/usr/bin/env python3
"""
memory_to_seren.py - Convert world / character / POI / OOI memories into SerenMemory
flat-list YAML for ingestion into the three-tier memory system.

Uses entity_utils.entity_slug() for guaranteed-unique, collision-proof refs.
Every export site MUST use the same slug recipe — see entity_utils.py.

Usage:
    python3 memory_to_seren.py --world world.json --output seren_memories.yaml
    python3 memory_to_seren.py --character char_mem.json --output seren_memories.yaml
    python3 memory_to_seren.py --world world.json --character char_mem.json --poi poi_mem.json --ooi ooi_mem.json --output seren_all.yaml
"""

import argparse
import json
import sys
from typing import Dict, List

import yaml

from entity_utils import entity_slug, make_memory_ref, asciify


# ── Shared helpers ────────────────────────────────────────────────────

TIER_MAP = {
    "long_term": "long",
    "short_term": "short",
    "near_term": "near",
}


def derive_topics(item: Dict) -> str:
    topics = []
    etype = item.get("type", "event")
    event_type = item.get("event_type", "")
    if etype:
        topics.append(etype)
    if event_type and event_type != etype:
        topics.append(event_type)
    era = item.get("era")
    if isinstance(era, dict):
        era_name = era.get("name", "")
        if era_name:
            topics.append(era_name.lower().replace(" ", "_"))
    elif isinstance(era, str):
        topics.append(era.lower().replace(" ", "_"))
    yl = item.get("year_label", "")
    if yl:
        topics.append(yl.replace(" ", "_"))
    return ", ".join(topics) if topics else "general"


def extract_entity_id_name(data: dict, source_type: str) -> tuple:
    """Extract (name, id) from a JSON file's entity block."""
    if source_type == "world":
        name = data.get("world_name", "world")
        return name, 0

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


def convert_memories(memories: Dict, name: str, entity_id: int) -> List[Dict]:
    """Convert a memories dict to SerenMemory entries using entity_slug refs."""
    entries = []
    slug = entity_slug(name, entity_id)

    for tier_key, tier_data in memories.items():
        tier = TIER_MAP.get(tier_key, "long")
        if isinstance(tier_data, dict):
            items = tier_data.get("items", [])
        elif isinstance(tier_data, list):
            items = tier_data
        else:
            items = []
        for idx, item in enumerate(items):
            memory_text = asciify(item.get("memory", ""))
            year_label = asciify(item.get("year_label", ""))
            ref = make_memory_ref(name, entity_id, tier, idx)
            topics = asciify(derive_topics(item))

            entry = {
                "tier": tier,
                "ref": ref,
                "topic": topics,
            }

            if tier == "near":
                entry["intent"] = memory_text
                entry["content"] = f"{year_label}: {memory_text}" if year_label else memory_text
            else:
                entry["content"] = f"{year_label}: {memory_text}" if year_label else memory_text

            entries.append(entry)
    return entries


# ── Load helpers ──────────────────────────────────────────────────────

def load_world_memories(filepath: str) -> Dict:
    with open(filepath) as f:
        data = json.load(f)
    memories = data.get("memories")
    if not memories:
        print(f"WARNING: world file '{filepath}' has no 'memories' key; skipping", file=sys.stderr)
        return {}
    return memories, data


def load_entity_memories(filepath: str, source_type: str) -> List[tuple]:
    """Load (memories_dict, name, entity_id) tuples from a JSON file.

    Returns list of (memories, name, eid) — one per entity found.
    """
    with open(filepath) as f:
        data = json.load(f)

    results = []

    if source_type == "world":
        mem = data.get("memories") or {}
        name, eid = extract_entity_id_name(data, "world")
        results.append((mem, name, eid))
        return results

    # Single-entity file: memories at top level
    top_mem = data.get("memories")
    if top_mem:
        name, eid = extract_entity_id_name(data, source_type)
        results.append((top_mem, name, eid))
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
    if not entities and data.get("memories"):
        name, eid = extract_entity_id_name(data, source_type)
        results.append((data["memories"], name, eid))
        return results

    for ent in entities:
        mem = ent.get("memories") or ent.get("memory") or {}
        name, eid = extract_entity_id_name(ent, source_type)
        results.append((mem, name, eid))

    return results


def main():
    parser = argparse.ArgumentParser(description="Convert memories to SerenMemory YAML (multi-source)")
    parser.add_argument("--world", type=str, default=None, help="World JSON (world_gen.py output)")
    parser.add_argument("--character", type=str, default=None, help="Character memories JSON (character_memory_gen.py output)")
    parser.add_argument("--poi", type=str, default=None, help="POI memories JSON (poi_memory_gen.py output)")
    parser.add_argument("--ooi", type=str, default=None, help="OOI memories JSON (ooi_memory_gen.py output)")
    parser.add_argument("--output", default="seren_memories.yaml", help="Output YAML file")
    args = parser.parse_args()

    all_entries = []

    if args.world:
        memories, data = load_world_memories(args.world)
        name, eid = extract_entity_id_name(data, "world")
        entries = convert_memories(memories, name, eid)
        all_entries.extend(entries)
        print(f"  world: {len(entries)} memories from {args.world}")

    if args.character:
        mem_list = load_entity_memories(args.character, "character")
        for mem, name, eid in mem_list:
            entries = convert_memories(mem, name, eid)
            all_entries.extend(entries)
        print(f"  character: {len(mem_list)} entities from {args.character}")

    if args.poi:
        mem_list = load_entity_memories(args.poi, "poi")
        for mem, name, eid in mem_list:
            entries = convert_memories(mem, name, eid)
            all_entries.extend(entries)
        print(f"  poi: {len(mem_list)} POIs from {args.poi}")

    if args.ooi:
        mem_list = load_entity_memories(args.ooi, "ooi")
        for mem, name, eid in mem_list:
            entries = convert_memories(mem, name, eid)
            all_entries.extend(entries)
        print(f"  ooi: {len(mem_list)} OOIs from {args.ooi}")

    if not all_entries:
        print("ERROR: No memories found. Provide at least one of --world, --character, --poi, --ooi", file=sys.stderr)
        sys.exit(1)

    # Build output
    doc = []
    for e in all_entries:
        entry = {
            "tier": e["tier"],
            "ref":  e["ref"],
            "topic": e["topic"],
            "content": e["content"],
        }
        if "intent" in e:
            entry["intent"] = e["intent"]
        doc.append(entry)

    with open(args.output, "w") as f:
        f.write("# SerenMemory entries - multi-source export\n")
        f.write("# Three-tier memory system: long, short, near\n\n")
        yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False)

    print(f"\nWrote {len(all_entries)} total memories to {args.output}")


if __name__ == "__main__":
    main()
