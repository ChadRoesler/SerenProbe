#!/usr/bin/env python3
"""
OOI (Object of Interest) Memory Generator v1
Takes an artifact or beast from world_gen.py output (Objects of Interest) and generates:
  - Long-term memory:  X items (X = number of decades the OOI has existed,
                        clamped 3-30). Major events, growth milestones, etc.
  - Short-term memory:  15 recent events / observations
  - Near-term memory:   10 upcoming plans / concerns

Memories are flavored by OOI type, alignment,
and reference real world entities (figures, artifacts, beasts, events).
"""

import json
import random
import sys
import os
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────
# 1.  OOI TYPE MEMORY THEMES
# ──────────────────────────────────────────────────────────────────────

OOI_TYPE_THEMES = {

    "artifact": {
        "foundation_events": [
            "was forged in a time of great need by a master craftsman",
            "was discovered in the depths of the earth by a lucky prospector",
            "was created as a gift for a ruler, imbued with symbolic power",
            "was carved from a rare material that could not be found elsewhere",
            "was blessed by the spirits before being given to the world",
        ],
        "life_events": [
            "was wielded in a great battle and became known as a weapon of legend",
            "was lost for a generation before being recovered from a distant land",
            "was stolen by a rival power and sparked a conflict to reclaim it",
            "was studied by scholars who discovered its true nature and purpose",
            "was used in a ritual that changed the course of history",
            "was given as a gift to seal an alliance between two powers",
            "was hidden away during a dark age to protect it from destruction",
            "was broken in combat and later reforged by a master smith",
            "was passed down through generations of a single family",
            "was displayed at a great gathering where all beheld its beauty",
            # Extended pool for greater variety over many decades
            "was carried into battle by a hero who became inseparable from it",
            "was placed in a temple as an offering to the gods",
            "was sought by a king who believed it would secure his reign",
            "was used to assassinate a tyrant, changing the political order",
            "was blessed by a wandering priest who gave it a new purpose",
            "was lost at sea and retrieved by a daring expedition",
            "was traded between kingdoms as a symbol of peace and trust",
            "was locked away in a vault for decades, forgotten by all but a few",
            "was copied by scholars who sought to understand its mysterious engravings",
            "was wielded by a mad queen who used its power to terrorize the land",
            "was stolen by a thief who vanished — the artifact was gone for years",
            "was discovered in an ancient ruin by a child who became its keeper",
            "was repaired by a reclusive smith who added new runes to its surface",
            "was displayed in a tournament where warriors vied for the right to hold it",
            "was used to forge a peace treaty between two warring factions",
            "was hidden in a false tomb to deceive those who sought its power",
            "was shattered in a magical catastrophe and painstakingly reassembled",
            "was carried across the desert by a caravan that was never seen again",
            "was recognized by a sage who revealed its true origin story",
            "was given to a foreign prince as a bride-price in a royal marriage",
            "was tested in a trial of worth — only the worthy could lift it",
            "was stolen and replaced with a clever forgery for a generation",
            "was used to light a sacred flame that burned for a full season",
            "was buried in a field and found again when a farmer plowed the earth",
            "was fought over by two rival houses in a bitter feud that lasted years",
        ],
        "growth_milestones": [],
        "thoughts": [
            "The artifact holds the weight of its history in every scratch and mark.",
            "Power sleeps in the artifact — waiting to be awakened.",
            "Every hand that has held it has left a trace.",
            "The artifact is a thread through the tapestry of history.",
            "It endures while empires rise and fall.",
            "The artifact remembers its makers, even if the world forgets.",
            "There is more to this object than meets the eye.",
        ],
    },

    "beast": {
        "foundation_events": [
            "was born into the wild — a creature of instinct and ancient blood",
            "was awakened from a deep slumber by the tremors of the world",
            "was summoned from the dark places by forces beyond mortal understanding",
            "was the first of its kind to be seen in this age — a herald of change",
            "was driven from its territory by a greater predator, coming to this land",
        ],
        "life_events": [
            "the beast was sighted near a settlement, causing panic and fear",
            "the beast claimed a territory and defended it against all intruders",
            "the beast was wounded in a battle with hunters and retreated to its lair",
            "the beast's roars were heard for miles, marking its dominance",
            "the beast was tracked by a band of adventurers who sought to slay it",
            "the beast grew in size and power, becoming a greater threat over time",
            "the beast was driven into a new region by a natural disaster",
            "the beast was rumored to possess supernatural qualities",
            "the beast's lair was discovered — filled with the remains of its victims",
            "the beast was encountered by a traveling merchant who barely escaped",
        ],
        "growth_milestones": [],
        "thoughts": [
            "The beast is a force of nature — untamed and unbroken.",
            "The beast remembers the ancient ways of the wild.",
            "There is a primal intelligence in the beast's eyes.",
            "The beast is part of the world's raw power — dangerous and beautiful.",
            "The beast does not hate — it simply exists.",
            "The beast's presence shapes the land around it.",
            "The beast is a reminder that the wild still endures.",
        ],
    },
}

# ──────────────────────────────────────────────────────────────────────
# 2.  WORLD DATA LOADING
# ──────────────────────────────────────────────────────────────────────

def load_world(filepath: str) -> Dict:
    with open(filepath) as f:
        return json.load(f)

# ──────────────────────────────────────────────────────────────────────
# 3.  OOI SELECTION
# ──────────────────────────────────────────────────────────────────────

OOI_TYPES = {
    "artifact": {
        "key": "artifacts",
        "id_field": "id",
        "name_field": "name",
        "type_field": "type",
        "label": "Artifact",
    },
    "beast": {
        "key": "beasts",
        "id_field": "id",
        "name_field": "name",
        "type_field": "type",
        "label": "Beast",
    },
}

def select_ooi(
    world: Dict,
    ooi_type: str,
    ooi_id: Optional[int] = None,
    rng: random.Random = random.Random(),
) -> Dict:
    """Select an OOI from the world data by type and optional ID."""
    type_info = OOI_TYPES.get(ooi_type)
    if not type_info:
        raise ValueError(f"Unknown OOI type '{ooi_type}'. Valid: {list(OOI_TYPES.keys())}")

    entities = world.get(type_info["key"], [])
    if not entities:
        raise ValueError(f"No {ooi_type}s in world data")

    if ooi_id is not None:
        for e in entities:
            if e[type_info["id_field"]] == ooi_id:
                return e
        raise ValueError(f"{type_info['label']} ID {ooi_id} not found")

    return rng.choice(entities)

# ──────────────────────────────────────────────────────────────────────
# 4.  CONTEXT RESOLUTION
# ──────────────────────────────────────────────────────────────────────

def resolve_era_for_year(year: int, eras: List[Dict]) -> Optional[Dict]:
    """Find which era a given year falls in. Returns the era dict or None."""
    for e in eras:
        start = e.get("start_year", -9999)
        end = e.get("end_year", 9999)
        if start <= year <= end:
            return e
    return None

def format_year_with_era(year: int, eras: List[Dict]) -> str:
    """Format a year with its era context, e.g. 'Year -150 (Age of Noble)'."""
    era = resolve_era_for_year(year, eras)
    if era:
        era_start = era["start_year"]
        year_in_era = year - era_start + 1
        return f"Year {year} ({era['name']}, year {year_in_era})"
    return f"Year {year}"

