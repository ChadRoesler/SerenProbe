#!/usr/bin/env python3
"""
POI (Point of Interest) Memory Generator v1
Takes a site / artifact / biome feature from world_gen.py output and generates:
  - Long-term memory:  X items (X = number of decades the POI has existed,
                        clamped 3-30). Major events, growth milestones, etc.
  - Short-term memory:  15 recent events / observations
  - Near-term memory:   10 upcoming plans / concerns

Memories are flavored by site type, biome, civilization, alignment,
and reference real world entities (figures, artifacts, beasts, events).
"""

import json
import random
import sys
import os
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────
# 1.  SITE / POI TYPE MEMORY THEMES
# ──────────────────────────────────────────────────────────────────────

POI_TYPE_THEMES = {

    "city": {
        "foundation_events": [
            "was founded as a gathering place for the people — the first walls rose from the earth",
            "began as a trading post where merchants met under the old tree",
            "was established by a visionary leader who dreamed of a great settlement",
            "grew from a handful of huts around a sacred spring into a proper town",
            "was carved from the wilderness with nothing but determination and hard labor",
        ],
        "life_events": [
            "the great market square was expanded, drawing traders from distant lands",
            "the city walls were raised to full height after years of labor",
            "a grand temple was built at the heart of the city, becoming a landmark",
            "the city's population swelled as refugees arrived from a war-torn region",
            "the aqueduct system was completed, bringing fresh water to every district",
            "the city burned for three days — half the old quarter was lost to the flames",
            "a new guild hall was established, bringing order to the crafts",
            "the city's gates were rebuilt with iron after the siege",
            "a great festival was held that lasted a full season, with guests from all realms",
            "the city was struck by a plague that claimed many lives",
            "a new library was built to house the accumulated wisdom of the ages",
            "the ruling council was restructured, bringing new blood to power",
        ],
        "growth_milestones": [
            "the first stone bridge was built across the river, linking the two halves",
            "the outer wall was extended to enclose the growing districts",
            "the market was moved to a larger square to accommodate the rising trade",
            "the city's first school was founded to educate the children",
            "a new harbor was dug to receive ships from across the waters",
            "the watchtowers were raised along the perimeter for better defense",
            "the city's population doubled within a decade",
            "a new quarter was planned and settled by immigrant families",
        ],
        "thoughts": [
            "The city breathes like a living thing — every stone remembers.",
            "The streets hum with the voices of those who walk them.",
            "A city is not walls and stone, but the people within.",
            "The walls have stood for generations — they will stand for more.",
            "Every brick laid is a promise to the future.",
            "The city grows, and so does its memory.",
            "There is always more to build, more to improve.",
        ],
    },

    "fortress": {
        "foundation_events": [
            "was built as a bastion of strength — stone upon stone, against the darkness",
            "was carved into the living rock to serve as an unbreakable hold",
            "was raised at a strategic pass to guard the realm from invasion",
            "was founded by warriors who swore to hold it against all enemies",
            "was built atop an ancient foundation, repurposing the old stones",
        ],
        "life_events": [
            "withstood a prolonged siege — the walls held and the attackers retreated",
            "the inner keep was expanded to house the garrison and supplies",
            "a deep well was dug within the fortress, ensuring water even in siege",
            "the fortress was breached once — the walls were repaired and strengthened",
            "a new barracks was built to house the growing garrison",
            "the fortress's armory was stocked with weapons from a master smith",
            "a secret tunnel was discovered beneath the fortress — leading to unknown depths",
            "the fortress endured a terrible winter that froze half the garrison",
            "the outer fortifications were rebuilt with stronger materials",
            "the fortress became a refuge for the surrounding region during war",
        ],
        "growth_milestones": [
            "the outer walls were doubled in height after the last siege",
            "a secondary gate was added to allow faster sorties",
            "the garrison was expanded to a full legion",
            "a watchtower was raised on the highest point for early warning",
            "the fortress's forge was upgraded to produce arms for the entire realm",
            "a tunnel network was dug to connect the fortress to the nearby settlements",
            "the fortress was connected to the trade road, bringing supplies and commerce",
        ],
        "thoughts": [
            "Stone and iron — the fortress remembers every blow.",
            "The walls are scarred, but they still stand.",
            "A fortress is a promise carved in stone: we will not fall.",
            "The sentries never sleep — neither do the walls.",
            "Strength is not just in the stone, but in the wills of those within.",
            "The fortress has stood through storms of war and weather alike.",
            "Every crack in the wall tells a story of defense.",
        ],
    },

    "village": {
        "foundation_events": [
            "began as a handful of families settling near a river for water and fertile land",
            "was founded by farmers who cleared the forest and planted the first fields",
            "grew from a single homestead into a small community of hardy folk",
            "was established at a crossroads where travelers would stop to rest",
            "was built around an old shrine that had long been a local landmark",
        ],
        "life_events": [
            "the village well was dug, providing clean water for all",
            "the mill was built by the river, grinding grain for the whole community",
            "the village was raided — the palisade was rebuilt and strengthened",
            "a terrible blight struck the crops — the village nearly starved",
            "the village chapel was built, becoming a center of community life",
            "the road was paved to connect the village to the nearest town",
            "a traveling fair came to the village and brought goods from afar",
            "the village council was formed to settle disputes and plan for the future",
            "a plague swept through — many were lost, but the village endured",
            "the village was granted a charter by the local lord, becoming a true settlement",
        ],
        "growth_milestones": [
            "the village expanded its fields to feed the growing population",
            "a new well was dug to serve the expanding outskirts",
            "the village palisade was upgraded to a proper wooden wall",
            "the market was established, drawing traders from the surrounding lands",
            "the village's first school was built to teach the children",
            "the village smithy was upgraded to work iron for tools and weapons",
            "a new neighborhood was settled beyond the old boundaries",
        ],
        "thoughts": [
            "The village knows the rhythm of the seasons — planting, growing, harvesting.",
            "Every family here has a story that intertwines with the others.",
            "The land gives and takes — we are part of its cycle.",
            "The old ways keep us rooted, but change comes nonetheless.",
            "The village sleeps under the stars, dreaming of the next dawn.",
            "We are small, but we endure.",
            "The fields remember the hands that worked them.",
        ],
    },

    "tower": {
        "foundation_events": [
            "was raised as a place of learning and power — a spire reaching toward the sky",
            "was built by a mage who sought solitude above the clouds",
            "was constructed to watch over the surrounding lands and warn of danger",
            "was carved from an ancient hill, its foundations deep and mysterious",
            "was built as a monument to a great hero, a beacon visible for miles",
        ],
        "life_events": [
            "the tower's top was struck by lightning — the damage was repaired with magic",
            "a new wing was built at the base to house students and visitors",
            "the tower's library was expanded with tomes from distant lands",
            "a great experiment shook the tower — strange lights were seen for days",
            "the tower was besieged by those who feared its power — it held",
            "a new crystal was placed at the tower's peak to focus magical energies",
            "the tower became a sanctuary for refugees seeking protection",
            "the tower's master vanished — the tower stood empty for a time",
            "a hidden chamber was discovered deep beneath the tower",
            "the tower was wreathed in strange mists that lasted a full season",
        ],
        "growth_milestones": [
            "the tower was raised higher — a new level was added to its height",
            "the outer wall was built to enclose the tower's grounds",
            "a secondary tower was raised nearby to house additional works",
            "the tower's foundation was reinforced after signs of settling",
            "a gate was built at the tower's base to control access",
            "the tower's beacon was upgraded to shine visible for leagues",
            "the tower was connected to a network of similar spires across the realm",
        ],
        "thoughts": [
            "The tower pierces the sky — a bridge between earth and the beyond.",
            "Height brings perspective — from the top, the world looks different.",
            "The spire holds secrets that only the heights can reveal.",
            "The tower remembers every step taken up its winding stairs.",
            "Power flows through the stone, channeled by those who built it.",
            "The tower stands alone, but it watches over everything.",
            "From the peak, one can see the shape of the world.",
        ],
    },

    "shrine": {
        "foundation_events": [
            "was built at a sacred site where the veil between worlds is thin",
            "was raised to honor the spirits of the land and the ancestors",
            "was built around a natural wonder — a spring, a tree, or a stone of power",
            "was constructed by pilgrims who found peace in the place",
            "was carved from the living rock as a place of meditation and worship",
        ],
        "life_events": [
            "the shrine was visited by a great pilgrimage that brought blessings",
            "a miracle was witnessed at the shrine — the faithful came in droves",
            "the shrine was desecrated by invaders — it was purified and restored",
            "a new altar was built to honor a newly awakened spirit",
            "the shrine's sacred flame was relit after a century of darkness",
            "a hermit took residence at the shrine, becoming its guardian",
            "the shrine was expanded with a hall for pilgrims to rest",
            "the shrine survived a natural disaster that destroyed the surrounding area",
            "a vision was seen at the shrine — the faithful interpreted its meaning",
            "the shrine became a neutral ground for diplomacy between warring factions",
        ],
        "growth_milestones": [
            "a new path was carved to the shrine to ease the pilgrimage",
            "a shelter was built to house pilgrims during the winter",
            "the shrine's grounds were consecrated with a great ritual",
            "a bell tower was raised to mark the hours of prayer",
            "the shrine was encircled by a wall of standing stones",
            "a sacred garden was planted around the shrine with blessed seeds",
            "the shrine's relic chamber was expanded to house more artifacts",
        ],
        "thoughts": [
            "The shrine holds the stillness — a place where time slows.",
            "The sacred is not in the stone, but in the devotion of those who come.",
            "The spirits speak here — if you know how to listen.",
            "The shrine is a thread connecting the world to what lies beyond.",
            "Peace dwells here, even when the world outside is in chaos.",
            "The shrine remembers every prayer whispered in its halls.",
            "The light within the shrine never fades.",
        ],
    },

    # Generic for artifacts and biome features

    "biome_feature": {
        "foundation_events": [
            "was shaped by the primordial forces of the world — fire, water, wind, and time",
            "emerged from the deep earth as the ages turned and the land shifted",
            "was carved by ancient glaciers that retreated eons ago",
            "was formed by a cataclysmic event that reshaped the region",
            "has stood since before any civilization, a relic of the raw world",
        ],
        "life_events": [
            "the feature was explored by adventurers who mapped its extent",
            "a settlement was built nearby, drawing from the feature's resources",
            "the feature was scarred by a great battle that took place on its slopes",
            "a natural phenomenon altered the feature — erosion, eruption, or flood",
            "the feature became a landmark for travelers navigating the region",
            "a beast was sighted within the feature, giving it a reputation of danger",
            "the feature was studied by scholars who documented its unique qualities",
            "a shrine was built at the feature, sanctifying it as a holy place",
            "the feature was used as a refuge during times of war and invasion",
            "a legend grew around the feature — tales of spirits and hidden treasures",
        ],
        "growth_milestones": [],
        "thoughts": [
            "The land remembers the forces that shaped it.",
            "The feature is ancient beyond the reckoning of mortal minds.",
            "Nature's hand is slow but patient — the feature changes over eons.",
            "The feature stands as a monument to the world's raw power.",
            "It was here before us, and it will remain after we are gone.",
            "The feature is a reminder that the world is older than any civilization.",
            "There is a deep stillness in the ancient places.",
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
# 3.  POI SELECTION
# ──────────────────────────────────────────────────────────────────────

POI_TYPES = {
    "site": {
        "key": "sites",
        "id_field": "id",
        "name_field": "name",
        "type_field": "site_type",
        "label": "Site",
    },
}

def select_poi(
    world: Dict,
    poi_type: str,
    poi_id: Optional[int] = None,
    rng: random.Random = random.Random(),
) -> Dict:
    """Select a POI from the world data by type and optional ID."""
    type_info = POI_TYPES.get(poi_type)
    if not type_info:
        raise ValueError(f"Unknown POI type '{poi_type}'. Valid: {list(POI_TYPES.keys())}")

    entities = world.get(type_info["key"], [])
    if not entities:
        raise ValueError(f"No {poi_type}s in world data")

    if poi_id is not None:
        for e in entities:
            if e[type_info["id_field"]] == poi_id:
                return e
        raise ValueError(f"{type_info['label']} ID {poi_id} not found")

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

def resolve_site_context(world: Dict, site: Dict) -> Dict:
    """Build rich context about a site POI from the world data."""
    civ_id = site.get("civ_id")
    civ = None
    if civ_id is not None:
        for c in world.get("civilizations", []):
            if c["id"] == civ_id:
                civ = c
                break

    # Biome info
    biome_id = site.get("biome_id", 0)
    biome_name = "Unknown"
    for b in world.get("biomes", []):
        if b["id"] == biome_id:
            biome_name = b["name"]
            break

    # Other sites from same civ
    same_civ_sites = []
    for s in world.get("sites", []):
        if s.get("civ_id") == civ_id:
            same_civ_sites.append(s)

    # Figures from same civ
    same_civ_figures = []
    for f in world.get("historical_figures", []):
        if f.get("civ_id") == civ_id:
            same_civ_figures.append(f)

    # Events involving this site
    site_events = []
    for ev in world.get("events", []):
        if ev.get("site_id") == site["id"]:
            site_events.append(ev)
        elif ev.get("site_name") == site["name"]:
            site_events.append(ev)

    # Artifacts from same civ
    civ_artifacts = []
    for a in world.get("artifacts", []):
        if a.get("civ_id") == civ_id:
            civ_artifacts.append(a)

    # Beasts that attacked this site
    site_beasts = []
    for ev in site_events:
        if ev.get("type") == "beast_attack":
            bid = ev.get("beast_id")
            if bid is not None:
                for b in world.get("beasts", []):
                    if b["id"] == bid:
                        site_beasts.append(b)
                        break

    site_type = site.get("site_type", "city")
    themes = POI_TYPE_THEMES.get(site_type, POI_TYPE_THEMES["city"])

    return {
        "poi_type": "site",
        "poi_subtype": site_type,
        "civ": civ,
        "civ_name": civ["name"] if civ else "the wilds",
        "civ_alignment": civ["alignment"] if civ else "neutral",
        "biome_name": biome_name,
        "biome_id": biome_id,
        "same_civ_sites": same_civ_sites,
        "same_civ_figures": same_civ_figures,
        "site_events": site_events,
        "civ_artifacts": civ_artifacts,
        "site_beasts": site_beasts,
        "themes": themes,
        "eras": world.get("eras", []),
        "end_year": world.get("config", {}).get("end_year", 500),
    }

# REMOVED: resolve_artifact_context (moved to ooi_memory_gen.py)
    """Build rich context about an artifact POI."""
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

    themes = POI_TYPE_THEMES.get("artifact", POI_TYPE_THEMES["artifact"])

    return {
        "poi_type": "artifact",
        "poi_subtype": artifact.get("type", "unknown"),
        "civ": civ,
        "civ_name": civ["name"] if civ else "the wilds",
        "civ_alignment": civ["alignment"] if civ else "neutral",
        "biome_name": "unknown",
        "biome_id": 0,
        "same_civ_sites": [],
        "same_civ_figures": [],
        "site_events": artifact_events,
        "civ_artifacts": [],
        "site_beasts": [],
        "themes": themes,
        "eras": world.get("eras", []),
        "end_year": world.get("config", {}).get("end_year", 500),
    }

def resolve_biome_context(world: Dict, poi: Dict) -> Dict:
    """Build rich context about a biome feature POI."""
    biome_id = poi.get("biome_id", 0)
    biome_name = "Unknown"
    for b in world.get("biomes", []):
        if b["id"] == biome_id:
            biome_name = b["name"]
            break

    themes = POI_TYPE_THEMES.get("biome_feature", POI_TYPE_THEMES["biome_feature"])

    # Events near this biome (check site events that match biome_id)
    biome_events = []
    for ev in world.get("events", []):
        site_id = ev.get("site_id")
        if site_id is not None:
            for s in world.get("sites", []):
                if s["id"] == site_id and s.get("biome_id") == biome_id:
                    biome_events.append(ev)
                    break

    return {
        "poi_type": "biome_feature",
        "poi_subtype": poi.get("feature_type", "natural feature"),
        "civ": None,
        "civ_name": "the wilds",
        "civ_alignment": "neutral",
        "biome_name": biome_name,
        "biome_id": biome_id,
        "same_civ_sites": [],
        "same_civ_figures": [],
        "site_events": biome_events,
        "civ_artifacts": [],
        "site_beasts": [],
        "themes": themes,
        "eras": world.get("eras", []),
        "end_year": world.get("config", {}).get("end_year", 500),
    }

def resolve_context(world: Dict, poi: Dict, poi_type: str) -> Dict:
    """Route to the appropriate context resolver based on POI type."""
    if poi_type == "site":
        return resolve_site_context(world, poi)
    elif poi_type == "biome_feature":
        return resolve_biome_context(world, poi)
    else:
        raise ValueError(f"Unknown POI type: {poi_type}")

# ──────────────────────────────────────────────────────────────────────
# 5.  DECADE COMPUTATION
# ──────────────────────────────────────────────────────────────────────

def compute_decades(poi: Dict, poi_type: str, world: Dict) -> int:
    """Compute the number of decades this POI has existed, clamped 3-30."""
    end_year = world.get("config", {}).get("end_year", 500)
    start_year = world.get("config", {}).get("start_year", 0)

    if poi_type == "site":
        founded = poi.get("founded_year", start_year)
        lifespan = end_year - founded
    elif poi_type == "biome_feature":
        lifespan = end_year - start_year
    else:
        raise ValueError(f"Unknown POI type: {poi_type}")

    if lifespan < 1:
        lifespan = 1

    decades = max(1, lifespan // 10)
    return max(3, min(30, decades))

def get_decade_ranges(poi: Dict, poi_type: str, world: Dict, num_decades: int) -> List[Tuple[int, int]]:
    """Compute decade year ranges for the POI's existence."""
    end_year = world.get("config", {}).get("end_year", 500)
    start_year = world.get("config", {}).get("start_year", 0)

    if poi_type == "site":
        start = poi.get("founded_year", start_year)
    elif poi_type == "biome_feature":
        start = start_year
    else:
        raise ValueError(f"Unknown POI type: {poi_type}")

    # Ensure start is within bounds
    if start < start_year:
        start = start_year

    total_span = end_year - start
    if total_span <= 0:
        return [(start, end_year)]

    decade_step = total_span / num_decades
    ranges = []
    for i in range(num_decades):
        decade_start = int(start + i * decade_step)
        decade_end = int(start + (i + 1) * decade_step)
        if decade_end > end_year:
            decade_end = end_year
        if decade_start < decade_end:
            ranges.append((decade_start, decade_end))
    return ranges

# ──────────────────────────────────────────────────────────────────────
# 6.  MEMORY GENERATION
# ──────────────────────────────────────────────────────────────────────

def _describe_event(ev: Dict) -> str:
    """Return a short human-readable description of a world event."""
    ev_type = ev.get("type", "event")
    if ev_type == "battle":
        return f"a great battle was fought — {ev.get('civ1_name', 'unknown')} against {ev.get('civ2_name', 'unknown')}"
    elif ev_type == "siege":
        return f"the site was besieged — it held firm through the assault"
    elif ev_type == "beast_attack":
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
    poi: Dict,
    context: Dict,
    num_decades: int,
    decade_ranges: List[Tuple[int, int]],
    rng: random.Random,
) -> List[Dict]:
    """
    Generate long-term memories per decade — each is a verbose decade
    culmination that weaves together real events, growth milestones,
    and reflections. Major events are highlighted as the most poignant.
    """
    memories = []
    themes = context["themes"]
    poi_type = context["poi_type"]
    poi_subtype = context["poi_subtype"]
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
            # Foundation decade — always foundation event
            pool = themes.get("foundation_events", [])
            template = pick_unique(pool, used_foundation, rng)
            paragraphs.append(f"In the beginning, {template}")

            # Supplement with a real event if available
            if decade_events and rng.random() < 0.5:
                ev = rng.choice(decade_events)
                ev_desc = _describe_event(ev)
                paragraphs.append(f"Shortly after, {ev_desc}.")

            paragraphs.append("This was the start of its story.")
            memory_type = "foundation"

        else:
            # Subsequent decades — verbose culmination
            memory_type = "life_event"

            # Opening: decade atmosphere
            openings = [
                f"The {decade_label} was a {rng.choice(['hard', 'quiet', 'turbulent', 'prosperous', 'dark', 'bright', 'forgotten'])} decade for {civ_name}.",
                f"In the {decade_label}, the site felt {rng.choice(['heavy with change', 'still and waiting', 'alive with possibility', 'worn and tired', 'sharp and dangerous'])}.",
                f"Looking back at the {decade_label}, it stands out — {rng.choice(['the air smelled of smoke', 'the harvest was bountiful', 'the nights were long', 'the wind carried strange news', 'everything felt fragile'])}.",
            ]
            paragraphs.append(rng.choice(openings))

            # Major events — highlight up to 2 real events
            if decade_events and rng.random() < 0.7:
                featured = rng.sample(decade_events, min(len(decade_events), 2))
                for ev in featured:
                    ev_desc = _describe_event(ev)
                    highlight_prefix = rng.choice([
                        "The most poignant moment",
                        "What marked that decade",
                        "The defining event",
                        "What struck deepest",
                    ])
                    paragraphs.append(f"{highlight_prefix} was {ev_desc}.")
            else:
                # No real events — use themed milestones
                if rng.random() < 0.5:
                    pool = themes.get("life_events", [])
                    template = pick_unique(pool, used_life, rng)
                    paragraphs.append(f"During this time, {template}")
                else:
                    pool = themes.get("growth_milestones", [])
                    if pool:
                        template = pick_unique(pool, used_growth, rng)
                        paragraphs.append(f"The site grew — {template}")
                    else:
                        pool = themes.get("life_events", [])
                        template = pick_unique(pool, used_life, rng)
                        paragraphs.append(f"During this time, {template}")

            # Reflection on the decade
            reflection_templates = [
                f"That decade taught {civ_name} that {rng.choice(['the world is cruel', 'kindness matters', 'strength alone is not enough', 'time changes everything', 'some wounds never heal', 'hope endures', 'a good plan saves lives'])}.",
                f"The {rng.choice(['lessons', 'faces', 'sounds', 'silences', 'shadows'])} of that time linger still.",
                f"It was a decade that {rng.choice(['shaped the site', 'broke its spirit', 'made it what it is', 'tested its limits', 'showed the truth', 'left its mark'])}.",
            ]
            paragraphs.append(rng.choice(reflection_templates))

        # Inject references to other entities
        full_text = " ".join(p for p in paragraphs if p)
        if context.get("same_civ_figures") and rng.random() < 0.3:
            other = rng.choice(context["same_civ_figures"])
            full_text += f" {other['name']} was active during this time."

        if context.get("site_beasts") and rng.random() < 0.2:
            beast = rng.choice(context["site_beasts"])
            full_text += f" The {beast['name']} terrorized the region."

        if context.get("civ_artifacts") and rng.random() < 0.2:
            art = rng.choice(context["civ_artifacts"])
            # Strip leading "The" to avoid "The The Mirror of Darkness"
            art_name = art['name']
            if art_name.startswith("The "):
                art_name = art_name[4:]
            full_text += f" The {art_name} was held in the area."

        # Alignment / type closing — vary to avoid identical tails
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

        site_type = context.get("poi_subtype", "site")
        type_variants = {
            "city": [
                " The city grew and changed.",
                " The city's streets pulsed with life.",
                " Within the city walls, the world turned.",
                " The city endured — its heart never stopped beating.",
                " The city swelled with trade and ambition.",
                " The city's towers scraped the sky — a monument to progress.",
                " The city was a living machine of stone and will.",
                " The city thrummed with the noise of countless lives.",
            ],
            "fortress": [
                " The fortress stood unyielding.",
                " The fortress walls held against all threats.",
                " Stone and steel — the fortress was a bastion of strength.",
                " The fortress remained, a silent guardian of the land.",
                " The fortress was carved from the mountain itself.",
                " The fortress's gates never opened to a foe.",
                " The fortress stood — patient, eternal, and terrible.",
                " The fortress was a promise carved in stone: we do not fall.",
            ],
            "village": [
                " The village endured through the years.",
                " The village prospered in its quiet way.",
                " Village life continued — planting, harvesting, living.",
                " The village weathered every storm, small but unbroken.",
                " The village grew slowly, one generation at a time.",
                " The village was a quiet heart in a loud world.",
                " The village's hearths burned steady through every season.",
                " The village survived — humble, stubborn, and alive.",
            ],
            "tower": [
                " The tower remained a beacon.",
                " The tower stood tall against the sky.",
                " The tower's peak scraped the clouds — a symbol of vigilance.",
                " The tower watched over the land, unchanging and patient.",
                " The tower was a sentinel against the darkness.",
                " The tower's light never faltered — a promise kept.",
                " The tower stood alone, proud and unyielding.",
                " The tower pierced the sky, a finger of stone pointing at fate.",
            ],
            "shrine": [
                " The shrine remained a place of peace.",
                " The shrine held its sacred stillness.",
                " Pilgrims came to the shrine — it gave them solace.",
                " The shrine endured, a quiet heart in a loud world.",
                " The shrine's candles burned with unwavering faith.",
                " The shrine was a sanctuary where the world fell silent.",
                " The shrine was woven from devotion and ancient stone.",
                " The shrine stood apart from the clamor of the age.",
            ],
        }.get(site_type, [" The place endured."])

        alignment_closer = rng.choice(alignment_variants)
        type_closer = rng.choice(type_variants)
        # Track used closers to avoid repeats within the same POI
        if "used_align_closers" not in context:
            context["used_align_closers"] = set()
            context["used_type_closers"] = set()
        if alignment_closer in context["used_align_closers"]:
            remaining = [v for v in alignment_variants if v not in context["used_align_closers"]]
            if remaining:
                alignment_closer = rng.choice(remaining)
        if type_closer in context["used_type_closers"]:
            remaining = [v for v in type_variants if v not in context["used_type_closers"]]
            if remaining:
                type_closer = rng.choice(remaining)
        context["used_align_closers"].add(alignment_closer)
        context["used_type_closers"].add(type_closer)

        full_text += " " + alignment_closer + " " + type_closer

        # Racial / thematic closing thought
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
            "poi_type": poi_type,
            "poi_subtype": poi_subtype,
            "era": era_name,
        })

    return memories

def generate_short_term_memories(
    poi: Dict,
    context: Dict,
    rng: random.Random,
) -> List[Dict]:
    """Generate 15 short-term / recent observations — seasonal events for current/last season."""
    memories = []
    themes = context["themes"]
    poi_type = context["poi_type"]
    poi_subtype = context["poi_subtype"]
    civ_name = context["civ_name"]
    biome_name = context.get("biome_name", "the land")

    current_year = context.get("end_year", 250)
    seasons_cycle = ["spring", "summer", "autumn", "winter"]
    current_season_idx = current_year % 4
    current_season = seasons_cycle[current_season_idx]
    last_season = seasons_cycle[(current_season_idx - 1) % 4]

    used_recent = set()
    used_thought = set()

    # 8 seasonal observations (4 current season, 4 last season)
    seasonal_pool = [
        f"Travelers passed through {civ_name} and remarked on the site's condition.",
        f"The {biome_name} around the site changed with the seasons — {rng.choice(['green', 'golden', 'white', 'bare'])} and alive.",
        f"A stranger arrived at the site — they carried news from distant lands.",
        f"Repairs were made to the {rng.choice(['main gate', 'outer wall', 'roof', 'well', 'road', 'bridge', 'shrine'])}.",
        f"Traders brought goods from afar — the site's market was busy.",
        f"The site's population {rng.choice(['grew', 'shrank', 'held steady'])} this season.",
        f"Signs of {rng.choice(['wildlife', 'bandits', 'spirit activity', 'weather change', 'disease', 'prosperity'])} were noted.",
        f"A {rng.choice(['festival', 'ritual', 'ceremony', 'gathering', 'tournament', 'market'])} was held at the site.",
        f"Word came of {rng.choice(['a war', 'a peace', 'a famine', 'a discovery', 'a death'])} in the wider realm.",
        f"The site's {rng.choice(['elder', 'leader', 'keeper', 'captain', 'master'])} made a proclamation.",
        f"A {rng.choice(['child was born', 'couple married', 'elder passed', 'stranger was welcomed', 'dispute was settled'])} at the site.",
        f"The weather this season was {rng.choice(['harsh', 'mild', 'unusual', 'beautiful', 'terrible'])} — it affected daily life.",
        f"A nearby {rng.choice(['forest', 'river', 'mountain', 'cave', 'field', 'ruin'])} was {rng.choice(['explored', 'mapped', 'damaged', 'reported', 'avoided'])}.",
        f"Supplies were {rng.choice(['gathered', 'distributed', 'stored', 'traded', 'found lacking'])} for the coming season.",
        f"The site's {rng.choice(['craftsmen', 'farmers', 'guards', 'scholars', 'priests'])} were busy with their work.",
    ]

    eras = context.get("eras", [])

    # Assign specific seasons
    for i in range(8):
        template = pick_unique(seasonal_pool, used_recent, rng)
        season = current_season if i < 4 else last_season
        year = current_year if season == current_season else current_year - 1
        year_label = format_year_with_era(year, eras)
        # Replace season placeholders
        template = template.replace("spring", season).replace("summer", season).replace("autumn", season).replace("winter", season)
        memories.append({
            "type": "seasonal_observation",
            "season": season,
            "year": year,
            "year_label": year_label,
            "memory": template,
            "poi_type": poi_type,
            "poi_subtype": poi_subtype,
        })

    # 7 seasonal thoughts / moods
    thought_pool = [
        f"This {current_season}, the site feels {rng.choice(['at peace', 'restless', 'forgotten', 'alive', 'haunted', 'blessed'])}.",
        f"The {current_season} {rng.choice(['air', 'light', 'stillness', 'darkness', 'warmth', 'cold'])} hangs over {civ_name} — it makes the site feel {rng.choice(['melancholy', 'hopeful', 'ancient', 'fragile', 'timeless'])}.",
        f"I wonder what the {rng.choice(['next season', 'coming year', 'distant future'])} will bring to this place.",
        f"This season last year was {rng.choice(['different', 'the same', 'worse', 'better'])} — the site has {rng.choice(['changed', 'endured', 'grown', 'suffered'])} since then.",
        f"The {rng.choice(['elders say', 'keepers tell', 'songs remember', 'runes record'])} that {rng.choice(['this season is sacred', 'the winter is harsh here', 'the spring brings renewal', 'the summer is for war', 'the autumn is for remembrance'])}.",
        f"The site has been {rng.choice(['dreaming', 'waiting', 'praying', 'wandering', 'working', 'watching'])} more than usual this season.",
        f"This season feels {rng.choice(['different', 'the same as always', 'charged with meaning', 'empty', 'precious', 'fleeting'])} — the site {rng.choice(['endures it', 'fears it', 'accepts it', 'fights it', 'surrenders to it'])}.",
    ]
    for i in range(7):
        thought = pick_unique(thought_pool, used_thought, rng)
        if rng.random() < 0.3 and context.get("civ_name"):
            thought = f"Considering {context['civ_name']}. " + thought
        elif rng.random() < 0.3:
            thought = f"The future is uncertain. " + thought

        memories.append({
            "type": "recent_thought",
            "memory": thought,
            "poi_type": poi_type,
            "poi_subtype": poi_subtype,
        })

    return memories

def generate_near_term_memories(
    poi: Dict,
    context: Dict,
    rng: random.Random,
) -> List[Dict]:
    """Generate 10 near-term / future-looking plans and concerns for the POI."""
    memories = []
    themes = context["themes"]
    poi_type = context["poi_type"]
    poi_subtype = context["poi_subtype"]
    civ_name = context["civ_name"]
    biome_name = context.get("biome_name", "the land")

    used_plans = set()
    used_worries = set()

    plan_pool = [
        f"There are plans to expand the site — new walls, new quarters, new hope.",
        f"A new {rng.choice(['gate', 'tower', 'well', 'road', 'bridge', 'hall', 'shrine'])} is to be built this coming year.",
        f"The site intends to {rng.choice(['fortify against threats', 'open trade routes', 'plant new fields', 'explore the surrounding caves', 'seek a patron', 'establish a school', 'build a library'])}.",
        f"We must prepare for the coming {rng.choice(['winter', 'war', 'festival', 'journey', 'hunting season', 'siege', 'caravan'])}.",
        f"The {rng.choice(['leader', 'council', 'elders', 'master'])} {rng.choice(['has called for', 'have called for', 'called for', 'ordered'])} {rng.choice(['a census', 'a gathering', 'a ritual', 'an expedition', 'a rebuilding effort', 'a diplomatic mission'])}.",
        f"The site aims to {rng.choice(['attract more settlers', 'improve its defenses', 'stockpile supplies', 'forge alliances', 'discover a lost artifact', 'train the youth', 'restore an old monument'])}.",
        f"We hope to {rng.choice(['make contact with a distant realm', 'recover from recent hardships', 'celebrate a milestone', 'honor the ancestors', 'complete the great project'])}.",
        f"The site will host a {rng.choice(['moot', 'council', 'gathering', 'celebration', 'ritual', 'tournament', 'ceremony'])} next season.",
        f"There is a vision to {rng.choice(['build a monument', 'plant a sacred grove', 'forge a legacy', 'train a successor', 'walk the old road', 'discover the truth of the past'])}.",
        f"We dream of {rng.choice(['peace and prosperity', 'becoming a great power', 'understanding the old mysteries', 'finding a place in history', 'simply surviving the years ahead'])}.",
    ]

    worry_pool = [
        f"I worry about the war in the east — it creeps closer each season.",
        f"I worry about the drought — the {biome_name} is already dry.",
        f"I worry about the raids on the caravans — trade is slowing.",
        f"I worry about the strange signs in the {biome_name} — the animals are restless.",
        f"I worry about the unrest in the region — the folk are troubled.",
        f"I worry about the plague spreading — too many have fallen sick.",
        f"I worry about the old spirits growing restless — the rituals have felt wrong.",
        f"I worry about the bandits on the roads — travel is dangerous now.",
        f"I worry about the harvest failing — we will starve if it does.",
        f"I worry about the river drying up — it has never done that before.",
        f"I fear that the old ways are fading — no one remembers them anymore.",
        f"I fear that the site's strength is declining — it is not what it once was.",
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
        f"I am concerned about the site's water supply — something feels wrong with it.",
        f"I am concerned about the {biome_name} itself — it is changing in ways we do not understand.",
    ]

    for i in range(6):
        template = pick_unique(plan_pool, used_plans, rng)
        memories.append({
            "type": "future_plan",
            "memory": template,
            "poi_type": poi_type,
            "poi_subtype": poi_subtype,
        })

    for i in range(4):
        template = pick_unique(worry_pool, used_worries, rng)
        memories.append({
            "type": "future_worry",
            "memory": template,
            "poi_type": poi_type,
            "poi_subtype": poi_subtype,
        })

    return memories

# ──────────────────────────────────────────────────────────────────────
# 7.  BIOME FEATURE GENERATION (derived from terrain, not a discrete entity)
# ──────────────────────────────────────────────────────────────────────

def generate_biome_feature_poi(
    world: Dict,
    biome_id: int,
    rng: random.Random,
) -> Dict:
    """Create a synthetic POI entry for a biome feature."""
    biome_name = "Unknown"
    for b in world.get("biomes", []):
        if b["id"] == biome_id:
            biome_name = b["name"]
            break

    # Find a representative location in the terrain
    terrain = world.get("terrain", {})
    biome_grid = terrain.get("biome_grid", [])
    elevation_grid = terrain.get("elevation_grid", [])

    sample_x, sample_y = 0, 0
    if biome_grid:
        w = len(biome_grid)
        if w > 0:
            h = len(biome_grid[0])
            # Find first occurrence of this biome
            for x in range(w):
                for y in range(h):
                    if biome_grid[x][y] == biome_id:
                        sample_x, sample_y = x, y
                        break
                else:
                    continue
                break

    elevation = 0.5
    if elevation_grid and sample_x < len(elevation_grid) and sample_y < len(elevation_grid[0]):
        elevation = elevation_grid[sample_x][sample_y]

    feature_types = {
        "Ocean": "vast sea",
        "Deep Ocean": "abyssal trench",
        "Shallows": "coastal shelf",
        "Beach": "sandy shore",
        "Grassland": "rolling plains",
        "Forest": "woodland expanse",
        "Dense Forest": "deep wildwood",
        "Taiga": "boreal wilderness",
        "Tundra": "frozen waste",
        "Desert": "arid expanse",
        "Badlands": "eroded badlands",
        "Savanna": "open savanna",
        "Swamp": "murky fenland",
        "Mountain": "mountain range",
        "High Mountain": "high peaks",
        "Volcanic": "volcanic zone",
    }

    feat_type = feature_types.get(biome_name, "natural feature")

    poi = {
        "id": -biome_id,  # negative to avoid collision
        "name": f"The {biome_name}",
        "type": "biome_feature",
        "biome_id": biome_id,
        "biome_name": biome_name,
        "feature_type": feat_type,
        "sample_location": {"x": sample_x, "y": sample_y},
        "elevation": elevation,
        "founded_year": world.get("config", {}).get("start_year", 0),
    }
    return poi

# ──────────────────────────────────────────────────────────────────────
# 8.  FACTS BUILDER — short single-line truths about the POI
# ──────────────────────────────────────────────────────────────────────

SITE_MATERIALS = {
    "city":     ["stone", "granite", "limestone", "brick", "wood", "iron"],
    "fortress": ["granite", "basalt", "iron", "stone", "obsidian"],
    "village":  ["wood", "thatch", "mud brick", "stone", "clay"],
    "tower":    ["stone", "brick", "wood", "iron"],
    "shrine":   ["marble", "granite", "gold", "obsidian", "crystal"],
}

SITE_INDUSTRIES = {
    "city":     ["mining", "smelting", "crafts", "farming", "trade", "brewing"],
    "fortress": ["mining", "smelting", "weaponcraft", "armorcraft", "engraving"],
    "village":  ["farming", "fishing", "hunting", "woodcutting", "weaving"],
    "tower":    ["alchemy", "research", "scribing", "enchanting"],
    "shrine":   ["pilgrimage", "offerings", "crafts", "herbalism"],
}

SITE_DEFENSES = {
    "city":     ["stone wall", "moat", "gate towers", "watch posts"],
    "fortress": ["double wall", "corner towers", "drawbridge", "murder holes"],
    "village":  ["palisade", "watch tower", "ditch"],
    "tower":    ["high walls", "iron gate", "spike trap"],
    "shrine":   ["outer wall", "sanctuary barrier", "guard posts"],
}

SITE_SIZE_CATEGORIES = {
    "city":     ["large city", "city", "small city"],
    "fortress": ["grand fortress", "fortress", "keep", "outpost"],
    "village":  ["large village", "village", "hamlet"],
    "tower":    ["great tower", "tower", "watchtower"],
    "shrine":   ["grand temple", "temple", "shrine"],
}

BIOME_FEATURE_FACTS = {
    "river":    ["flow rate", "width", "depth", "water clarity", "fish stock"],
    "lake":     ["surface area", "depth", "water clarity", "fish stock", "surrounding veg"],
    "mountain": ["peak height", "rock type", "snow line", "volcanic activity"],
    "forest":   ["tree density", "old growth", "underbrush", "wildlife"],
    "desert":   ["dune height", "aridity", "sand depth", "oasis presence"],
}


def build_poi_facts(poi: Dict, context: Dict, poi_type: str, rng: random.Random) -> Dict[str, str]:
    """Build short single-line facts about the POI."""
    facts = {}

    if poi_type == "site":
        site_type = poi.get("site_type", "city")
        sub_rng = random.Random(poi["id"] * 17 + 7)
        materials_pool = SITE_MATERIALS.get(site_type, ["stone"])
        industries_pool = SITE_INDUSTRIES.get(site_type, ["farming"])
        defenses_pool = SITE_DEFENSES.get(site_type, ["wall"])
        size_pool = SITE_SIZE_CATEGORIES.get(site_type, ["settlement"])

        facts["site_type"] = site_type
        facts["founded_year"] = str(poi.get("founded_year", "?"))
        facts["population"] = str(poi.get("population", 0))
        facts["size_class"] = sub_rng.choice(size_pool)
        facts["primary_material"] = sub_rng.choice(materials_pool)
        facts["industry"] = sub_rng.choice(industries_pool)
        facts["defenses"] = sub_rng.choice(defenses_pool)
        facts["is_capital"] = "yes" if poi.get("is_capital") else "no"
        facts["civ"] = context.get("civ_name", "unknown")

        # Ruler — pick a notable figure from the same civ
        if context.get("same_civ_figures"):
            ruler_rng = random.Random(poi["id"] * 31 + 3)
            ruler = ruler_rng.choice(context["same_civ_figures"])
            facts["ruler"] = ruler.get("name", "unknown")
            facts["ruler_title"] = ruler.get("title", "lord")

    elif poi_type == "biome_feature":
        feature_type = poi.get("feature_type", "landmark")
        facts["feature_type"] = feature_type
        facts["biome"] = context.get("biome_name", "unknown")
        facts["elevation"] = str(poi.get("elevation", 0))
        if "sample_location" in poi:
            facts["location"] = str(poi["sample_location"])
        # Add some generated properties
        feat_rng = random.Random(hash(poi.get("name", "unknown")) & 0xFFFFFF)
        props = BIOME_FEATURE_FACTS.get(feature_type, ["size"])
        for p in props:
            facts[p] = str(feat_rng.randint(1, 10))

    return facts


# ──────────────────────────────────────────────────────────────────────
# 9.  MAIN
# ──────────────────────────────────────────────────────────────────────

def generate_poi_memories(
    world_path: str,
    output_path: str,
    poi_type: str = "site",
    poi_id: Optional[int] = None,
    poi_biome_id: Optional[int] = None,
    seed: Optional[int] = None,
    decade_override: Optional[int] = None,
) -> Dict:
    if seed is None:
        seed = random.randint(0, 2**31)
    rng = random.Random(seed)

    print(f"Loading world from {world_path}...")
    world = load_world(world_path)

    # Handle biome features specially (they're not stored as entities)
    if poi_type == "biome_feature":
        if poi_biome_id is None:
            # Pick a random biome
            biomes = world.get("biomes", [])
            land_biomes = [b for b in biomes if b["id"] not in (0, 1, 2)]
            if not land_biomes:
                land_biomes = biomes
            chosen = rng.choice(land_biomes)
            poi_biome_id = chosen["id"]
        poi = generate_biome_feature_poi(world, poi_biome_id, rng)
        print(f"POI: {poi['name']} (biome feature, id={poi_biome_id})")
    else:
        print(f"Selecting {poi_type}...")
        poi = select_poi(world, poi_type=poi_type, poi_id=poi_id, rng=rng)
        print(f"POI: {poi.get('name', 'unknown')} ({poi_type}), id={poi.get('id', '?')}")

    print("Resolving context...")
    context = resolve_context(world, poi, poi_type)

    num_decades = decade_override if decade_override is not None else compute_decades(poi, poi_type, world)
    print(f"Decades: {num_decades}")

    decade_ranges = get_decade_ranges(poi, poi_type, world, num_decades)
    print(f"Decade ranges: {decade_ranges}")

    print(f"Generating {num_decades} long-term memories...")
    long_term = generate_long_term_memories(poi, context, num_decades, decade_ranges, rng)

    print("Generating 15 short-term memories...")
    short_term = generate_short_term_memories(poi, context, rng)

    print("Generating 10 near-term memories...")
    near_term = generate_near_term_memories(poi, context, rng)

    # Build output
    poi_info = {}
    if poi_type == "site":
        poi_info = {
            "id": poi["id"],
            "name": poi["name"],
            "type": poi_type,
            "subtype": poi.get("site_type"),
            "biome_id": poi.get("biome_id"),
            "biome_name": context.get("biome_name"),
            "founded_year": poi.get("founded_year"),
            "founded_season": poi.get("founded_season"),
            "population": poi.get("population"),
            "is_capital": poi.get("is_capital", False),
            "civ_id": poi.get("civ_id"),
        }
    # REMOVED: artifact branch (moved to ooi_memory_gen.py)
    # REMOVED: beast branch (moved to ooi_memory_gen.py)
    elif poi_type == "biome_feature":
        poi_info = {
            "id": poi["id"],
            "name": poi["name"],
            "type": poi_type,
            "subtype": poi.get("feature_type"),
            "biome_id": poi.get("biome_id"),
            "biome_name": poi.get("biome_name"),
            "sample_location": poi.get("sample_location"),
            "elevation": poi.get("elevation"),
        }

    # Build short facts
    facts = build_poi_facts(poi, context, poi_type, rng)

    output = {
        "poi": poi_info,
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

    print(f"\nExported POI memories to {output_path}")
    print(f"  Long-term:  {len(long_term)} memories (decades)")
    print(f"  Short-term: {len(short_term)} memories")
    print(f"  Near-term:  {len(near_term)} memories")
    print(f"  Total:      {len(long_term) + len(short_term) + len(near_term)} memories")

    return output

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate DF-style POI (Point of Interest) memories")
    parser.add_argument("--world", type=str, required=True, help="Path to world JSON from world_gen.py")
    parser.add_argument("--output", type=str, default="/tmp/poi_memories.json", help="Output JSON path")
    parser.add_argument("--poi-type", type=str, default="site",
                        choices=["site", "biome_feature"],
                        help="Type of POI to generate memories for")
    parser.add_argument("--poi-id", type=int, default=None, help="ID of the POI (for site/artifact/beast)")
    parser.add_argument("--biome-id", type=int, default=None,
                        help="Biome ID for biome_feature POI type")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--decades", type=int, default=None, help="Override number of decades")
    args = parser.parse_args()

    generate_poi_memories(
        world_path=args.world,
        output_path=args.output,
        poi_type=args.poi_type,
        poi_id=args.poi_id,
        poi_biome_id=args.biome_id,
        seed=args.seed,
        decade_override=args.decades,
    )
