#!/usr/bin/env python3
"""
probe_config_gen.py — Generate a SerenProbe ProbeConfig YAML from a pipeline output directory.

Scans the world directory for entity subdirectories (Characters, Beasts, Artifacts, POIs)
and builds Memory / Loci / Corpus store configs for each entity that has both a
_questions.yaml and a _memory.yaml file.

Usage:
    python3 probe_config_gen.py \
        --world-dir /path/to/MyWorld \
        --city-name MyWorld \
        --output /path/to/probe_config.yaml
"""

import argparse
import os
import sys
from typing import Dict, List

import yaml


# ── Static CorpusRegrades (must be included verbatim per spec) ────────

STATIC_CORPUS_REGRADES = [
    {"Name": "hop-sweep",      "hops": [1, 2, 3]},
    {"Name": "hop-x-packet",   "hops": [1, 2], "n_results": [10, 30]},
    {"Name": "hop-terms",      "hops": [2], "hop_terms": [2, 4, 8], "hop_budget": [5, 10]},
    {"Name": "rrf-sweep",      "rrf_k": [30, 60, 100]},
    {"Name": "floor-sweep",    "loci_floor": [0.0, 0.1, 0.3]},
    {"Name": "weight-sweep",   "loci_weight": [0.3, 0.5, 0.7, 1.0, 2.0, 3.0, 5.0, 10.0]},
    {"Name": "hop-x-weight",   "hops": [1, 2], "loci_weight": [1.0, 3.0], "n_results": [10, 30]},
    {"Name": "packet-sweep",   "n_results": [10, 15, 20, 30]},
    {"Name": "floor-x-weight", "loci_floor": [0.1, 0.3], "loci_weight": [0.5, 1.0]},
]


# ── Scan the world directory for entities ─────────────────────────────

def find_entity_dirs(world_dir: str) -> List[Dict]:
    """Scan world_dir for entity subdirectories with _questions.yaml + _memory.yaml.

    Returns list of dicts:
        { "name": "Mokrak", "safe_name": "Mokrak", "dir": "/path/to/char",
          "questions": "/path/to/questions.yaml", "memory": "/path/to/memory.yaml",
          "loci": "/path/to/loci.yaml", "type": "character" }
    """
    entities = []
    entity_types = {
        "Characters": "character",
        "Beasts": "beast",
        "Artifacts": "artifact",
        "POIs": "poi",
    }

    for subdir, etype in entity_types.items():
        base = os.path.join(world_dir, subdir)
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            ent_dir = os.path.join(base, entry)
            if not os.path.isdir(ent_dir):
                continue
            questions = os.path.join(ent_dir, f"{entry}_questions.yaml")
            memory = os.path.join(ent_dir, f"{entry}_memory.yaml")
            loci = os.path.join(ent_dir, f"{entry}_loci.yaml")
            if os.path.isfile(questions) and os.path.isfile(memory):
                entities.append({
                    "name": entry,
                    "safe_name": entry,
                    "dir": ent_dir,
                    "questions": questions,
                    "memory": memory,
                    "loci": loci,
                    "type": etype,
                })

    return entities


# ── Build the probe config ────────────────────────────────────────────