def resolve_artifact_context(world: Dict, artifact: Dict) -> Dict:
    """Build rich context about an artifact OOI."""
    civ_id = artifact.get("civ_id")
    civ = None
    if civ_id is not None:
        for c in world.get("civilizations", []):
            if c["id"] == civ_id:
                civ = c
                break

    # Events involving this artifact
    artifact_events = []
    for ev in world.get("events", []):
        if ev.get("artifact_id") == artifact["id"]:
            artifact_events.append(ev)

    themes = OOI_TYPE_THEMES.get("artifact", OOI_TYPE_THEMES["artifact"])

    # ── Resolve artifact relationships ──
    raw_rels = artifact.get("relationships", [])
    resolved_rels = []
    creator_names = []
    wielder_names = []
    wielder_ids = []
    thief_names = []
    thief_ids = []
    breaker_names = []
    breaker_ids = []
    repairer_names = []
    repairer_ids = []
    loser_names = []
    loser_ids = []
    recoverer_names = []
    recoverer_ids = []

    for rel in raw_rels:
        resolved = dict(rel)
        rtype = rel.get("type", "unknown")
        fname = rel.get("figure_name", "Unknown")
        fid = rel.get("figure_id")  # numeric ID for structured edge walking

        if rtype == "creator":
            resolved["related_name"] = fname
            creator_names.append(fname)
        elif rtype == "wielder":
            resolved["related_name"] = fname
            wielder_names.append(fname)
            # Collect figure_id for structured edge walking (artifact→wielder)
            if fid is not None:
                wielder_ids.append(fid)
        elif rtype == "thief":
            resolved["related_name"] = fname
            thief_names.append(fname)
            if fid is not None:
                thief_ids.append(fid)
        elif rtype == "breaker":
            resolved["related_name"] = fname
            breaker_names.append(fname)
            if fid is not None:
                breaker_ids.append(fid)
        elif rtype == "repairer":
            resolved["related_name"] = fname
            repairer_names.append(fname)
            if fid is not None:
                repairer_ids.append(fid)
        elif rtype == "loser":
            resolved["related_name"] = fname
            loser_names.append(fname)
            if fid is not None:
                loser_ids.append(fid)
        elif rtype == "recoverer":
            resolved["related_name"] = fname
            recoverer_names.append(fname)
            if fid is not None:
                recoverer_ids.append(fid)

        resolved_rels.append(resolved)

    return {
        "ooi_type": "artifact",
        "ooi_subtype": artifact.get("type", "unknown"),
        "civ": civ,
        "civ_name": civ["name"] if civ else "the wilds",
        "civ_alignment": civ["alignment"] if civ else "neutral",
        "biome_name": "unknown",
        "biome_id": 0,
        "same_civ_sites": [s for s in world.get("sites", []) if s.get("civ_id") == civ_id],
        "same_civ_figures": [f for f in world.get("historical_figures", []) if f.get("civ_id") == civ_id],
        "site_events": artifact_events,
        "civ_artifacts": [],
        "site_beasts": [],
        "themes": themes,
        "eras": world.get("eras", []),
        "end_year": world.get("config", {}).get("end_year", 500),
        # ── Artifact relationship data ──
        "relationships_resolved": resolved_rels,
        "creator_names": creator_names,
        "wielder_names": wielder_names,
        "wielder_ids": wielder_ids,
        "thief_names": thief_names,
        "thief_ids": thief_ids,
        "breaker_names": breaker_names,
        "breaker_ids": breaker_ids,
        "repairer_names": repairer_names,
        "repairer_ids": repairer_ids,
        "loser_names": loser_names,
        "loser_ids": loser_ids,
        "recoverer_names": recoverer_names,
        "recoverer_ids": recoverer_ids,
        "related_names": creator_names + wielder_names + thief_names + breaker_names + repairer_names + loser_names + recoverer_names,
    }

def resolve_beast_context(world: Dict, beast: Dict) -> Dict:
    """Build rich context about a beast OOI."""
    themes = OOI_TYPE_THEMES.get("beast", OOI_TYPE_THEMES["beast"])

    # Events involving this beast
    beast_events = []
    for ev in world.get("events", []):
        if ev.get("beast_id") == beast["id"]:
            beast_events.append(ev)

    # Sites this beast attacked
    attacked_sites = []
    for ev in beast_events:
        sid = ev.get("site_id")
        if sid is not None:
            for s in world.get("sites", []):
                if s["id"] == sid:
                    attacked_sites.append(s)
                    break

    # ── Resolve beast relationships ──
    raw_rels = beast.get("relationships", [])
    resolved_rels = []
    rival_beast_names = []
    enemy_site_names = []
    ally_site_names = []
    neutral_site_names = []

    for rel in raw_rels:
        resolved = dict(rel)
        rtype = rel.get("type", "unknown")

        # Beast-to-beast rivalries
        if rel.get("beast_id") is not None:
            resolved["related_name"] = rel.get("beast_name", "Unknown Beast")
            if rtype in ("rival", "territorial", "enemy"):
                rival_beast_names.append(resolved["related_name"])

        # Beast-to-city relationships
        if rel.get("site_id") is not None:
            resolved["related_name"] = rel.get("site_name", "Unknown Site")
            if rtype == "enemy":
                enemy_site_names.append(resolved["related_name"])
            elif rtype == "ally":
                ally_site_names.append(resolved["related_name"])
            elif rtype == "neutral":
                neutral_site_names.append(resolved["related_name"])

        resolved_rels.append(resolved)

    return {
        "ooi_type": "beast",
        "ooi_subtype": beast.get("type", "unknown"),
        "civ": None,
        "civ_name": "the wilds",
        "civ_alignment": beast.get("alignment", "chaos"),
        "biome_name": "unknown",
        "biome_id": 0,
        "same_civ_sites": [],
        "same_civ_figures": [],
        "site_events": beast_events,
        "civ_artifacts": [],
        "site_beasts": [beast],
        "attacked_sites": attacked_sites,
        "themes": themes,
        "eras": world.get("eras", []),
        "end_year": world.get("config", {}).get("end_year", 500),
        # ── Beast relationship data ──
        "relationships_resolved": resolved_rels,
        "rival_beast_names": rival_beast_names,
        "enemy_site_names": enemy_site_names,
        "ally_site_names": ally_site_names,
        "neutral_site_names": neutral_site_names,
        "related_names": rival_beast_names + enemy_site_names + ally_site_names,
    }

def resolve_ooi_context(world: Dict, ooi: Dict, ooi_type: str) -> Dict:
    """Route to the appropriate context resolver based on OOI type."""
    if ooi_type == "artifact":
        return resolve_artifact_context(world, ooi)
    elif ooi_type == "beast":
        return resolve_beast_context(world, ooi)
    else:
        raise ValueError(f"Unknown OOI type: {ooi_type}")

# ──────────────────────────────────────────────────────────────────────
# 5.  DECADE COMPUTATION
# ──────────────────────────────────────────────────────────────────────

def _get_ooi_death_year(ooi: Dict, ooi_type: str, world: Dict) -> Optional[int]:
    """Find the year an OOI was destroyed/killed, if applicable."""
    if ooi_type == "beast":
        active = ooi.get("active", True)
        if not active:
            # Find slain event
            for ev in world.get("events", []):
                if ev.get("type") == "beast_slain" and ev.get("beast_id") == ooi["id"]:
                    return ev["year"]
    return None

