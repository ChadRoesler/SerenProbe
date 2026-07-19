#!/usr/bin/env python3
"""
Procedural Fantasy World History Generator
Inspired by Dwarf Fortress — generates terrain, races, civilizations,
historical figures, and a year-by-year + season-by-season event log.

Outputs JSON suitable for SerenMemory / SerenLoci RAG eval pipelines.
"""

import json
import random
import sys
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Set
from collections import defaultdict, Counter
from enum import Enum

# ──────────────────────────────────────────────────────────────────────
# 1.  RACE-SPECIFIC NAME GENERATORS + ALIGNMENT-BASED TITLES
# ──────────────────────────────────────────────────────────────────────

# Each race gets its own syllable tables, generation rules,
# and alignment-tied title tables.

RACE_NAME_RULES = {

    "Dwarf": {
        # Dwarves: consonant-heavy, guttural
        "syllables": [
            "dur", "mor", "kaz", "thok", "gum", "kad", "rak", "nur",
            "bal", "tor", "grim", "baz", "mok", "tur", "gan", "kul",
            "thra", "gorn", "dum", "zol", "bram", "krag", "thul", "mard",
            "orn", "dwal", "gund", "thrak", "bor", "naz", "vorn", "kazad"
        ],
        "prefixes": ["Th", "K", "G", "D", "B", "M", "N", "V", "Z", "Gr"],
        "suffixes": ["ak", "ur", "on", "im", "an", "or", "en", "ul", "az", "oth"],
        "patterns": [("syllable", 2), ("syllable", 3), ("prefix_syllable_suffix", 1)],
        "site_suffixes": ["hold", "hall", "deep", "mine", "gate", "fort", "peak", "vale", "hearth", "forge"],
        # Alignment-based title tables
        "titles": {
            "good": {
                "ruler":      ["High King", "Thane of", "Lord of", "Ancestor of"],
                "noble":      ["Baron", "Duke", "Elder", "Sage of", "Keeper of"],
                "champion":   ["Hammerer of", "Ironheart", "Stonewarden", "Guardian of"],
                "common":     ["Miner", "Smith of", "Engraver", "Brewer", "Gemcutter"],
            },
            "evil": {
                "ruler":      ["Dark King", "Tyrant of", "Despoiler of", "Usurper of"],
                "noble":      ["Baron of", "Duke of", "Tormentor", "Corruptor of"],
                "champion":   ["Skullcrusher", "Doomhammerer", "Bloodbeard", "Rend of"],
                "common":     ["Torturer", "Graverobber", "Slave Driver", "Poisoner"],
            },
            "order": {
                "ruler":      ["High King", "Lawgiver of", "Steadfast of", "Warden of"],
                "noble":      ["Baron", "Duke", "Justiciar", "Censor of", "Sentinel of"],
                "champion":   ["Ironwarden", "Fortress of", "Unyielding", "Bulwark of"],
                "common":     ["Craftsman", "Surveyor", "Architect", "Clerk of"],
            },
            "chaos": {
                "ruler":      ["Mad King", "Wyrm of", "Omen of", "Ruin of"],
                "noble":      ["Baron of", "Duke of", "Tempest of", "Nightmare of"],
                "champion":   ["Berserker", "Fury of", "Ravager", "Storm of"],
                "common":     ["Wild One", "Trickster", "Saboteur", "Shapeshifter"],
            },
            "neutral": {
                "ruler":      ["King", "Queen", "Sovereign of", "Monarch of"],
                "noble":      ["Baron", "Duke", "Elder", "Councilor", "Steward of"],
                "champion":   ["Champion", "Warden", "Captain", "Guardian"],
                "common":     ["Farmer", "Merchant", "Craftsman", "Scholar", "Miner"],
            },
        },
    },

    "Elf": {
        # Elves: flowing, lots of vowels
        "syllables": [
            "ael", "thae", "mir", "lor", "an", "dir", "aelin", "thas",
            "laer", "cae", "riel", "naer", "sind", "gal", "oth", "ion",
            "elen", "tir", "fal", "mar", "ven", "alas", "eri", "anor",
            "lain", "ros", "car", "mel", "lin", "nor", "thal", "ion"
        ],
        "prefixes": ["Ae", "El", "Tha", "Ca", "Me", "Fi", "Na", "Si", "La", "Ri"],
        "suffixes": ["ion", "iel", "an", "or", "il", "on", "as", "el", "ir", "ar"],
        "patterns": [("syllable", 2), ("syllable", 3), ("prefix_syllable_suffix", 1)],
        "site_suffixes": ["wood", "glade", "vale", "hollow", "dell", "grove", "haven", "bough", "leaf", "thorn"],
        "titles": {
            "good": {
                "ruler":      ["High King", "Lord of", "Queen of", "Sovereign of"],
                "noble":      ["Elder", "Sage of", "Keeper of", "Warden of"],
                "champion":   ["Guardian of", "Starfury", "Lightbringer", "Silverwarden"],
                "common":     ["Gardener", "Singer", "Craftsman", "Scholar", "Healer"],
            },
            "evil": {
                "ruler":      ["Shadow King", "Dark Queen", "Corruptor of", "Blight of"],
                "noble":      ["Dreadlord", "Nightweaver", "Soulrender", "Poison of"],
                "champion":   ["Darkfang", "Soulreaver", "Nightstalker", "Voidwalker"],
                "common":     ["Poisoner", "Trickster", "Spider", "Corruptor"],
            },
            "order": {
                "ruler":      ["High King", "Lawgiver of", "Steadfast of", "Warden of"],
                "noble":      ["Elder", "Justiciar", "Sentinel of", "Censor of"],
                "champion":   ["Ironwarden", "Fortress of", "Unyielding", "Bulwark of"],
                "common":     ["Scribe", "Archivist", "Surveyor", "Clerk of"],
            },
            "chaos": {
                "ruler":      ["Wild King", "Fey of", "Trickster of", "Riddle of"],
                "noble":      ["Will o' Wisp", "Phantom", "Shade of", "Whisper of"],
                "champion":   ["Berserker", "Fury of", "Ravager", "Storm of"],
                "common":     ["Wild One", "Trickster", "Saboteur", "Shapeshifter"],
            },
            "neutral": {
                "ruler":      ["King", "Queen", "Sovereign of", "Monarch of"],
                "noble":      ["Elder", "Councilor", "Steward of", "Keeper of"],
                "champion":   ["Champion", "Warden", "Captain", "Guardian"],
                "common":     ["Farmer", "Merchant", "Craftsman", "Scholar", "Singer"],
            },
        },
    },

    "Human": {
        "syllables": [
            "ald", "ric", "bran", "well", "ced", "ric", "ed", "mund",
            "fal", "con", "rad", "bert", "god", "win", "fred", "rick",
            "theo", "dore", "gan", "ulf", "gar", "rick", "bald", "mer",
            "wil", "ram", "bert", "olf", "stan", "ford", "mund", "ric"
        ],
        "prefixes": ["Al", "Bran", "Ce", "Ed", "Fa", "Go", "The", "Wil", "Ran", "Bal"],
        "suffixes": ["ric", "mund", "bert", "win", "fred", "dore", "gan", "ulf", "rick", "wald"],
        "patterns": [("syllable", 2), ("syllable", 3), ("prefix_syllable_suffix", 1)],
        "site_suffixes": ["burg", "ford", "haven", "watch", "bridge", "hall", "gate", "town", "shire", "mark"],
        "titles": {
            "good": {
                "ruler":      ["High King", "Lord of", "Queen of", "Protector of"],
                "noble":      ["Baron", "Duke", "Earl of", "Keeper of", "Sage of"],
                "champion":   ["Knight of", "Paladin", "Defender of", "Lightbringer"],
                "common":     ["Farmer", "Merchant", "Craftsman", "Scholar", "Healer"],
            },
            "evil": {
                "ruler":      ["Dark Lord", "Tyrant of", "Usurper of", "Conqueror of"],
                "noble":      ["Baron of", "Duke of", "Tormentor", "Corruptor of"],
                "champion":   ["Black Knight", "Reaver", "Slayer of", "Doombringer"],
                "common":     ["Torturer", "Graverobber", "Slave Driver", "Poisoner"],
            },
            "order": {
                "ruler":      ["High King", "Lawgiver of", "Steadfast of", "Warden of"],
                "noble":      ["Baron", "Duke", "Justiciar", "Censor of", "Sentinel of"],
                "champion":   ["Ironwarden", "Fortress of", "Unyielding", "Bulwark of"],
                "common":     ["Craftsman", "Surveyor", "Architect", "Clerk of"],
            },
            "chaos": {
                "ruler":      ["Mad King", "Wyrm of", "Omen of", "Ruin of"],
                "noble":      ["Baron of", "Duke of", "Tempest of", "Nightmare of"],
                "champion":   ["Berserker", "Fury of", "Ravager", "Storm of"],
                "common":     ["Wild One", "Trickster", "Saboteur", "Shapeshifter"],
            },
            "neutral": {
                "ruler":      ["King", "Queen", "Sovereign of", "Monarch of"],
                "noble":      ["Baron", "Duke", "Elder", "Councilor", "Steward of"],
                "champion":   ["Champion", "Warden", "Captain", "Guardian"],
                "common":     ["Farmer", "Merchant", "Craftsman", "Scholar", "Miner"],
            },
        },
    },

    "Orc": {
        # Orcs: harsh, guttural, simple
        "syllables": [
            "ghash", "mug", "urg", "oth", "nakh", "grum", "shar", "durg",
            "bol", "gash", "morn", "krug", "zog", "thrak", "gnash", "lurg",
            "snag", "brut", "grol", "nash", "thok", "morg", "gul", "zagh",
            "dush", "krag", "snort", "graz", "makh", "shog", "narg", "gorth"
        ],
        "prefixes": ["Gr", "M", "Sh", "D", "G", "N", "Z", "Sn", "Br", "Kr"],
        "suffixes": ["ak", "uk", "oth", "nakh", "gash", "morn", "durg", "gul", "zog", "grum"],
        "patterns": [("syllable", 2), ("syllable", 3), ("prefix_syllable_suffix", 1)],
        "site_suffixes": ["camp", "pit", "hold", "maw", "fang", "tower", "den", "lair", "fort", "throne"],
        "titles": {
            "good": {
                "ruler":      ["High Chieftain", "Warden of", "Peacemaker", "Uniter of"],
                "noble":      ["Elder", "Bonekeeper", "Sage of", "Truthspeaker"],
                "champion":   ["Ironhide", "Stonewall", "Guardian of", "Mountainbreaker"],
                "common":     ["Farmer", "Craftsman", "Herder", "Builder", "Cook"],
            },
            "evil": {
                "ruler":      ["Warlord", "Bloodking", "Despoiler of", "Conqueror of"],
                "noble":      ["Warleader", "Skullkeeper", "Tormentor", "Bonecrusher"],
                "champion":   ["Bloodreaver", "Skullcrusher", "Doombringer", "Rend of"],
                "common":     ["Torturer", "Graverobber", "Slave Driver", "Poisoner"],
            },
            "order": {
                "ruler":      ["High Chieftain", "Lawgiver of", "Steadfast of", "Warden of"],
                "noble":      ["Elder", "Justiciar", "Sentinel of", "Censor of"],
                "champion":   ["Ironwarden", "Fortress of", "Unyielding", "Bulwark of"],
                "common":     ["Craftsman", "Surveyor", "Architect", "Clerk of"],
            },
            "chaos": {
                "ruler":      ["Mad Chieftain", "Wyrm of", "Omen of", "Ruin of"],
                "noble":      ["Baron of", "Duke of", "Tempest of", "Nightmare of"],
                "champion":   ["Berserker", "Fury of", "Ravager", "Storm of"],
                "common":     ["Wild One", "Trickster", "Saboteur", "Shapeshifter"],
            },
            "neutral": {
                "ruler":      ["Chieftain", "Warlord", "Sovereign of", "Monarch of"],
                "noble":      ["Elder", "Councilor", "Steward of", "Keeper of"],
                "champion":   ["Champion", "Warden", "Captain", "Guardian"],
                "common":     ["Farmer", "Merchant", "Craftsman", "Scholar", "Miner"],
            },
        },
    },

    "Goblin": {
        # Goblins: small, snappy, sibilant
        "syllables": [
            "snik", "gob", "blik", "mek", "tak", "zit", "nog", "rik",
            "skab", "glib", "nack", "tock", "wig", "blot", "snag", "grik",
            "klik", "spig", "trog", "miz", "glop", "skit", "nob", "rak",
            "zib", "glot", "snik", "gak", "blip", "trak", "skig", "nok"
        ],
        "prefixes": ["Sn", "Bl", "G", "M", "T", "Z", "N", "Sk", "Kl", "Gl"],
        "suffixes": ["ik", "ak", "ot", "ig", "ob", "ek", "og", "ib", "ok", "ag"],
        "patterns": [("syllable", 1), ("syllable", 2), ("prefix_syllable_suffix", 1)],
        "site_suffixes": ["warren", "den", "pit", "hole", "cave", "mound", "hive", "nest", "dig", "hollow"],
        "titles": {
            "good": {
                "ruler":      ["High Boss", "Peacemaker", "Uniter of", "Warden of"],
                "noble":      ["Elder", "Keeper of", "Sage of", "Truthspeaker"],
                "champion":   ["Ironhide", "Stonewall", "Guardian of", "Mountainbreaker"],
                "common":     ["Farmer", "Craftsman", "Herder", "Builder", "Cook"],
            },
            "evil": {
                "ruler":      ["Big Boss", "Darkmaster", "Despoiler of", "Conqueror of"],
                "noble":      ["Warleader", "Skullkeeper", "Tormentor", "Bonecrusher"],
                "champion":   ["Bloodreaver", "Skullcrusher", "Doombringer", "Rend of"],
                "common":     ["Torturer", "Graverobber", "Slave Driver", "Poisoner"],
            },
            "order": {
                "ruler":      ["High Boss", "Lawgiver of", "Steadfast of", "Warden of"],
                "noble":      ["Elder", "Justiciar", "Sentinel of", "Censor of"],
                "champion":   ["Ironwarden", "Fortress of", "Unyielding", "Bulwark of"],
                "common":     ["Craftsman", "Surveyor", "Architect", "Clerk of"],
            },
            "chaos": {
                "ruler":      ["Mad Boss", "Wyrm of", "Omen of", "Ruin of"],
                "noble":      ["Baron of", "Duke of", "Tempest of", "Nightmare of"],
                "champion":   ["Berserker", "Fury of", "Ravager", "Storm of"],
                "common":     ["Wild One", "Trickster", "Saboteur", "Shapeshifter"],
            },
            "neutral": {
                "ruler":      ["Boss", "Warlord", "Sovereign of", "Monarch of"],
                "noble":      ["Elder", "Councilor", "Steward of", "Keeper of"],
                "champion":   ["Champion", "Warden", "Captain", "Guardian"],
                "common":     ["Farmer", "Merchant", "Craftsman", "Scholar", "Miner"],
            },
        },
    },
}