def build_probe_config(
    city_name: str,
    world_dir: str,
    starting_port: int = 7620,
) -> Dict:
    """Build a ProbeConfig dict from the pipeline output directory."""
    entities = find_entity_dirs(world_dir)

    # Dataset root for path construction — use relative datasets/ path
    ds_root = f"datasets/{city_name}"

    # Default seeds point to the world-level files
    world_loci = os.path.join(world_dir, f"{city_name}_loci.yaml")
    world_memory = os.path.join(world_dir, f"{city_name}_memory.yaml")
    world_questions = os.path.join(world_dir, f"{city_name}_questions.yaml")

    config: Dict = {
        "ProbeConfig": {
            "StartingPort": starting_port,
            "DefaultQuestions": f"{ds_root}/{city_name}_questions.yaml",
            "DefaultLociSeed":   f"{ds_root}/{city_name}_loci.yaml",
            "DefaultMemorySeed": f"{ds_root}/{city_name}_memory.yaml",
        }
    }

    # ── World store (always first, port = starting_port) ──
    # The world-level files sit at the root of the city directory
    world_name_safe = city_name  # e.g. "Pilorus"
    world_port_base = starting_port

    # ── Memory stores ──
    mem_configs = []
    mem_count = 0
    port = world_port_base

    # World memory store
    mem_configs.append({
        "Name": f"{world_name_safe}-mem",
        "Port": port,
        "Seed": [f"{ds_root}/{world_name_safe}_memory.yaml"],
        "Questions": [f"{ds_root}/{world_name_safe}_questions.yaml"],
    })
    mem_count += 1
    port += 3

    # Entity memory stores
    for ent in entities:
        mem_configs.append({
            "Name": f"{ent['safe_name']}-mem",
            "Port": port,
            "Seed": [f"{ds_root}/{ent['type']}s/{ent['safe_name']}/{ent['safe_name']}_memory.yaml"],
            "Questions": [f"{ds_root}/{ent['type']}s/{ent['safe_name']}/{ent['safe_name']}_questions.yaml"],
        })
        mem_count += 1
        port += 3

    config["ProbeConfig"]["Memory"] = {
        "MemoryCount": mem_count,
        "MemoryConfigs": mem_configs,
    }

    # ── Loci stores ──
    loci_configs = []
    loci_count = 0
    port = world_port_base + 1  # first loci port = starting_port + 1

    # World loci store
    loci_configs.append({
        "Name": f"{world_name_safe}-loci",
        "Port": port,
        "Flags": ["vector"],
        "Seed": f"{ds_root}/{world_name_safe}_loci.yaml",
        "Questions": [f"{ds_root}/{world_name_safe}_questions.yaml"],
    })
    loci_count += 1
    port += 3

    # Entity loci stores
    for ent in entities:
        loci_configs.append({
            "Name": f"{ent['safe_name']}-loci",
            "Port": port,
            "Flags": ["vector"],
            "Seed": f"{ds_root}/{ent['type']}s/{ent['safe_name']}/{ent['safe_name']}_loci.yaml",
            "Questions": [f"{ds_root}/{ent['type']}s/{ent['safe_name']}/{ent['safe_name']}_questions.yaml"],
        })
        loci_count += 1
        port += 3

    config["ProbeConfig"]["Loci"] = {
        "LociCount": loci_count,
        "LociConfigs": loci_configs,
    }

    # ── Corpus stores ──
    corpus_configs = []
    corpus_count = 0
    port = world_port_base + 2  # first corpus port = starting_port + 2

    # World corpus store
    corpus_configs.append({
        "Name": f"{world_name_safe}-scc",
        "Port": port,
        "Questions": [f"{ds_root}/{world_name_safe}_questions.yaml"],
        "Stores": [
            {"Store": f"{world_name_safe}-loci"},
            {"Store": f"{world_name_safe}-mem"},
        ],
    })
    corpus_count += 1
    port += 3

    # Entity corpus stores
    for ent in entities:
        corpus_configs.append({
            "Name": f"{ent['safe_name']}-scc",
            "Port": port,
            "Questions": [f"{ds_root}/{ent['type']}s/{ent['safe_name']}/{ent['safe_name']}_questions.yaml"],
            "Stores": [
                {"Store": f"{ent['safe_name']}-loci"},
                {"Store": f"{ent['safe_name']}-mem"},
            ],
        })
        corpus_count += 1
        port += 3

    # ── Cross corpora (multi-tenant) ──
    #
    # MEMBERSHIP IS A FUNCTION OF WHICH GENERATOR RAN, not a hand-kept list:
    #   character_memory_gen + ooi_memory_gen  -> Characters   (chars, beasts, artifacts)
    #   poi_memory_gen + the world's own export -> Geography
    #   the union                               -> All
    # So All == Characters | Geography by construction. A third hand-maintained
    # list would be a third thing to forget to update.
    #
    # PORTS. Every store above occupies an interleaved triple
    # (mem=base+3i, loci=base+3i+1, corpus=base+3i+2), so the highest port in use
    # is base + 3*total - 1 and base + 3*total is the first free one. Computing it
    # rather than hardcoding is the whole point -- the hand-written cross config
    # put Characters-scc and All-scc both on 7654.
    total_stores = 1 + len(entities)          # world + entities
    cross_port = starting_port + 3 * total_stores

    def _stores_for(types: List[str], include_world: bool) -> List[Dict]:
        out: List[Dict] = []
        if include_world:
            out.append({"Store": f"{world_name_safe}-loci"})
            out.append({"Store": f"{world_name_safe}-mem"})
        for e in entities:
            if e["type"] in types:
                out.append({"Store": f"{e['safe_name']}-loci"})
                out.append({"Store": f"{e['safe_name']}-mem"})
        return out

    CROSS = [
        ("Characters", ["character", "beast", "artifact"], False),
        ("Geography",  ["poi"],                            True),
        ("All",        ["character", "beast", "artifact", "poi"], True),
    ]
    for label, types, with_world in CROSS:
        stores = _stores_for(types, with_world)
        # A "cross" corpus over one tenant is just that tenant's SCC with a longer
        # name, and it would report a dilution score with nothing to dilute.
        if len(stores) < 4:      # fewer than two members
            continue
        corpus_configs.append({
            "Name": f"{label}-scc",
            "Port": cross_port,
            "Questions": [f"{ds_root}/{label}_questions.yaml"],
            "Stores": stores,
        })
        corpus_count += 1
        cross_port += 1

    config["ProbeConfig"]["Corpus"] = {
        "CorpusRegrades": STATIC_CORPUS_REGRADES,
        "CorpusCount": corpus_count,
        "CorpusConfigs": corpus_configs,
    }

    return config


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate SerenProbe ProbeConfig YAML from pipeline output"
    )
    parser.add_argument("--world-dir", type=str, required=True,
                        help="Path to the world output directory (e.g. /output/MyWorld)")
    parser.add_argument("--city-name", type=str, required=True,
                        help="City/world name used in filenames (e.g. MyWorld)")
    parser.add_argument("--output", type=str, default="probe_config.yaml",
                        help="Output YAML path")
    parser.add_argument("--starting-port", type=int, default=7620,
                        help="First port for store configs (default: 7620)")
    args = parser.parse_args()

    config = build_probe_config(args.city_name, args.world_dir, args.starting_port)

    with open(args.output, "w") as f:
        f.write("# SerenProbe ProbeConfig - auto-generated by pipeline\n")
        f.write(f"# City: {args.city_name}\n\n")
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"\nWrote probe config to {args.output}")
    print(f"  Memory stores:  {config['ProbeConfig']['Memory']['MemoryCount']}")
    print(f"  Loci stores:    {config['ProbeConfig']['Loci']['LociCount']}")
    print(f"  Corpus stores:  {config['ProbeConfig']['Corpus']['CorpusCount']}")
    print(f"  Starting port:  {args.starting_port}")


if __name__ == "__main__":
    main()