def compute_decades(ooi: Dict, ooi_type: str, world: Dict) -> int:
    """Compute the number of decades this OOI has existed, clamped 3-30."""
    end_year = world.get("config", {}).get("end_year", 500)
    start_year = world.get("config", {}).get("start_year", 0)

    death_year = _get_ooi_death_year(ooi, ooi_type, world)
    effective_end = death_year if death_year is not None else end_year

    if ooi_type == "artifact":
        created = ooi.get("created_year", start_year)
        lifespan = effective_end - created
    elif ooi_type == "beast":
        spawned = ooi.get("year_spawned", start_year)
        lifespan = effective_end - spawned
    else:
        raise ValueError(f"Unknown OOI type: {ooi_type}")

    if lifespan < 1:
        lifespan = 1

    decades = max(1, lifespan // 10)
    return max(3, min(30, decades))

def get_decade_ranges(ooi: Dict, ooi_type: str, world: Dict, num_decades: int) -> List[Tuple[int, int]]:
    """Compute decade year ranges for the OOI's existence."""
    end_year = world.get("config", {}).get("end_year", 500)
    start_year = world.get("config", {}).get("start_year", 0)

    death_year = _get_ooi_death_year(ooi, ooi_type, world)
    effective_end = death_year if death_year is not None else end_year

    if ooi_type == "artifact":
        start = ooi.get("created_year", start_year)
    elif ooi_type == "beast":
        start = ooi.get("year_spawned", start_year)
    else:
        raise ValueError(f"Unknown OOI type: {ooi_type}")

    # Ensure start is within bounds
    if start < start_year:
        start = start_year

    total_span = effective_end - start
    if total_span <= 0:
        return [(start, effective_end)]

    decade_step = total_span / num_decades
    ranges = []
    for i in range(num_decades):
        decade_start = int(start + i * decade_step)
        decade_end = int(start + (i + 1) * decade_step)
        if decade_end > effective_end:
            decade_end = effective_end
        if decade_start < decade_end:
            ranges.append((decade_start, decade_end))
    return ranges

# ──────────────────────────────────────────────────────────────────────
# 6.  MEMORY GENERATION
# ──────────────────────────────────────────────────────────────────────

def _describe_event(ev: Dict, ooi_type: str = None) -> str:
    """Return a short human-readable description of a world event.
    When ooi_type='beast', beast_attack events use first-person voice.
    """
    ev_type = ev.get("type", "event")
    if ev_type == "battle":
        return f"a great battle was fought — {ev.get('civ1_name', 'unknown')} against {ev.get('civ2_name', 'unknown')}"
    elif ev_type == "siege":
        return f"the site was besieged — it held firm through the assault"
    elif ev_type == "beast_attack":
        if ooi_type == "beast":
            kills = ev.get("kills", "many")
            site_name = ev.get("site_name", "a settlement")
            return f"I attacked {site_name}, killing {kills}"
        return f"a {ev.get('beast_type', 'beast')} called {ev.get('beast_name', 'unknown')} attacked, killing {ev.get('kills', 'many')}"
    elif ev_type == "site_founded":
        return f"a new site was founded nearby: {ev.get('site_name', 'unknown')}"
    elif ev_type == "war_declared":
        return f"war was declared — {ev.get('attacker_name', 'unknown')} marched against {ev.get('defender_name', 'unknown')}"
    elif ev_type == "war_ended":
        return f"war ended — {ev.get('winner_name', 'unknown')} claimed victory over {ev.get('loser_name', 'unknown')}"
    elif ev_type == "diplomatic_meeting":
        return f"envoys from {ev.get('civ1_name', 'unknown')} and {ev.get('civ2_name', 'unknown')} met for talks"
    elif ev_type == "artifact_created":
        aname = ev.get('artifact_name', 'unknown')
        # Strip leading "The " to avoid "the The Shield of Fate" double-article
        if aname.startswith("The "):
            aname = aname[4:]
        return f"the {aname} was created — a {ev.get('artifact_type', 'unknown')} of {ev.get('material', 'unknown')}"
    elif ev_type == "birth":
        return f"{ev.get('figure_name', 'unknown')} was born"
    elif ev_type == "death":
        return f"{ev.get('figure_name', 'unknown')} died — {ev.get('cause', 'unknown cause')}"
    elif ev_type == "title_promotion":
        return f"{ev.get('figure_name', 'unknown')} rose to {ev.get('new_title', 'a new title')}"
    else:
        readable = ev_type.replace('_', ' ')
        return f"an event of type '{readable}' occurred"

def pick_unique(templates: List[str], used: set, rng: random.Random) -> str:
    """Pick from a list, avoiding recent repeats."""
    available = [t for t in templates if t not in used]
    if not available:
        used.clear()
        available = templates
    choice = rng.choice(available)
    used.add(choice)
    return choice

def generate_long_term_memories(
    ooi: Dict,
    context: Dict,
    num_decades: int,
    decade_ranges: List[Tuple[int, int]],
    rng: random.Random,
) -> List[Dict]:
    """Generate long-term memories per decade for the OOI."""
    """
    Generate long-term memories per decade — each is a verbose decade
    culmination that weaves together real events, growth milestones,
    and reflections. Major events are highlighted as the most poignant.
    """
    memories = []
    themes = context["themes"]
    ooi_type = context["ooi_type"]
    ooi_subtype = context["ooi_subtype"]
    civ_name = context["civ_name"]

    used_foundation = set()
    used_life = set()
    used_growth = set()

    # Group real events by decade
    site_events = context.get("site_events", [])
    events_by_decade = defaultdict(list)
    for ev in site_events:
        eyear = ev.get("year", 0)
        for i, (ds, de) in enumerate(decade_ranges):
            if ds <= eyear <= de:
                events_by_decade[i].append(ev)
                break

    eras = context.get("eras", [])

    for i, (decade_start, decade_end) in enumerate(decade_ranges):
        decade_era = resolve_era_for_year(decade_start, eras)
        if decade_era:
            era_start = decade_era["start_year"]
            year_in_era = decade_start - era_start + 1
            decade_label = f"{decade_start}s ({decade_era['name']}, year {year_in_era})"
        else:
            decade_label = f"{decade_start}s"

        decade_events = events_by_decade.get(i, [])

        # Build a multi-sentence narrative paragraph
        paragraphs = []

        if i == 0:
            pool = themes.get("foundation_events", [])
            template = pick_unique(pool, used_foundation, rng)
            paragraphs.append(f"In the beginning, {template}")
            if decade_events and rng.random() < 0.5:
                ev = rng.choice(decade_events)
                ev_desc = _describe_event(ev, context.get("ooi_type"))
                paragraphs.append(f"Shortly after, {ev_desc}.")
            paragraphs.append("This was the start of its story.")
            memory_type = "foundation"

        else:
            memory_type = "life_event"
            openings = [
                f"The {decade_label} was a {rng.choice(['hard', 'quiet', 'turbulent', 'prosperous', 'dark', 'bright', 'forgotten'])} decade for this {ooi_subtype}.",
                f"In the {decade_label}, the {ooi_subtype} felt {rng.choice(['heavy with change', 'still and waiting', 'alive with possibility', 'worn and tired', 'sharp and dangerous'])}.",
                f"Looking back at the {decade_label}, it stands out — {rng.choice(['the air smelled of smoke', 'the nights were long', 'the wind carried strange news', 'everything felt fragile'])}.",
            ]
            paragraphs.append(rng.choice(openings))

            if decade_events and rng.random() < 0.7:
                featured = rng.sample(decade_events, min(len(decade_events), 2))
                for ev in featured:
                    ev_desc = _describe_event(ev, context.get("ooi_type"))
                    highlight_prefix = rng.choice([
                        "The most poignant moment",
                        "What marked that decade",
                        "The defining event",
                    ])
                    paragraphs.append(f"{highlight_prefix} was {ev_desc}.")
            else:
                if rng.random() < 0.5:
                    pool = themes.get("life_events", [])
                    template = pick_unique(pool, used_life, rng)
                    paragraphs.append(f"During this time, {template}")
                else:
                    pool = themes.get("growth_milestones", [])
                    if pool:
                        template = pick_unique(pool, used_growth, rng)
                        paragraphs.append(f"The {ooi_subtype} grew — {template}")
                    else:
                        pool = themes.get("life_events", [])
                        template = pick_unique(pool, used_life, rng)
                        paragraphs.append(f"During this time, {template}")

            reflection_templates = [
                f"That decade taught {civ_name} that {rng.choice(['the world is cruel', 'time changes everything', 'hope endures', 'a good plan saves lives'])}.",
                f"The {rng.choice(['lessons', 'shadows'])} of that time linger still.",
                f"It was a decade that {rng.choice(['shaped', 'tested', 'marked'])} the {ooi_subtype}.",
            ]
            paragraphs.append(rng.choice(reflection_templates))

        full_text = " ".join(p for p in paragraphs if p)
        if context.get("same_civ_figures") and rng.random() < 0.3:
            other = rng.choice(context["same_civ_figures"])
            full_text += f" {other['name']} was active during this time."

        if context.get("site_beasts") and rng.random() < 0.2:
            beast = rng.choice(context["site_beasts"])
            full_text += f" The {beast['name']} terrorized the region."

        if context.get("civ_artifacts") and rng.random() < 0.2:
            art = rng.choice(context["civ_artifacts"])
            full_text += f" The {art['name']} was held in the area."

        # ── Relationship references for beasts ──
        if context.get("rival_beast_names") and rng.random() < 0.3:
            rival = rng.choice(context["rival_beast_names"])
            full_text += f" The {rival} was a constant rival — our territories clashed."
        if context.get("enemy_site_names") and rng.random() < 0.3:
            enemy_site = rng.choice(context["enemy_site_names"])
            full_text += f" The settlement of {enemy_site} was a hated foe."
        if context.get("ally_site_names") and rng.random() < 0.3:
            ally_site = rng.choice(context["ally_site_names"])
            full_text += f" The {ally_site} was left in peace — we had an understanding."

        # ── Relationship references for artifacts ──
        if context.get("creator_names") and rng.random() < 0.4:
            creator = rng.choice(context["creator_names"])
            full_text += f" My creator {creator} — I remember the forge."
        if context.get("wielder_names") and rng.random() < 0.3:
            wielder = rng.choice(context["wielder_names"])
            full_text += f" {wielder} wielded me with purpose."
        if context.get("thief_names") and rng.random() < 0.2:
            thief = rng.choice(context["thief_names"])
            full_text += f" The thief {thief} stole me — I was taken from my place."
        if context.get("breaker_names") and rng.random() < 0.2:
            breaker = rng.choice(context["breaker_names"])
            full_text += f" {breaker} broke me — I was damaged and lost."
        if context.get("repairer_names") and rng.random() < 0.3:
            repairer = rng.choice(context["repairer_names"])
            full_text += f" {repairer} repaired me — I was made whole again."
        if context.get("recoverer_names") and rng.random() < 0.3:
            recoverer = rng.choice(context["recoverer_names"])
            full_text += f" {recoverer} recovered me — I was found and returned."
        if context.get("loser_names") and rng.random() < 0.2:
            loser = rng.choice(context["loser_names"])
            full_text += f" {loser} lost me — I was forgotten for a time."

        civ_alignment = context.get("civ_alignment", "neutral")
        alignment_variants = {
            "good": [
                " These were prosperous times.",
                " The years brought abundance.",
                " Fortune smiled upon the land.",
                " Hope and plenty marked those years.",
                " The land grew rich and content.",
                " Peace and kindness flourished here.",
                " Each season brought new blessings.",
                " The people prospered under a gentle hand.",
            ],
            "evil": [
                " Darkness hung over the land.",
                " Shadows lengthened with each passing year.",
                " Cruelty and fear were the companions of those days.",
                " The land groaned under the weight of suffering.",
                " Violence and despair marked every season.",
                " The weak suffered while the strong grew bolder.",
                " Torment was woven into the fabric of life.",
                " The world grew colder and more merciless.",
            ],
            "order": [
                " Order and discipline prevailed.",
                " The law held firm through the seasons.",
                " Structure and hierarchy shaped every aspect of life.",
                " Rules governed all — and all obeyed.",
                " The land was measured and accounted for.",
                " Every stone had its place and every soul its duty.",
                " The machinery of society ran without pause.",
                " Precision and control defined those years.",
            ],
            "chaos": [
                " Chaos reigned — nothing was certain.",
                " The world was wild and unpredictable.",
                " Anarchy and change were the only constants.",
                " Turbulence marked every season.",
                " The land churned with disorder and upheaval.",
                " Nothing was safe — the very earth seemed to shift.",
                " Madness and flux were the rulers of those days.",
                " The old certainties burned away in the fire of change.",
            ],
            "neutral": [
                " The years passed, as years do.",
                " Time flowed without great incident.",
                " The seasons turned in their eternal rhythm.",
                " Life continued, unremarkable but steady.",
                " The world turned, indifferent and slow.",
                " Days came and went — little changed.",
                " The land endured, as it always had.",
                " Another decade, another quiet turning of the wheel.",
            ],
        }.get(civ_alignment, [" The years passed."])
        alignment_closer = rng.choice(alignment_variants)
        # Track used closers to avoid repeats within the same artifact
        if "used_closers" not in context:
            context["used_closers"] = set()
        if alignment_closer in context["used_closers"]:
            # Pick a different one
            remaining = [v for v in alignment_variants if v not in context["used_closers"]]
            if remaining:
                alignment_closer = rng.choice(remaining)
        context["used_closers"].add(alignment_closer)
        full_text += " " + alignment_closer

        thought_pool = themes.get("thoughts", ["Time passes."])
        racial_thought = rng.choice(thought_pool)

        decade_era_info = resolve_era_for_year(decade_start, eras)
        era_name = decade_era_info["name"] if decade_era_info else None

        memories.append({
            "decade": decade_label,
            "year_range": [decade_start, decade_end],
            "type": memory_type,
            "memory": full_text.strip(),
            "reflection": racial_thought,
            "civ_alignment": civ_alignment,
            "ooi_type": ooi_type,
            "ooi_subtype": ooi_subtype,
            "era": era_name,
        })

    return memories


def generate_short_term_memories(
    ooi: Dict,
    context: Dict,
    rng: random.Random,
) -> List[Dict]:
    """Generate 15 short-term / recent observations — seasonal events for the OOI."""
    memories = []
    themes = context["themes"]
    ooi_type = context["ooi_type"]
    ooi_subtype = context["ooi_subtype"]
    civ_name = context["civ_name"]

    current_year = context.get("end_year", 250)
    seasons_cycle = ["spring", "summer", "autumn", "winter"]
    current_season_idx = current_year % 4
    current_season = seasons_cycle[current_season_idx]
    last_season = seasons_cycle[(current_season_idx - 1) % 4]

    eras = context.get("eras", [])

    used_recent = set()
    used_thought = set()

    # 8 seasonal observations (4 current, 4 last) — OOI flavored
    seasonal_pool = [
        f"Travelers passed through {civ_name} and remarked on the {ooi_subtype}'s condition.",
        f"The land around the {ooi_subtype} changed with the seasons — {rng.choice(['green', 'golden', 'white', 'bare'])} and alive.",
        f"A stranger arrived carrying news from distant lands — the {ooi_subtype} drew their gaze.",
        f"The {ooi_subtype} was {rng.choice(['examined', 'polished', 'repaired', 'studied', 'guarded', 'moved', 'displayed'])} this season.",
        f"Traders brought goods from afar — the {ooi_subtype} was {rng.choice(['appraised', 'admired', 'catalogued', 'discussed', 'bargained over'])}.",
        f"Word of the {ooi_subtype} {rng.choice(['spread', 'grew', 'faded', 'changed', 'circulated'])} this season.",
        f"Signs of {rng.choice(['wildlife', 'bandits', 'spirit activity', 'weather change', 'disease', 'prosperity'])} were noted near the {ooi_subtype}.",
        f"A {rng.choice(['festival', 'ritual', 'ceremony', 'gathering', 'tournament', 'market'])} involved the {ooi_subtype}.",
        f"Word came of {rng.choice(['a war', 'a peace', 'a famine', 'a discovery', 'a death'])} in the wider realm.",
        f"A {rng.choice(['scholar', 'elder', 'keeper', 'captain', 'master'])} made a proclamation about the {ooi_subtype}.",
        f"A {rng.choice(['child was born', 'couple married', 'elder passed', 'stranger was welcomed', 'dispute was settled'])} near the {ooi_subtype}.",
        f"The weather this season was {rng.choice(['harsh', 'mild', 'unusual', 'beautiful', 'terrible'])} — it affected the {ooi_subtype}.",
        f"A nearby {rng.choice(['forest', 'river', 'mountain', 'cave', 'field', 'ruin'])} was {rng.choice(['explored', 'mapped', 'damaged', 'reported', 'avoided'])}.",
        f"Supplies for tending the {ooi_subtype} were {rng.choice(['gathered', 'distributed', 'stored', 'traded', 'found lacking'])}.",
        f"{civ_name}'s {rng.choice(['craftsmen', 'farmers', 'guards', 'scholars', 'priests'])} were busy with their work near the {ooi_subtype}.",
    ]

    # ── Add relationship-specific seasonal observations for beasts ──
    if context.get("ooi_type") == "beast":
        if context.get("rival_beast_names"):
            rival = rng.choice(context["rival_beast_names"])
            seasonal_pool.append(
                f"The rival {rival} was {rng.choice(['sighted nearby', 'heard in the distance', 'tracked through the wilds', 'felt as a presence'])} this season."
            )
        if context.get("enemy_site_names"):
            enemy_site = rng.choice(context["enemy_site_names"])
            seasonal_pool.append(
                f"The settlement of {enemy_site} {rng.choice(['fortified', 'sent hunters', 'prayed for protection', 'reported signs of the beast'])} this season."
            )
        if context.get("ally_site_names"):
            ally_site = rng.choice(context["ally_site_names"])
            seasonal_pool.append(
                f"The {ally_site} was {rng.choice(['at peace', 'left untouched', 'quiet this season', 'free of the beast'])}."
            )

    # ── Add relationship-specific seasonal observations for artifacts ──
    if context.get("ooi_type") == "artifact":
        if context.get("wielder_names"):
            wielder = rng.choice(context["wielder_names"])
            seasonal_pool.append(
                f"{wielder} {rng.choice(['examined', 'polished', 'studied', 'displayed', 'carried', 'guarded', 'used'])} me this season."
            )
        if context.get("creator_names"):
            creator = rng.choice(context["creator_names"])
            seasonal_pool.append(
                f"I remember {creator}'s hands — the forge, the shaping, the birth of my form."
            )
        if context.get("thief_names"):
            thief = rng.choice(context["thief_names"])
            seasonal_pool.append(
                f"The thief {thief} was {rng.choice(['sighted near', 'hunted by the guards', 'rumored to be plotting', 'seen with my kind'])} this season."
            )
        if context.get("repairer_names"):
            repairer = rng.choice(context["repairer_names"])
            seasonal_pool.append(
                f"{repairer} {rng.choice(['tended to me', 'polished my surface', 'mended my cracks', 'restored my gleam', 'examined my condition'])}."
            )
        if context.get("recoverer_names"):
            recoverer = rng.choice(context["recoverer_names"])
            seasonal_pool.append(
                f"{recoverer} keeps me safe — {rng.choice(['a trusted guardian', 'a worthy keeper', 'a vigilant watcher'])}."
            )
        if context.get("loser_names"):
            loser = rng.choice(context["loser_names"])
            seasonal_pool.append(
                f"I was lost once — {loser} {rng.choice(['misplaced', 'dropped', 'forgot', 'abandoned'])} me. I remember the silence."
            )
        if context.get("breaker_names"):
            breaker = rng.choice(context["breaker_names"])
            seasonal_pool.append(
                f"The scar from {breaker} still shows — {rng.choice(['a crack in my form', 'a shadow in my heart', 'a flaw in my surface', 'a memory of pain'])}."
            )
        if context.get("rival_beast_names"):
            rival = rng.choice(context["rival_beast_names"])
            seasonal_pool.append(
                f"The rival {rival} was {rng.choice(['sighted nearby', 'heard in the distance', 'tracked through the wilds', 'felt as a presence'])} this season."
            )
        if context.get("enemy_site_names"):
            enemy_site = rng.choice(context["enemy_site_names"])
            seasonal_pool.append(
                f"The settlement of {enemy_site} {rng.choice(['fortified', 'sent hunters', 'prayed for protection', 'reported signs of the beast'])} this season."
            )
        if context.get("ally_site_names"):
            ally_site = rng.choice(context["ally_site_names"])
            seasonal_pool.append(
                f"The {ally_site} was {rng.choice(['at peace', 'left untouched', 'quiet this season', 'free of the beast'])}."
            )

    # Assign specific seasons
    for i in range(8):
        template = pick_unique(seasonal_pool, used_recent, rng)
        season = current_season if i < 4 else last_season
        year = current_year if season == current_season else current_year - 1
        year_label = format_year_with_era(year, eras)
        template = template.replace("spring", season).replace("summer", season).replace("autumn", season).replace("winter", season)
        memories.append({
            "type": "seasonal_observation",
            "season": season,
            "year": year,
            "year_label": year_label,
            "memory": template,
            "ooi_type": ooi_type,
            "ooi_subtype": ooi_subtype,
        })

    # 7 seasonal thoughts / moods — OOI flavored
    thought_pool = [
        f"This {current_season}, the {ooi_subtype} feels {rng.choice(['at peace', 'restless', 'forgotten', 'alive', 'haunted', 'blessed'])}.",
        f"The {current_season} {rng.choice(['air', 'light', 'stillness', 'darkness', 'warmth', 'cold'])} hangs over {civ_name} — it makes the {ooi_subtype} feel {rng.choice(['melancholy', 'hopeful', 'ancient', 'fragile', 'timeless'])}.",
        f"I wonder what the {rng.choice(['next season', 'coming year', 'distant future'])} will bring to the {ooi_subtype}.",
        f"This season last year was {rng.choice(['different', 'the same', 'worse', 'better'])} — the {ooi_subtype} has {rng.choice(['changed', 'endured', 'grown', 'suffered'])} since then.",
        f"The {rng.choice(['elders say', 'keepers tell', 'songs remember', 'runes record'])} that {rng.choice(['this season is sacred', 'the winter is harsh here', 'the spring brings renewal', 'the summer is for war', 'the autumn is for remembrance'])}.",
        f"The {ooi_subtype} has been {rng.choice(['dreaming', 'waiting', 'praying', 'wandering', 'working', 'watching'])} more than usual this season.",
        f"This season feels {rng.choice(['different', 'the same as always', 'charged with meaning', 'empty', 'precious', 'fleeting'])} — the {ooi_subtype} {rng.choice(['endures it', 'fears it', 'accepts it', 'fights it', 'surrenders to it'])}.",
    ]
    for i in range(7):
        thought = pick_unique(thought_pool, used_thought, rng)
        if rng.random() < 0.3 and context.get("civ_name"):
            thought = f"Considering {context['civ_name']}. " + thought
        elif rng.random() < 0.3:
            thought = f"The future is uncertain. " + thought

        memories.append({
            "type": "seasonal_thought",
            "season": current_season,
            "year": current_year,
            "year_label": format_year_with_era(current_year, eras),
            "memory": thought,
            "ooi_type": ooi_type,
            "ooi_subtype": ooi_subtype,
        })

    return memories


def generate_near_term_memories(
    ooi: Dict,
    context: Dict,
    rng: random.Random,
) -> List[Dict]:
    """Generate 10 near-term / future-looking plans and concerns for the OOI."""
    memories = []
    themes = context["themes"]
    ooi_type = context["ooi_type"]
    ooi_subtype = context["ooi_subtype"]
    civ_name = context["civ_name"]

    used_plans = set()
    used_worries = set()

    # ── Base plan pool — varies by OOI type ──
    if ooi_type == "beast":
        plan_pool = [
            f"I will expand my territory — the land will be mine.",
            f"I hunt the herds that graze near my domain — they grow fat and slow.",
            f"I will challenge the rival that dares enter my hunting grounds.",
            f"I will delve deeper into the caves — there is something ancient there.",
            f"I will mark my scent across the ridge — the pack will know my range.",
            f"The coming season calls for a great hunt — I will feast.",
            f"I will climb the peak and survey my domain — from there all is visible.",
            f"I will teach the young ones to stalk and kill — they must learn.",
            f"The river has shifted — I will claim the new watering hole.",
            f"I will grow stronger — the wild will make me fiercer.",
        ]
    elif ooi_type == "artifact":
        plan_pool = [
            f"I will be studied by scholars — they will uncover my secrets.",
            f"I will be displayed at a great gathering — all will behold my beauty.",
            f"I will be moved to a safer place — the danger is too great here.",
            f"I will be reforged — a new purpose awaits me.",
            f"I will be awakened — the power within me will stir.",
            f"I will be honored in a ritual — the spirits will bless me.",
            f"I will be guarded more closely — the thieves grow bold.",
            f"I will be wielded in a great battle — I will taste glory again.",
            f"I will be passed to a worthy keeper — one who understands my worth.",
            f"I will rest — the ages weigh on me and I need stillness.",
        ]
    else:
        plan_pool = [
            f"There are plans to study the {ooi_subtype} — new insights, new understanding, new power.",
            f"A new {rng.choice(['ritual', 'expedition', 'crafting project', 'pilgrimage', 'trade agreement', 'alliance', 'festival'])} involving the {ooi_subtype} is planned this coming year.",
            f"The {ooi_subtype} intends to {rng.choice(['reveal its secrets', 'attract scholars', 'draw power from the land', 'be moved to a safer place', 'be reforged', 'be awakened', 'be honored'])}.",
            f"We must prepare for the coming {rng.choice(['winter', 'war', 'festival', 'journey', 'hunting season', 'siege', 'caravan'])} that may affect the {ooi_subtype}.",
            f"The {rng.choice(['leader', 'council', 'elders', 'master'])} has called for {rng.choice(['a census', 'a gathering', 'a ritual', 'an expedition', 'a study', 'a diplomatic mission'])} regarding the {ooi_subtype}.",
            f"We aim to {rng.choice(['attract more pilgrims', 'improve the {ooi_subtype}\'s guardians', 'stockpile offerings', 'forge alliances', 'discover a lost truth', 'train the next keeper', 'restore an old legend'])}.",
            f"We hope to {rng.choice(['make contact with a distant realm', 'recover from recent hardships', 'celebrate a milestone', 'honor the ancestors', 'complete the great work'])} involving the {ooi_subtype}.",
            f"A {rng.choice(['moot', 'council', 'gathering', 'celebration', 'ritual', 'tournament', 'ceremony'])} will center on the {ooi_subtype} next season.",
            f"There is a vision to {rng.choice(['build a monument', 'plant a sacred grove', 'forge a legacy', 'train a successor', 'walk the old road', 'discover the truth of the past'])} around the {ooi_subtype}.",
            f"We dream of {rng.choice(['peace and prosperity', 'becoming a great power', 'understanding the old mysteries', 'finding a place in history', 'simply surviving the years ahead'])} with the {ooi_subtype}.",
        ]

    # ── Base worry pool — varies by OOI type ──
    if ooi_type == "beast":
        worry_pool = [
            f"I worry about the hunters — they track me with iron and fire.",
            f"I worry about the rival beast — it covets my territory.",
            f"I worry about the drought — the watering holes are shrinking.",
            f"I worry about the strange scents on the wind — something new is coming.",
            f"I worry about the sickness among the herd — the meat is thin.",
            f"I worry about the fires in the distance — the whole land may burn.",
            f"I worry about the elder beast — it grows weak and the pack will challenge.",
            f"I worry about the encroaching settlements — they push deeper into the wild.",
            f"I worry about the silence of the forest — the prey have fled.",
            f"I worry about the mountain — it rumbles and the caves may collapse.",
        ]
    elif ooi_type == "artifact":
        worry_pool = [
            f"I worry about being forgotten — locked away in a dark vault.",
            f"I worry about being broken — by carelessness or malice.",
            f"I worry about being stolen — taken from those who understand me.",
            f"I worry about being misused — wielded for cruel purposes.",
            f"I worry about being lost — dropped into the sea or buried in rubble.",
            f"I worry about the rust and decay — time wears me down.",
            f"I worry about the changing world — no one will remember my significance.",
            f"I worry about the thieves' guild — they have marked me as a prize.",
            f"I worry about the war — I may be melted down for arrowheads.",
            f"I worry about the ages passing — I will outlive all who cherish me.",
        ]
    else:
        worry_pool = [
            f"I worry about the war in the east — it creeps closer each season.",
            f"I worry about the drought — the land is already dry.",
            f"I worry about the raids on the caravans — trade is slowing.",
            f"I worry about the strange signs in the region — the animals are restless.",
            f"I worry about the unrest in the region — the folk are troubled.",
            f"I worry about the plague spreading — too many have fallen sick.",
            f"I worry about the old spirits growing restless — the rituals have felt wrong.",
            f"I worry about the bandits on the roads — travel is dangerous now.",
            f"I worry about the harvest failing — we will starve if it does.",
            f"I worry about the river drying up — it has never done that before.",
            f"I fear that the old ways are fading — no one remembers them anymore.",
            f"I fear that the {ooi_subtype}'s power is declining — it is not what it once was.",
            f"I fear that the spirits are angry — they have not answered our prayers.",
            f"I fear that change is coming — and we are not ready for it.",
            f"I fear that we have wasted our best years — chasing shadows.",
            f"I fear the darkness gathering beyond the walls — it grows when we sleep.",
            f"I fear that we are being forgotten — our name will not live on.",
            f"I fear that our enemies are closing in — we can feel them watching.",
            f"I am concerned about our neighbors — they are far and we cannot rely on them.",
            f"I am concerned about our reputation — a lie is spreading about us.",
            f"I am concerned about our debts — the collector comes next season.",
            f"I am concerned about a rival's schemes — they smile too much.",
            f"I am concerned about the coming winter — we have not stored enough.",
            f"I am concerned about a promise we cannot keep — it weighs on us.",
            f"I am concerned about the silence from the north — no traders have come.",
            f"I am concerned about the strange dreams the {rng.choice(['leader', 'elder', 'seer', 'watcher'])} has been having.",
            f"I am concerned about the shadow seen in the distance — it moved wrong.",
            f"I am concerned about the price of goods rising — soon we will not afford bread.",
            f"I am concerned about the {ooi_subtype}'s condition — something feels wrong with it.",
            f"I am concerned about the land itself — it is changing in ways we do not understand.",
        ]

    # ── Add relationship-specific near-term entries for beasts ──
    if context.get("ooi_type") == "beast":
        if context.get("rival_beast_names"):
            rival = rng.choice(context["rival_beast_names"])
            worry_pool.append(f"I worry about the rival {rival} — they grow bolder each season.")
            plan_pool.append(f"We must track the rival {rival} — they encroach on our territory.")
        if context.get("enemy_site_names"):
            enemy_site = rng.choice(context["enemy_site_names"])
            worry_pool.append(f"I worry about {enemy_site} — they send hunters after us.")
            plan_pool.append(f"We will strike {enemy_site} before they fortify further.")
        if context.get("ally_site_names"):
            ally_site = rng.choice(context["ally_site_names"])
            worry_pool.append(f"I worry that the peace with {ally_site} will not hold.")
            plan_pool.append(f"We will keep {ally_site} as a safe haven — they respect us.")

    # ── Add relationship-specific near-term entries for artifacts ──
    if context.get("ooi_type") == "artifact":
        if context.get("thief_names"):
            thief = rng.choice(context["thief_names"])
            worry_pool.append(f"I fear the thief {thief} — they may try to steal me again.")
            plan_pool.append(f"I must be guarded — {thief} cannot be trusted.")
        if context.get("breaker_names"):
            breaker = rng.choice(context["breaker_names"])
            worry_pool.append(f"I fear {breaker} — they would break me if given the chance.")
            plan_pool.append(f"I must stay far from {breaker} — they bring only harm.")
        if context.get("loser_names"):
            loser = rng.choice(context["loser_names"])
            worry_pool.append(f"I fear being lost again — {loser} was careless.")
            plan_pool.append(f"I must stay in safe hands — not like {loser}.")
        if context.get("wielder_names"):
            wielder = rng.choice(context["wielder_names"])
            plan_pool.append(f"{wielder} will wield me — I will be used for a great purpose.")
        if context.get("recoverer_names"):
            recoverer = rng.choice(context["recoverer_names"])
            plan_pool.append(f"{recoverer} will keep me safe — I am secure.")
        if context.get("repairer_names"):
            repairer = rng.choice(context["repairer_names"])
            plan_pool.append(f"{repairer} will tend to me — I will be maintained.") 
        if context.get("rival_beast_names"):
            rival = rng.choice(context["rival_beast_names"])
            worry_pool.append(f"I worry about the rival {rival} — they grow bolder each season.")
            plan_pool.append(f"We must track the rival {rival} — they encroach on our territory.")
        if context.get("enemy_site_names"):
            enemy_site = rng.choice(context["enemy_site_names"])
            worry_pool.append(f"I worry about {enemy_site} — they send hunters after us.")
            plan_pool.append(f"We will strike {enemy_site} before they fortify further.")
        if context.get("ally_site_names"):
            ally_site = rng.choice(context["ally_site_names"])
            worry_pool.append(f"I worry that the peace with {ally_site} will not hold.")
            plan_pool.append(f"We will keep {ally_site} as a safe haven — they respect us.")

    for i in range(6):
        template = pick_unique(plan_pool, used_plans, rng)
        memories.append({
            "type": "future_plan",
            "memory": template,
            "ooi_type": ooi_type,
            "ooi_subtype": ooi_subtype,
        })

    for i in range(4):
        template = pick_unique(worry_pool, used_worries, rng)
        memories.append({
            "type": "future_worry",
            "memory": template,
            "ooi_type": ooi_type,
            "ooi_subtype": ooi_subtype,
        })

    return memories

# ──────────────────────────────────────────────────────────────────────
# 7.  BIOME FEATURE GENERATION (derived from terrain, not a discrete entity)
# ──────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────
# 8.  FACTS BUILDER — short single-line truths about the OOI
# ──────────────────────────────────────────────────────────────────────

ARTIFACT_MATERIAL_PROPERTIES = {
    "wood":    ["light", "flexible", "resonant", "flammable"],
    "stone":   ["heavy", "durable", "cold", "unyielding"],
    "iron":    ["heavy", "strong", "magnetic", "rust-prone"],
    "gold":    ["heavy", "lustrous", "soft", "tarnish-resistant"],
    "crystal": ["light", "sharp", "transparent", "resonant"],
    "bone":    ["light", "brittle", "warm", "ancient"],
    "leather": ["light", "flexible", "supple", "warm"],
}

ARTIFACT_ENCHANTMENTS = [
    "glows faintly",
    "humming with power",
    "cold to the touch",
    "warm like a living thing",
    "etched with runes",
    "pulsing with energy",
    "whispers when held",
    "shimmers in moonlight",
    "burns with inner fire",
    "dripping with shadow",
]

BEAST_STAT_BASES = {
    "forgotten_beast": {"str": 8, "spd": 4, "tough": 7, "int": 2},
    "titan":           {"str": 9, "spd": 5, "tough": 8, "int": 3},
    "dragon":          {"str": 10, "spd": 6, "tough": 9, "int": 4},
    "giant":           {"str": 7, "spd": 3, "tough": 6, "int": 2},
    "werebeast":       {"str": 6, "spd": 7, "tough": 5, "int": 3},
}

BEAST_HABITATS = {
    "forgotten_beast": ["deep caverns", "ancient tombs", "dark forests", "cursed ruins"],
    "titan":           ["mountain peaks", "volcanic wastes", "frozen tundra"],
    "dragon":          ["volcanic lair", "mountain cave", "ruined tower", "ancient temple"],
    "giant":           ["mountain valleys", "open plains", "rocky badlands"],
    "werebeast":       ["dark forests", "swamp fens", "abandoned villages", "moonlit moors"],
}


def build_ooi_facts(ooi: Dict, context: Dict, ooi_type: str, rng: random.Random, current_year: Optional[int] = None) -> Dict[str, str]:
    """Build short single-line facts about the OOI.

    ``current_year`` is used to compute artifact age when broken_by
    would otherwise be "none" (see Question_Eval §2 — no "none" for
    death/destruction facts).
    """
    facts = {}

    if ooi_type == "artifact":
        material = ooi.get("material", "stone")
        artifact_type = ooi.get("type", "unknown")
        facts["artifact_type"] = artifact_type
        facts["material"] = material
        facts["alignment"] = ooi.get("alignment", "neutral")
        facts["created_year"] = str(ooi.get("created_year", "?"))

        # Creator — look up from same_civ_figures
        creator_id = ooi.get("creator_id")
        if creator_id is not None and context.get("same_civ_figures"):
            for fig in context["same_civ_figures"]:
                if fig.get("id") == creator_id:
                    facts["creator"] = fig.get("name", "unknown")
                    facts["creator_title"] = fig.get("title", "")
                    facts["creator_id"] = str(creator_id)
                    break
        if not facts.get("creator"):
            facts["creator"] = "unknown"

        # Wielders — from relationship data in context
        wielder_names = context.get("wielder_names", [])
        wielder_ids = context.get("wielder_ids", [])
        if wielder_names:
            facts["wielded_by"] = ", ".join(wielder_names)
            facts["wielded_by_ids"] = ", ".join(str(i) for i in wielder_ids)
        else:
            facts["wielded_by"] = "none"
            facts["wielded_by_ids"] = "none"

        # Thieves
        thief_names = context.get("thief_names", [])
        thief_ids = context.get("thief_ids", [])
        if thief_names:
            facts["stolen_by"] = ", ".join(thief_names)
            facts["stolen_by_ids"] = ", ".join(str(i) for i in thief_ids)
        else:
            facts["stolen_by"] = "none"
            facts["stolen_by_ids"] = "none"

        # Breakers — use artifact age instead of "none" for death/destruction
        breaker_names = context.get("breaker_names", [])
        breaker_ids = context.get("breaker_ids", [])
        if breaker_names:
            facts["broken_by"] = ", ".join(breaker_names)
            facts["broken_by_ids"] = ", ".join(str(i) for i in breaker_ids)
        elif current_year is not None:
            created = ooi.get("created_year")
            if created is not None:
                age = current_year - created
                facts["broken_by"] = f"age:{age}"
            else:
                facts["broken_by"] = "none"
                facts["broken_by_ids"] = "none"
        else:
            facts["broken_by"] = "none"
            facts["broken_by_ids"] = "none"

        # Repairers
        repairer_names = context.get("repairer_names", [])
        repairer_ids = context.get("repairer_ids", [])
        if repairer_names:
            facts["repaired_by"] = ", ".join(repairer_names)
            facts["repaired_by_ids"] = ", ".join(str(i) for i in repairer_ids)
        else:
            facts["repaired_by"] = "none"
            facts["repaired_by_ids"] = "none"

        # Losers (lost the artifact)
        loser_names = context.get("loser_names", [])
        loser_ids = context.get("loser_ids", [])
        if loser_names:
            facts["lost_by"] = ", ".join(loser_names)
            facts["lost_by_ids"] = ", ".join(str(i) for i in loser_ids)
        else:
            facts["lost_by"] = "none"
            facts["lost_by_ids"] = "none"

        # Recoverers (found / recovered the artifact)
        recoverer_names = context.get("recoverer_names", [])
        recoverer_ids = context.get("recoverer_ids", [])
        if recoverer_names:
            facts["recovered_by"] = ", ".join(recoverer_names)
            facts["recovered_by_ids"] = ", ".join(str(i) for i in recoverer_ids)
        else:
            facts["recovered_by"] = "none"
            facts["recovered_by_ids"] = "none"

        # Properties
        prop_rng = random.Random(ooi["id"] * 23 + 5)
        material_props = ARTIFACT_MATERIAL_PROPERTIES.get(material, ["solid"])
        facts["property"] = prop_rng.choice(material_props)
        facts["enchantment"] = prop_rng.choice(ARTIFACT_ENCHANTMENTS)
        facts["weight_class"] = prop_rng.choice(["light", "moderate", "heavy", "very heavy"])

        # Current location / owner
        if ooi.get("civ_id") is not None:
            facts["held_by_civ"] = context.get("civ_name", "unknown")
        else:
            facts["held_by_civ"] = "none (lost)"

    elif ooi_type == "beast":
        beast_type = ooi.get("type", "forgotten_beast")
        facts["beast_type"] = beast_type
        facts["alignment"] = ooi.get("alignment", "chaos")
        facts["kills"] = str(ooi.get("kills", 0))
        facts["active"] = "yes" if ooi.get("active", True) else "dead"

        # Stats
        stat_rng = random.Random(ooi["id"] * 29 + 11)
        base = BEAST_STAT_BASES.get(beast_type, {"str": 5, "spd": 5, "tough": 5, "int": 1})
        for stat_name, base_val in base.items():
            bonus = stat_rng.randint(-1, 2) + (ooi.get("kills", 0) // 10)
            facts[f"stat_{stat_name}"] = str(max(1, base_val + bonus))

        # Habitat
        hab_rng = random.Random(ooi["id"] * 37 + 3)
        habitats = BEAST_HABITATS.get(beast_type, ["wilderness"])
        facts["habitat"] = hab_rng.choice(habitats)
        facts["size_class"] = hab_rng.choice(["small", "medium", "large", "enormous"])

    return facts


# ──────────────────────────────────────────────────────────────────────
# 9.  MAIN
# ──────────────────────────────────────────────────────────────────────

def generate_ooi_memories(
    world_path: str,
    output_path: str,
    ooi_type: str = "artifact",
    ooi_id: Optional[int] = None,
    seed: Optional[int] = None,
    decade_override: Optional[int] = None,
) -> Dict:
    if seed is None:
        seed = random.randint(0, 2**31)
    rng = random.Random(seed)

    print(f"Loading world from {world_path}...")
    world = load_world(world_path)

    print(f"Selecting {ooi_type}...")
    ooi = select_ooi(world, ooi_type=ooi_type, ooi_id=ooi_id, rng=rng)
    print(f"OOI: {ooi.get('name', 'unknown')} ({ooi_type}), id={ooi.get('id', '?')}")

    print("Resolving context...")
    context = resolve_ooi_context(world, ooi, ooi_type)

    num_decades = decade_override if decade_override is not None else compute_decades(ooi, ooi_type, world)
    print(f"Decades: {num_decades}")

    decade_ranges = get_decade_ranges(ooi, ooi_type, world, num_decades)
    print(f"Decade ranges: {decade_ranges}")

    print(f"Generating {num_decades} long-term memories...")
    long_term = generate_long_term_memories(ooi, context, num_decades, decade_ranges, rng)

    print("Generating 15 short-term memories...")
    short_term = generate_short_term_memories(ooi, context, rng)

    print("Generating 10 near-term memories...")
    near_term = generate_near_term_memories(ooi, context, rng)

    # Build output
    ooi_info = {}
    if ooi_type == "artifact":
        ooi_info = {
            "id": ooi["id"],
            "name": ooi["name"],
            "type": ooi_type,
            "subtype": ooi.get("type"),
            "material": ooi.get("material"),
            "created_year": ooi.get("created_year"),
            "created_season": ooi.get("created_season"),
            "alignment": ooi.get("alignment"),
            "civ_id": ooi.get("civ_id"),
        }
    elif ooi_type == "beast":
        ooi_info = {
            "id": ooi["id"],
            "name": ooi["name"],
            "type": ooi_type,
            "subtype": ooi.get("type"),
            "description": ooi.get("description"),
            "year_spawned": ooi.get("year_spawned"),
            "season_spawned": ooi.get("season_spawned"),
            "alignment": ooi.get("alignment"),
            "active": ooi.get("active", True),
            "kills": ooi.get("kills", 0),
        }
    # REMOVED: biome_feature branch (moved to poi_memory_gen.py)

    # Build short facts
    current_year = world.get("config", {}).get("end_year", 500)
    facts = build_ooi_facts(ooi, context, ooi_type, rng, current_year=current_year)

    output = {
        "ooi": ooi_info,
        "context": {
            "civ_name": context["civ_name"],
            "civ_alignment": context["civ_alignment"],
            "biome_name": context.get("biome_name"),
            "num_decades": num_decades,
            "num_site_events": len(context.get("site_events", [])),
            "num_same_civ_figures": len(context.get("same_civ_figures", [])),
            "num_same_civ_sites": len(context.get("same_civ_sites", [])),
            "num_civ_artifacts": len(context.get("civ_artifacts", [])),
            "num_site_beasts": len(context.get("site_beasts", [])),
        },
        "facts": facts,
        "memories": {
            "long_term": {
                "count": len(long_term),
                "items": long_term,
            },
            "short_term": {
                "count": len(short_term),
                "items": short_term,
            },
            "near_term": {
                "count": len(near_term),
                "items": near_term,
            },
        },
        "seed": seed,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nExported OOI memories to {output_path}")
    print(f"  Long-term:  {len(long_term)} memories (decades)")
    print(f"  Short-term: {len(short_term)} memories")
    print(f"  Near-term:  {len(near_term)} memories")
    print(f"  Total:      {len(long_term) + len(short_term) + len(near_term)} memories")

    return output

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate DF-style OOI (Object of Interest) memories")
    parser.add_argument("--world", type=str, required=True, help="Path to world JSON from world_gen.py")
    parser.add_argument("--output", type=str, default="/tmp/ooi_memories.json", help="Output JSON path")
    parser.add_argument("--ooi-type", type=str, default="artifact",
                        choices=["artifact", "beast"],
                        help="Type of OOI to generate memories for")
    parser.add_argument("--ooi-id", type=int, default=None, help="ID of the OOI (for artifact/beast)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--decades", type=int, default=None, help="Override number of decades")
    args = parser.parse_args()

    generate_ooi_memories(
        world_path=args.world,
        output_path=args.output,
        ooi_type=args.ooi_type,
        ooi_id=args.ooi_id,
        seed=args.seed,
        decade_override=args.decades,
    )