# ── Name generation helper functions ────────────────────────────────


def _pick_pattern(pattern_defs: List[Tuple]) -> str:
    """Pick a name-generation pattern and produce a name."""
    pattern_name = random.choices(
        [p[0] for p in pattern_defs],
        weights=[p[1] for p in pattern_defs],
        k=1
    )[0]
    return pattern_name


def _gen_from_syllables(rules: Dict, count: int) -> str:
    parts = []
    for _ in range(count):
        parts.append(random.choice(rules["syllables"]))
    return "".join(parts).capitalize()


def generate_name(rules: Dict) -> str:
    pattern = _pick_pattern(rules["patterns"])
    if pattern == "syllable":
        count = random.randint(2, 3)
        return _gen_from_syllables(rules, count)
    elif pattern == "prefix_syllable_suffix":
        prefix = random.choice(rules["prefixes"])
        suffix = random.choice(rules["suffixes"])
        mid = random.choice(rules["syllables"])
        return (prefix + mid + suffix).capitalize()
    else:
        return _gen_from_syllables(rules, 2)


def generate_site_name(rules: Dict) -> str:
    """Generate a settlement/city name for a race."""
    base = generate_name(rules)
    suffix = random.choice(rules["site_suffixes"])
    if random.random() < 0.3:
        return f"{base}{suffix}"
    else:
        return f"{base}-{suffix}"


def generate_beast_name() -> str:
    """Generate a name for a megabeast or procedurally generated creature."""
    prefixes = [
        "Blood", "Shadow", "Fire", "Ice", "Thunder", "Void", "Iron", "Crystal",
        "Doom", "Storm", "Night", "Silver", "Bronze", "Elder", "Fang", "Horn",
        "Scale", "Venom", "Ash", "Bone"
    ]
    suffixes = [
        "fang", "scale", "wing", "horn", "eye", "claw", "tail", "maw",
        "heart", "soul", "beast", "drake", "wyrm", "titan", "lord", "king",
        "fiend", "serpent", "hunter", "stalker"
    ]
    prefix = random.choice(prefixes)
    suffix = random.choice(suffixes)
    if random.random() < 0.4:
        return f"{prefix}{suffix}"
    else:
        adj = random.choice(["Great", "Elder", "Ancient", "Fell", "Dread", "Savage", "Feral", "Vile"])
        return f"{adj} {prefix}{suffix}"


def generate_artifact_name() -> Tuple[str, str]:
    """Generate a name for a procedural artifact. Returns (name, type)."""
    prefixes = [
        "The Star", "The Moon", "The Sun", "The World", "The Void", "The Heart",
        "The Crown", "The Scepter", "The Blade", "The Shield", "The Ring",
        "The Stone", "The Flame", "The Tome", "The Key", "The Mirror"
    ]
    suffixes = [
        "of Kings", "of Power", "of Light", "of Darkness", "of Eternity",
        "of Ages", "of Fate", "of Glory", "of Ruin", "of Dreams",
        "of the Ancients", "of the Fallen", "of the Deep", "of the Sky"
    ]
    # Map name-prefix → allowed artifact types so name and type are coherent
    prefix_type_map = {
        "The Blade":  ["weapon"],
        "The Shield": ["armor", "shield"],
        "The Crown":  ["crown"],
        "The Scepter":["weapon", "instrument"],
        "The Ring":   ["ring"],
        "The Tome":   ["tome"],
        "The Mirror": ["instrument", "armor"],
        "The Stone":  ["tome", "instrument"],
        "The Star":   ["instrument", "tome"],
        "The Moon":   ["instrument", "tome"],
        "The Sun":    ["instrument", "tome"],
        "The World":  ["tome", "crown"],
        "The Void":   ["armor", "tome"],
        "The Heart":  ["crown", "ring"],
        "The Key":    ["instrument"],
        "The Flame":  ["weapon", "instrument"],
    }
    prefix = random.choice(prefixes)
    suffix = random.choice(suffixes)
    allowed_types = prefix_type_map.get(prefix, ["weapon", "armor", "crown", "ring", "tome", "instrument"])
    art_type = random.choice(allowed_types)
    return (f"{prefix} {suffix}", art_type)


# ──────────────────────────────────────────────────────────────────────
# 2.  ALIGNMENT SYSTEM
# ──────────────────────────────────────────────────────────────────────

ALIGNMENTS = ["good", "evil", "order", "chaos", "neutral"]

# Title tiers (higher tier = more titleworthy)
TITLE_TIERS = ["common", "champion", "noble", "ruler"]

# How deeds map to alignment shifts
DEED_ALIGNMENT_MAP = {
    "battle":          {"good": 0, "evil": 1, "order": 0, "chaos": 1, "neutral": 0},
    "heroism":         {"good": 1, "evil": 0, "order": 1, "chaos": 0, "neutral": 0},
    "cruelty":         {"good": 0, "evil": 1, "order": 0, "chaos": 1, "neutral": 0},
    "diplomacy":       {"good": 1, "evil": 0, "order": 1, "chaos": 0, "neutral": 1},
    "discovery":       {"good": 0, "evil": 0, "order": 0, "chaos": 1, "neutral": 1},
    "creation":        {"good": 1, "evil": 0, "order": 1, "chaos": 1, "neutral": 0},
    "destruction":     {"good": 0, "evil": 1, "order": 0, "chaos": 1, "neutral": 0},
    "rule":            {"good": 0, "evil": 0, "order": 1, "chaos": 0, "neutral": 1},
    "rebellion":       {"good": 0, "evil": 0, "order": 0, "chaos": 1, "neutral": 0},
    "sacrifice":       {"good": 1, "evil": 0, "order": 1, "chaos": 0, "neutral": 0},
    "war":             {"good": 0, "evil": 1, "order": 0, "chaos": 1, "neutral": 0},
    "peace":           {"good": 1, "evil": 0, "order": 1, "chaos": 0, "neutral": 1},
    "magic":           {"good": 0, "evil": 0, "order": 0, "chaos": 1, "neutral": 0},
    "faith":           {"good": 1, "evil": 0, "order": 1, "chaos": 0, "neutral": 0},
    "trade":           {"good": 0, "evil": 0, "order": 1, "chaos": 0, "neutral": 1},
    "art":             {"good": 1, "evil": 0, "order": 0, "chaos": 1, "neutral": 0},
}

# ──────────────────────────────────────────────────────────────────────
# 3.  WORLD DATA STRUCTURES
# ──────────────────────────────────────────────────────────────────────

