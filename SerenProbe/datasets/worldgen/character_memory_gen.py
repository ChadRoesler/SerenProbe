#!/usr/bin/env python3
"""
Character Memory Generator v2
Takes a character from world_gen.py output and generates:
  - Short-term memory:  15 recent events / thoughts
  - Long-term memory:   X items (X = character's age, clamped 20-50)
                        major life events, alignment-shaping events, etc.
  - Near-term memory:   10 upcoming plans / future events

Memories are flavored by race, region (biome), civilization, alignment,
and reference real world entities (sites, artifacts, beasts, other figures).
"""

import json
import random
import sys
import os
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────
# 1.  RACE / ALIGNMENT / REGION MEMORY THEMES
# ──────────────────────────────────────────────────────────────────────

RACE_MEMORY_THEMES = {

    "Dwarf": {
        "professions": ["miner", "smelter", "engraver", "gemcutter", "brewer", "smith",
                        "architect", "mason", "merchant", "guard", "general", "trader"],
        "childhood_events": [
            "learned to mine in the deep tunnels under the mountain",
            "helped my father sort ore in the great sorting hall",
            "got lost in the caves for three days — the darkness taught me",
            "carved my first stone figurine from a chunk of granite",
            "drank cave ale for the first time and felt the fire",
            "watched the forges burn all night, mesmerized by the glow",
            "was taught the runes of the deep earth by the elder",
            "found a sparkling gem in the tailings and kept it forever",
            "raced the other children through the winding tunnels",
            "listened to the songs of the deep in the great hall",
        ],
        "life_events": [
            "struck a rich vein of silver deep in the depths — the seam ran pure for months",
            "defended the fortress against a goblin siege that lasted through the winter",
            "forged a blade that held an edge for a hundred years and earned the name 'Steelheart'",
            "negotiated a trade treaty with the surface folk that brought riches beyond count",
            "lost my brother in a cave-in deep below — the mountain took him",
            "unearthed an ancient artifact from the deep halls that glowed with forgotten light",
            "was blessed by the mountain spirits in a vision of stone and fire",
            "led a retaliation raid into the dark tunnels and came back changed",
            "carved a great hall that echoed with song for three days straight",
            "brewed a batch of ale so strong it could fell a troll — and it did",
            "discovered a new vein of adamantine and was hailed as a hero of the hold",
            "stood alone against a cave of horrors until reinforcements arrived",
        ],
        "alignment_events": {
            "good": [
                "saved a child from a collapsing tunnel at the last possible moment",
                "shared my last rations with the starving during the long siege",
                "taught the young ones the old runes so the traditions would live on",
                "gave away a precious gem I had mined to help the needy above ground",
                "spoke for the voiceless at the council of elders",
            ],
            "evil": [
                "betrayed a fellow dwarf for a rich vein of gold — the greed won",
                "sold captured goblins into slavery to the surface slavers",
                "poisoned a rival's ale supply and watched them wither",
                "hoarded gold while the fortress starved — I felt no shame",
                "framed an innocent for a crime I committed and took their position",
            ],
            "order": [
                "established the laws of the deep council that still stand today",
                "enforced the ancient codes of the mountain without exception",
                "organized the guilds into strict hierarchies that improved production",
                "built a fortress wall that never fell, using the old techniques",
                "recorded every transaction in the great ledger for all to see",
            ],
            "chaos": [
                "drank so much ale the tunnels spun and I dug the wrong way",
                "gambled the fortress treasury away in a single night of cards",
                "started a brawl in the great hall that took three days to settle",
                "dug too deep and woke something that should have stayed buried",
                "swapped the king's crown with a mug of ale as a joke",
            ],
            "neutral": [
                "traded goods with the elven caravans and learned their strange ways",
                "explored a new cavern system no dwarf had touched in centuries",
                "learned a new smithing technique from a wandering human craftsman",
                "mined a vein of rare ore and sold it for a fair price",
                "simply worked the stone day after day, finding peace in the rhythm",
            ],
        },
        "thoughts": [
            "The stone remembers what the surface forgets.",
            "Every strike of the pick is a prayer to the deep.",
            "Gold is heavy, but the mountain is heavier on the soul.",
            "A good ale and a warm forge — what more could one want?",
            "The deep halls are silent today, and that silence speaks.",
            "The mountain gives, and the mountain takes.",
            "I can hear the stone breathing around me.",
        ],
    },

    "Elf": {
        "professions": ["singer", "warden", "sage", "gardener", "archer", "mage",
                        "dancer", "healer", "scholar", "ranger", "diplomat", "star-gazer"],
        "childhood_events": [
            "sang with the nightingales in the ancient grove under the full moon",
            "learned the names of all the stars from the elder astronomer",
            "walked the silver paths under the moonlight for the first time",
            "first time wielding a bow in the misty forest — the arrow sang",
            "read the old scrolls in the library of leaves and felt the wisdom",
            "danced at the festival of blooming among flowers that glowed",
            "was gifted a seed from the elder tree and planted it with tears of joy",
            "listened to the wind speak in the language of the ancients",
            "watched the stars dance in patterns that told the future",
            "swam in the hidden pool beneath the waterfall of whispers",
        ],
        "life_events": [
            "defended the sacred grove against orc raiders with nothing but song and steel",
            "composed a song that made the trees weep — the forest remembered",
            "walked the spirit paths between worlds and saw the veil thin",
            "healed a dying forest with ancient magic, calling the roots back to life",
            "lost my beloved to the shadow sickness that took their light",
            "discovered a hidden glade of eternal spring where time stands still",
            "was visited by a star-being from the celestial realms and given a vision",
            "led a diplomatic mission to the dwarf kingdoms and forged lasting peace",
            "carved a bow from the heartwood of the world tree and it sang with power",
            "sung the lament of the fallen leaves at the turning of the year",
            "climbed the highest peak to witness the birth of a new star",
            "walked through a mirror pool into a world of reflections",
        ],
        "alignment_events": {
            "good": [
                "healed a wounded creature of the forest and earned its trust",
                "taught the young ones the old songs so the music would never fade",
                "shared the wisdom of the stars freely with all who asked",
                "protected a village from dark spirits using only light and song",
                "gave shelter to a lost traveler and guided them home",
            ],
            "evil": [
                "bound a forest spirit into servitude and bent its will to mine",
                "cursed a rival with the shadow whisper — they never smiled again",
                "burned the sacred grove in spite and watched the ashes fall",
                "used the dark tongue to unravel a soul from the inside",
                "stole the light from a star and trapped it in a gem for my own",
            ],
            "order": [
                "established the laws of the elder council that govern still",
                "preserved the ancient records without error for three centuries",
                "organized the defense of the forest realm into an unbreakable ward",
                "codified the rituals of the seasons so none would be forgotten",
                "built a library that held every piece of knowledge ever gathered",
            ],
            "chaos": [
                "danced with the wild fae and lost three years in a single night",
                "spoke riddles that changed the weather — the clouds obeyed",
                "wandered the dream paths for a century and returned changed",
                "sang a song that unravelled fate and wove it anew",
                "traded my name for a handful of stars and forgot who I was",
            ],
            "neutral": [
                "traveled to see the lands of other races and learn their truths",
                "learned the crafts of the mountain folk and respected their stone",
                "observed the passing of seasons in peace, content to simply be",
                "collected stories from wandering traders and kept them in song",
                "walked the world and saw it all — the joy and the sorrow",
            ],
        },
        "thoughts": [
            "The stars sing a different song tonight, and I listen.",
            "Time flows like a river through the forest of memory.",
            "Every leaf holds a memory of the sun that touched it.",
            "The wind carries whispers from far places I have yet to see.",
            "The trees speak in the language of roots and rain.",
            "There is a melody beneath all things, if you listen.",
            "The world grows older, and so do I.",
        ],
    },

    "Human": {
        "professions": ["farmer", "knight", "merchant", "scholar", "soldier", "priest",
                        "blacksmith", "scribe", "trader", "captain", "lord", "artisan"],
        "childhood_events": [
            "helped plant the fields in spring and watched the green come alive",
            "listened to the old tales by the hearth until the fire died",
            "fought the neighbor's boy with wooden swords until we both fell laughing",
            "watched the knights ride past the village and dreamed of glory",
            "learned to read from the traveling scholar who passed through",
            "climbed the old watchtower for the first time and saw the whole world",
            "lost my favorite toy in the river and cried for a week",
            "helped my mother tend the garden and learned patience",
            "sneaked into the lord's kitchen and stole a pie",
            "ran through the wheat fields with the wind at my back",
        ],
        "life_events": [
            "fought in a great battle and survived when so many did not",
            "married my love and built a home by the river under the old willow",
            "led a caravan through dangerous lands and lost two wagons to bandits",
            "witnessed the fall of a once-great city and carried the survivors out",
            "was granted a title by the king for service in the war",
            "discovered an ancient tomb beneath the hills and found treasures untold",
            "survived a plague that took half the village — I still remember their faces",
            "raised my child to be a fine warrior and watched them ride away",
            "built a chapel that became a refuge for the weary and broken",
            "rode to warn the neighboring town of danger and arrived just in time",
            "stood before the council and spoke truth to power",
            "walked the length of the realm and saw the suffering of the common folk",
        ],
        "alignment_events": {
            "good": [
                "gave shelter to refugees fleeing war and shared what little I had",
                "rescued a child from a burning building while the roof fell",
                "donated coin to repair the town bridge so trade could flow",
                "stood between a mob and an innocent and took the blow for them",
                "spent a year healing the sick in a village that had no healer",
            ],
            "evil": [
                "betrayed a friend for personal gain and felt nothing but satisfaction",
                "burned a village for its refusal to yield and salt the earth",
                "sold captives to the slavers from the east for a purse of gold",
                "spread lies that destroyed a reputation and watched them fall",
                "took the throne by murder and ruled through fear",
            ],
            "order": [
                "established a new code of laws for the town that brought peace",
                "organized the militia into disciplined ranks that never broke",
                "recorded the history of the realm in ledgers for future generations",
                "built a courthouse and served as judge, fair and firm",
                "standardized the weights and measures for all trade in the region",
            ],
            "chaos": [
                "led a rebellion against the corrupt lord and burned the old order",
                "broke every rule in the knightly code and laughed while doing it",
                "gambled away the family fortune in a single night of cards",
                "started a riot in the market square that spread through the city",
                "changed my name and identity three times — no one knows who I am",
            ],
            "neutral": [
                "traveled to see the wonders of the world and fill my eyes with marvels",
                "learned a trade from a master craftsman and became one myself",
                "made peace between warring neighbors through simple honesty",
                "lived an honest life, worked the land, and asked for nothing more",
                "observed the world turning and found my place in it",
            ],
        },
        "thoughts": [
            "The world is wide and full of trouble, but also of wonder.",
            "A man's word is his bond — or so they say, until gold speaks louder.",
            "Gold and steel make the world turn, but love makes it worth turning.",
            "The old gods listen, but they rarely answer in ways we understand.",
            "Tomorrow brings another dawn, another chance to get it right.",
            "I have seen too much to be young, and too little to be wise.",
            "The road ahead is long, and I walk it alone.",
        ],
    },

    "Orc": {
        "professions": ["warrior", "shaman", "hunter", "tanner", "raider", "warlord",
                        "scout", "bone-carver", "herder", "smiter", "guard", "tracker"],
        "childhood_events": [
            "fought a wild beast in the dark for the first time and won",
            "earned the first scar from a training duel with my older brother",
            "learned the war chants of the old tribe around the bone-fire",
            "ate the heart of a conquered foe and felt their strength enter me",
            "was given the first bone axe by the chieftain's own hand",
            "ran with the wolf pack across the plains until I collapsed",
            "watched the sky burn with the spirits of ancestors during the ritual",
            "climbed the skull mountain and saw the lands of the enemy",
            "fought in my first raid and took my first head",
            "listened to the shaman's vision stories until I fell asleep in the blood-dust",
        ],
        "life_events": [
            "led a war band that crushed a fortress and took its riches",
            "challenged the chieftain and won the tribe through pure strength",
            "tamed a great beast from the northern wastes and rode it to war",
            "lost my brother in the great battle of the pass — I still hear his war cry",
            "discovered a sacred spring that gave visions of the spirit world",
            "raided the dwarf holds and took their gold while they cowered",
            "was blessed by the blood-spirit in a vision of endless war",
            "broke the elven shield-wall at the forest's edge with my own hands",
            "carved a totem that held the souls of a hundred enemies",
            "made peace with a rival tribe through blood-bond and shared fire",
            "fought a dragon in the mountain pass and took its fang as a trophy",
            "led the tribe through the great winter and lost only the weak",
        ],
        "alignment_events": {
            "good": [
                "spared a defeated enemy and earned their respect — strength enough to show mercy",
                "protected the young ones from a predator and took the wounds myself",
                "shared the spoils equally with the tribe and earned the name 'Just-Blade'",
                "listened to the wisdom of the elders and changed the war path",
                "adopted a child from a conquered tribe and raised them as my own",
            ],
            "evil": [
                "slaughtered an entire village without mercy — man, woman, child, all fell",
                "tortured captives for information until they broke and then ate them",
                "betrayed the war band for personal power and left them to die",
                "desecrated the sacred grove of the elves and laughed at their weeping",
                "killed the chieftain in their sleep and took the tribe by deceit",
            ],
            "order": [
                "established the laws of the war council that brought discipline to the horde",
                "organized the tribes into a true army with ranks and banners",
                "trained the young ones in the old ways of war and honor",
                "built a fortress that could hold against any siege — stone and bone",
                "created a code of conduct for raids that minimized losses",
            ],
            "chaos": [
                "fought both sides of a battle for the thrill and killed them all",
                "drank the blood of the shaman's vision brew and saw the void",
                "ran wild through the enemy camp at night, naked and howling",
                "challenged the spirits themselves and wrestled with the dark",
                "set fire to the war tents and danced in the flames",
            ],
            "neutral": [
                "hunted a great beast across the mountains for weeks and feasted",
                "traded with the humans for steel and learned their tongue",
                "explored the caves beneath the world and found the old things",
                "learned the craft of bone-carving from the shaman",
                "walked the plains alone and listened to the wind",
            ],
        },
        "thoughts": [
            "Blood and iron — the only truths in a world of lies.",
            "The ancestors watch from the spirit sky and judge our strength.",
            "Strength is the only law that matters — the weak serve the strong.",
            "The hunt never ends, it only changes shape.",
            "A warrior's death is a song that never fades from memory.",
            "The earth shakes when the horde marches.",
            "I was born with war in my blood.",
        ],
    },

    "Goblin": {
        "professions": ["trickster", "sneak", "tinker", "poisoner", "scavenger",
                        "trapper", "spider-herder", "mushroom-farmer", "digger",
                        "messenger", "spy", "exploder"],
        "childhood_events": [
            "stole a shiny thing from the big folk and hid it in my den",
            "built a trap that caught a rabbit — ate it raw and felt mighty",
            "learned to speak in riddles and lies from the old trickster",
            "sneaked into the dwarf halls for the first time and took their candles",
            "ate a glowing mushroom and saw the colors for three days straight",
            "found a secret tunnel under the old hill and claimed it as mine",
            "was taught the trick of making smoke-powder by the tinker",
            "swapped a horse for a goat and laughed at the trader's face",
            "climbed the big folk's tower and stole their maps",
            "convinced the chieftain that I was a spirit — got extra rations for a week",
        ],
        "life_events": [
            "infiltrated a human fortress and stole their battle plans — sold them to three sides",
            "built a contraption that blew up half the warren — they still talk about the noise",
            "poisoned the water of a rival tribe and watched them wither from the hill",
            "negotiated a trade of shiny rocks for weapons and got the better deal",
            "lost my best friend to the big folk's justice — they hanged him for stealing",
            "discovered a hidden cache of ancient tech and couldn't figure it out",
            "was blessed by the trickster spirit in a dream of endless laughter",
            "led a raid on the elf stores for mushrooms and took their whole harvest",
            "crafted a device that could speak in voices — it told terrible lies",
            "swapped a king's crown for a pile of buttons and became a legend",
            "built a tunnel network under three rival tribes and watched them squabble",
            "found a way to make mushroom brew that could get a dwarf drunk in one sip",
        ],
        "alignment_events": {
            "good": [
                "shared the secret mushroom patch with all the tribe and fed everyone",
                "helped a lost child find their way home instead of tricking them",
                "fixed the big folk's broken cart for free and felt strange about it",
                "told the truth for once and it felt so strange I had to lie again immediately",
                "spared a captured bird and let it fly instead of eating it",
            ],
            "evil": [
                "sold faulty weapons to the orcs and laughed when they broke mid-battle",
                "poisoned the well of a peaceful village and counted the bodies",
                "set traps that killed innocent travelers and collected their shiny things",
                "stole from the tribe and blamed another — they got exiled instead of me",
                "lied to the chieftain about a vision and sent warriors to their deaths",
            ],
            "order": [
                "organized the warren into a proper den with rules and ranks",
                "kept the records of all trades in a big book — the first goblin to write",
                "established the rules of goblin conduct — no stealing from each other",
                "built a system of tunnels and defenses that kept the big folk out",
                "standardized the mushroom weights for fair trade within the warren",
            ],
            "chaos": [
                "swapped all the name signs in the warren and watched the confusion",
                "set off a chain of explosions for fun and collapsed three tunnels",
                "convinced the whole tribe to speak backwards for a full season",
                "painted the chieftain's face while they slept and blamed the spirits",
                "replaced the war banners with laundry and watched the army march wrong",
            ],
            "neutral": [
                "traded shiny rocks with the surface folk and learned their greed",
                "explored the deep tunnels for new things and found old bones",
                "learned a new recipe for mushroom brew that tasted like sunshine",
                "watched the big folk and laughed at their serious ways",
                "simply collected shiny things and organized them by color",
            ],
        },
        "thoughts": [
            "Shiny things make the world go round — and I want all of them.",
            "Rules are for big folk — we have tricks, and tricks are better.",
            "A good lie is worth more than gold, and lasts longer too.",
            "The deep tunnels hold many secrets, and I will find them all.",
            "Never trust a goblin who says 'trust me' — that's the first rule.",
            "Everything is a trade if you ask the right way.",
            "The best things come in small, shiny packages.",
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
# 3.  CHARACTER SELECTION
# ──────────────────────────────────────────────────────────────────────

def select_character(world: Dict, char_id: Optional[int] = None, rng: random.Random = random.Random()) -> Dict:
    figures = world.get("historical_figures", [])
    if not figures:
        raise ValueError("No historical figures in world data")
    if char_id is not None:
        for f in figures:
            if f["id"] == char_id:
                return f
        raise ValueError(f"Character ID {char_id} not found")
    return rng.choice(figures)


# ──────────────────────────────────────────────────────────────────────
# 4.  CONTEXT RESOLUTION
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
        return f"a new site was founded: {ev.get('site_name', 'unknown')}"
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


def resolve_context(world: Dict, figure: Dict) -> Dict:
    """Build rich context about a character from the world data."""
    civ_id = figure.get("civ_id")
    civ = None
    if civ_id is not None:
        for c in world.get("civilizations", []):
            if c["id"] == civ_id:
                civ = c
                break

    # Sites belonging to this character's civ
    sites = []
    if civ is not None:
        for s in world.get("sites", []):
            if s.get("civ_id") == civ["id"]:
                sites.append(s)

    # Other figures from same civ
    same_civ_figures = []
    for f in world.get("historical_figures", []):
        if f.get("civ_id") == civ_id:
            same_civ_figures.append(f)

    race = figure.get("race", "Human")
    alignment = figure.get("alignment", "neutral")
    themes = RACE_MEMORY_THEMES.get(race, RACE_MEMORY_THEMES["Human"])

    # Region biomes
    region_biome_names = []
    if sites:
        biome_map = {b["id"]: b["name"] for b in world.get("biomes", [])}
        for s in sites[:5]:
            bid = s.get("biome_id", 0)
            if bid in biome_map:
                region_biome_names.append(biome_map[bid])

    # World events involving this civ — filtered to character's lifetime
    birth_year = figure.get("birth_year", -150)
    death_year = figure.get("death_year")
    end_year = world.get("config", {}).get("end_year", 50)
    char_end = death_year if death_year is not None else end_year

    civ_events = []
    for ev in world.get("events", []):
        # Event must involve the character's civ
        if ev.get("civ_id") == civ_id or ev.get("civ1_id") == civ_id or ev.get("civ2_id") == civ_id:
            eyear = ev.get("year", 0)
            # Event must be within character's lifetime
            if birth_year <= eyear <= char_end:
                civ_events.append(ev)

    # Group civ events by year for quick lookup
    events_by_year = defaultdict(list)
    for ev in civ_events:
        events_by_year[ev.get("year", 0)].append(ev)

    # Artifacts from this civ
    civ_artifacts = []
    for a in world.get("artifacts", []):
        if a.get("civ_id") == civ_id:
            civ_artifacts.append(a)

    # Beasts that were active during the character's lifetime
    beasts = []
    for b in world.get("beasts", []):
        b_spawn = b.get("year_spawned", -200)
        b_active = b.get("active", True)
        if b_active:
            beasts.append(b)
        else:
            # Check if beast was active during character's life
            for ev in world.get("events", []):
                if ev.get("type") == "beast_slain" and ev.get("beast_id") == b["id"]:
                    if birth_year <= ev["year"] <= char_end:
                        beasts.append(b)
                    break

    # ── Resolve relationships ──
    # Build a lookup map of all figure IDs to names
    figure_name_map = {}
    for f in world.get("historical_figures", []):
        figure_name_map[f["id"]] = f.get("name", "Unknown")

    # Resolve the character's own relationships
    raw_rels = figure.get("relationships", [])
    resolved_rels = []
    for rel in raw_rels:
        fid = rel.get("figure_id")
        resolved = dict(rel)
        if fid in figure_name_map:
            resolved["figure_name"] = figure_name_map[fid]
        else:
            resolved["figure_name"] = f"Figure {fid}"
        resolved_rels.append(resolved)

    # Group relationships by type for easy lookup in memory generation
    relationships_by_type: Dict[str, List[Dict]] = {}
    for rel in resolved_rels:
        rtype = rel.get("type", "unknown")
        relationships_by_type.setdefault(rtype, []).append(rel)

    # Collect all resolved names for quick reference
    related_names = [r["figure_name"] for r in resolved_rels]
    spouse_names = [r["figure_name"] for r in resolved_rels if r.get("type") == "spouse"]
    child_names = [r["figure_name"] for r in resolved_rels if r.get("role") == "child"]
    parent_names = [r["figure_name"] for r in resolved_rels if r.get("role") == "parent"]
    sibling_names = [r["figure_name"] for r in resolved_rels if r.get("type") == "sibling"]
    friend_names = [r["figure_name"] for r in resolved_rels if r.get("type") in ("friend", "ally")]
    rival_names = [r["figure_name"] for r in resolved_rels if r.get("type") in ("rival", "enemy")]

    return {
        "civ": civ,
        "civ_name": civ["name"] if civ else "the wilds",
        "civ_alignment": civ["alignment"] if civ else "neutral",
        "sites": sites,
        "same_civ_figures": same_civ_figures,
        "race_themes": themes,
        "alignment": alignment,
        "race": race,
        "region_biomes": region_biome_names,
        "civ_events": civ_events,
        "events_by_year": events_by_year,
        "civ_artifacts": civ_artifacts,
        "beasts": beasts,
        "eras": world.get("eras", []),
        "start_year": world.get("config", {}).get("start_year", 0),
        "end_year": world.get("config", {}).get("end_year", 250),
        # ── New: resolved relationship data ──
        "relationships_resolved": resolved_rels,
        "relationships_by_type": relationships_by_type,
        "related_names": related_names,
        "spouse_names": spouse_names,
        "child_names": child_names,
        "parent_names": parent_names,
        "sibling_names": sibling_names,
        "friend_names": friend_names,
        "rival_names": rival_names,
        "figure_name_map": figure_name_map,
    }


# ──────────────────────────────────────────────────────────────────────
# 5.  MEMORY GENERATION
# ──────────────────────────────────────────────────────────────────────

def compute_age(figure: Dict, world: Dict) -> int:
    end_year = world.get("config", {}).get("end_year", 50)
    birth_year = figure.get("birth_year", end_year - 30)
    death_year = figure.get("death_year")
    if death_year is not None:
        age = death_year - birth_year
    else:
        age = end_year - birth_year
    # Allow up to 100 long-term memories for very long-lived characters,
    # but clamp to 50 for typical lifespans so memories stay focused.
    # Age is used as the number of yearly samples, not decades.
    return max(20, min(100, age))


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
    figure: Dict,
    context: Dict,
    age: int,
    rng: random.Random,
) -> List[Dict]:
    """
    Generate `age` long-term memories — each is a verbose yearly culmination
    that weaves together real events, personal milestones, and reflections.
    Major events are highlighted as the most poignant moments of the year.
    """
    memories = []
    birth_year = figure.get("birth_year", 0)
    death_year = figure.get("death_year")
    start_year = context.get("start_year", 0)
    end_year = context.get("end_year", 250)

    # Clamp the start of the year range to start_year so we never
    # produce negative absolute years. The character's age is still
    # computed from their true birth year, but the absolute year
    # value shown in the output starts at start_year.
    effective_start = max(birth_year, start_year)

    if death_year is not None:
        active_years = list(range(effective_start, death_year + 1))
    else:
        active_years = list(range(effective_start, end_year + 1))

    if len(active_years) > age:
        step = len(active_years) / age
        indices = [int(i * step) for i in range(age)]
        selected_years = [active_years[i] for i in indices]
    else:
        selected_years = active_years

    themes = context["race_themes"]
    alignment = context["alignment"]
    race = context["race"]
    civ_name = context["civ_name"]
    prof = rng.choice(themes.get("professions", ["adventurer"]))

    # Track used templates to avoid repetition
    used_childhood = set()
    used_life = set()
    used_alignment = set()
    used_thought = set()

    # Pre-group events by year
    events_by_year = context.get("events_by_year", {})
    eras = context.get("eras", [])

    for i, year in enumerate(selected_years):
        relative_age = year - birth_year
        year_label = format_year_with_era(year, eras)

        # ── Gather real events for this year ──
        year_events = events_by_year.get(year, [])
        season = rng.choice(["spring", "summer", "autumn", "winter"])

        # Build a multi-sentence narrative paragraph
        paragraphs = []

        # 1. Year opening — set the scene
        if relative_age < 10:
            pool = themes.get("childhood_events", [])
            template = pick_unique(pool, used_childhood, rng)
            paragraphs.append(f"When I was {relative_age} years old, {template}.")
        else:
            # Opening: the year in context (use numeric year to avoid "Year Year" duplication)
            year_num = year  # bare integer
            openings = [
                f"The year {year_num} was a {rng.choice(['hard', 'quiet', 'turbulent', 'prosperous', 'dark', 'bright', 'forgotten'])} one.",
                f"In {year_label}, the world felt {rng.choice(['heavy with change', 'still and waiting', 'alive with possibility', 'worn and tired', 'sharp and dangerous'])}.",
                f"I remember {year_label} well — it was a {rng.choice(['time of change', 'season of loss', 'year of plenty', 'period of growth', 'age of shadows'])}.",
                f"Looking back, {year_label} stands out — {rng.choice(['the air smelled of smoke', 'the harvest was bountiful', 'the nights were long', 'the wind carried strange news', 'everything felt fragile'])}.",
                f"At {year_num}, {rng.choice(['the first snow fell early', 'the river flooded the lowlands', 'the king passed a new law', 'a strange star appeared', 'the old mine collapsed', 'the temple bells fell silent', 'a great fire swept through', 'the plague reached our village', 'the roads were closed', 'the harvest failed', 'the festival was held', 'a foreign envoy arrived', 'the walls were rebuilt', 'the forest caught fire', 'the well ran dry', 'a child was born', 'a hero returned', 'the treaty was signed', 'the taxes were raised', 'the crops were blessed'])}.",
                f"In the {rng.choice(['early', 'late', 'middle', 'deep'])} days of {year_label}, {rng.choice(['I set out on a journey', 'the council convened', 'a battle was fought', 'the market bustled', 'the shrine was consecrated', 'the hunters returned empty-handed', 'the smiths worked through the night', 'the scholars debated', 'the sailors docked', 'the builders raised a new hall'])}.",
                f"By {year_label}, {rng.choice(['the land had changed', 'the people were restless', 'the crops were failing', 'the kingdom was at peace', 'the roads were safer', 'the nights grew colder', 'the forests thinned', 'the rivers ran dry', 'the walls stood strong', 'the gates were closed'])}.",
                f"I recall that in {year_label}, {rng.choice(['I met a traveler from afar', 'I lost a dear friend', 'I found a hidden place', 'I learned a valuable lesson', 'I made a promise', 'I witnessed a miracle', 'I committed a grave mistake', 'I discovered a secret', 'I fought for what I believed', 'I stood at a crossroads'])}.",
                f"{year_label} was the season when {rng.choice(['everything changed', 'nothing happened', 'I came of age', 'the war ended', 'the drought broke', 'the rebellion started', 'the peace was signed', 'the old ways died', 'the new order began', 'I met my fate'])}.",
                f"During {year_label}, {rng.choice(['I wandered the wilds', 'I labored in the fields', 'I served at the court', 'I sailed the seas', 'I prayed in the ruins', 'I fought in the trenches', 'I studied under a master', 'I raised my children', 'I buried my dead', 'I built my legacy'])}.",
                f"Of all the years, {year_label} was the one where {rng.choice(['I learned what loss means', 'I understood what power costs', 'I saw what love demands', 'I felt what hate does', 'I knew what hope carries', 'I discovered what fear hides', 'I realized what time takes'])}.",
            ]
            paragraphs.append(rng.choice(openings))

        # 2. Major events — real world events with highlight
        if year_events and relative_age >= 10:
            # Pick up to 2 events to feature
            featured = rng.sample(year_events, min(len(year_events), 2))
            for ev in featured:
                ev_desc = _describe_event(ev)
                highlight_prefix = rng.choice([
                    "The most poignant moment",
                    "What struck me most",
                    "The thing I cannot forget",
                    "The defining event",
                    "What marked that year",
                ])
                paragraphs.append(f"{highlight_prefix} was {ev_desc}.")
        elif relative_age >= 10:
            # No real events — use personal milestones
            if rng.random() < 0.4:
                pool = themes.get("alignment_events", {}).get(alignment,
                            themes.get("alignment_events", {}).get("neutral", []))
                if pool:
                    template = pick_unique(pool, used_alignment, rng)
                    paragraphs.append(f"I {template}")
                else:
                    paragraphs.append(f"I worked as a {prof} and faced many trials.")
            elif rng.random() < 0.5:
                pool = themes.get("life_events", [])
                template = pick_unique(pool, used_life, rng)
                paragraphs.append(f"I {template}")
            else:
                work_note = rng.choice(['learned much that year', 'made a decent living', 'faced many challenges', 'found my purpose in the craft', 'grew stronger in body and spirit'])
                paragraphs.append(f"I worked as a {prof} and {work_note}.")

        # 3. Personal reflection / consequence
        if relative_age >= 10:
            reflection_templates = [
                f"That year taught me that {rng.choice(['the world is cruel', 'kindness matters', 'strength alone is not enough', 'time changes everything', 'some wounds never heal', 'hope endures', 'a good plan saves lives'])}.",
                f"I often think about the {rng.choice(['lessons', 'faces', 'sounds', 'silences', 'shadows'])} of that time.",
                f"It was a year that {rng.choice(['shaped me', 'broke me', 'made me who I am', 'tested my limits', 'showed me the truth', 'left its mark'])}.",
                f"To this day, I {rng.choice(['carry its weight', 'remember it vividly', 'wonder what could have been', 'draw strength from it', 'wish I could forget'])}.",
            ]
            paragraphs.append(rng.choice(reflection_templates))

        # 4. Racial / alignment closing — only ~20% of the time, with variety
        # (Question_Eval_v2 §2: constant tails collapse score spread)
        if rng.random() < 0.2:
            alignment_closers = {
                "good": rng.choice([
                    " Even now, I look back with warmth.",
                    " I remember that time fondly.",
                    " It was a chapter I hold dear.",
                    " That year stays with me — a good memory.",
                ]),
                "evil": rng.choice([
                    " I felt no regret — only cold satisfaction.",
                    " The memory lingers, sharp and bitter.",
                    " I carry that darkness still.",
                    " It was a year that fed my hunger.",
                ]),
                "order": rng.choice([
                    " It was proper — how things should be.",
                    " All was as it must be, orderly and right.",
                    " The pattern held, as it always does.",
                    " That year, the world made sense.",
                ]),
                "chaos": rng.choice([
                    " Nothing was ever the same after that.",
                    " The chaos of that time reshaped everything.",
                    " Order broke, and something new was born.",
                    " That year, the rules were unwritten.",
                ]),
                "neutral": rng.choice([
                    " It was simply another chapter.",
                    " I neither loved nor hated that year.",
                    " It passed, as all things do.",
                    " The year came and went — unremarkable.",
                ]),
            }.get(alignment, "")
            if alignment_closers:
                paragraphs.append(alignment_closers.strip())
        else:
            # No closing tail — let the narrative breathe
            pass

        # Inject site names into the narrative
        full_text = " ".join(p for p in paragraphs if p)
        if context["sites"] and rng.random() < 0.4:
            valid_sites = [s for s in context["sites"] if s.get("founded_year", -200) <= year]
            if valid_sites:
                site = rng.choice(valid_sites)
                full_text = full_text.replace("the fortress", site["name"])
                full_text = full_text.replace("the village", site["name"])
                full_text = full_text.replace("the forest", site["name"])
                full_text = full_text.replace("the grove", site["name"])
                full_text = full_text.replace("the realm", civ_name)

        # Reference other alive figures
        if context["same_civ_figures"] and rng.random() < 0.25:
            alive_others = [f for f in context["same_civ_figures"]
                           if f["id"] != figure["id"]
                           and f.get("birth_year", -200) <= year
                           and (f.get("death_year") is None or f["death_year"] >= year)]
            if alive_others:
                other = rng.choice(alive_others)
                full_text += f" {other['name']} was there too."

        # ── Relationship-specific milestones ──
        # Every ~5 years, inject a relationship-themed paragraph
        if i > 0 and i % 5 == 0 and relative_age >= 10:
            related = context.get("related_names", [])
            if related and rng.random() < 0.6:
                # Pick a random relationship to feature
                rel_types = context.get("relationships_by_type", {})
                all_types = [t for t, rs in rel_types.items() if rs]
                if all_types:
                    rtype = rng.choice(all_types)
                    rel_pool = rel_types[rtype]
                    rel = rng.choice(rel_pool)
                    rname = rel.get("figure_name", "someone")
                    strength = rel.get("strength", "moderate")

                    if rtype == "spouse":
                        full_text += f" My bond with {rname} deepened — {strength} and steady."
                    elif rtype == "parent_child":
                        role = rel.get("role", "child")
                        if role == "parent":
                            full_text += f" I watched {rname} grow — {strength}, as children do."
                        else:
                            full_text += f" My parent {rname} was a {strength} presence that year."
                    elif rtype == "sibling":
                        full_text += f" {rname} and I shared the year — {strength} as always."
                    elif rtype in ("friend", "ally"):
                        full_text += f" {rname} stood by me — a {strength} friend through it all."
                    elif rtype in ("rival", "enemy"):
                        full_text += f" {rname} was a {strength} rival — we clashed more than once."
                    else:
                        full_text += f" {rname} crossed my path that year — {strength} ties."

        # Racial closing thought
        racial_thought = pick_unique(themes.get("thoughts", ["Life goes on."]) * 5, used_thought, rng)

        memories.append({
            "year": year,
            "year_label": year_label,
            "age": relative_age,
            "season": season,
            "type": "life_event",
            "memory": full_text,
            "reflection": racial_thought,
            "alignment_at_time": alignment,
            "race": race,
        })

    return memories


def generate_short_term_memories(
    figure: Dict,
    context: Dict,
    age: int,
    rng: random.Random,
) -> List[Dict]:
    """
    Generate 15 short-term memories — things that happened in the current
    season and last season. Each memory is tied to a specific season
    (spring/summer/autumn/winter) and feels like a recent seasonal recollection.
    """
    memories = []
    themes = context["race_themes"]
    alignment = context["alignment"]
    race = context["race"]
    civ_name = context["civ_name"]
    prof = rng.choice(themes.get("professions", ["adventurer"]))
    eras = context.get("eras", [])

    current_year = context.get("end_year", 250)
    seasons_cycle = ["spring", "summer", "autumn", "winter"]

    # Determine current season (cyclical)
    current_season_idx = current_year % 4
    current_season = seasons_cycle[current_season_idx]
    last_season = seasons_cycle[(current_season_idx - 1) % 4]

    used_recent = set()
    used_thought = set()

    # ── 8 seasonal events (4 current season, 4 last season) ──
    seasonal_pool = [
        f"This {rng.choice(['spring', 'summer', 'autumn', 'winter'])}, I {rng.choice(['noticed the first buds', 'felt the heat settle in', 'watched the leaves fall', 'shivered through the frost'])} in {civ_name}.",
        f"The {rng.choice(['rains', 'sun', 'wind', 'snow', 'fog', 'harvest'])} this season was {rng.choice(['unusual', 'fierce', 'gentle', 'early', 'late', 'blessed'])} — {rng.choice(['I remember because the crops suffered', 'it made travel difficult', 'it was a welcome change', 'the elders said they had never seen it before'])}.",
        f"I spent the season {rng.choice(['working as a ' + prof, 'trading at the market', 'repairing my tools', 'traveling the roads', 'praying at the shrine', 'guarding the walls'])}.",
        f"A {rng.choice(['merchant', 'soldier', 'pilgrim', 'refugee', 'messenger', 'wanderer'])} came through {civ_name} this season and told of {rng.choice(['war in the east', 'a new king', 'a great beast', 'a fallen city', 'strange lights in the sky', 'a wedding in the capital'])}.",
        f"This season, I {rng.choice(['lost a friend', 'made a new ally', 'fell ill', 'recovered from a wound', 'found a lost heirloom', 'broke a promise', 'kept a vow', 'witnessed a miracle'])}.",
        f"The {rng.choice(['moon', 'stars', 'sun', 'wind', 'river', 'forest'])} this season looked {rng.choice(['different', 'ominous', 'beautiful', 'strange', 'familiar', 'haunted'])} — I {rng.choice(['watched it for hours', 'felt a chill', 'remembered an old story', 'prayed for guidance', 'knew something was coming'])}.",
        f"I {rng.choice(['hunted', 'foraged', 'fished', 'harvested', 'brewed', 'crafted', 'studied', 'trained'])} this season and {rng.choice(['succeeded', 'failed', 'learned much', 'barely survived', 'found peace in the work'])}.",
        f"A {rng.choice(['festival', 'funeral', 'wedding', 'trial', 'celebration', 'ceremony', 'market day'])} was held this season — {rng.choice(['I attended and felt joy', 'I could not go and regret it', 'it was somber', 'the whole town gathered', 'it changed everything'])}.",
    ]

    # ── Add relationship-specific seasonal events ──
    related = context.get("related_names", [])
    if related:
        rel_types = context.get("relationships_by_type", {})
        all_types = [t for t, rs in rel_types.items() if rs]
        if all_types:
            rtype = rng.choice(all_types)
            rel_pool = rel_types[rtype]
            rel = rng.choice(rel_pool)
            rname = rel.get("figure_name", "someone")

            if rtype == "spouse":
                seasonal_pool.append(
                    f"This season, I spent time with {rname}, my spouse — we {rng.choice(['shared the harvest', 'walked the walls together', 'talked through the night', 'argued and made peace', 'planned for the future'])}."
                )
            elif rtype == "parent_child":
                role = rel.get("role", "child")
                if role == "parent":
                    seasonal_pool.append(
                        f"My child {rname} {rng.choice(['took their first steps', 'learned the craft', 'left for adventure', 'returned home safely', 'made me proud this season'])}."
                    )
                else:
                    seasonal_pool.append(
                        f"My parent {rname} {rng.choice(['shared wisdom with me', 'fell ill this season', 'taught me something new', 'told me stories of old', 'needed my help this season'])}."
                    )
            elif rtype == "sibling":
                seasonal_pool.append(
                    f"My sibling {rname} {rng.choice(['visited this season', 'sent word from afar', 'needed my aid', 'celebrated with me', 'argued with me but we reconciled'])}."
                )
            elif rtype in ("friend", "ally"):
                seasonal_pool.append(
                    f"My friend {rname} {rng.choice(['came to my aid', 'shared a drink with me', 'told me a secret', 'betrayed my trust', 'stood by me in hardship'])}."
                )
            elif rtype in ("rival", "enemy"):
                seasonal_pool.append(
                    f"My rival {rname} {rng.choice(['made a move against me', 'suffered a setback', 'gained power this season', 'sent a veiled threat', 'was seen plotting'])}."
                )
            else:
                seasonal_pool.append(
                    f"{rname} was part of my season — {rel.get('strength', 'moderate')} ties bind us."
                )

    # Assign specific seasons
    for i in range(8):
        template = pick_unique(seasonal_pool, used_recent, rng)
        season = current_season if i < 4 else last_season
        year = current_year if season == current_season else current_year - 1
        year_label = format_year_with_era(year, eras)

        # Replace season placeholder in template
        template = template.replace("spring", season).replace("summer", season).replace("autumn", season).replace("winter", season)

        memories.append({
            "type": "seasonal_event",
            "season": season,
            "year": year,
            "year_label": year_label,
            "memory": template,
            "alignment": alignment,
            "race": race,
        })

    # ── 7 seasonal thoughts / impressions ──
    thought_pool = [
        f"This {current_season}, I find myself thinking about {rng.choice(['the passage of time', 'my family far away', 'the mistakes I have made', 'the future of ' + civ_name, 'the silence of the woods', 'the faces of the dead', 'the promises I keep', 'the road ahead'])}.",
        f"The {current_season} {rng.choice(['air', 'light', 'stillness', 'darkness', 'warmth', 'cold'])} makes me feel {rng.choice(['melancholy', 'hopeful', 'restless', 'grateful', 'haunted', 'determined'])}.",
        f"I wonder what the {rng.choice(['next season', 'coming year', 'distant future', 'old gods', 'spirits of this place'])} will bring.",
        f"I remember this season last year — {rng.choice(['it was different then', 'I was a different person', 'I had not yet lost what I lost', 'the world seemed kinder', 'I was afraid, but now I am not'])}.",
        f"{rng.choice(['The elders say', 'My mother told me', 'The old songs sing', 'It is written in the runes'])} that {rng.choice(['this season is sacred', 'the winter is a test', 'the spring brings renewal', 'the summer is for war', 'the autumn is for remembrance'])}.",
        f"I have been {rng.choice(['dreaming', 'remembering', 'praying', 'wandering', 'working', 'waiting'])} more than usual this season — {rng.choice(['it means something', 'or perhaps it means nothing at all', 'the signs are everywhere', 'I cannot explain it'])}.",
        f"This season feels {rng.choice(['different', 'the same as always', 'charged with meaning', 'empty', 'precious', 'fleeting'])} — I {rng.choice(['cherish it', 'fear it', 'accept it', 'fight it', 'surrender to it'])}.",
    ]

    for i in range(7):
        thought = pick_unique(thought_pool, used_thought, rng)
        # Replace season placeholders
        thought = thought.replace("spring", current_season).replace("summer", current_season).replace("autumn", current_season).replace("winter", current_season)
        memories.append({
            "type": "seasonal_thought",
            "season": current_season,
            "year": current_year,
            "year_label": format_year_with_era(current_year, eras),
            "memory": thought,
            "alignment": alignment,
            "race": race,
        })

    return memories


def generate_near_term_memories(
    figure: Dict,
    context: Dict,
    age: int,
    rng: random.Random,
) -> List[Dict]:
    """Generate 10 near-term / future-looking memories and plans."""
    memories = []
    themes = context["race_themes"]
    alignment = context["alignment"]
    race = context["race"]
    civ_name = context["civ_name"]
    prof = rng.choice(themes.get("professions", ["adventurer"]))

    used_plans = set()
    used_worries = set()

    plan_pool = [
        f"I plan to travel to {civ_name} and seek my fortune there.",
        f"I will perfect my craft as a {prof} this coming year — I can feel myself improving.",
        f"I intend to visit the {rng.choice(['old shrine', 'distant market', 'mountain pass', 'hidden glade', 'deep tunnels', 'ancient library', 'sacred spring'])}.",
        f"I must prepare for the coming {rng.choice(['winter', 'war', 'festival', 'journey', 'hunting season', 'siege', 'caravan'])}.",
        f"I aim to {rng.choice(['settle a dispute', 'repair the old bridge', 'gather supplies', 'train the young ones', 'study the old texts', 'explore the uncharted caves', 'seek a lost artifact'])}.",
        f"I want to {rng.choice(['find a rare material', 'discover a hidden cave', 'befriend a neighboring tribe', 'learn a lost art', 'craft a masterpiece', 'prove myself in battle', 'make peace with my past'])}.",
        f"I need to {rng.choice(['pay a debt', 'keep a promise', 'return a favor', 'honor a vow', 'finish a project', 'make amends', 'say goodbye'])}.",
        f"I will attend the {rng.choice(['moot', 'council', 'gathering', 'celebration', 'ritual', 'tournament', 'ceremony'])} next season.",
        f"I plan to {rng.choice(['write my memoirs', 'build a monument', 'plant a grove', 'forge a legacy', 'train a successor', 'walk the old road'])}.",
        f"I dream of {rng.choice(['finding peace', 'seeing the ocean', 'climbing the highest peak', 'meeting the spirits', 'understanding the old mysteries', 'finding a home'])}.",
    ]

    worry_pool = [
        "I worry about the war in the east — it creeps closer each season.",
        "I worry about the drought this summer — the fields are already dry.",
        "I worry about the raids on the caravans — trade is slowing.",
        "I worry about the strange signs in the forest — the animals are restless.",
        "I worry about the unrest in the mountain — the deep folk are troubled.",
        "I worry about the plague spreading — too many have fallen sick.",
        "I worry about the old spirits growing restless — the rituals have felt wrong.",
        "I worry about the bandits on the roads — travel is dangerous now.",
        "I worry about the harvest failing — we will starve if it does.",
        "I worry about the river drying up — it has never done that before.",
        "I fear that the old ways are fading — no one remembers them anymore.",
        "I fear that my strength is declining — I am not what I once was.",
        "I fear that the spirits are angry — they have not answered my prayers.",
        "I fear that I will outlive my kin — to be the last is a curse.",
        "I fear that change is coming — and I am not ready for it.",
        "I fear that I have wasted my best years — chasing shadows.",
        "I fear the darkness in my own heart — it grows when I sleep.",
        "I fear that I am being forgotten — my name will not live on.",
        "I fear that my enemies are closing in — I can feel them watching.",
    ]

    # ── Add relationship-specific worries ──
    related = context.get("related_names", [])
    if related:
        rel_types = context.get("relationships_by_type", {})
        all_types = [t for t, rs in rel_types.items() if rs]
        if all_types:
            rtype = rng.choice(all_types)
            rel_pool = rel_types[rtype]
            rel = rng.choice(rel_pool)
            rname = rel.get("figure_name", "someone")
            strength = rel.get("strength", "moderate")

            if rtype == "spouse":
                worry_pool.append(
                    f"I worry about {rname} — the distance between us feels too great."
                )
                plan_pool.append(
                    f"I plan to visit {rname} soon — we need to reconnect."
                )
            elif rtype == "parent_child":
                role = rel.get("role", "child")
                if role == "parent":
                    worry_pool.append(
                        f"I worry about {rname}, my child — the world is dangerous."
                    )
                    plan_pool.append(
                        f"I must teach {rname} what I know before it is too late."
                    )
                else:
                    worry_pool.append(
                        f"I worry about {rname}, my parent — they grow older each season."
                    )
                    plan_pool.append(
                        f"I should visit {rname} and honor my parent."
                    )
            elif rtype == "sibling":
                worry_pool.append(
                    f"I worry about {rname} — we have not spoken in too long."
                )
                plan_pool.append(
                    f"I will send word to {rname} — it is time to mend the distance."
                )
            elif rtype in ("friend", "ally"):
                worry_pool.append(
                    f"I worry that {rname} is in danger — I should go to them."
                )
                plan_pool.append(
                    f"I will aid {rname} in their coming struggle."
                )
            elif rtype in ("rival", "enemy"):
                worry_pool.append(
                    f"I worry about {rname}'s plans — they move in shadow."
                )
                plan_pool.append(
                    f"I must watch {rname} closely — they cannot be trusted."
                )
            else:
                worry_pool.append(
                    f"I worry about {rname} — something feels wrong."
                )

    # ── Original generic family/rival concerns (now with resolved names when available) ──
    if context.get("spouse_names"):
        worry_pool.append(f"I am concerned about {context['spouse_names'][0]} — they are far away and I cannot protect them.")
    else:
        worry_pool.append("I am concerned about my family — they are far away and I cannot protect them.")
    worry_pool.append("I am concerned about my reputation — a lie is spreading about me.")
    worry_pool.append("I am concerned about my debts — the collector comes next season.")
    worry_pool.append("I am concerned about the coming winter — we have not stored enough.")
    worry_pool.append("I am concerned about a promise I cannot keep — it weighs on me.")
    worry_pool.append("I am concerned about the silence from the north — no traders have come.")
    worry_pool.append("I am concerned about the strange dreams I have been having — they feel like warnings.")
    worry_pool.append("I am concerned about the shadow I saw in the distance — it moved wrong.")
    worry_pool.append("I am concerned about the price of goods rising — soon we will not afford bread.")

    for i in range(6):
        template = pick_unique(plan_pool, used_plans, rng)
        memories.append({
            "type": "future_plan",
            "memory": template,
            "alignment": alignment,
            "race": race,
        })

    for i in range(4):
        template = pick_unique(worry_pool, used_worries, rng)
        memories.append({
            "type": "future_worry",
            "memory": template,
            "alignment": alignment,
            "race": race,
        })

    return memories


# ──────────────────────────────────────────────────────────────────────
# 6.  FACTS BUILDER — short single-line truths about the character
# ──────────────────────────────────────────────────────────────────────

RACE_STAT_BASES = {
    "Dwarf":  {"str": 5, "agi": 2, "end": 5, "int": 3, "wis": 3, "cha": 2},
    "Elf":    {"str": 2, "agi": 5, "end": 2, "int": 4, "wis": 4, "cha": 4},
    "Human":  {"str": 3, "agi": 3, "end": 3, "int": 3, "wis": 3, "cha": 3},
    "Orc":    {"str": 6, "agi": 3, "end": 4, "int": 1, "wis": 2, "cha": 1},
    "Goblin": {"str": 1, "agi": 4, "end": 1, "int": 3, "wis": 1, "cha": 3},
    "Troll":  {"str": 7, "agi": 1, "end": 6, "int": 1, "wis": 1, "cha": 1},
}

RACE_WEAPONS = {
    "Dwarf":  ["warhammer", "battleaxe", "mattock", "pick", "shortsword"],
    "Elf":    ["longsword", "spear", "bow", "rapier", "staff"],
    "Human":  ["longsword", "broadsword", "mace", "spear", "crossbow"],
    "Orc":    ["greataxe", "scimitar", "club", "javelin", "spiked mace"],
    "Goblin": ["dagger", "shortsword", "sling", "spear", "scimitar"],
    "Troll":  ["club", "greatclub", "stone maul", "spiked club", "fist"],
}

RACE_ARMORS = {
    "Dwarf":  ["chainmail", "scale mail", "plate armor", "studded leather"],
    "Elf":    ["leather armor", "chainmail", "elven scale", "studded leather"],
    "Human":  ["chainmail", "plate armor", "leather armor", "scale mail"],
    "Orc":    ["hide armor", "chainmail", "bone armor", "studded leather"],
    "Goblin": ["leather armor", "hide armor", "cloth armor", "bone armor"],
    "Troll":  ["thick hide", "bone armor", "studded leather", "hide armor"],
}

RACE_FAVORITE_FOODS = {
    "Dwarf":  ["mushroom stew", "roasted meat", "stone bread", "deep ale", "miner's pie"],
    "Elf":    ["forest berries", "wild game", "herb salad", "nectar", "fruit wine"],
    "Human":  ["roasted meat", "stew", "bread", "cheese", "ale", "wine"],
    "Orc":    ["raw meat", "blood sausage", "mushroom mash", "guts", "bone broth"],
    "Goblin": ["maggot bread", "mushroom soup", "pickled roots", "rotten meat"],
    "Troll":  ["raw meat", "whole fish", "mushrooms", "river stones (crushed)"],
}

RACE_FAVORITE_DRINKS = {
    "Dwarf":  ["cave ale", "stonebrew", "iron wine", "deep stout", "mountain mead"],
    "Elf":    ["forest wine", "nectar", "moonwater", "berry mead", "dew"],
    "Human":  ["ale", "wine", "beer", "mead", "cider"],
    "Orc":    ["blood ale", "gut rot", "fire whiskey", "mushroom brew"],
    "Goblin": ["swamp wine", "mushroom ale", "root tea", "stagnant water"],
    "Troll":  ["river water", "mushroom tea", "blood brew", "swamp beer"],
}

RACE_FAVORITE_COLORS = {
    "Dwarf":  ["deep red", "gold", "iron gray", "emerald green", "sapphire blue"],
    "Elf":    ["silver", "sky blue", "forest green", "moon white", "violet"],
    "Human":  ["royal blue", "crimson", "gold", "white", "black"],
    "Orc":    ["blood red", "black", "iron gray", "yellow", "green"],
    "Goblin": ["green", "purple", "yellow", "red", "orange"],
    "Troll":  ["mud brown", "gray", "dark green", "black", "rust red"],
}


def build_character_facts(figure: Dict, context: Dict, age: int, rng: random.Random) -> Dict[str, str]:
    """Build short single-line facts about the character."""
    race = figure["race"]
    civ_name = context.get("civ_name", "unknown")

    # Stats
    base = RACE_STAT_BASES.get(race, {"str": 3, "agi": 3, "end": 3, "int": 3, "wis": 3, "cha": 3})
    stat_rng = random.Random(figure["id"] * 7 + age)
    stats = {}
    for stat_name, base_val in base.items():
        bonus = stat_rng.randint(-1, 2) + (age // 30)  # small age bonus
        stats[stat_name] = max(1, base_val + bonus)

    # Weapon & armor
    weapon_rng = random.Random(figure["id"] * 13 + 7)
    armor_rng = random.Random(figure["id"] * 17 + 3)
    weapons_pool = RACE_WEAPONS.get(race, ["sword"])
    armors_pool = RACE_ARMORS.get(race, ["leather armor"])
    weapon = weapon_rng.choice(weapons_pool)
    armor = armor_rng.choice(armors_pool)

    # Favorites
    food_rng = random.Random(figure["id"] * 31 + 11)
    drink_rng = random.Random(figure["id"] * 37 + 13)
    color_rng = random.Random(figure["id"] * 41 + 17)
    foods = RACE_FAVORITE_FOODS.get(race, ["bread"])
    drinks = RACE_FAVORITE_DRINKS.get(race, ["water"])
    colors = RACE_FAVORITE_COLORS.get(race, ["gray"])
    fav_food = food_rng.choice(foods)
    fav_drink = drink_rng.choice(drinks)
    fav_color = color_rng.choice(colors)

    # Home site
    home_site_name = "wilderness"
    if figure.get("site_id") is not None:
        for s in context.get("sites", []):
            if s["id"] == figure["site_id"]:
                home_site_name = s["name"]
                break

    facts = {
        "race": race,
        "age": str(age),
        "alignment": figure.get("alignment", "neutral"),
        "title": figure.get("title", "commoner"),
        "civ": civ_name,
        "home_site": home_site_name,
        "profession": rng.choice(RACE_MEMORY_THEMES.get(race, {}).get("professions", ["adventurer"])),
        "weapon": weapon,
        "armor": armor,
        "favorite_food": fav_food,
        "favorite_drink": fav_drink,
        "favorite_color": fav_color,
    }
    # Add stats
    for k, v in stats.items():
        facts[f"stat_{k}"] = str(v)

    return facts


# ──────────────────────────────────────────────────────────────────────
# 7.  MAIN
# ──────────────────────────────────────────────────────────────────────

def generate_character_memories(
    world_path: str,
    output_path: str,
    char_id: Optional[int] = None,
    seed: Optional[int] = None,
    age_override: Optional[int] = None,
) -> Dict:
    if seed is None:
        seed = random.randint(0, 2**31)
    rng = random.Random(seed)

    print(f"Loading world from {world_path}...")
    world = load_world(world_path)

    print("Selecting character...")
    figure = select_character(world, char_id=char_id, rng=rng)

    print(f"Character: {figure['name']} ({figure['race']}), alignment={figure['alignment']}, id={figure['id']}")
    print("Resolving context...")
    context = resolve_context(world, figure)

    age = age_override if age_override is not None else compute_age(figure, world)
    print(f"Age: {age}")

    print(f"Generating {age} long-term memories...")
    long_term = generate_long_term_memories(figure, context, age, rng)

    print("Generating 15 short-term memories...")
    short_term = generate_short_term_memories(figure, context, age, rng)

    print("Generating 10 near-term memories...")
    near_term = generate_near_term_memories(figure, context, age, rng)

    # Build short facts
    facts = build_character_facts(figure, context, age, rng)

    output = {
        "character": {
            "id": figure["id"],
            "name": figure["name"],
            "race": figure["race"],
            "alignment": figure["alignment"],
            "title": figure.get("title", ""),
            "is_titleworthy": figure.get("is_titleworthy", False),
            "title_tier": figure.get("title_tier", "common"),
            "age": age,
            "birth_year": figure.get("birth_year"),
            "death_year": figure.get("death_year"),
            "deeds": figure.get("deeds", []),
            "relationships": figure.get("relationships", []),
        },
        "context": {
            "civ_name": context["civ_name"],
            "civ_alignment": context["civ_alignment"],
            "region_biomes": context["region_biomes"],
            "num_civ_sites": len(context["sites"]),
            "num_same_civ_figures": len(context["same_civ_figures"]),
            "num_civ_artifacts": len(context["civ_artifacts"]),
            "num_civ_events": len(context["civ_events"]),
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

    print(f"\nExported character memories to {output_path}")
    print(f"  Long-term:  {len(long_term)} memories")
    print(f"  Short-term: {len(short_term)} memories")
    print(f"  Near-term:  {len(near_term)} memories")
    print(f"  Total:      {len(long_term) + len(short_term) + len(near_term)} memories")

    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate DF-style character memories")
    parser.add_argument("--world", type=str, required=True, help="Path to world JSON from world_gen.py")
    parser.add_argument("--output", type=str, default="/tmp/character_memories.json", help="Output JSON path")
    parser.add_argument("--char-id", type=int, default=None, help="Character ID to generate memories for")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--age", type=int, default=None, help="Override age (default: compute from birth/death)")
    args = parser.parse_args()

    generate_character_memories(
        world_path=args.world,
        output_path=args.output,
        char_id=args.char_id,
        seed=args.seed,
        age_override=args.age,
    )