class Season(Enum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"

SEASON_NAMES = [s.value for s in Season]


@dataclass
class WorldConfig:
    world_name: str = "Aerdor"
    seed: int = 0
    world_width: int = 100
    world_height: int = 80
    num_civilized_races: int = 4
    num_tribal_races: int = 2
    num_civs_per_race: int = 2
    max_history_years: int = 500
    start_year: int = 0
    end_year: int = 250
    civ_max_sites: int = 20


@dataclass
class Biome:
    id: int
    name: str
    color: str


BIOMES = [
    Biome(0, "Ocean", "#1a3a5c"),
    Biome(1, "Deep Ocean", "#0d2b45"),
    Biome(2, "Shallows", "#3b7baa"),
    Biome(3, "Beach", "#d4c4a8"),
    Biome(4, "Grassland", "#7c9c5e"),
    Biome(5, "Forest", "#4a7a3a"),
    Biome(6, "Dense Forest", "#2d5a1e"),
    Biome(7, "Taiga", "#5a7a5a"),
    Biome(8, "Tundra", "#b0b8c0"),
    Biome(9, "Desert", "#c8b45a"),
    Biome(10, "Badlands", "#a07050"),
    Biome(11, "Savanna", "#8ca85a"),
    Biome(12, "Swamp", "#6a5a3a"),
    Biome(13, "Mountain", "#8a8a8a"),
    Biome(14, "High Mountain", "#c0c0c0"),
    Biome(15, "Volcanic", "#5a2a1a"),
]


@dataclass
class Civilization:
    id: int
    name: str
    race: str
    alignment: str
    color: str = "#808080"
    sites: List[int] = field(default_factory=list)
    leader_id: Optional[int] = None
    culture_desc: str = ""
    total_population: int = 0
    events: List[Dict] = field(default_factory=list)


@dataclass
class ProceduralBeast:
    id: int
    name: str
    type: str  # "megabeast", "dragon", "titan", "forgotten_beast"
    description: str
    year_spawned: int
    season_spawned: str
    alignment: str = "chaos"
    active: bool = True
    kills: int = 0
    events: List[Dict] = field(default_factory=list)
    relationships: List[Dict] = field(default_factory=list)


@dataclass
class Artifact:
    id: int
    name: str
    type: str  # "weapon", "armor", "crown", "ring", "tome", "instrument"
    material: str
    created_year: int
    created_season: str
    alignment: str = "neutral"
    creator_id: Optional[int] = None
    civ_id: Optional[int] = None
    description: str = ""
    relationships: List[Dict] = field(default_factory=list)


@dataclass
class Era:
    id: int
    name: str
    start_year: int
    end_year: Optional[int] = None  # None = ongoing/current
    description: str = ""
    trigger_event_type: str = ""
    trigger_summary: str = ""


@dataclass
class Site:
    id: int
    name: str
    x: int
    y: int
    biome_id: int
    founded_year: int
    founded_season: str
    site_type: str  # "city", "fortress", "tower", "village", "shrine"
    population: int = 0
    is_capital: bool = False
    civ_id: Optional[int] = None
    events: List[Dict] = field(default_factory=list)


@dataclass
class HistoricalFigure:
    id: int
    name: str
    race: str
    birth_year: int
    birth_season: str
    alignment: str = "neutral"
    alignment_score: int = 0
    is_titleworthy: bool = False
    title_tier: str = "common"
    title: str = ""
    death_year: Optional[int] = None
    death_season: Optional[str] = None
    civ_id: Optional[int] = None
    site_id: Optional[int] = None
    deeds: List[str] = field(default_factory=list)
    relationships: List[Dict] = field(default_factory=list)

@dataclass
class WorldHistory:
    config: WorldConfig
    biomes: List[List[int]] = field(default_factory=list)
    facts: Dict = field(default_factory=dict)
    memories: Dict = field(default_factory=dict)
    elevation: List[List[float]] = field(default_factory=list)
    civilizations: List[Civilization] = field(default_factory=list)
    historical_figures: List[HistoricalFigure] = field(default_factory=list)
    sites: List[Site] = field(default_factory=list)
    beasts: List[ProceduralBeast] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    events: List[Dict] = field(default_factory=list)
    event_year_index: Dict[int, List[Dict]] = field(default_factory=lambda: defaultdict(list))
    eras: List[Era] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# 4.  TITLE ASSIGNMENT
# ──────────────────────────────────────────────────────────────────────

def determine_alignment(rng: random.Random, bias: Optional[str] = None) -> Tuple[str, int]:
    """Determine alignment for a figure. Returns (alignment_str, score)."""
    if bias and bias in ALIGNMENTS:
        base = ALIGNMENTS.index(bias)
    else:
        base = ALIGNMENTS.index("neutral")
    # Slight random variation
    offset = rng.randint(-1, 1)
    idx = max(0, min(len(ALIGNMENTS) - 1, base + offset))
    score = (idx - 2) * 10  # -20 to +20 range
    return ALIGNMENTS[idx], score


def compute_title_tier(deeds: List[str], rng: random.Random) -> str:
    """Determine how titleworthy a figure is based on their deeds."""
    if not deeds:
        return "common"
    # Count significant deeds
    significant = 0
    for d in deeds:
        if d in ("heroism", "battle", "war", "rule", "creation", "destruction",
                  "discovery", "magic", "faith", "diplomacy", "sacrifice"):
            significant += 1
    if significant >= 8:
        return "ruler"
    if significant >= 5:
        return "noble"
    if significant >= 2:
        return "champion"
    return "common"


def assign_title(figure: HistoricalFigure, rng: random.Random) -> str:
    """Assign a title based on race, alignment, and title tier."""
    rules = RACE_NAME_RULES.get(figure.race, RACE_NAME_RULES["Human"])
    titles_by_alignment = rules.get("titles", {}).get(figure.alignment, rules["titles"]["neutral"])
    tier = figure.title_tier
    options = titles_by_alignment.get(tier, titles_by_alignment.get("common", ["Citizen"]))
    prefix = rng.choice(options)
    # For ruler/noble titles with "of", append civ name if available
    if figure.civ_id is not None:
        # We'll resolve civ name later; for now just the prefix
        pass
    return prefix


# ──────────────────────────────────────────────────────────────────────
# 5.  WORLD GENERATION
# ──────────────────────────────────────────────────────────────────────

def generate_terrain(world: WorldHistory, rng: random.Random) -> None:
    """Generate a simple 2D terrain map with biomes."""
    w, h = world.config.world_width, world.config.world_height
    elev = [[0.0] * h for _ in range(w)]

    num_hills = w * h // 50
    for _ in range(num_hills):
        cx = rng.randint(0, w - 1)
        cy = rng.randint(0, h - 1)
        radius = rng.randint(5, 15)
        height = rng.uniform(0.3, 1.0)
        for x in range(max(0, cx - radius), min(w, cx + radius)):
            for y in range(max(0, cy - radius), min(h, cy + radius)):
                dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                if dist < radius:
                    elev[x][y] += height * (1 - dist / radius)

    max_e = max(max(row) for row in elev) or 1.0
    min_e = min(min(row) for row in elev)
    for x in range(w):
        for y in range(h):
            elev[x][y] = (elev[x][y] - min_e) / (max_e - min_e)

    biomes = [[0] * h for _ in range(w)]
    for x in range(w):
        for y in range(h):
            e = elev[x][y]
            if e < 0.15:
                biomes[x][y] = 0
            elif e < 0.25:
                biomes[x][y] = 3
            elif e < 0.35:
                biomes[x][y] = 4
            elif e < 0.50:
                biomes[x][y] = rng.choice([4, 5, 11])
            elif e < 0.65:
                biomes[x][y] = rng.choice([5, 6, 12])
            elif e < 0.75:
                biomes[x][y] = rng.choice([7, 8, 9, 10])
            elif e < 0.90:
                biomes[x][y] = 13
            else:
                biomes[x][y] = rng.choice([14, 15])

    world.elevation = elev
    world.biomes = biomes


def biome_is_land(biome_id: int) -> bool:
    return biome_id not in (0, 1, 2)


def find_suitable_sites(world: WorldHistory, rng: random.Random, pref_biomes: List[int], count: int) -> List[Tuple[int, int]]:
    sites = []
    w, h = world.config.world_width, world.config.world_height
    for x in range(w):
        for y in range(h):
            if world.biomes[x][y] in pref_biomes:
                sites.append((x, y))
    if not sites:
        for x in range(w):
            for y in range(h):
                if biome_is_land(world.biomes[x][y]):
                    sites.append((x, y))
    if not sites:
        return []
    rng.shuffle(sites)
    return sites[:count]


# ──────────────────────────────────────────────────────────────────────
# 6.  NAME / RACE HELPERS
# ──────────────────────────────────────────────────────────────────────

def get_name_rules(race_name: str) -> Dict:
    return RACE_NAME_RULES.get(race_name, RACE_NAME_RULES["Human"])


def generate_race_name(race_name: str, rng: random.Random) -> str:
    rules = get_name_rules(race_name)
    return generate_name(rules)


def generate_site_name_for_race(race_name: str, rng: random.Random) -> str:
    rules = get_name_rules(race_name)
    return generate_site_name(rules)


# ──────────────────────────────────────────────────────────────────────
# 7.  ENTITY GENERATION
# ──────────────────────────────────────────────────────────────────────

RACE_PREF_BIOMES = {
    "Dwarf": [13, 14, 10],
    "Elf": [5, 6, 7],
    "Human": [4, 5, 11],
    "Orc": [9, 10, 12],
    "Goblin": [12, 9, 4],
}

RACE_COLORS = {
    "Dwarf": "#8B4513",
    "Elf": "#2E8B57",
    "Human": "#4169E1",
    "Orc": "#556B2F",
    "Goblin": "#800000",
}

CIVILIZED_RACES = ["Dwarf", "Elf", "Human"]
TRIBAL_RACES = ["Orc", "Goblin"]
ALL_RACES = CIVILIZED_RACES + TRIBAL_RACES


def generate_initial_sites(world: WorldHistory, rng: random.Random) -> None:
    """Generate initial sites and civilizations."""
    config = world.config
    id_counter = [0]

    def next_id():
        id_counter[0] += 1
        return id_counter[0]

    all_race_names = ALL_RACES * 2
    rng.shuffle(all_race_names)

    civilized_races = all_race_names[:config.num_civilized_races]
    tribal_races = all_race_names[config.num_civilized_races:config.num_civilized_races + config.num_tribal_races]
    selected_races = civilized_races + tribal_races

    for race_name in selected_races:
        rules = get_name_rules(race_name)
        pref_biomes = RACE_PREF_BIOMES.get(race_name, [4, 5])
        color = RACE_COLORS.get(race_name, "#808080")

        # Civ alignment bias — each civ leans one way
        civ_alignment = rng.choice(ALIGNMENTS)

        num_civs = rng.randint(1, config.num_civs_per_race)
        for ci in range(num_civs):
            civ_name = generate_name(rules)
            civ = Civilization(
                id=next_id(),
                name=civ_name,
                race=race_name,
                alignment=civ_alignment,
                color=color,
            )
            world.civilizations.append(civ)

            sites = find_suitable_sites(world, rng, pref_biomes, config.civ_max_sites)
            if not sites:
                continue

            # Capital
            cx, cy = sites[0]
            site_name = generate_site_name_for_race(race_name, rng)
            capital = Site(
                id=next_id(),
                name=site_name,
                x=cx, y=cy,
                biome_id=world.biomes[cx][cy],
                founded_year=config.start_year,
                founded_season="spring",
                site_type="city",
                population=20,
                is_capital=True,
                civ_id=civ.id,
            )
            civ.sites.append(capital)
            world.sites.append(capital)

            # Additional sites
            for sx, sy in sites[1:min(4, len(sites))]:
                site_name = generate_site_name_for_race(race_name, rng)
                site = Site(
                    id=next_id(),
                    name=site_name,
                    x=sx, y=sy,
                    biome_id=world.biomes[sx][sy],
                    founded_year=config.start_year,
                    founded_season="spring",
                    site_type=rng.choice(["village", "fortress", "shrine", "tower"]),
                    population=rng.randint(5, 15),
                    is_capital=False,
                    civ_id=civ.id,
                )
                civ.sites.append(site)
                world.sites.append(site)

            # Historical figures — with alignment and title assignment
            num_figures = rng.randint(4, 8)
            for fi in range(num_figures):
                name = generate_race_name(race_name, rng)

                # Determine alignment (bias toward civ alignment)
                alignment, score = determine_alignment(rng, bias=civ_alignment)

                # Determine title tier based on initial deeds
                initial_deeds = []
                num_deeds = rng.randint(1, 6)
                deed_pool = list(DEED_ALIGNMENT_MAP.keys())
                for _ in range(num_deeds):
                    d = rng.choice(deed_pool)
                    initial_deeds.append(d)

                tier = compute_title_tier(initial_deeds, rng)
                is_worthy = tier in ("champion", "noble", "ruler")

                fig = HistoricalFigure(
                    id=next_id(),
                    name=name,
                    race=race_name,
                    alignment=alignment,
                    alignment_score=score,
                    is_titleworthy=is_worthy,
                    title_tier=tier,
                    title="",  # will fill below
                    birth_year=config.start_year + rng.randint(-50, 10),
                    birth_season=rng.choice(SEASON_NAMES),
                    civ_id=civ.id,
                    site_id=civ.sites[0].id if civ.sites else None,
                    deeds=initial_deeds,
                )

                # Assign title based on alignment + tier
                title_options = rules["titles"].get(alignment, rules["titles"]["neutral"])
                tier_options = title_options.get(tier, title_options.get("common", ["Citizen"]))
                prefix = rng.choice(tier_options)
                if tier in ("ruler", "noble") and "of" in prefix:
                    fig.title = f"{prefix} {civ.name}"
                else:
                    fig.title = prefix

                if fi == 0:
                    prefix2 = rng.choice(tier_options)
                    if "of" in prefix2:
                        fig.title = f"{prefix2} {civ.name}"
                    else:
                        fig.title = f"{prefix2} of {civ.name}"
                    fig.title_tier = "ruler"

                world.historical_figures.append(fig)

    # Initial beasts — with alignment
    num_beasts = rng.randint(2, 5)
    beast_types = ["megabeast", "dragon", "titan", "forgotten_beast"]
    for bi in range(num_beasts):
        btype = rng.choice(beast_types)
        beast = ProceduralBeast(
            id=next_id(),
            name=generate_beast_name(),
            type=btype,
            alignment="chaos",
            description=f"A terrifying {btype} of immense power",
            year_spawned=config.start_year + rng.randint(0, 50),
            season_spawned=rng.choice(SEASON_NAMES),
        )
        world.beasts.append(beast)

    # Initial artifacts — with alignment and creator
    num_artifacts = rng.randint(3, 8)
    artifact_types = ["weapon", "armor", "crown", "ring", "tome", "instrument"]
    materials = ["gold", "silver", "iron", "steel", "mithril", "adamantine", "crystal", "bone", "wood"]
    for ai in range(num_artifacts):
        # Pick a creator from existing historical figures
        creator_id = None
        creator_civ_id = None
        if world.historical_figures:
            creator = rng.choice(world.historical_figures)
            creator_id = creator.id
            creator_civ_id = creator.civ_id
        art_name, art_type = generate_artifact_name()
        art = Artifact(
            id=next_id(),
            name=art_name,
            type=art_type,
            material=rng.choice(materials),
            alignment=rng.choice(ALIGNMENTS),
            created_year=config.start_year + rng.randint(0, 80),
            created_season=rng.choice(SEASON_NAMES),
            creator_id=creator_id,
            civ_id=creator_civ_id,
        )
        world.artifacts.append(art)


# ──────────────────────────────────────────────────────────────────────
# 8.  HISTORY SIMULATION
# ──────────────────────────────────────────────────────────────────────

def simulate_history(world: WorldHistory, rng: random.Random) -> None:
    """Run year-by-year, season-by-season simulation of world history."""
    config = world.config
    id_counter = [len(world.historical_figures) + len(world.sites) + len(world.beasts) + len(world.artifacts)]

    def next_id():
        id_counter[0] += 1
        return id_counter[0]

    sites_by_civ = defaultdict(list)
    for s in world.sites:
        if s.civ_id is not None:
            sites_by_civ[s.civ_id].append(s)

    figures_by_civ = defaultdict(list)
    for f in world.historical_figures:
        if f.civ_id is not None:
            figures_by_civ[f.civ_id].append(f)

    wars = []

    for year in range(config.start_year, config.end_year + 1):
        for season in SEASON_NAMES:
            events_this_season = []

            # ── POPULATION GROWTH ──
            for civ in world.civilizations:
                for site in sites_by_civ.get(civ.id, []):
                    # Growth varies by site type: cities boom, villages slow, shrines minimal
                    # Rates reduced ~90% from original to keep populations in hundreds, not thousands
                    growth_rate = {
                        "city":     rng.randint(1, 3),
                        "fortress": rng.randint(1, 2),
                        "tower":    rng.randint(1, 2),
                        "village":  rng.randint(0, 1),
                        "shrine":   rng.randint(0, 1),
                    }.get(site.site_type, rng.randint(0, 2))
                    site.population += growth_rate
                    if site.population > 200 and len(sites_by_civ[civ.id]) < config.civ_max_sites:
                        site.population //= 2
                        new_site_name = generate_site_name_for_race(civ.race, rng)
                        w, h = config.world_width, config.world_height
                        candidates = []
                        for dx in range(-10, 11):
                            for dy in range(-10, 11):
                                nx, ny = site.x + dx, site.y + dy
                                if 0 <= nx < w and 0 <= ny < h:
                                    if biome_is_land(world.biomes[nx][ny]):
                                        candidates.append((nx, ny))
                        if candidates:
                            rng.shuffle(candidates)
                            nx, ny = candidates[0]
                            new_site = Site(
                                id=next_id(),
                                name=new_site_name,
                                x=nx, y=ny,
                                biome_id=world.biomes[nx][ny],
                                founded_year=year,
                                founded_season=season,
                                site_type=rng.choice(["village", "fortress", "tower", "shrine"]),
                                population=site.population // 4,
                                is_capital=False,
                                civ_id=civ.id,
                            )
                            sites_by_civ[civ.id].append(new_site)
                            world.sites.append(new_site)
                            events_this_season.append({
                                "type": "site_founded",
                                "site_id": new_site.id,
                                "site_name": new_site.name,
                                "civ_id": civ.id,
                                "civ_name": civ.name,
                                "year": year,
                                "season": season,
                            })

            # ── CHARACTER BIRTHS ──
            for civ in world.civilizations:
                if rng.random() < 0.05:  # 5% per civ per season
                    name = generate_race_name(civ.race, rng)
                    alignment, score = determine_alignment(rng, bias=civ.alignment)
                    initial_deeds = [rng.choice(list(DEED_ALIGNMENT_MAP.keys())) for _ in range(rng.randint(0, 3))]
                    tier = compute_title_tier(initial_deeds, rng)
                    is_worthy = tier in ("champion", "noble", "ruler")

                    fig = HistoricalFigure(
                        id=next_id(),
                        name=name,
                        race=civ.race,
                        alignment=alignment,
                        alignment_score=score,
                        is_titleworthy=is_worthy,
                        title_tier=tier,
                        title="",
                        birth_year=year,
                        birth_season=season,
                        civ_id=civ.id,
                        site_id=rng.choice(sites_by_civ.get(civ.id, [None])).id if sites_by_civ.get(civ.id) else None,
                        deeds=initial_deeds,
                    )

                    # Assign title
                    rules = get_name_rules(civ.race)
                    title_options = rules["titles"].get(alignment, rules["titles"]["neutral"])
                    tier_options = title_options.get(tier, title_options.get("common", ["Citizen"]))
                    prefix = rng.choice(tier_options)
                    if tier in ("ruler", "noble") and "of" in prefix:
                        fig.title = f"{prefix} {civ.name}"
                    else:
                        fig.title = prefix

                    world.historical_figures.append(fig)
                    figures_by_civ[civ.id].append(fig)
                    events_this_season.append({
                        "type": "birth",
                        "figure_id": fig.id,
                        "figure_name": fig.name,
                        "race": fig.race,
                        "alignment": fig.alignment,
                        "title": fig.title,
                        "civ_id": civ.id,
                        "year": year,
                        "season": season,
                    })

            # ── CHARACTER DEATHS ──
            for civ in world.civilizations:
                figs = figures_by_civ.get(civ.id, [])
                alive = [f for f in figs if f.death_year is None]
                if alive and rng.random() < 0.02:
                    victim = rng.choice(alive)
                    victim.death_year = year
                    victim.death_season = season
                    cause = rng.choice(["old age", "battle", "disease", "accident", "hunting accident", "execution"])
                    events_this_season.append({
                        "type": "death",
                        "figure_id": victim.id,
                        "figure_name": victim.name,
                        "alignment": victim.alignment,
                        "title": victim.title,
                        "cause": cause,
                        "year": year,
                        "season": season,
                    })

            # ── DEEDS / TITLE PROMOTIONS ──
            for civ in world.civilizations:
                figs = figures_by_civ.get(civ.id, [])
                alive = [f for f in figs if f.death_year is None]
                for f in alive:
                    if rng.random() < 0.01:  # 1% per figure per season
                        deed = rng.choice(list(DEED_ALIGNMENT_MAP.keys()))
                        f.deeds.append(deed)
                        # Re-evaluate title tier
                        old_tier = f.title_tier
                        new_tier = compute_title_tier(f.deeds, rng)
                        if new_tier != old_tier:
                            f.title_tier = new_tier
                            f.is_titleworthy = f.title_tier in ("champion", "noble", "ruler")
                            # Update title
                            rules = get_name_rules(f.race)
                            title_options = rules["titles"].get(f.alignment, rules["titles"]["neutral"])
                            tier_options = title_options.get(f.title_tier, title_options.get("common", ["Citizen"]))
                            prefix = rng.choice(tier_options)
                            if f.title_tier in ("ruler", "noble") and "of" in prefix and f.civ_id is not None:
                                civ_name = next(c.name for c in world.civilizations if c.id == f.civ_id)
                                f.title = f"{prefix} {civ_name}"
                            else:
                                f.title = prefix
                            events_this_season.append({
                                "type": "title_promotion",
                                "figure_id": f.id,
                                "figure_name": f.name,
                                "old_tier": old_tier,
                                "new_tier": new_tier,
                                "new_title": f.title,
                                "deed": deed,
                                "year": year,
                                "season": season,
                            })

            # ── WARS ──
            for i, civ_a in enumerate(world.civilizations):
                for j, civ_b in enumerate(world.civilizations):
                    if i >= j:
                        continue
                    already_at_war = any(
                        (w[0] == civ_a.id and w[1] == civ_b.id and not w[4]) or
                        (w[0] == civ_b.id and w[1] == civ_a.id and not w[4])
                        for w in wars
                    )
                    if already_at_war:
                        continue
                    sites_a = sites_by_civ.get(civ_a.id, [])
                    sites_b = sites_by_civ.get(civ_b.id, [])
                    if not sites_a or not sites_b:
                        continue
                    nearby = False
                    for sa in sites_a:
                        for sb in sites_b:
                            dist = ((sa.x - sb.x) ** 2 + (sa.y - sb.y) ** 2) ** 0.5
                            if dist < 20:
                                nearby = True
                                break
                        if nearby:
                            break
                    if not nearby:
                        continue
                    if rng.random() < 0.01:  # 1% per nearby pair per season
                        wars.append((civ_a.id, civ_b.id, year, season, False))
                        events_this_season.append({
                            "type": "war_declared",
                            "attacker_id": civ_a.id,
                            "attacker_name": civ_a.name,
                            "defender_id": civ_b.id,
                            "defender_name": civ_b.name,
                            "year": year,
                            "season": season,
                        })

            for wi, (civ1, civ2, start_y, start_s, ended) in enumerate(wars):
                if ended:
                    continue
                if rng.random() < 0.08:  # 8% per war per season
                    events_this_season.append({
                        "type": "battle",
                        "civ1_id": civ1,
                        "civ1_name": next(c.name for c in world.civilizations if c.id == civ1),
                        "civ2_id": civ2,
                        "civ2_name": next(c.name for c in world.civilizations if c.id == civ2),
                        "year": year,
                        "season": season,
                    })
                if rng.random() < 0.03:  # 3% per war per season to end
                    winner = rng.choice([civ1, civ2])
                    loser = civ2 if winner == civ1 else civ1
                    wars[wi] = (civ1, civ2, start_y, start_s, True)
                    events_this_season.append({
                        "type": "war_ended",
                        "winner_id": winner,
                        "winner_name": next(c.name for c in world.civilizations if c.id == winner),
                        "loser_id": loser,
                        "loser_name": next(c.name for c in world.civilizations if c.id == loser),
                        "year": year,
                        "season": season,
                    })

            # ── BEAST ACTIVITY ──
            for beast in world.beasts:
                if beast.active and rng.random() < 0.03:  # 3% per beast per season
                    target_site = rng.choice(world.sites) if world.sites else None
                    if target_site:
                        beast.kills += rng.randint(1, 5)
                        target_site.population = max(0, target_site.population - rng.randint(1, 3))
                        events_this_season.append({
                            "type": "beast_attack",
                            "beast_id": beast.id,
                            "beast_name": beast.name,
                            "beast_type": beast.type,
                            "alignment": beast.alignment,
                            "site_id": target_site.id,
                            "site_name": target_site.name,
                            "kills": beast.kills,
                            "year": year,
                            "season": season,
                        })
                        if rng.random() < 0.005:  # 0.5% chance to be slain per attack
                            beast.active = False
                            events_this_season.append({
                                "type": "beast_slain",
                                "beast_id": beast.id,
                                "beast_name": beast.name,
                                "year": year,
                                "season": season,
                            })

            # ── ARTIFACT CREATION ──
            if rng.random() < 0.005:  # 0.5% per season
                # Pick a creator from existing historical figures
                creator_id = None
                creator_civ_id = None
                if world.historical_figures:
                    creator = rng.choice(world.historical_figures)
                    creator_id = creator.id
                    creator_civ_id = creator.civ_id
                art_name2, art_type2 = generate_artifact_name()
                art = Artifact(
                    id=next_id(),
                    name=art_name2,
                    type=art_type2,
                    material=rng.choice(["gold", "silver", "iron", "steel", "mithril", "crystal", "bone"]),
                    alignment=rng.choice(ALIGNMENTS),
                    created_year=year,
                    created_season=season,
                    creator_id=creator_id,
                    civ_id=creator_civ_id,
                )
                world.artifacts.append(art)
                events_this_season.append({
                    "type": "artifact_created",
                    "artifact_id": art.id,
                    "artifact_name": art.name,
                    "artifact_type": art.type,
                    "material": art.material,
                    "alignment": art.alignment,
                    "year": year,
                    "season": season,
                })

            # ── ARTIFACT RELATIONSHIP EVENTS (wielded, stolen, broken, repaired, lost, recovered, claimed) ──
            if world.artifacts and rng.random() < 0.008:  # 0.8% per season — roughly 1 per 10 years per artifact
                target_artifact = rng.choice(world.artifacts)
                # Pick a figure involved (if any exist)
                target_figure = rng.choice(world.historical_figures) if world.historical_figures else None
                figure_id = target_figure.id if target_figure else None
                figure_name = target_figure.name if target_figure else "someone"

                ev_type = rng.choice(["artifact_wielded", "artifact_claimed", "artifact_given",
                                       "artifact_stolen", "artifact_broken", "artifact_repaired",
                                       "artifact_lost", "artifact_recovered"])
                events_this_season.append({
                    "type": ev_type,
                    "artifact_id": target_artifact.id,
                    "artifact_name": target_artifact.name,
                    "figure_id": figure_id,
                    "figure_name": figure_name,
                    "year": year,
                    "season": season,
                })

            # ── DIPLOMATIC EVENTS ──
            if rng.random() < 0.01 and len(world.civilizations) >= 2:  # 1% per season
                a, b = rng.sample(world.civilizations, 2)
                events_this_season.append({
                    "type": "diplomatic_meeting",
                    "civ1_id": a.id,
                    "civ1_name": a.name,
                    "civ2_id": b.id,
                    "civ2_name": b.name,
                    "year": year,
                    "season": season,
                })

            # ── SIEGES / RAIDS ──
            if rng.random() < 0.01:  # 1% per season
                target = rng.choice(world.sites) if world.sites else None
                if target and target.civ_id is not None:
                    events_this_season.append({
                        "type": "siege",
                        "site_id": target.id,
                        "site_name": target.name,
                        "civ_id": target.civ_id,
                        "year": year,
                        "season": season,
                    })

            if events_this_season:
                world.events.extend(events_this_season)
                world.event_year_index[year].extend(events_this_season)


# ──────────────────────────────────────────────────────────────────────
# 9.  RELATIONSHIP GENERATION
# ──────────────────────────────────────────────────────────────────────

RELATIONSHIP_TYPES = [
    "parent_child",
    "sibling",
    "spouse",
    "friend",
    "rival",
    "ally",
    "mentor",
    "student",
    "colleague",
    "enemy",
]


# ──────────────────────────────────────────────────────────────────────
# 9b.  ERA GENERATION
# ──────────────────────────────────────────────────────────────────────

def generate_eras(world: WorldHistory, rng: random.Random) -> List[Era]:
    """Generate era definitions from event history, spanning the full timeline."""
    eras: List[Era] = []
    events = world.events
    if not events:
        return eras
    
    # Sort events by year
    sorted_events = sorted(events, key=lambda e: e.get("year", 0))
    
    # Spread 8 eras evenly across the full timeline (not just first 248 years)
    start = world.config.start_year
    end = world.config.end_year
    total_span = end - start + 1
    num_eras = 8
    span = max(1, total_span // num_eras)
    
    era_id_counter = [len(world.historical_figures) + len(world.sites) + len(world.beasts) + len(world.artifacts) + 1]
    
    def next_eid():
        era_id_counter[0] += 1
        return era_id_counter[0]
    
    # Generate era names from a pool
    era_adjectives = [
        "Crimson", "Golden", "Silver", "Iron", "Shadow", "Dawn", "Dusk",
        "Fallen", "Rising", "Sundering", "Radiant", "Ember", "Frost",
        "Storm", "Ashen", "Verdant", "Sapphire", "Obsidian", "Marble",
        "Crystal", "Bronze", "Steel", "Bone", "Ash", "Ruby", "Pearl",
    ]
    era_nouns = [
        "Age", "Era", "Times", "Years", "Age of", "Epoch",
    ]
    
    rng.shuffle(era_adjectives)
    rng.shuffle(era_nouns)
    
    for i in range(num_eras):
        e_start = start + i * span
        e_end = min(start + (i + 1) * span - 1, end)
        if e_start > end:
            break
        
        # Extend the last era to fully cover the timeline (avoids orphan years at the end)
        if i == num_eras - 1:
            e_end = end
        
        adj = era_adjectives[i % len(era_adjectives)]
        noun = era_nouns[i % len(era_nouns)]
        # Fix "Age of of" double-of bug: check if noun ends with "of" not just contains "of"
        if noun.endswith("of") or noun == "Age of":
            era_name = f"{noun} {adj}"
        elif "of" in noun:
            era_name = f"{noun} of {adj}"
        else:
            era_name = f"The {adj} {noun}"
        
        # Find trigger event in this era's range
        trigger_events = [e for e in sorted_events if e_start <= e.get("year", 0) <= e_end]
        trigger_type = "unknown"
        trigger_summary = "the beginning of the era"
        if trigger_events:
            first = trigger_events[0]
            trigger_type = first.get("type", "event")
            # Build a summary from event fields
            if trigger_type == "war_declared":
                trigger_summary = f"War declared between {first.get('attacker_name', 'unknown')} and {first.get('defender_name', 'unknown')}"
            elif trigger_type == "battle":
                trigger_summary = f"Battle between {first.get('civ1_name', 'unknown')} and {first.get('civ2_name', 'unknown')}"
            elif trigger_type == "beast_attack":
                trigger_summary = f"{first.get('beast_name', 'a beast')} began its rampage"
            elif trigger_type == "site_founded":
                trigger_summary = f"The founding of {first.get('site_name', 'a settlement')}"
            elif trigger_type in ("artifact_created", "artifact_given", "artifact_wielded", "artifact_claimed"):
                trigger_summary = f"The {trigger_type.replace('artifact_', '')} of {first.get('artifact_name', 'an artifact')}"
            elif trigger_type == "beast_slain":
                trigger_summary = f"The slaying of {first.get('beast_name', 'a beast')}"
            elif trigger_type == "birth":
                trigger_summary = f"The birth of {first.get('figure_name', 'a figure')}"
            elif trigger_type == "death":
                trigger_summary = f"The death of {first.get('figure_name', 'a figure')}"
            else:
                trigger_summary = f"A {trigger_type.replace('_', ' ')} occurred"
        
        # Build description from event counts in this era — fix Python repr leak
        type_counts = Counter(e.get("type", "unknown") for e in trigger_events)
        if type_counts:
            desc_parts = []
            # Human-readable plural forms for event types
            plural_map = {
                "birth":           "births",
                "death":           "deaths",
                "battle":          "battles",
                "war_declared":    "wars declared",
                "war_ended":       "wars ended",
                "peace_treaty":    "peace treaties",
                "site_founded":    "site foundings",
                "site_abandoned":  "sites abandoned",
                "site_destroyed":  "sites destroyed",
                "beast_attack":    "beast attacks",
                "beast_slain":     "beasts slain",
                "artifact_created":"artifacts created",
                "artifact_wielded":"artifacts wielded",
                "artifact_claimed":"artifacts claimed",
                "artifact_given":  "artifacts given",
                "artifact_stolen": "artifacts stolen",
                "artifact_broken": "artifacts broken",
                "artifact_repaired":"artifacts repaired",
                "artifact_lost":   "artifacts lost",
                "artifact_recovered":"artifacts recovered",
                "title_promotion": "title promotions",
                "figure_relationship":"figure relationships",
                "trade":           "trade caravans",
                "disaster":        "disasters",
                "migration":       "migrations",
                "exploration":     "explorations",
                "diplomacy":       "diplomatic missions",
                "construction":    "constructions",
            }
            for t, c in type_counts.most_common(3):
                plural = plural_map.get(t, f"{t}s")
                desc_parts.append(f"{c} {plural}")
            description = f"The world turned through seasons of change — {' and '.join(desc_parts)} defined this age."
        else:
            description = "An era of relative peace."
        
        era = Era(
            id=next_eid(),
            name=era_name,
            start_year=e_start,
            end_year=e_end,
            description=description,
            trigger_event_type=trigger_type,
            trigger_summary=trigger_summary,
        )
        eras.append(era)
    
    return eras


def _overlap(a: HistoricalFigure, b: HistoricalFigure) -> bool:
    """Check if two figures were alive at the same time."""
    a_start = a.birth_year
    a_end = a.death_year if a.death_year is not None else 9999
    b_start = b.birth_year
    b_end = b.death_year if b.death_year is not None else 9999
    return a_start <= b_end and b_start <= a_end


def _shared_civ(a: HistoricalFigure, b: HistoricalFigure) -> bool:
    """Check if two figures share a civilization."""
    return a.civ_id is not None and a.civ_id == b.civ_id


def _year_diff(a: HistoricalFigure, b: HistoricalFigure) -> int:
    """Absolute difference in birth years."""
    return abs(a.birth_year - b.birth_year)


def generate_relationships(world: WorldHistory, rng: random.Random) -> None:
    """
    Generate relationships between historical figures based on shared civs,
    overlapping lifetimes, and birth-year proximity.
    """
    figures = world.historical_figures
    if len(figures) < 2:
        return

    # Build per-civ index for efficiency
    civ_figures: Dict[Optional[int], List[HistoricalFigure]] = {}
    for f in figures:
        cid = f.civ_id
        civ_figures.setdefault(cid, []).append(f)



    for cid, group in civ_figures.items():
        if len(group) < 2:
            continue

        # Sort by birth year so we can detect parent-child / sibling patterns
        sorted_group = sorted(group, key=lambda f: f.birth_year)

        for i, a in enumerate(sorted_group):
            if not a.deeds:
                continue  # skip undeveloped background figures

            # Only generate relationships for a subset of figures
            if rng.random() > 0.3:
                continue

            # Look at nearby figures in the sorted list (similar birth years)
            window = sorted_group[max(0, i - 10):min(len(sorted_group), i + 10)]
            for b in window:
                if a.id == b.id:
                    continue
                if not _overlap(a, b):
                    continue

                ydiff = _year_diff(a, b)

                # Determine relationship type based on context
                rel_type = None

                # Parent-child: ~20+ year gap, older figure is parent
                if ydiff >= 18 and rng.random() < 0.15:
                    if a.birth_year < b.birth_year:
                        rel_type = "parent_child"
                    else:
                        rel_type = "parent_child"
                    # Mark direction — older is parent
                    parent_id = a.id if a.birth_year < b.birth_year else b.id
                    child_id = b.id if a.birth_year < b.birth_year else a.id

                    a.relationships.append({
                        "type": "parent_child",
                        "figure_id": child_id if a.id == parent_id else parent_id,
                        "role": "parent" if a.id == parent_id else "child",
                        "strength": rng.choice(["strong", "moderate", "strained"]),
                    })
                    b.relationships.append({
                        "type": "parent_child",
                        "figure_id": parent_id if b.id == child_id else child_id,
                        "role": "child" if b.id == child_id else "parent",
                        "strength": rng.choice(["strong", "moderate", "strained"]),
                    })
                    continue

                # Sibling: similar birth years, same civ
                if ydiff <= 3 and rng.random() < 0.12:
                    rel_type = "sibling"
                    a.relationships.append({
                        "type": "sibling",
                        "figure_id": b.id,
                        "role": "sibling",
                        "strength": rng.choice(["close", "distant", "estranged"]),
                    })
                    b.relationships.append({
                        "type": "sibling",
                        "figure_id": a.id,
                        "role": "sibling",
                        "strength": rng.choice(["close", "distant", "estranged"]),
                    })
                    continue

                # Spouse: similar birth years, opposite alignment tendencies
                if ydiff <= 5 and rng.random() < 0.08:
                    rel_type = "spouse"
                    a.relationships.append({
                        "type": "spouse",
                        "figure_id": b.id,
                        "role": "spouse",
                        "strength": "married",
                    })
                    b.relationships.append({
                        "type": "spouse",
                        "figure_id": a.id,
                        "role": "spouse",
                        "strength": "married",
                    })
                    continue

                # Social relationships — friend, rival, ally, enemy
                if rng.random() < 0.2:
                    # Alignment similarity influences the type
                    alignment_same = a.alignment == b.alignment
                    if alignment_same:
                        rel_type = rng.choice(["friend", "ally", "colleague"])
                    else:
                        rel_type = rng.choice(["rival", "enemy", "colleague"])

                    a.relationships.append({
                        "type": rel_type,
                        "figure_id": b.id,
                        "role": rel_type,
                        "strength": "casual",
                    })
                    b.relationships.append({
                        "type": rel_type,
                        "figure_id": a.id,
                        "role": rel_type,
                        "strength": "casual",
                    })


def generate_beast_relationships(world: WorldHistory, rng: random.Random) -> None:
    """
    Generate basic relationships for beasts:
    - Rival beasts: beasts whose active periods overlap
    - City relationships: friendly/enemy based on alignment
    """
    beasts = world.beasts
    if len(beasts) < 1:
        return

    # Build a map of all site names for reference
    site_names = {s.id: s.name for s in world.sites}

    # ── Beast vs beast rivalries ──
    for i, a in enumerate(beasts):
        if not a.active:
            continue
        for b in beasts[i+1:]:
            if not b.active:
                continue
            # Check if their active periods overlap (both spawned and active)
            if abs(a.year_spawned - b.year_spawned) <= 50:
                # Determine rivalry intensity
                same_alignment = a.alignment == b.alignment
                if same_alignment:
                    rel_type = rng.choice(["rival", "territorial"])
                    strength = "moderate"
                else:
                    rel_type = "enemy"
                    strength = "fierce"

                a.relationships.append({
                    "type": rel_type,
                    "beast_id": b.id,
                    "beast_name": b.name,
                    "strength": strength,
                })
                b.relationships.append({
                    "type": rel_type,
                    "beast_id": a.id,
                    "beast_name": a.name,
                    "strength": strength,
                })

    # ── Beast vs city relationships ──
    for beast in beasts:
        if not beast.active:
            continue

        # Pick a few sites that were active during the beast's lifetime
        beast_year = beast.year_spawned
        relevant_sites = [s for s in world.sites
                         if s.founded_year is not None and s.founded_year <= beast_year + 50]

        if not relevant_sites:
            continue

        # Relationship based on alignment
        num_relationships = rng.randint(1, min(3, len(relevant_sites)))
        for site in rng.sample(relevant_sites, num_relationships):
            if beast.alignment in ("chaos", "evil"):
                rel_type = "enemy"
                strength = "hostile"
            elif beast.alignment == "neutral":
                rel_type = "neutral"
                strength = "cautious"
            else:
                rel_type = "ally"
                strength = "peaceful"

            beast.relationships.append({
                "type": rel_type,
                "site_id": site.id,
                "site_name": site.name,
                "strength": strength,
            })


def generate_artifact_relationships(world: WorldHistory, rng: random.Random) -> None:
    """
    Generate basic relationships for artifacts:
    - Creator (the figure who forged it)
    - Historical owners/wielders (from events)
    - Theft, breakage, recovery events
    """
    artifacts = world.artifacts
    if not artifacts:
        return

    # Build figure name lookup
    figure_names = {f.id: f.name for f in world.historical_figures}

    for art in artifacts:
        rels = []

        # ── Creator relationship ──
        cid = art.creator_id
        if cid is not None and cid in figure_names:
            rels.append({
                "type": "creator",
                "figure_id": cid,
                "figure_name": figure_names[cid],
                "strength": "forged",
            })

        # ── Historical wielders from events ──
        for ev in world.events:
            if ev.get("artifact_id") != art.id:
                continue
            ev_type = ev.get("type", "")
            figure_id = ev.get("figure_id")
            if figure_id is not None and figure_id in figure_names:
                fname = figure_names[figure_id]
                if ev_type in ("artifact_wielded", "artifact_claimed", "artifact_given"):
                    rels.append({
                        "type": "wielder",
                        "figure_id": figure_id,
                        "figure_name": fname,
                        "year": ev.get("year", 0),
                        "strength": "held",
                    })
                elif ev_type == "artifact_stolen":
                    rels.append({
                        "type": "thief",
                        "figure_id": figure_id,
                        "figure_name": fname,
                        "year": ev.get("year", 0),
                        "strength": "stolen",
                    })
                elif ev_type == "artifact_broken":
                    rels.append({
                        "type": "breaker",
                        "figure_id": figure_id,
                        "figure_name": fname,
                        "year": ev.get("year", 0),
                        "strength": "broken",
                    })
                elif ev_type == "artifact_repaired":
                    rels.append({
                        "type": "repairer",
                        "figure_id": figure_id,
                        "figure_name": fname,
                        "year": ev.get("year", 0),
                        "strength": "repaired",
                    })
                elif ev_type == "artifact_lost":
                    rels.append({
                        "type": "loser",
                        "figure_id": figure_id,
                        "figure_name": fname,
                        "year": ev.get("year", 0),
                        "strength": "lost",
                    })
                elif ev_type == "artifact_recovered":
                    rels.append({
                        "type": "recoverer",
                        "figure_id": figure_id,
                        "figure_name": fname,
                        "year": ev.get("year", 0),
                        "strength": "recovered",
                    })

        # Deduplicate by (type, figure_id) — keep most recent
        seen = set()
        unique_rels = []
        for rel in reversed(rels):
            key = (rel["type"], rel["figure_id"])
            if key not in seen:
                seen.add(key)
                unique_rels.append(rel)
        unique_rels.reverse()

        art.relationships = unique_rels


# ──────────────────────────────────────────────────────────────────────
# 10.  MEMORY & FACT GENERATION (inline — no separate scripts needed)
# ──────────────────────────────────────────────────────────────────────

# ── helpers ────────────────────────────────────────────────────────────

def pick_unique(pool: List[str], used: Set[str], rng: random.Random) -> str:
    """Pick from pool, excluding used items; if exhausted, fall back to fresh pick."""
    available = [t for t in pool if t not in used]
    if not available:
        return rng.choice(pool)
    choice = rng.choice(available)
    used.add(choice)
    return choice

def format_year_with_era(year: int, eras: List[Dict]) -> str:
    """Return a label like '150 AE' or '50 BE' using era names."""
    for era in reversed(eras):
        e_start = era.get("start_year", 0)
        e_end = era.get("end_year", 0)
        if e_start <= year <= e_end:
            return f"{year} ({era['name']})"
    return str(year)

def resolve_era_for_year(year: int, eras: List[Dict]) -> Optional[Dict]:
    for era in eras:
        if era.get("start_year", 0) <= year <= era.get("end_year", 0):
            return era
    return None



# ──────────────────────────────────────────────────────────────────────
# 10.  WORLD-LEVEL FACTS & MEMORIES
# ──────────────────────────────────────────────────────────────────────

# ── helpers (reused from above) ────────────────────────────────────

def pick_unique(pool: List[str], used: Set[str], rng: random.Random) -> str:
    """Pick from pool, excluding used items; if exhausted, fall back to fresh pick."""
    available = [t for t in pool if t not in used]
    if not available:
        return rng.choice(pool)
    choice = rng.choice(available)
    used.add(choice)
    return choice

def format_year_with_era(year: int, eras: List[Dict]) -> str:
    """Return a label like '150 AE' or '50 BE' using era names."""
    for era in reversed(eras):
        e_start = era.get("start_year", 0)
        e_end = era.get("end_year", 0)
        if e_start <= year <= e_end:
            return f"{year} ({era['name']})"
    return str(year)

def resolve_era_for_year(year: int, eras: List[Dict]) -> Optional[Dict]:
    for era in eras:
        if era.get("start_year", 0) <= year <= era.get("end_year", 0):
            return era
    return None


# ── World Facts ────────────────────────────────────────────────────

WORLD_FACT_TEMPLATES = {
    "civilization": [
        "The {name} civilization was founded in year {founded_year} by the {primary_race} race.",
        "The {name} civilization is aligned with {alignment} forces.",
        "The {name} civilization comprises {num_sites} settlements, including {capital_name}.",
        "The dominant race in the {name} civilization is {primary_race}.",
        "The {name} civilization has produced {num_artifacts} known artifacts.",
        "The {name} civilization has endured for {age} years since its founding.",
    ],
    "era": [
        "The era of {name} spanned from year {start_year} to {end_year}.",
        "The {name} era was triggered by {trigger_summary}.",
        "During the {name} era, {description}",
    ],
    "event": [
        "In year {year}, {summary}",
        "The event of {type} occurred in {year}: {summary}",
    ],
    "site": [
        "The {site_type} of {name} was founded in year {founded_year}.",
        "{name} is a {site_type} with population {population}.",
        "The settlement of {name} lies in a {biome_name} biome.",
    ],
    "figure": [
        "{name} the {title} was a {race} {profession} who lived from {birth_year} to {death_year}.",
        "{name} was known for their {alignment} alignment and served {civ_name}.",
    ],
    "beast": [
        "The {type} {name} spawned in year {year_spawned} and has {kills} kills.",
        "{name} is a {type} of {alignment} alignment.",
    ],
    "artifact": [
        "The {type} {name} was created in year {created_year} from {material}.",
        "{name} is a {type} artifact of {alignment} alignment.",
    ],
}

def generate_world_facts(world: WorldHistory, rng: random.Random) -> Dict[str, str]:
    """
    Generate high-level lore facts about the world from simulation data.
    Returns a dict mapping fact keys to values.
    """
    facts = {}
    world_dict = to_serializable(world)
    civs = world_dict.get("civilizations", [])
    eras = world_dict.get("eras", [])
    events = world_dict.get("events", [])
    sites = world_dict.get("sites", [])
    figures = world_dict.get("historical_figures", [])
    beasts = world_dict.get("beasts", [])
    artifacts = world_dict.get("artifacts", [])
    config = world_dict.get("config", {})

    end_year = config.get("end_year", 250)

    # ── World summary facts ──
    facts["world_name"] = config.get("world_name", "Unknown")
    facts["world_year_span"] = f"{config.get('start_year', 0)} to {end_year}"
    facts["num_civilizations"] = str(len(civs))
    facts["num_sites"] = str(len(sites))
    facts["num_figures"] = str(len(figures))
    facts["num_artifacts"] = str(len(artifacts))
    facts["num_beasts"] = str(len(beasts))
    facts["num_eras"] = str(len(eras))
    facts["num_events"] = str(len(events))

    # ── Civilization facts ──
    for c in civs:
        prefix = c.get("name", "civ").lower().replace(" ", "_")
        facts[f"civ_{prefix}_name"] = c.get("name", "")
        facts[f"civ_{prefix}_race"] = c.get("primary_race", "mixed")
        facts[f"civ_{prefix}_alignment"] = c.get("alignment", "neutral")
        facts[f"civ_{prefix}_founded"] = str(c.get("founded_year", 0))
        # Count sites for this civ
        civ_sites = [s for s in sites if s.get("civ_id") == c.get("id")]
        facts[f"civ_{prefix}_sites"] = str(len(civ_sites))
        # Find capital
        capitals = [s for s in civ_sites if s.get("is_capital")]
        facts[f"civ_{prefix}_capital"] = capitals[0].get("name", "") if capitals else ""
        # Count artifacts for this civ
        civ_arts = [a for a in artifacts if a.get("civ_id") == c.get("id")]
        facts[f"civ_{prefix}_artifacts"] = str(len(civ_arts))
        # Count figures
        civ_figs = [f for f in figures if f.get("civ_id") == c.get("id")]
        facts[f"civ_{prefix}_figures"] = str(len(civ_figs))

    # ── Era facts ──
    for era in eras:
        prefix = era.get("name", "era").lower().replace(" ", "_")
        facts[f"era_{prefix}_name"] = era.get("name", "")
        facts[f"era_{prefix}_span"] = f"{era.get('start_year', 0)} to {era.get('end_year', 'present')}"
        facts[f"era_{prefix}_trigger"] = era.get("trigger_summary", "unknown")
        facts[f"era_{prefix}_description"] = era.get("description", "")

    # ── Major event facts ──
    # Sample up to 5 notable events spread across all eras (not just the first half)
    major_candidates = [e for e in events if e.get("type") in ("war", "battle", "founding", "siege", "conquest",
                                                               "war_declared", "war_ended", "beast_attack",
                                                               "beast_slain", "artifact_created")]
    major_events = []
    if eras:
        # Pick one event from each era, collecting up to 5 across the full timeline
        for era in eras:
            if len(major_events) >= 5:
                break
            es = era["start_year"]
            ee = era["end_year"]
            candidates_in_era = [e for e in major_candidates if es <= e.get("year", 0) <= ee]
            if candidates_in_era:
                major_events.append(rng.choice(candidates_in_era))
    # Fill remaining slots with any notable events if we didn't get 5
    while len(major_events) < 5:
        remaining = [e for e in major_candidates if e not in major_events]
        if not remaining:
            break
        major_events.append(rng.choice(remaining))
    for i, ev in enumerate(major_events[:5]):
        summary = summarize_event(ev) if ev.get("type") in ("battle", "war_declared", "war_ended", "beast_attack", "beast_slain", "artifact_created") else ev.get("summary", "something happened")
        facts[f"major_event_{i+1}"] = f"In year {ev.get('year', 0)}, {summary}."

    # ── Notable site facts ──
    for s in sites[:10]:  # first 10 sites
        prefix = s.get("name", "site").lower().replace(" ", "_")
        facts[f"site_{prefix}_type"] = s.get("site_type", "unknown")
        facts[f"site_{prefix}_population"] = str(s.get("population", 0))
        facts[f"site_{prefix}_founded"] = str(s.get("founded_year", 0))

    # ── Notable figure facts ──
    for f in figures[:10]:
        prefix = f.get("name", "figure").lower().replace(" ", "_")
        facts[f"figure_{prefix}_race"] = f.get("race", "unknown")
        facts[f"figure_{prefix}_title"] = f.get("title", "commoner")
        facts[f"figure_{prefix}_alignment"] = f.get("alignment", "neutral")
        death_year = f.get('death_year')
        if death_year is None:
            lifespan_str = f"{f.get('birth_year', 0)}-present (still alive)"
        else:
            lifespan_str = f"{f.get('birth_year', 0)}-{death_year}"
        facts[f"figure_{prefix}_lifespan"] = lifespan_str

    # ── Artifact facts ──
    for a in artifacts:
        prefix = a.get("name", "artifact").lower().replace(" ", "_")
        facts[f"artifact_{prefix}_type"] = a.get("type", "unknown")
        facts[f"artifact_{prefix}_material"] = a.get("material", "unknown")
        facts[f"artifact_{prefix}_created"] = str(a.get("created_year", 0))

    # ── Beast facts ──
    for b in beasts:
        prefix = b.get("name", "beast").lower().replace(" ", "_")
        facts[f"beast_{prefix}_type"] = b.get("type", "unknown")
        facts[f"beast_{prefix}_kills"] = str(b.get("kills", 0))

    return facts


# ── World Memories ─────────────────────────────────────────────────

def summarize_event(e: Dict) -> str:
    """Build a readable summary from an event dict."""
    etype = e.get("type", "event")
    year = e.get("year", 0)
    
    if etype == "battle":
        c1 = e.get("civ1_name", "unknown")
        c2 = e.get("civ2_name", "unknown")
        return f"Battle between {c1} and {c2}"
    elif etype == "war_declared":
        att = e.get("attacker_name", "unknown")
        defend = e.get("defender_name", "unknown")
        return f"War declared: {att} attacked {defend}"
    elif etype == "war_ended":
        win = e.get("winner_name", "unknown")
        lose = e.get("loser_name", "unknown")
        return f"War ended: {win} defeated {lose}"
    elif etype == "beast_attack":
        bname = e.get("beast_name", "a beast")
        btype = e.get("beast_type", "creature")
        site = e.get("site_name", "a settlement")
        kills = e.get("kills", 0)
        return f"The {btype} {bname} attacked {site}, killing {kills}"
    elif etype == "birth":
        name = e.get("figure_name", "someone")
        race = e.get("race", "unknown")
        return f"{name} was born, a {race}"
    elif etype == "death":
        name = e.get("figure_name", "someone")
        cause = e.get("cause", "unknown causes")
        return f"{name} died of {cause}"
    elif etype == "title_promotion":
        name = e.get("figure_name", "someone")
        new_title = e.get("new_title", "a title")
        return f"{name} became {new_title}"
    else:
        # Generic fallback: use available fields
        parts = []
        for key in ("figure_name", "site_name", "civ1_name", "attacker_name", "winner_name"):
            if key in e:
                parts.append(e[key])
                break
        if etype in ("war_declared", "war_ended", "battle"):
            parts.append(f"({etype})")
        else:
            parts.append(etype)
        return " ".join(parts)


def generate_world_memories(world: WorldHistory, rng: random.Random) -> Dict:
    """
    Generate world-level memories as a chronicle of major events.
    Returns dict with long_term, short_term, near_term keys.
    """
    world_dict = to_serializable(world)
    events = world_dict.get("events", [])
    eras = world_dict.get("eras", [])
    civs = world_dict.get("civilizations", [])
    sites = world_dict.get("sites", [])
    figures = world_dict.get("historical_figures", [])
    config = world_dict.get("config", {})

    end_year = config.get("end_year", 250)
    start_year = config.get("start_year", 0)

    long_term = []
    short_term = []
    near_term = []

    # ── Long-term: major historical events sampled across timeline ──
    major_types = {"battle", "war_declared", "war_ended", "beast_attack"}
    major_events = [e for e in events if e.get("type") in major_types]
    # Sort by year
    major_events.sort(key=lambda e: e.get("year", 0))
    # Deduplicate: group events by their summary key, keeping only the first occurrence
    seen_summaries = set()
    unique_major = []
    for e in major_events:
        summary = summarize_event(e)
        if summary not in seen_summaries:
            seen_summaries.add(summary)
            unique_major.append(e)
    # Sample 30 events evenly across the deduplicated timeline
    sample_size = min(30, len(unique_major))
    if sample_size > 0:
        step = max(1, len(unique_major) // sample_size)
        sampled_indices = list(range(0, len(unique_major), step))[:sample_size]
        sampled_events = [unique_major[i] for i in sampled_indices]
    else:
        sampled_events = []
    for e in sampled_events:
        yr = e.get("year", 0)
        long_term.append({
            "type": "world_event",
            "year": yr,
            "year_label": format_year_with_era(yr, eras),
            "era": resolve_era_for_year(yr, eras),
            "memory": summarize_event(e),
            "event_type": e.get("type", "event"),
        })

    # Pad with civilization and era descriptions if few major events
    if len(long_term) < 10:
        for c in civs[:5]:
            yr = c.get("founded_year", 0)
            long_term.append({
                "type": "civilization_founding",
                "year": yr,
                "year_label": format_year_with_era(yr, eras),
                "era": resolve_era_for_year(yr, eras),
                "memory": f"The {c.get('name', 'unknown')} civilization was founded by the {c.get('primary_race', 'unknown')} race.",
                "civ_name": c.get("name", ""),
            })
        for era in eras:
            yr = era.get("start_year", 0)
            long_term.append({
                "type": "era_begin",
                "year": yr,
                "year_label": format_year_with_era(yr, eras),
                "era": resolve_era_for_year(yr, eras),
                "memory": f"The era of {era.get('name', 'unknown')} began, triggered by {era.get('trigger_summary', 'unknown')}.",
                "era_name": era.get("name", ""),
            })

    # ── Short-term: recent events (last 15 years) ──
    recent_events = [e for e in events if e.get("year", 0) >= end_year - 15]
    recent_events.sort(key=lambda e: e.get("year", 0))
    for e in recent_events[:15]:
        yr = e.get("year", 0)
        short_term.append({
            "type": "recent_event",
            "year": yr,
            "year_label": format_year_with_era(yr, eras),
            "era": resolve_era_for_year(yr, eras),
            "memory": summarize_event(e),
            "event_type": e.get("type", "event"),
        })

    # Pad with seasonal observations if few recent events
    if len(short_term) < 8:
        season_names = ["spring", "summer", "autumn", "winter"]
        for i in range(5):
            yr = end_year - rng.randint(0, 14)
            season = rng.choice(season_names)
            short_term.append({
                "type": "seasonal_observation",
                "year": yr,
                "year_label": format_year_with_era(yr, eras),
                "era": resolve_era_for_year(yr, eras),
                "memory": f"The {season} season brought {rng.choice(['heavy rains', 'mild weather', 'harsh storms', 'gentle breezes', 'deep snow'])} to the land.",
            })

    # ── Near-term: current affairs, concerns, plans ──
    # Current tensions from recent war events
    recent_wars = [e for e in events if e.get("type") in ("war_declared", "war_ended") and e.get("year", 0) >= end_year - 10]
    for w in recent_wars[:4]:
        yr = w.get("year", 0)
        near_term.append({
            "type": "ongoing_conflict",
            "year": yr,
            "year_label": format_year_with_era(yr, eras),
            "era": resolve_era_for_year(yr, eras),
            "memory": summarize_event(w),
        })

    # General near-term concerns
    concern_templates = [
        "The {civ_name} civilization concerns about {topic}",
        "There are rumors of {topic} in the land",
        "The people whisper about {topic}",
        "A {topic} is said to be stirring",
    ]
    civ_names = [c.get("name", "the realm") for c in civs[:3]]
    topics = ["beast sightings", "ancient ruins", "forgotten magic", "border disputes",
              "trade disputes", "dragon activity", "goblin raids", "elven prophecies",
              "dwarven secrets", "orc uprisings"]
    for i in range(6):
        civ_name = rng.choice(civ_names) if civ_names else "the realm"
        topic = rng.choice(topics)
        template = rng.choice(concern_templates)
        near_term.append({
            "type": "concern",
            "year": end_year,
            "year_label": format_year_with_era(end_year, eras),
            "era": resolve_era_for_year(end_year, eras),
            "memory": template.format(civ_name=civ_name, topic=topic),
        })

    return {
        "long_term": {"count": len(long_term), "items": long_term},
        "short_term": {"count": len(short_term), "items": short_term},
        "near_term": {"count": len(near_term), "items": near_term},
    }


# ── Integration ────────────────────────────────────────────────────

def generate_memories_and_facts(world: WorldHistory, rng: random.Random) -> None:
    """
    Generate world-level facts and memories from simulation data.
    Attaches results to the WorldHistory dataclass.
    """
    facts = generate_world_facts(world, rng)
    memories = generate_world_memories(world, rng)

    world.facts = facts
    world.memories = memories

# ──────────────────────────────────────────────────────────────────────
# 11.  OUTPUT — JSON for SerenMemory / SerenLoci
# ──────────────────────────────────────────────────────────────────────

def to_serializable(obj):
    if isinstance(obj, list):
        return [to_serializable(item) for item in obj]
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for f_name in obj.__dataclass_fields__:
            val = getattr(obj, f_name)
            if isinstance(val, Enum):
                result[f_name] = val.value
            elif hasattr(val, "__dataclass_fields__"):
                result[f_name] = to_serializable(val)
            elif isinstance(val, list):
                result[f_name] = [to_serializable(item) for item in val]
            elif isinstance(val, dict):
                result[f_name] = {k: to_serializable(v) for k, v in val.items()}
            else:
                result[f_name] = val
        return result
    return obj


def export_json(world: WorldHistory, filepath: str) -> None:
    data = {
        "world_name": world.config.world_name,
        "seed": world.config.seed,
        "config": to_serializable(world.config),
        "biomes": [b.__dict__ for b in BIOMES],
        "terrain": {
            "biome_grid": world.biomes,
            "elevation_grid": world.elevation,
        },
        "civilizations": to_serializable(world.civilizations),
        "historical_figures": to_serializable(world.historical_figures),
        "sites": to_serializable(world.sites),
        "beasts": to_serializable(world.beasts),
        "artifacts": to_serializable(world.artifacts),
        "eras": to_serializable(world.eras),
        "events": world.events,
        "events_by_year": dict(world.event_year_index),
        "facts": world.facts,
        "memories": world.memories,
        "summary": {
            "num_civilizations": len(world.civilizations),
            "num_historical_figures": len(world.historical_figures),
            "num_sites": len(world.sites),
            "num_beasts": len(world.beasts),
            "num_artifacts": len(world.artifacts),
            "num_eras": len(world.eras),
            "num_events": len(world.events),
            "year_span": f"{world.config.start_year} to {world.config.end_year}",
        },
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Exported world to {filepath}")


# ──────────────────────────────────────────────────────────────────────
# 10.  MAIN
# ──────────────────────────────────────────────────────────────────────

def generate_world(
    seed: Optional[int] = None,
    world_name: str = "Aerdor",
    start_year: int = -200,
    end_year: int = 50,
    output_path: Optional[str] = None,
) -> WorldHistory:
    if seed is None:
        seed = random.randint(0, 2**31)
    rng = random.Random(seed)

    config = WorldConfig(
        world_name=world_name,
        seed=seed,
        start_year=start_year,
        end_year=end_year,
    )
    world = WorldHistory(config=config)

    print(f"Generating world '{world_name}' (seed {seed})...")

    print("  Terrain...")
    generate_terrain(world, rng)

    print("  Civilizations, figures, sites...")
    generate_initial_sites(world, rng)

    print(f"  History ({start_year} to {end_year}, {len(SEASON_NAMES)} seasons/year)...")
    simulate_history(world, rng)

    print("  Relationships...")
    generate_relationships(world, rng)
    generate_beast_relationships(world, rng)
    generate_artifact_relationships(world, rng)

    print("  Eras...")
    world.eras = generate_eras(world, rng)

    print("  Memories & Facts...")
    generate_memories_and_facts(world, rng)

    print(f"\n{'='*50}")
    print(f"World: {world_name} (seed {seed})")
    print(f"  Civilizations: {len(world.civilizations)}")
    print(f"  Sites: {len(world.sites)}")
    print(f"  Historical Figures: {len(world.historical_figures)}")
    print(f"  Beasts: {len(world.beasts)}")
    print(f"  Artifacts: {len(world.artifacts)}")
    print(f"  Events: {len(world.events)}")
    print(f"  Eras: {len(world.eras)}")
    print(f"{'='*50}\n")

    if output_path:
        export_json(world, output_path)

    return world


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate a DF-style fantasy world history")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--name", type=str, default="Aerdor", help="World name")
    parser.add_argument("--start-year", type=int, default=0, help="Start year")
    parser.add_argument("--end-year", type=int, default=250, help="End year")
    parser.add_argument("--output", type=str, default="world.json", help="Output JSON path")
    args = parser.parse_args()

    generate_world(
        seed=args.seed,
        world_name=args.name,
        start_year=args.start_year,
        end_year=args.end_year,
        output_path=args.output,
    )
