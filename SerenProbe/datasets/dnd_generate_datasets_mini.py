#!/usr/bin/env python3
"""
Generate a massive D&D / fantasy-realm dataset — every character and location
gets their OWN SerenLoci[Vector] (70 facts) + SerenMemory (20 short, 40 long,
10 near) + corpus questions.  Three cross-entity corpus callosums:
  Characters → all 16 NPCs
  Geography  → 3 cities, 2 POI, 1 country
  Everything → all of the above

Design: each entity is a separate domain (its own YAML files).  Cross-entity
question files merge questions from the relevant domains.

Run from the repo root:  python dnd_generate_datasets.py
"""
from __future__ import annotations
import os, random, re, yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dnd")

# Words that carry no retrieval signal -- must match the linter's _STOP closely enough
# that a disambiguator we consider "distinctive" here is still distinctive there.
_STOP = {
    "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
    "the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "with", "from",
    "is", "are", "was", "were", "be", "been", "being", "does", "do", "did", "have",
    "has", "had", "and", "or", "but", "me", "my", "their", "they", "them", "it",
    "its", "tell", "about", "give", "describe", "summarize", "list", "near", "last",
    "recent", "happened", "involving", "concerning", "upcoming", "event", "plans",
    "incident", "stranger", "people", "region", "threat", "lore", "history", "ancient",
    "one", "three", "days", "week", "weeks", "month", "year", "years", "old", "new",
}


def _content_words(text):
    """Distinctive words in a piece of text: lowercase, de-punctuated, stopped, len>2."""
    t = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return [w for w in t.split() if w not in _STOP and len(w) > 2]


def _discriminator(target_text, sibling_texts, n=4):
    """Words that appear in target_text and NOT in any sibling -- the phrase that
    SINGLES OUT the target row from its neighbours.

    This is the whole fix for the ambiguity wall. The old generator wrote a HARDCODED
    generic hint ('incident involving a stranger') next to each query, with zero
    connection to the row it pointed at -- so the query shared no distinctive words
    with its own answer (overlap 0) and matched all 40 sibling memories equally. The
    lint correctly called every one AMBIGUOUS.

    Pulling the hint FROM the target's own text, minus anything a sibling also says,
    guarantees the query names something only the target carries. Proven in sandbox:
    old hint overlap 0 / 3 siblings tie; new hint overlap 4 / 0 siblings tie.

    Falls back to the target's own distinctive words (ignoring siblings) if the row is
    a near-duplicate of a sibling and nothing is unique -- better a weak discriminator
    than an empty one.
    """
    tw = _content_words(target_text)
    sib = set(w for s in sibling_texts for w in _content_words(s))
    unique = [w for w in tw if w not in sib]
    picks = unique[:n] if unique else tw[:n]
    return " ".join(picks)


def _dump_list(items):
    return yaml.safe_dump(items, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)

def _dump_questions(qs):
    return yaml.safe_dump({"questions": qs}, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)

def _probeconfig(domain, start_port=7520, questions_file="questions.yaml"):
    d = domain
    return f"""# Auto-generated eval topology for '{d}' (D&D realm).
ProbeConfig:
  StartingPort: {start_port}
  DefaultQuestions: [datasets/dnd/{d}/{questions_file}]
  Memory:
    MemoryCount: 1
    MemoryConfigs:
      - Name: {d}-mem
        Port: {start_port}
        Seed: [datasets/dnd/{d}/memory.yaml]
        Questions: [datasets/dnd/{d}/{questions_file}]
  Loci:
    LociCount: 2
    LociConfigs:
      - Name: {d}-loci-v
        Port: {start_port + 1}
        Flags: [vector]
        Seed: [datasets/dnd/{d}/loci.yaml]
        Questions: [datasets/dnd/{d}/{questions_file}]
      - Name: {d}-loci-nv
        Port: {start_port + 2}
        Seed: [datasets/dnd/{d}/loci.yaml]
        Questions: [datasets/dnd/{d}/{questions_file}]
  Corpus:
    CorpusRegrades:
      - Name: hop-sweep
        hops: [1, 2, 3]
      - Name: hop-x-packet
        hops: [1, 2]
        n_results: [10, 30]
      - Name: hop-terms
        hops: [2]
        hop_terms: [2, 4, 8]
        hop_budget: [5, 10]
      - Name: rrf-sweep         # RRF k: how sharply top ranks dominate the merge
        rrf_k: [30, 60, 100]
      - Name: floor-sweep       # drop weak loci hits (precision / abstention)
        loci_floor: [0.0, 0.1, 0.3]
      - Name: weight-sweep      # how hard to lean on the deterministic store
        loci_weight: [0.3, 0.5, 0.7, 1.0, 2.0, 3.0, 5.0, 10.0]
      - Name: hop-x-weight
        hops: [1, 2]
        loci_weight: [1.0, 3.0]
        n_results: [10, 30]
      - Name: packet-sweep      # briefing size - the coverage lever
        n_results: [10, 15, 20, 30]
      - Name: floor-x-weight    # the interaction of the two loci knobs
        loci_floor: [0.1, 0.3]
        loci_weight: [0.5, 1.0]
    CorpusCount: 2
    CorpusConfigs:
      - Name: {d}-scc-v
        Port: {start_port + 4}
        Stores:
          - Store: {d}-loci-v
          - Store: {d}-mem
        Questions: [datasets/dnd/{d}/{questions_file}]
      - Name: {d}-scc-nv
        Port: {start_port + 3}
        Stores:
          - Store: {d}-loci-nv
          - Store: {d}-mem
        Questions: [datasets/dnd/{d}/{questions_file}]
"""

def emit(domain, loci, memory, questions, start_port=7520):
    d = os.path.join(OUT, domain)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "loci.yaml"), "w", encoding="utf-8").write(_dump_list(loci))
    open(os.path.join(d, "memory.yaml"), "w", encoding="utf-8").write(_dump_list(memory))
    open(os.path.join(d, "questions.yaml"), "w", encoding="utf-8").write(_dump_questions(questions))
    open(os.path.join(d, "ProbeConfig.yml"), "w", encoding="utf-8").write(_probeconfig(domain, start_port))
    return len(loci), len(memory), len(questions)

def emit_cross(name, questions):
    d = os.path.join(OUT, "cross")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, f"{name}_questions.yaml"), "w", encoding="utf-8").write(_dump_questions(questions))

# ──────────────────────────────────────────────────────────────
#  Character templates — each gets 70 loci + 20s/40l/10n memory
# ──────────────────────────────────────────────────────────────
RACES = {
    "Dwarf":    {"homeland": "Ironhold", "lifespan": "~240 years", "traits": "stonecunning, poison resist"},
    "Elf":      {"homeland": "Silverwood", "lifespan": "~750 years", "traits": "trance, keen senses, fey ancestry"},
    "Human":    {"homeland": "Crossroads", "lifespan": "~80 years", "traits": "versatile, ambitious"},
    "Halfling": {"homeland": "Crossroads", "lifespan": "~150 years", "traits": "lucky, brave, nimble"},
    "Tiefling": {"homeland": "Crossroads", "lifespan": "~100 years", "traits": "hellish resistance, darkvision"},
    "Orc":      {"homeland": "the Iron Wastes", "lifespan": "~60 years", "traits": "aggressive, relentless"},
    "Lizardfolk":{"homeland": "the Sunken Temple", "lifespan": "~80 years", "traits": "cold-blooded, natural armor"},
    "Ogre":     {"homeland": "the Iron Wastes", "lifespan": "~90 years", "traits": "giant strength, thick skin"},
    "Dark Elf": {"homeland": "the Underdark", "lifespan": "~700 years", "traits": "sunlight sensitivity, drow magic"},
    "Fire Gnome":{"homeland": "Wyvern's Peak", "lifespan": "~200 years", "traits": "fire resistance, craft affinity"},
    "Wraith":   {"homeland": "the Shadowfell", "lifespan": "immortal", "traits": "incorporeal, life drain"},
    "Grick":    {"homeland": "the Sunken Temple", "lifespan": "~30 years", "traits": "camouflage, tentacle reach"},
    "Phase Spider":{"homeland": "the Ethereal Plane", "lifespan": "~50 years", "traits": "phase shift, ethereal jaunt"},
    "Half-Orc": {"homeland": "Crossroads", "lifespan": "~70 years", "traits": "savage attacks, relentless"},
    "Gnome":    {"homeland": "Silverwood", "lifespan": "~250 years", "traits": "small stature, craft affinity"},
    "High Elf": {"homeland": "Silverwood", "lifespan": "~800 years", "traits": "keen senses, cantrip, extra language"},
}

CHAR_DOMAINS = [f"char_{c[0]}" for c in [
    ("thorn","","","","",""), ("elara","","","","",""), ("shadowking","","","","",""), ("grickle","","","","",""),
]]
LOC_DOMAINS = [f"loc_{l[0]}" for l in [
    ("ironhold","",""), ("aethelgard","",""),
]]
ALL_DOMAINS = CHAR_DOMAINS + LOC_DOMAINS

CHARACTERS = [
    # (id, name, race, class, alignment, role)
    ("thorn",     "Thorne Ironheart",    "Dwarf",      "Fighter",    "Lawful Good",     "Major NPC"),
    ("elara",     "Elara Moonshadow",    "Elf",        "Wizard",     "Neutral Good",    "Major NPC"),
    ("shadowking","The Shadow King",     "Wraith",     "Warlock",    "Lawful Evil",     "Big Bad Guy"),
    ("grickle",   "Grickle",             "Grick",      "Monster",    "Unaligned",       "Semi-Intelligent"),
]

LOCATIONS = [
    # (id, name, type)
    ("ironhold",     "Ironhold",         "city"),
    ("aethelgard",   "Aethelgard",       "country"),
]

def _char_loci(pid, info, races, rnd):
    """Generate 70 loci facts for a character."""
    cid, name, race, cls, align, role = info
    p = pid
    loci = []
    race_info = races[race]

    # ── 1-10: Basic identity ──
    basic = [
        ("name", name, "character's full name"),
        ("race", race, "fantasy race"),
        ("class", cls, "character class"),
        ("alignment", align, "moral alignment"),
        ("role", role, "narrative role"),
        ("level", str(rnd.randint(5, 12)), "character level"),
        ("background", rnd.choice(["soldier", "scholar", "criminal", "noble", "hermit", "urchin"]), "life background"),
        ("age", str(rnd.randint(25, 200)), "age in years"),
        ("height", f"{rnd.randint(50,75)} inches", "height"),
        ("weight", f"{rnd.randint(120,250)} lbs", "weight"),
    ]
    for k, v, w in basic:
        loci.append({"project": p, "key": f"{cid}_{k}", "value": v, "why": w})

    # ── 11-16: Ability scores ──
    stats = {"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8}
    # vary per character
    for s in ["str", "dex", "con", "int", "wis", "cha"]:
        v = max(6, min(20, stats[s] + rnd.randint(-3, 3)))
        loci.append({"project": p, "key": f"{cid}_stat_{s}", "value": str(v), "why": f"{s.upper()} ability score"})
        mod = (v - 10) // 2
        loci.append({"project": p, "key": f"{cid}_stat_{s}_mod", "value": f"{mod:+d}", "why": f"{s.upper()} modifier"})

    # ── 22-30: Skills ──
    skills = ["athletics", "acrobatics", "stealth", "perception", "investigation", "survival",
              "intimidation", "persuasion", "deception", "arcana", "history", "religion",
              "medicine", "nature", "animal_handling", "insight"]
    for sk in skills[:9]:
        v = rnd.randint(4, 14)
        loci.append({"project": p, "key": f"{cid}_skill_{sk}", "value": f"+{v}", "why": f"{sk} skill bonus"})

    # ── 31-40: Equipment ──
    equip_slots = [
        ("weapon_1", rnd.choice(["battleaxe", "longsword", "staff", "dagger", "mace", "warhammer"]), "primary weapon"),
        ("weapon_2", rnd.choice(["handaxe", "shortsword", "knife", "sling", "whip", "club"]), "secondary weapon"),
        ("armor", rnd.choice(["plate mail", "chain mail", "leather armor", "studded leather", "robe"]), "worn armor"),
        ("shield", rnd.choice(["wooden shield", "metal shield", "buckler", "none"]), "carried shield"),
        ("ranged", rnd.choice(["shortbow", "crossbow", "longbow", "sling", "javelin", "none"]), "ranged weapon"),
        ("ammo", f"{rnd.randint(10,40)} arrows/bolts", "ammunition supply"),
        ("potion_1", "potion of healing", "healing potion"),
        ("potion_2", rnd.choice(["potion of invisibility", "potion of growth", "potion of clairvoyance", "potion of resistance"]), "special potion"),
        ("tool", rnd.choice(["thieves' tools", "smith's tools", "herbalism kit", "disguise kit", "musical instrument"]), "tool kit"),
        ("gold", f"{rnd.randint(50,500)} gp", "carried gold"),
    ]
    for k, v, w in equip_slots:
        loci.append({"project": p, "key": f"{cid}_{k}", "value": v, "why": w})

    # ── 41-48: Abilities / spells ──
    abilities = [
        ("ability_1", rnd.choice(["second wind", "action surge", "sneak attack", "wild shape", "channel divinity", "rage"]), "class ability"),
        ("ability_2", rnd.choice(["cunning action", "evasion", "indomitable", "metamagic", "divine smite", "reckless attack"]), "class ability"),
        ("ability_3", rnd.choice(["extra attack", "uncanny dodge", "counter spell", "dispel magic", "shield", "misty step"]), "combat ability"),
        ("spell_1", rnd.choice(["magic missile", "fireball", "cure wounds", "bless", "hex", "darkness"]), "known spell"),
        ("spell_2", rnd.choice(["invisibility", "fly", "haste", "slow", "polymorph", "blight"]), "known spell"),
        ("spell_3", rnd.choice(["lightning bolt", "ice storm", "death ward", "revivify", "banishment", "dimension door"]), "known spell"),
        ("feat_1", rnd.choice(["sentinel", "tough", "mobile", "alert", "war caster", "sharpshooter"]), "character feat"),
        ("feat_2", rnd.choice(["heavy armor master", "defensive duelist", "polearm master", "crossbow expert", "dual wielder", "tough"]), "character feat"),
    ]
    for k, v, w in abilities:
        loci.append({"project": p, "key": f"{cid}_{k}", "value": v, "why": w})

    # ── 49-56: Faction / deity / location / relationships ──
    factions = ["Iron Guard", "Silverwood Circle", "Crown Loyalists", "Free Traders",
                "Shadow Syndicate", "Cult of the Black Flame", "Emerald Enclave"]
    deities = ["Moradin", "Corellon", "Tyr", "Tymora", "Asmodeus", "Gruumsh", "Zehir"]
    loci.append({"project": p, "key": f"{cid}_faction", "value": rnd.choice(factions), "why": "faction affiliation"})
    loci.append({"project": p, "key": f"{cid}_deity", "value": rnd.choice(deities), "why": "worshipped deity"})
    loci.append({"project": p, "key": f"{cid}_location", "value": rnd.choice([loc[1] for loc in LOCATIONS]), "why": "current location"})
    loci.append({"project": p, "key": f"{cid}_homeland", "value": race_info["homeland"], "why": "ancestral homeland"})
    # Relationships
    others = [info2 for info2 in CHARACTERS if info2[0] != cid]
    for i in range(3):
        other = rnd.choice(others)
        rel = rnd.choice(["ally", "enemy", "contact", "rival"])
        loci.append({"project": p, "key": f"{cid}_rel_{i}", "value": f"{other[1]} ({rel})", "why": f"{rel} relationship"})

    # ── 59-66: Backstory / lore ──
    backstories = [
        f"{name} was born in {race_info['homeland']} to a family of {rnd.choice(['smiths', 'herders', 'merchants', 'scholars', 'warriors'])}.",
        f"As a youth, {name} {rnd.choice(['survived a great fire', 'fought in a border skirmish', 'studied under a master', 'stole from a noble', 'sailed across the sea'])}.",
        f"{name} {rnd.choice(['discovered a hidden artifact', 'made a pact with a dark entity', 'swore an oath to a fallen friend', 'uncovered a conspiracy', 'broke a family curse'])}.",
        f"Now {name} seeks {rnd.choice(['revenge', 'redemption', 'knowledge', 'power', 'peace', 'justice'])}.",
    ]
    for i, bs in enumerate(backstories):
        loci.append({"project": p, "key": f"{cid}_backstory_{i}", "value": bs, "why": "personal history"})

    # ── 66-70: Quests / goals ──
    quests = [
        (f"{cid}_quest_1", f"Find the {rnd.choice(['Sunken Temple', 'Lost Mine', 'Crystal Cave', 'Hidden Vault'])}", "current quest"),
        (f"{cid}_quest_2", f"Defeat {rnd.choice(['the Shadow King', 'the Crimson Lord', 'the Ashen One', 'the Void Walker'])}", "primary goal"),
        (f"{cid}_ideal", rnd.choice(["justice", "freedom", "power", "knowledge", "honor", "greed"]), "core ideal"),
        (f"{cid}_flaw", rnd.choice(["arrogance", "cowardice", "greed", "wrath", "vanity", "naivety"]), "character flaw"),
        (f"{cid}_bond", rnd.choice(["family", "comrades", "homeland", "faith", "oath", "treasure"]), "strong bond"),
    ]
    for k, v, w in quests:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    # Pad to exactly 70 if needed
    used_notes = set()
    while len([x for x in loci if x["project"] == p]) < 70:
        nid = rnd.randint(100, 999)
        while nid in used_notes:
            nid = rnd.randint(100, 999)
        used_notes.add(nid)
        extra_k = f"{cid}_note_{nid}"
        extra_v = rnd.choice(["trained in heavy armor", "speaks draconic", "scarred from battle",
                              "carries a lucky charm", "owes a life debt", "has a secret identity"])
        loci.append({"project": p, "key": extra_k, "value": extra_v, "why": "notable detail"})

    return [d for d in loci if d["project"] == p][:70]

def _char_memory(pid, info, races, rnd):
    """Generate 20 short + 40 long + 10 near memory entries for a character."""
    cid, name, race, cls, align, role = info
    race_info = races[race]
    homeland = race_info["homeland"]
    p = pid
    memory = []

    # ── 20 SHORT: recent events ──
    short_events = [
        f"{name} survived an ambush near {homeland} last week.",
        f"{name} discovered a hidden passage in the {rnd.choice(['old ruins', 'deep caves', 'abandoned keep'])}.",
        f"A traveling merchant told {name} of a rising threat in the {rnd.choice(['east', 'north', 'south', 'west'])}.",
        f"{name} trained under {rnd.choice(['a grizzled veteran', 'a wandering monk', 'an ancient sage'])} for three days.",
        f"{name} helped defend {homeland} from a {rnd.choice(['goblin raid', 'orc warband', 'bandit attack'])}.",
        f"A strange omen — {rnd.choice(['a red comet', 'a double moon', 'a flock of ravens', 'a blood-red sunrise'])} — was seen over {homeland}.",
        f"{name} received a coded message from an old contact.",
        f"During a storm, {name} lost a valuable {rnd.choice(['heirloom', 'map', 'weapon', 'potion'])} in the river.",
        f"{name} overheard a plot to {rnd.choice(['assassinate the ruler', 'poison the well', 'burn the granary'])} in {homeland}.",
        f"A {rnd.choice(['dwarf', 'elf', 'gnome', 'halfling'])} sage visited {name} seeking aid against the Shadow King.",
        f"{name} found a wounded {rnd.choice(['griffin', 'eagle', 'wolf', 'stag'])} and nursed it back to health.",
        f"The local {rnd.choice(['guild', 'temple', 'garrison'])} offered a bounty for a dangerous {rnd.choice(['monster', 'outlaw', 'necromancer'])}.",
        f"{name} participated in a {rnd.choice(['moot', 'council', 'hearing', 'ritual'])} at {homeland}.",
        f"A rival challenged {name} to a {rnd.choice(['duel', 'contest', 'race', 'debate'])} and lost.",
        f"{name} deciphered an old prophecy mentioning the {rnd.choice(['Shadow King', 'Sunken Temple', 'Crystal Crown'])}.",
        f"A mysterious fog rolled into {homeland} for three nights straight.",
        f"{name} was {rnd.choice(['blessed', 'cursed', 'marked', 'chosen'])} by a wandering {rnd.choice(['priest', 'druid', 'warlock', 'seer'])}.",
        f"{name} unearthed a {rnd.choice(['mosaic', 'statue', 'altar', 'tomb'])} from an forgotten era.",
        f"A {rnd.choice(['fire', 'flood', 'quake', 'blight'])} damaged part of {homeland}.",
        f"{name} swore a temporary truce with a former enemy.",
    ]
    for i, ev in enumerate(short_events[:20]):
        memory.append({"tier": "short", "ref": f"{cid}_short_{i:02d}", "topic": "recent",
                       "content": ev})

    # ── 40 LONG: lore (character lore, racial lore, homeland lore) ──
    char_lore = [
        f"{name} was born under the sign of the {rnd.choice(['Dragon', 'Wolf', 'Raven', 'Lion', 'Serpent'])}.",
        f"The {race} people believe {name} is a {rnd.choice(['chosen one', 'cursed soul', 'reborn hero', 'prophesied child'])}.",
        f"{name}'s {rnd.choice(['father', 'mother', 'mentor', 'sibling'])} taught them the ways of the {cls}.",
        f"In {name}'s youth, they {rnd.choice(['tamed a wild beast', 'survived a plague', 'climbed a frozen peak', 'sailed the abyssal sea'])}.",
        f"{name} carries a {rnd.choice(['birthmark', 'tattoo', 'scar', 'amulet'])} shaped like a {rnd.choice(['star', 'moon', 'crown', 'skull'])}.",
        f"A {rnd.choice(['dragon', 'lich', 'god', 'fey lord'])} once offered {name} a bargain — power in exchange for years of life.",
        f"{name} once {rnd.choice(['saved a village', 'burned a fortress', 'betrayed a comrade', 'broke a siege'])}.",
        f"The {race} see {name} as a {rnd.choice(['bridge', 'outcast', 'hero', 'warning'])} between the old world and the new.",
        f"{name} learned the {rnd.choice(['sword', 'staff', 'bow', 'tome'])} from a {rnd.choice(['wandering master', 'secret order', 'ancient text', 'dream vision'])}.",
        f"In {name}'s past, they {rnd.choice(['crossed the burning desert', 'sailed the frozen sea', "climbed the world's edge", 'descended into the abyss'])}.",
        f"A {rnd.choice(['crown', 'ring', 'staff', 'mask'])} was {rnd.choice(['given to', 'taken from', 'hidden by', 'forged for'])} {name}.",
        f"{name} once {rnd.choice(['defeated a champion', 'outwitted a trickster', 'healed a plague', 'uncovered a lie'])}.",
        f"The {race} ancestors spoke of {name} in {rnd.choice(['dreams', 'visions', 'prophecies', 'omens'])}.",
        f"{name} carries a {rnd.choice(['blessing', 'curse', 'mark', 'gift'])} from the {rnd.choice(['gods', 'fey', 'ancients', 'elements'])}.",
        f"A {rnd.choice(['battle', 'journey', 'ritual', 'betrayal'])} in {name}'s youth left a {rnd.choice(['scar', 'debt', 'grudge', 'vow'])}.",
        f"{name} {rnd.choice(['discovered a hidden truth', 'made a powerful enemy', 'found a lost relic', 'broke an ancient oath'])}.",
        f"The {race} elders {rnd.choice(['warn', 'trust', 'fear', 'honor'])} {name} for their {rnd.choice(['courage', 'wisdom', 'power', 'mercy'])}.",
        f"{name} {rnd.choice(['wields a legendary weapon', 'carries a forbidden spell', 'knows a secret path', 'holds a royal claim'])}.",
    ]
    racial_lore = [
        f"The {race} race originated in {race_info['homeland']} long before the First Age.",
        f"{race} tradition holds that their creator, {rnd.choice(['Moradin', 'Corellon', 'the Earth Mother', 'the Storm Lord'])}, shaped them from {rnd.choice(['stone', 'wood', 'starlight', 'clay', 'shadow'])}.",
        f"The {race} once fought a great war against the {rnd.choice(['Dragon Empire', 'Underdark Horde', 'Fey Court', 'Abyssal Legion'])}.",
        f"A sacred {rnd.choice(['mountain', 'forest', 'temple', 'cave'])} in {race_info['homeland']} is the spiritual heart of the {race}.",
        f"{race} smiths are known for crafting {rnd.choice(['runed weapons', 'starlight blades', 'thunder hammers', 'crystal armor'])}.",
        f"The {race} calendar marks {rnd.randint(4,8)} seasons, each tied to a {rnd.choice(['elemental', 'celestial', 'ancestral'])} festival.",
        f"{race} legends tell of a {rnd.choice(['dragon', 'titan', 'god', 'demon'])} who once ruled {race_info['homeland']}.",
        f"The {race} tongue has {rnd.choice(['no word for betrayal', 'seven words for courage', 'a silent form of prayer', 'ancient runic script'])}.",
        f"An ancient {race} prophecy says: '{rnd.choice(['When the mountain burns', 'When the forest weeps', 'When the star falls'])} ...'",
        f"The {race} once allied with the {rnd.choice(['Elves', 'Dwarves', 'Humans', 'Gnomes'])} to defeat the {rnd.choice(['Orc Horde', 'Undead Legion', 'Demon Invasion'])}.",
    ]
    homeland_lore = [
        f"{race_info['homeland']} was founded by {rnd.choice(['a legendary hero', 'an exiled king', 'a wandering tribe', 'a divine oracle'])}.",
        f"The {rnd.choice(['first stone', 'first tree', 'first gate', 'first throne'])} of {race_info['homeland']} was laid in the Age of {rnd.choice(['Ancients', 'Stars', 'Blood', 'Bones'])}.",
        f"A {rnd.choice(['dragon', 'behemoth', 'leviathan', 'titan'])} sleeps beneath {race_info['homeland']}.",
        f"The {rnd.choice(['wells', 'forests', 'mines', 'walls'])} of {race_info['homeland']} are said to be blessed by {rnd.choice(['the gods', 'the fey', 'the ancients', 'the elements'])}.",
        f"Every {rnd.randint(7, 50)} years, a great {rnd.choice(['fair', 'tournament', 'pilgrimage', 'muster'])} is held in {race_info['homeland']}.",
        f"{race_info['homeland']} exports {rnd.choice(['fine ore', 'rare timber', 'enchanted goods', 'precious gems'])} to the rest of Aethelgard.",
        f"A {rnd.choice(['curse', 'blessing', 'prophecy', 'riddle'])} hangs over {race_info['homeland']} — '{rnd.choice(['the stones remember', 'the trees whisper', 'the wind carries secrets'])}'.",
        f"The {rnd.choice(['oldest', 'tallest', 'deepest', 'most sacred'])} part of {race_info['homeland']} is the {rnd.choice(['Great Hall', 'Silver Glade', 'Iron Foundry', 'Star Tower'])}.",
        f"{race_info['homeland']} was once besieged by the {rnd.choice(['Shadow Legion', 'Bone Horde', 'Iron Pact', 'Crimson Court'])}.",
        f"A {rnd.choice(['hidden vault', 'secret garden', 'forgotten library', 'ancient forge'])} lies beneath {race_info['homeland']}.",
    ]
    all_lore = char_lore + racial_lore + homeland_lore
    for i, lr in enumerate(all_lore[:40]):
        memory.append({"tier": "long", "ref": f"{cid}_long_{i:02d}", "topic": "lore",
                       "content": lr})

    # ── 10 NEAR: upcoming events ──
    near_events = [
        f"{name} plans to {rnd.choice(['explore the Sunken Temple', "raid the Shadow King's fortress", 'escort a caravan to Crossroads', 'study under a master in Silverwood'])} next week.",
        f"A {rnd.choice(['prophecy', 'vision', 'dream', 'omen'])} warns that {rnd.choice(['the Shadow King will rise', 'a great flood will come', 'the dead will walk', 'a star will fall'])} soon.",
        f"{name} must {rnd.choice(['gather allies', 'forge a weapon', 'decipher a map', 'prepare a ritual'])} before the next full moon.",
        f"A {rnd.choice(['noble', 'guild', 'temple', 'crown'])} has summoned {name} to {rnd.choice(['Crossroads', 'Ironhold', 'Silverwood'])} for a parley.",
        f"{name} suspects a {rnd.choice(['betrayal', 'ambush', 'conspiracy', 'curse'])} brewing in {race_info['homeland']}.",
        f"The {rnd.choice(['season of frost', 'season of flame', 'season of mists', 'season of stars'])} approaches, bringing {rnd.choice(['danger', 'blessing', 'change', 'revelation'])}.",
        f"{name} was offered a {rnd.choice(['map', 'key', 'spell', 'title'])} in exchange for a dangerous quest.",
        f"A rival {rnd.choice(['challenged', 'threatened', 'bribed', 'courted'])} {name}, forcing a difficult choice.",
        f"{name} will attend a {rnd.choice(['funeral', 'coronation', 'wedding', 'festival'])} in {rnd.choice(['Ironhold', 'Silverwood', 'Crossroads'])}.",
        f"A mysterious {rnd.choice(['package', 'letter', 'artifact', 'messenger'])} arrived for {name}, hinting at a greater plot.",
    ]
    for i, ne in enumerate(near_events[:10]):
        memory.append({"tier": "near", "ref": f"{cid}_near_{i:02d}", "topic": "upcoming",
                       "content": ne})

    return memory

def _char_questions(pid, info, races, rnd, memory, loci):
    """Generate 30 questions for a character — loci, memory, and corpus types."""
    cid, name, race, cls, align, role = info
    race_info = races[race]
    p = pid
    qs = []

    # Build lookup from loci for value extraction
    loc_lookup = {}
    for d in loci:
        if d.get('key','').startswith(cid):
            loc_lookup[d['key']] = d.get('value', '')

    # ── 13 Loci questions — expect_content only where value is non-obvious ──
    # Numeric stats and race/class are uniquely identified by expect_key alone;
    # keep expect_content for alignment, homeland, faction, deity.
    loci_qs = [
        (f"what race is {name}?", f"{cid}_race",          False),
        (f"what class is {name}?", f"{cid}_class",        False),
        (f"what alignment does {name} have?", f"{cid}_alignment", True),
        (f"what level is {name}?", f"{cid}_level",        False),
        (f"where is {name}'s homeland?", f"{cid}_homeland", True),
        (f"what faction does {name} follow?", f"{cid}_faction",  True),
        (f"what deity does {name} worship?", f"{cid}_deity",    True),
        (f"what str score does {name} have?", f"{cid}_stat_str", False),
        (f"what dex score does {name} have?", f"{cid}_stat_dex", False),
        (f"what con score does {name} have?", f"{cid}_stat_con", False),
        (f"what int score does {name} have?", f"{cid}_stat_int", False),
        (f"what wis score does {name} have?", f"{cid}_stat_wis", False),
        (f"what cha score does {name} have?", f"{cid}_stat_cha", False),
    ]
    for query, key, keep_val in loci_qs:
        val = loc_lookup.get(key, '')
        ec = [val] if (keep_val and val) else []
        qs.append({"asks": "loci", "query": query, "expect_key": [f"{p}/{key}"],
                   "expect_content": ec, "hops": 1})

    # ── 6 Memory questions — query disambiguated by a phrase pulled FROM the target row ──
    mem_refs = [f"{cid}_short_00", f"{cid}_short_01", f"{cid}_long_10", f"{cid}_long_20", f"{cid}_near_00", f"{cid}_near_01"]
    mem_stems = [
        f"what happened to {name} recently",
        f"what did {name} do lately",
        f"what is the {race} lore about {name}",
        f"what is known of {name}'s past",
        f"what does {name} intend to do",
        f"what lies ahead for {name}",
    ]
    # A ref -> content map, so we can pull BOTH the target text and its siblings.
    by_ref = {m["ref"]: (m.get("content", "") or m.get("intent", "")) for m in memory}
    for i, ref in enumerate(mem_refs):
        target = by_ref.get(ref, "")
        # Siblings = every OTHER memory row in this store. The discriminator must beat
        # ALL of them, not just the ones in the same tier -- the store holds all 70.
        siblings = [t for r, t in by_ref.items() if r != ref]
        disc = _discriminator(target, siblings, n=4)
        # Query = a natural stem + the distinctive phrase from the target itself. Not the
        # answer verbatim (that is a lexical gimme), not a generic category (that is the
        # ambiguity we just fixed) -- the specific words that only this row carries.
        query = f"{mem_stems[i]} — {disc}" if disc else mem_stems[i]
        ec_val = target[:80] if target else ""
        qs.append({"asks": "memory", "query": query, "expect_ref": [ref],
                   "expect_content": [ec_val] if ec_val else [], "hops": 1})

    # ── 14 Corpus questions — add expect_key, use relevant content ──
    # Build key sets for reuse
    basic_keys  = [f"{p}/{cid}_race", f"{p}/{cid}_class", f"{p}/{cid}_alignment"]
    identity_keys = basic_keys + [f"{p}/{cid}_level", f"{p}/{cid}_background"]
    combat_keys = [f"{p}/{cid}_weapon_1", f"{p}/{cid}_weapon_2", f"{p}/{cid}_armor",
                   f"{p}/{cid}_shield", f"{p}/{cid}_ranged"]
    equip_keys  = combat_keys + [f"{p}/{cid}_potion_1", f"{p}/{cid}_potion_2",
                                 f"{p}/{cid}_tool", f"{p}/{cid}_gold"]
    ability_keys = [f"{p}/{cid}_ability_1", f"{p}/{cid}_ability_2", f"{p}/{cid}_ability_3",
                    f"{p}/{cid}_spell_1", f"{p}/{cid}_spell_2", f"{p}/{cid}_spell_3",
                    f"{p}/{cid}_feat_1", f"{p}/{cid}_feat_2"]
    story_keys  = [f"{p}/{cid}_backstory_0", f"{p}/{cid}_backstory_1",
                   f"{p}/{cid}_backstory_2", f"{p}/{cid}_backstory_3"]
    rel_keys    = [f"{p}/{cid}_rel_0", f"{p}/{cid}_rel_1", f"{p}/{cid}_rel_2"]
    quest_keys  = [f"{p}/{cid}_quest_1", f"{p}/{cid}_quest_2",
                   f"{p}/{cid}_ideal", f"{p}/{cid}_bond", f"{p}/{cid}_flaw"]

    # Each entry: (query, content_list, key_list). Queries carry the DISCRIMINATING
    # noun that singles the keys out against a 70-doc store -- a bare "tell me about X"
    # ties every doc; "what weapons and armor" lands on the combat keys. Small key sets:
    # a 5-key expectation against a broad query is 5 ways to look ambiguous at once.
    corpus_qs = [
        (f"what race, class, and alignment is {name}?",
         [race, cls, align], basic_keys),
        (f"what homeland and faction does {name} belong to?",
         [race_info["homeland"]], [f"{p}/{cid}_homeland", f"{p}/{cid}_faction"]),
        (f"what weapons and armor does {name} carry?",
         [], combat_keys),
        (f"what ability scores does {name} have — strength, dexterity, constitution?",
         [], [f"{p}/{cid}_stat_str", f"{p}/{cid}_stat_dex", f"{p}/{cid}_stat_con"]),
        (f"what role and background does {name} hold in the realm?",
         [role], [f"{p}/{cid}_role", f"{p}/{cid}_background", f"{p}/{cid}_level"]),
        (f"what backstory and early life shaped {name}?",
         [], story_keys),
        (f"what abilities, spells, and feats does {name} know?",
         [], ability_keys),
        (f"what potions, tools, and gold does {name} carry?",
         [], [f"{p}/{cid}_potion_1", f"{p}/{cid}_potion_2", f"{p}/{cid}_tool", f"{p}/{cid}_gold"]),
        (f"what allies, enemies, and rivals does {name} have?",
         [], rel_keys),
        (f"what quests, ideals, bonds, and flaws drive {name}?",
         [], quest_keys),
    ]
    for query, content, keys in corpus_qs:
        qs.append({"asks": "corpus", "query": query,
                   "expect_content": content, "expect_key": keys, "hops": 1})

    return qs

def _loc_loci(pid, info, rnd):
    """Generate 70 loci facts for a location."""
    lid, lname, ltype = info
    p = pid
    loci = []

    # ── 1-10: Basic identity ──
    basic = [
        ("name", lname, "location name"),
        ("type", ltype, "location type"),
        ("population", f"{rnd.randint(500, 50000)}", "population count"),
        ("size", rnd.choice(["small", "medium", "large", "vast"]), "relative size"),
        ("climate", rnd.choice(["temperate", "cold", "arid", "tropical", "mountain"]), "climate zone"),
        ("terrain", rnd.choice(["plains", "forest", "mountains", "swamp", "coast", "hills"]), "primary terrain"),
        ("government", rnd.choice(["monarchy", "council", "oligarchy", "theocracy", "autonomy"]), "government type"),
        ("defense", rnd.choice(["city watch", "militia", "standing army", "mercenary guild"]), "defense force"),
        ("economy", rnd.choice(["trade hub", "mining", "agriculture", "artisan", "religious"]), "economic base"),
        ("language", rnd.choice(["Common", "Dwarvish", "Elvish", "Gnomish", "Draconic"]), "primary language"),
    ]
    for k, v, w in basic:
        loci.append({"project": p, "key": f"{lid}_{k}", "value": v, "why": w})

    # ── 11-20: Geography ──
    geography = [
        ("region", f"the {rnd.choice(['north', 'south', 'east', 'west'])} of Aethelgard", "geographic region"),
        ("river", rnd.choice(["the Whitewater", "the Silverflow", "the Deeprun", "the Winding"]), "nearby river"),
        ("mountain", rnd.choice(["the Spine", "the Crown Range", "the Iron Peaks", "the Misthorns"]), "nearby mountain range"),
        ("forest", rnd.choice(["the Greatwood", "the Silverwood", "the Darkholt", "the Weald"]), "nearby forest"),
        ("coast", rnd.choice(["the Sunken Coast", "the Jagged Shore", "the Pearl Strand", "none"]), "coastline"),
        ("elevation", f"{rnd.randint(100, 5000)} ft", "average elevation"),
        ("area", f"{rnd.randint(50, 5000)} sq mi", "total area"),
        ("biome", rnd.choice(["temperate forest", "grassland", "taiga", "desert", "wetland"]), "primary biome"),
        ("natural_port", rnd.choice(["yes", "no", "seasonal"]), "natural harbor access"),
        ("resource_1", rnd.choice(["iron ore", "gold", "timber", "stone", "gemstones", "salt"]), "primary natural resource"),
    ]
    for k, v, w in geography:
        loci.append({"project": p, "key": f"{lid}_{k}", "value": v, "why": w})

    # ── 21-30: Districts & buildings ──
    districts = [
        (f"{lid}_district_1", rnd.choice(["Merchant Quarter", "Temple Ward", "Craftsman Row", "Noble Enclave"]), "city district"),
        (f"{lid}_district_2", rnd.choice(["Dock Ward", "Garrison District", "Market Square", "Scholar's Green"]), "city district"),
        (f"{lid}_district_3", rnd.choice(["Artisan Alley", "Temple Heights", "Foreign Quarter", "The Warrens"]), "city district"),
    ]
    for k, v, w in districts:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    buildings = [
        (f"{lid}_bldg_1", rnd.choice(["Grand Keep", "Town Hall", "Governor's Palace"]), "seat of power"),
        (f"{lid}_bldg_2", rnd.choice(["Temple of Light", "Shrine of the Ancients", "Chapel of Storms"]), "place of worship"),
        (f"{lid}_bldg_3", rnd.choice(["The Gilded Tankard", "The Rusty Anchor", "The Silver Flagon"]), "popular inn"),
        (f"{lid}_bldg_4", rnd.choice(["Iron Market", "Crystal Exchange", "Timber Bazaar"]), "main market"),
        (f"{lid}_bldg_5", rnd.choice(["City Garrison", "Watchtower", "Gate Fortress"]), "defensive structure"),
    ]
    for k, v, w in buildings:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    # ── 31-40: Factions & economy ──
    factions_present = [
        (f"{lid}_faction_1", rnd.choice(["Iron Guard", "Silverwood Circle", "Free Traders", "Shadow Syndicate"]), "active faction"),
        (f"{lid}_faction_2", rnd.choice(["Crown Loyalists", "Emerald Enclave", "Cult of the Black Flame", "Miner's Union"]), "active faction"),
        (f"{lid}_faction_3", rnd.choice(["Artisan Guild", "Merchant Council", "Watch Brotherhood", "Sage Academy"]), "active faction"),
    ]
    for k, v, w in factions_present:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    exports = [
        (f"{lid}_export_1", rnd.choice(["ore", "timber", "grain", "gems", "textiles", "weapons"]), "primary export"),
        (f"{lid}_export_2", rnd.choice(["ale", "cheese", "pottery", "leather", "spices", "wine"]), "secondary export"),
        (f"{lid}_export_3", rnd.choice(["magic scrolls", "enchanted tools", "rare herbs", "artwork"]), "tertiary export"),
    ]
    for k, v, w in exports:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    imports = [
        (f"{lid}_import_1", rnd.choice(["iron", "salt", "silk", "glass", "oil", "incense"]), "primary import"),
        (f"{lid}_import_2", rnd.choice(["spices", "wine", "jewelry", "books", "armor", "potions"]), "secondary import"),
    ]
    for k, v, w in imports:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    # ── 41-50: History / lore ──
    histories = [
        f"{lname} was founded by {rnd.choice(['a legendary hero', 'an exiled king', 'a wandering tribe', 'a divine oracle'])} in the Age of {rnd.choice(['Ancients', 'Stars', 'Blood', 'Bones'])}.",
        f"The {rnd.choice(['first stone', 'first tree', 'first gate', 'first throne'])} of {lname} was laid in the {rnd.choice(['1st', '2nd', '3rd', '5th'])} century.",
        f"{lname} withstood a siege by the {rnd.choice(['Shadow Legion', 'Bone Horde', 'Iron Pact', 'Crimson Court'])} for {rnd.randint(30, 300)} days.",
        f"A {rnd.choice(['dragon', 'behemoth', 'leviathan', 'titan'])} once attacked {lname}, destroying the {rnd.choice(['east gate', 'main temple', 'north wall', 'royal quarter'])}.",
        f"The {rnd.choice(['Great Fire', 'Plague of Shadows', 'Winter of Sorrow', 'Revolt of the Guilds'])} devastated {lname}.",
        f"{lname} was rebuilt under {rnd.choice(['a wise queen', 'a council of elders', 'a dwarven engineer', 'an elven architect'])}.",
        f"A {rnd.choice(['hidden vault', 'secret garden', 'forgotten library', 'ancient forge'])} lies beneath {lname}.",
        f"{lname} is protected by a {rnd.choice(['blessing', 'curse', 'ward', 'prophecy'])} from the {rnd.choice(['gods', 'fey', 'ancients', 'elements'])}.",
        f"The {rnd.choice(['oldest', 'tallest', 'deepest', 'most sacred'])} part of {lname} is the {rnd.choice(['Great Hall', 'Silver Glade', 'Iron Foundry', 'Star Tower'])}.",
        f"A {rnd.choice(['treaty', 'alliance', 'pact', 'charter'])} was signed in {lname} that shaped the fate of Aethelgard.",
    ]
    for i, h in enumerate(histories[:10]):
        loci.append({"project": p, "key": f"{lid}_history_{i}", "value": h, "why": "historical lore"})

    # ── 51-60: Notable features, dangers, races, religions ──
    features = [
        (f"{lid}_notable_1", rnd.choice(["ancient standing stones", "a singing fountain", "a living wall of thorns", "a bottomless well"]), "notable feature"),
        (f"{lid}_notable_2", rnd.choice(["a giant statue of a forgotten king", "a portal to the Feywild", "a tree that bears silver fruit", "a bridge of crystal"]), "notable feature"),
        (f"{lid}_danger_1", rnd.choice(["goblin tunnels", "giant spiders", "undead crypts", "shifting sands"]), "local danger"),
        (f"{lid}_danger_2", rnd.choice(["quicksand pits", "poisonous gas vents", "bandit hideouts", "cursed ruins"]), "local danger"),
        (f"{lid}_race_1", rnd.choice(["Dwarf", "Elf", "Human", "Halfling", "Gnome"]), "common race"),
        (f"{lid}_race_2", rnd.choice(["Orc", "Tiefling", "Half-Elf", "Lizardfolk", "Goblin"]), "minority race"),
        (f"{lid}_religion_1", rnd.choice(["Temple of Tyr", "Shrine of Corellon", "Altar of Moradin", "Chapel of Tymora"]), "primary religion"),
        (f"{lid}_religion_2", rnd.choice(["Cult of the Black Flame", "Druid Circle", "Ancestor Worship", "Star Faith"]), "secondary religion"),
        (f"{lid}_ruler", rnd.choice(["King Aldric", "Queen Seraphina", "Lord Tamsin", "Council of Elders"]), "current ruler"),
        (f"{lid}_guard", f"{rnd.randint(50, 500)} guards", "garrison size"),
    ]
    for k, v, w in features:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    # ── 61-66: Festivals, trade, notable events ──
    extra = [
        (f"{lid}_festival", rnd.choice(["Feast of the Harvest", "Night of Stars", "Dragon's Wake", "Moonfall"]), "annual festival"),
        (f"{lid}_trade_route", rnd.choice(["the Northern Road", "the Silver Pass", "the Coast Road", "the Old Dwarf Way"]), "major trade route"),
        (f"{lid}_ally", rnd.choice(["Ironhold", "Silverwood", "Crossroads", "the Free Cities"]), "allied settlement"),
        (f"{lid}_enemy", rnd.choice(["the Orc Wastes", "the Underdark", "the Shadow Lands", "the Sunken Temple"]), "hostile neighbor"),
        (f"{lid}_status", rnd.choice(["prosperous", "declining", "besieged", "neutral", "booming"]), "current status"),
    ]
    for k, v, w in extra:
        loci.append({"project": p, "key": k, "value": v, "why": w})

    # Pad to 70
    used_notes = set()
    while len([x for x in loci if x["project"] == p]) < 70:
        nid = rnd.randint(100, 999)
        while nid in used_notes:
            nid = rnd.randint(100, 999)
        used_notes.add(nid)
        extra_k = f"{lid}_extra_{nid}"
        extra_v = rnd.choice(["ancient aqueduct", "hidden catacombs", "royal hunting grounds",
                              "enchanted grove", "secret smuggler tunnels", "abandoned quarry"])
        loci.append({"project": p, "key": extra_k, "value": extra_v, "why": "notable detail"})

    return [d for d in loci if d["project"] == p][:70]

def _loc_memory(pid, info, rnd):
    """Generate 20 short + 40 long + 10 near memory entries for a location."""
    lid, lname, ltype = info
    p = pid
    memory = []

    # ── 20 SHORT: recent events ──
    short_events = [
        f"A {rnd.choice(['caravan', 'pilgrimage', 'military column', 'merchant fleet'])} arrived at {lname} last week.",
        f"{lname} suffered a {rnd.choice(['fire', 'flood', 'earthquake', 'plague'])} in the {rnd.choice(['east quarter', 'market district', 'docks', 'temple ward'])}.",
        f"A {rnd.choice(['noble', 'guild master', 'prophet', 'general'])} was {rnd.choice(['assassinated', 'kidnapped', 'crowned', 'exiled'])} in {lname}.",
        f"{lname} held a {rnd.choice(['grand festival', 'military parade', 'holy ceremony', 'public trial'])} last {rnd.choice(['week', 'month', 'season'])}.",
        f"A {rnd.choice(['dragon', 'giant', 'beast', 'undead horde'])} was sighted near {lname}'s {rnd.choice(['walls', 'gates', 'fields', 'forests'])}.",
        f"The {rnd.choice(['city council', 'ruling lord', 'temple elders'])} of {lname} passed a new {rnd.choice(['trade law', 'tax code', 'defense levy', 'building code'])}.",
        f"A {rnd.choice(['fireball', 'meteor', 'strange light', 'dark cloud'])} was seen over {lname} at midnight.",
        f"{lname}'s {rnd.choice(['wells', 'granaries', 'treasury', 'armory'])} were {rnd.choice(['poisoned', 'raided', 'blessed', 'sealed'])}.",
        f"A {rnd.choice(['herald', 'messenger', 'prophet', 'spy'])} arrived from {rnd.choice(['the Shadow Lands', 'the Underdark', 'the Fey Court', 'the Iron Wastes'])}.",
        f"{lname} opened {rnd.choice(['a new gate', 'a rebuilt temple', 'a trade route', 'a military outpost'])}.",
        f"A {rnd.choice(['scholar', 'sage', 'alchemist', 'archeologist'])} discovered {rnd.choice(['ancient ruins', 'a hidden vault', 'a magical artifact', 'a fossilized dragon'])} near {lname}.",
        f"{lname} hosted a {rnd.choice(['summit', 'council', 'moot', 'tournament'])} of the neighboring realms.",
        f"A {rnd.choice(['blight', 'plague', 'curse', 'famine'])} struck {lname}'s {rnd.choice(['crops', 'livestock', 'water supply', 'population'])}.",
        f"The {rnd.choice(['garrison', 'watch', 'militia'])} of {lname} repelled a {rnd.choice(['goblin raid', 'orc warband', 'bandit attack', 'undead incursion'])}.",
        f"A {rnd.choice(['prophet', 'oracle', 'seer', 'astrologer'])} foretold a {rnd.choice(['great change', 'coming war', 'blessing', 'doom'])} for {lname}.",
        f"{lname} celebrated the {rnd.choice(['anniversary', 'centennial', 'jubilee'])} of its founding.",
        f"A {rnd.choice(['mine', 'quarry', 'forest', 'fishery'])} near {lname} was {rnd.choice(['exhausted', 'expanded', 'nationalized', 'cursed'])}.",
        f"{lname} received a {rnd.choice(['trade delegation', 'diplomatic envoy', 'religious pilgrimage', 'military reinforcement'])} from {rnd.choice(['Ironhold', 'Silverwood', 'Crossroads', 'the Crown'])}.",
        f"A {rnd.choice(['storm', 'blizzard', 'hurricane', 'sandstorm'])} battered {lname} for {rnd.randint(3, 7)} days.",
        f"{lname} {rnd.choice(['banned', 'legalized', 'subsidized', 'outlawed'])} {rnd.choice(['slavery', 'magic trade', 'goblin labor', 'necromancy'])}.",
    ]
    for i, ev in enumerate(short_events[:20]):
        memory.append({"tier": "short", "ref": f"{lid}_short_{i:02d}", "topic": "recent",
                       "content": ev})

    # ── 40 LONG: lore ──
    long_lore = [
        f"{lname} was carved from {rnd.choice(['living stone', 'ancient forest', 'the bones of a titan', 'starlight crystal'])}.",
        f"The {rnd.choice(['founder', 'first king', 'matriarch', 'architect'])} of {lname} was guided by a {rnd.choice(['dream', 'vision', 'prophecy', 'divine sign'])}.",
        f"{lname} is built atop the ruins of a {rnd.choice(['precursor civilization', "dragon's lair", "titan's tomb", 'fey court'])}.",
        f"A {rnd.choice(['blessing', 'curse', 'enchantment', 'ward'])} protects {lname} from {rnd.choice(['invaders', 'plague', 'magic', 'time'])}.",
        f"The {rnd.choice(['deepest mines', 'oldest trees', 'sacred springs', 'hidden libraries'])} of {lname} hold secrets of the {rnd.choice(['First Age', 'Dawn Era', 'Age of Ancients', 'Starfall'])}.",
        f"{lname} was once a {rnd.choice(['neutral ground', 'battlefield', 'trading post', 'prison colony'])} during the {rnd.choice(['Great War', 'Dragon War', 'Underdark Invasion', 'Fey Uprising'])}.",
        f"The {rnd.choice(['architecture', 'culture', 'traditions', 'laws'])} of {lname} were influenced by {rnd.choice(['Dwarven', 'Elven', 'Human', 'Gnomish'])} settlers.",
        f"A {rnd.choice(['dragon', 'phoenix', 'unicorn', 'kraken'])} was {rnd.choice(['tamed', 'defeated', 'befriended', 'worshipped'])} by the people of {lname}.",
        f"{lname}'s {rnd.choice(['motto', 'crest', 'banner', 'sigil'])} is '{rnd.choice(['Through Stone and Steel', 'By Leaf and Tide', 'In Light We Trust', 'Never Bow'])}'.",
        f"The {rnd.choice(['oldest family', 'founding guild', 'original temple', 'first garrison'])} of {lname} dates back {rnd.randint(300, 2000)} years.",
        f"{lname} was rebuilt after the {rnd.choice(['Great Fire', 'Plague Winter', 'Siege of Bones', "Dragon's Wrath"])}.",
        f"A {rnd.choice(['secret tunnel', 'hidden passage', 'forgotten door', 'magical portal'])} connects {lname} to {rnd.choice(['the Underdark', 'the Feywild', 'the Shadowfell', 'the Elemental Plane'])}.",
        f"The {rnd.choice(['wells', 'springs', 'rivers', 'canals'])} of {lname} are said to be {rnd.choice(['healing waters', 'blessed by spirits', 'poisoned by a curse', 'linked to the sea god'])}.",
        f"{lname} is home to a {rnd.choice(['famous academy', 'great library', 'renowned forge', 'sacred garden'])} that attracts scholars from across Aethelgard.",
        f"A {rnd.choice(['treaty', 'alliance', 'pact', 'accord'])} was signed in {lname} that ended the {rnd.choice(['Border War', 'Trade Dispute', 'Religious Schism', 'Succession Crisis'])}.",
        f"The {rnd.choice(['walls', 'gates', 'towers', 'bridges'])} of {lname} were built by {rnd.choice(['a dwarven king', 'an elven mage', 'a giant smith', 'a forgotten god'])}.",
        f"{lname} has a {rnd.choice(['hidden treasure', 'lost spell', 'secret weapon', 'forbidden knowledge'])} guarded by a {rnd.choice(['cryptic riddle', 'magical seal', 'divine oath', 'guardian entity'])}.",
        f"Every {rnd.randint(5, 100)} years, a {rnd.choice(['celestial alignment', 'planetary conjunction', 'spiritual eclipse', 'great migration'])} occurs over {lname}.",
        f"{lname} was visited by a {rnd.choice(['wandering god', 'dragon lord', 'fey monarch', 'celestial being'])} who {rnd.choice(['blessed the land', 'left a gift', 'cast a prophecy', 'imposed a geas'])}.",
        f"The {rnd.choice(['oldest inhabitant', 'longest-ruling lord', 'most famous sage', 'greatest hero'])} of {lname} is remembered in {rnd.choice(['songs', 'statues', 'holidays', 'mosaics'])}.",
        f"{lname} is known for its {rnd.choice(['soaring spires', 'deep vaults', 'ancient groves', 'crystal streams'])}.",
        f"A {rnd.choice(['dwarven', 'elven', 'human', 'gnomish'])} quarter of {lname} preserves traditions from the {rnd.choice(['First Age', 'Age of Exile', 'Dawn Era', 'Starfall'])}.",
        f"The {rnd.choice(['gates', 'walls', 'bridges', 'towers'])} of {lname} bear {rnd.choice(['runes', 'carvings', 'murals', 'tapestries'])} of the {rnd.choice(['founding', 'great siege', 'golden age', 'last battle'])}.",
        f"A {rnd.choice(['subterranean river', 'hidden canyon', 'floating garden', 'sunken courtyard'])} lies at the heart of {lname}.",
        f"{lname} was once ruled by a {rnd.choice(['dragon lord', 'fey queen', 'dwarven king', 'shadow council'])} before the {rnd.choice(['unification', 'rebellion', 'exile', 'pact'])}.",
        f"The {rnd.choice(['holy symbol', 'royal scepter', 'ancient crown', 'crystal heart'])} of {lname} is kept in the {rnd.choice(['Grand Temple', 'Royal Palace', 'Hidden Vault', 'Star Tower'])}.",
        f"A {rnd.choice(['plague of shadows', 'curse of silence', 'blessing of plenty', 'age of wonders'])} once swept through {lname}.",
        f"{lname} is built on {rnd.choice(['seven levels', 'a floating island', 'an ancient dam', 'a petrified forest'])}.",
        f"The {rnd.choice(["founder's", "conqueror's", "saint's", "hero's"])} {rnd.choice(['sword', 'crown', 'tome', 'staff'])} rests in {lname}.",
        f"A {rnd.choice(['celestial choir', 'dwarven forge', 'elven chorus', 'gnomish workshop'])} sounds nightly in {lname}'s {rnd.choice(['temple quarter', 'market square', 'great hall', 'star garden'])}.",
        f"The {rnd.choice(['deep roads', 'spirit paths', 'star ways', 'root tunnels'])} connect {lname} to the {rnd.choice(['Underdark', 'Feywild', 'Astral Plane', 'Elemental Plane'])}.",
        f"{lname} was {rnd.choice(['blessed', 'cursed', 'chosen', 'marked'])} by a {rnd.choice(['god', 'dragon', 'titan', 'fey lord'])} in the {rnd.choice(['dawn age', 'age of blood', 'starfall era', 'first century'])}.",
        f"A {rnd.choice(['maze', 'labyrinth', 'puzzle', 'riddle'])} of {rnd.choice(['crystal', 'stone', 'water', 'light'])} lies beneath {lname}.",
        f"The {rnd.choice(['people', 'guilds', 'clans', 'orders'])} of {lname} are bound by the {rnd.choice(['Iron Charter', 'Silver Accord', 'Golden Pact', 'Crystal Oath'])}.",
        f"{lname} is known for its {rnd.choice(['enchanted smiths', 'starlight weavers', 'runescribes', 'alchemists'])}.",
        f"A {rnd.choice(['menagerie', 'zoo', 'sanctuary', 'preserve'])} of {rnd.choice(['mythical beasts', 'elementals', 'fey creatures', 'shadow beings'])} is hidden near {lname}.",
        f"The {rnd.choice(['oldest tree', 'tallest spire', 'deepest well', 'widest gate'])} of {lname} is a {rnd.choice(['pilgrimage site', 'tourist attraction', 'defensive position', 'magical focus'])}.",
        f"A {rnd.choice(['rebel lord', 'hidden sage', 'wandering hero', 'exiled prince'])} from {lname} once {rnd.choice(['conquered', 'saved', 'betrayed', 'unified'])} the {rnd.choice(['north', 'south', 'east', 'west'])}.",
        f"{lname} celebrates a {rnd.choice(['festival of light', 'night of stars', 'feast of blades', 'dance of seasons'])} every {rnd.randint(3, 12)} months.",
        f"The {rnd.choice(['first stone', 'first tree', 'first gate', 'first throne'])} of {lname} was {rnd.choice(['laid', 'planted', 'raised', 'carved'])} by {rnd.choice(['a titan', 'a god', 'a king', 'a mage'])}.",
    ]
    for i, lr in enumerate(long_lore[:40]):
        memory.append({"tier": "long", "ref": f"{lid}_long_{i:02d}", "topic": "lore",
                       "content": lr})

    # ── 10 NEAR: upcoming events ──
    near_events = [
        f"A {rnd.choice(['caravan', 'army', 'pilgrimage', 'fleet'])} is expected to reach {lname} within {rnd.choice(['days', 'weeks', 'a month'])}.",
        f"{lname} plans to {rnd.choice(['fortify the walls', 'expand the market', 'open a new temple', 'build a bridge'])} before winter.",
        f"A {rnd.choice(['noble wedding', 'royal coronation', 'holy festival', 'grand tournament'])} is scheduled in {lname} next month.",
        f"{lname} fears a {rnd.choice(['raid', 'siege', 'plague', 'uprising'])} from {rnd.choice(['the Orc Wastes', 'the Underdark', 'the Shadow Lands', 'the Sunken Temple'])}.",
        f"A {rnd.choice(['prophet', 'oracle', 'astrologer', 'sage'])} foretells a {rnd.choice(['great change', 'coming war', 'divine blessing', 'natural disaster'])} for {lname}.",
        f"{lname} will host a {rnd.choice(['trade summit', 'diplomatic meeting', 'religious council', 'military muster'])} next season.",
        f"A {rnd.choice(['treasure', 'artifact', 'spell', 'secret'])} is rumored to be hidden in {lname}'s {rnd.choice(['ruins', 'caves', 'sewers', 'catacombs'])}.",
        f"{lname} must prepare for the {rnd.choice(['winter', 'summer', 'drought', 'monsoon'])} season.",
        f"A {rnd.choice(['rival city', 'hostile faction', 'jealous lord', 'enemy kingdom'])} threatens {lname}'s {rnd.choice(['trade routes', 'borders', 'supply lines', 'alliances'])}.",
        f"{lname} will elect a new {rnd.choice(['lord', 'council', 'guild master', 'high priest'])} by the next full moon.",
    ]
    for i, ne in enumerate(near_events[:10]):
        memory.append({"tier": "near", "ref": f"{lid}_near_{i:02d}", "topic": "upcoming",
                       "content": ne})

    return memory

def _loc_questions(pid, info, rnd, memory, loci):
    """Generate 30 questions for a location — loci, memory, and corpus types."""
    lid, lname, ltype = info
    p = pid
    qs = []

    # ── 10 Loci questions — drop value-pasting into query, drop expect_content for numeric/type ──
    loc_lookup = {}
    for d in loci:
        if d.get('key','').startswith(lid):
            loc_lookup[d['key']] = d.get('value', '')

    # (query, key, keep_val)
    loci_qs = [
        (f"what type of location is {lname}?",  f"{lid}_type",        False),
        (f"what population does {lname} have?", f"{lid}_population",  False),
        (f"what climate is {lname} in?",        f"{lid}_climate",     True),
        (f"what terrain is {lname} on?",        f"{lid}_terrain",     True),
        (f"what government rules {lname}?",     f"{lid}_government",  True),
        (f"what defense protects {lname}?",     f"{lid}_defense",     True),
        (f"what export does {lname} produce?",  f"{lid}_export_1",    True),
        (f"what language does {lname} speak?",  f"{lid}_language",    True),
        (f"what primary bldg is in {lname}?",   f"{lid}_bldg_1",      True),
        (f"what festival does {lname} celebrate?", f"{lid}_festival", True),
    ]
    for base_query, key, keep_val in loci_qs:
        value = loc_lookup.get(key, '')
        ec = [value] if (keep_val and value) else []
        qs.append({"asks": "loci", "query": base_query, "expect_key": [f"{p}/{key}"],
                   "expect_content": ec, "hops": 1})

    # ── 6 Memory questions — query disambiguated by a phrase pulled FROM the target row ──
    mem_refs = [f"{lid}_short_00", f"{lid}_short_01", f"{lid}_long_00", f"{lid}_long_05", f"{lid}_near_00", f"{lid}_near_01"]
    mem_stems = [
        f"what recently happened in {lname}",
        f"who recently came to {lname}",
        f"what are the origins of {lname}",
        f"what is the old history of {lname}",
        f"what is planned for {lname}",
        f"what danger faces {lname}",
    ]
    by_ref = {m["ref"]: (m.get("content", "") or m.get("intent", "")) for m in memory}
    for i, ref in enumerate(mem_refs):
        target = by_ref.get(ref, "")
        siblings = [t for r, t in by_ref.items() if r != ref]
        disc = _discriminator(target, siblings, n=4)
        query = f"{mem_stems[i]} — {disc}" if disc else mem_stems[i]
        ec_val = target[:80] if target else ""
        qs.append({"asks": "memory", "query": query, "expect_ref": [ref],
                   "expect_content": [ec_val] if ec_val else [], "hops": 1})

    # ── 14 Corpus questions — expect_key referencing ONLY keys the loci actually emits ──
    # Bug fixed here: several groups pointed at keys that _loc_loci never generates
    # (_founding, _religion, _trade_1/2, _resource_2, _danger_3). Those are Tier-1
    # unreachable by construction -- the expectation can never be found because the fact
    # was never seeded. Every key below is checked against the real generator output.
    basic_keys  = [f"{p}/{lid}_type", f"{p}/{lid}_climate", f"{p}/{lid}_terrain"]
    govt_keys   = [f"{p}/{lid}_government", f"{p}/{lid}_defense"]
    econ_keys   = [f"{p}/{lid}_export_1", f"{p}/{lid}_export_2", f"{p}/{lid}_economy"]
    lore_keys   = [f"{p}/{lid}_history_0", f"{p}/{lid}_history_1", f"{p}/{lid}_history_2"]
    bldg_keys   = [f"{p}/{lid}_bldg_1", f"{p}/{lid}_bldg_2", f"{p}/{lid}_bldg_3",
                   f"{p}/{lid}_district_1", f"{p}/{lid}_district_2"]
    danger_keys = [f"{p}/{lid}_danger_1", f"{p}/{lid}_danger_2"]
    culture_keys = [f"{p}/{lid}_festival", f"{p}/{lid}_religion_1", f"{p}/{lid}_race_1",
                    f"{p}/{lid}_race_2"]
    trade_keys  = [f"{p}/{lid}_export_1", f"{p}/{lid}_import_1", f"{p}/{lid}_resource_1",
                   f"{p}/{lid}_trade_route"]
    geo_keys    = [f"{p}/{lid}_river", f"{p}/{lid}_mountain", f"{p}/{lid}_forest",
                   f"{p}/{lid}_biome", f"{p}/{lid}_region"]

    # A corpus question is discriminable only if its query names something specific
    # enough to single out its keys against a 70-doc store. "describe X's government"
    # with the word 'government' in the query lands on the government key; a bare
    # "tell me about X" does not. Each query below carries the discriminating noun.
    corpus_qs = [
        (f"what type, climate, and terrain define {lname}?",
         [ltype], basic_keys),
        (f"what government and defenses does {lname} have?",
         [], govt_keys),
        (f"what economy and exports does {lname} rely on?",
         [], econ_keys),
        (f"what is the founding history of {lname}?",
         [], lore_keys),
        (f"what buildings and districts stand in {lname}?",
         [], bldg_keys),
        (f"what dangers threaten {lname}?",
         [], danger_keys),
        (f"what festival, religion, and races are found in {lname}?",
         [], culture_keys),
        (f"what rivers, mountains, and forests surround {lname}?",
         [], geo_keys),
        (f"what exports, imports, and trade routes serve {lname}?",
         [], trade_keys),
        (f"give me a full geographic dossier on {lname}",
         [ltype], basic_keys + geo_keys),
    ]
    for query, content, keys in corpus_qs:
        qs.append({"asks": "corpus", "query": query,
                   "expect_content": content, "expect_key": keys, "hops": 1})

    return qs

# ── Entity builders ──

def _build_character(info, rnd):
    cid, name, race, cls, align, role = info
    pid = f"char_{cid}"
    loci = _char_loci(pid, info, RACES, rnd)
    memory = _char_memory(pid, info, RACES, rnd)
    questions = _char_questions(pid, info, RACES, rnd, memory, loci)
    # Add quiet_in: this question should NOT return in other domains (glob patterns)
    other = [f"{d}-*" for d in ALL_DOMAINS if d != pid]
    for q in questions:
        q["quiet_in"] = other
    return pid, loci, memory, questions

def _build_location(info, rnd):
    lid, lname, ltype = info
    pid = f"loc_{lid}"
    loci = _loc_loci(pid, info, rnd)
    memory = _loc_memory(pid, info, rnd)
    questions = _loc_questions(pid, info, rnd, memory, loci)
    # Add quiet_in: this question should NOT return in other domains (glob patterns)
    other = [f"{d}-*" for d in ALL_DOMAINS if d != pid]
    for q in questions:
        q["quiet_in"] = other
    return pid, loci, memory, questions

# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    rnd = random.Random(6006)
    print(f"{'domain':<20}{'loci':>6}{'memory':>8}{'questions':>11}")
    print("-" * 47)

    all_character_data = []
    all_location_data = []
    all_character_questions = []
    all_location_questions = []

    for i, cinfo in enumerate(CHARACTERS):
        pid, loci, mem, qs = _build_character(cinfo, random.Random(6006 + i * 10))
        nl, nm, nq = emit(pid, loci, mem, qs, start_port=7520 + i * 20)
        print(f"{pid:<20}{nl:>6}{nm:>8}{nq:>11}")
        all_character_data.append((pid, loci, mem))
        all_character_questions.append((pid, qs))

    for i, linfo in enumerate(LOCATIONS):
        pid, loci, mem, qs = _build_location(linfo, random.Random(7007 + i * 10))
        nl, nm, nq = emit(pid, loci, mem, qs, start_port=7520 + (len(CHARACTERS) + i) * 20)
        print(f"{pid:<20}{nl:>6}{nm:>8}{nq:>11}")
        all_location_data.append((pid, loci, mem))
        all_location_questions.append((pid, qs))

    print("\nwrote dnd/<domain>/{loci,memory,questions}.yaml + ProbeConfig.yml")

    # ── Cross-entity corpus callosums ──
    # Characters corpus: questions about all characters
    cross_char_qs = []
    for pid, qs in all_character_questions:
        for q in qs:
            qc = q.copy()
            # quiet_in: should NOT return in location domains (glob patterns)
            qc["quiet_in"] = [f"{d}-*" for d in LOC_DOMAINS]
            cross_char_qs.append(qc)
    emit_cross("characters", cross_char_qs)

    # Geography corpus: questions about all locations
    cross_geo_qs = []
    for pid, qs in all_location_questions:
        for q in qs:
            qc = q.copy()
            # quiet_in: should NOT return in character domains (glob patterns)
            qc["quiet_in"] = [f"{d}-*" for d in CHAR_DOMAINS]
            cross_geo_qs.append(qc)
    emit_cross("geography", cross_geo_qs)

    # Everything corpus: all questions combined
    cross_all_qs = []
    # For everything, add all character questions with quiet_in for locations
    for pid, qs in all_character_questions:
        for q in qs:
            qc = q.copy()
            qc["quiet_in"] = [f"{d}-*" for d in LOC_DOMAINS]
            cross_all_qs.append(qc)
    # Add all location questions with quiet_in for characters
    for pid, qs in all_location_questions:
        for q in qs:
            qc = q.copy()
            qc["quiet_in"] = [f"{d}-*" for d in CHAR_DOMAINS]
            cross_all_qs.append(qc)
    emit_cross("everything", cross_all_qs)

    # ── Unified ProbeConfig referencing ALL stores + cross corpora ──
    char_pids = [f"char_{c[0]}" for c in CHARACTERS]
    loc_pids  = [f"loc_{l[0]}"  for l in LOCATIONS]
    all_pids = char_pids + loc_pids
    port = 7520

    # Build memory configs
    mem_lines = []
    for pid in all_pids:
        mem_lines.append(f'''      - Name: {pid}-mem
        Port: {port}
        Seed: [datasets/dnd/{pid}/memory.yaml]
        Questions: [datasets/dnd/{pid}/questions.yaml]''')
        port += 1

    # Build loci configs
    loci_lines = []
    for pid in all_pids:
        loci_lines.append(f'''      - Name: {pid}-loci-v
        Port: {port}
        Flags: [vector]
        Seed: [datasets/dnd/{pid}/loci.yaml]
        Questions: [datasets/dnd/{pid}/questions.yaml]''')
        port += 1
        loci_lines.append(f'''      - Name: {pid}-loci-nv
        Port: {port}
        Seed: [datasets/dnd/{pid}/loci.yaml]
        Questions: [datasets/dnd/{pid}/questions.yaml]''')
        port += 1

    # Build per-domain corpus configs
    corpus_lines = []
    for pid in all_pids:
        corpus_lines.append(f'''      - Name: {pid}-scc-v
        Port: {port}
        Stores:
          - Store: {pid}-loci-v
          - Store: {pid}-mem
        Questions: [datasets/dnd/{pid}/questions.yaml]''')
        port += 1
        corpus_lines.append(f'''      - Name: {pid}-scc-nv
        Port: {port}
        Stores:
          - Store: {pid}-loci-nv
          - Store: {pid}-mem
        Questions: [datasets/dnd/{pid}/questions.yaml]''')
        port += 1

    # Cross-entity corpus configs
    # Characters corpus
    char_store_lines = '\n          '.join([f'- Store: {p}-loci-v' for p in char_pids] + [f'- Store: {p}-mem' for p in char_pids])
    corpus_lines.append(f'''      - Name: cross-characters
        Port: {port}
        Stores:
          {char_store_lines}
        Questions: [datasets/dnd/cross/characters_questions.yaml]''')
    port += 1

    # Geography corpus
    loc_store_lines = '\n          '.join([f'- Store: {p}-loci-v' for p in loc_pids] + [f'- Store: {p}-mem' for p in loc_pids])
    corpus_lines.append(f'''      - Name: cross-geography
        Port: {port}
        Stores:
          {loc_store_lines}
        Questions: [datasets/dnd/cross/geography_questions.yaml]''')
    port += 1

    # Everything corpus
    all_store_lines = '\n          '.join([f'- Store: {p}-loci-v' for p in all_pids] + [f'- Store: {p}-mem' for p in all_pids])
    corpus_lines.append(f'''      - Name: cross-everything
        Port: {port}
        Stores:
          {all_store_lines}
        Questions: [datasets/dnd/cross/everything_questions.yaml]''')
    port += 1

    unified_pc = f"""# Unified ProbeConfig — all D&D realm stores + cross-entity corpora.
ProbeConfig:
  StartingPort: 7520
  DefaultQuestions: [datasets/dnd/cross/everything_questions.yaml]

  Memory:
    MemoryCount: {len(mem_lines)}
    MemoryConfigs:
{chr(10).join(mem_lines)}
  Loci:
    LociCount: {len(loci_lines)}
    LociConfigs:
{chr(10).join(loci_lines)}
  Corpus:
    CorpusRegrades:
      - Name: hop-sweep
        hops: [1, 2, 3]
      - Name: hop-x-packet
        hops: [1, 2]
        n_results: [10, 30]
      - Name: hop-terms
        hops: [2]
        hop_terms: [2, 4, 8]
        hop_budget: [5, 10]
      - Name: rrf-sweep         # RRF k: how sharply top ranks dominate the merge
        rrf_k: [30, 60, 100]
      - Name: floor-sweep       # drop weak loci hits (precision / abstention)
        loci_floor: [0.0, 0.1, 0.3]
      - Name: weight-sweep      # how hard to lean on the deterministic store
        loci_weight: [0.3, 0.5, 0.7, 1.0, 2.0, 3.0, 5.0, 10.0]
      - Name: hop-x-weight
        hops: [1, 2]
        loci_weight: [1.0, 3.0]
        n_results: [10, 30]
      - Name: packet-sweep      # briefing size - the coverage lever
        n_results: [10, 15, 20, 30]
      - Name: floor-x-weight    # the interaction of the two loci knobs
        loci_floor: [0.1, 0.3]
        loci_weight: [0.5, 1.0]
    CorpusCount: {len(corpus_lines)}
    CorpusConfigs:
{chr(10).join(corpus_lines)}
"""
    open(os.path.join(OUT, "ProbeConfig_unified.yml"), "w", encoding="utf-8").write(unified_pc)
    print(f"Unified ProbeConfig: {len(mem_lines)} memory, {len(loci_lines)} loci, {len(corpus_lines)} corpus stores")

    print(f"Cross corpora: characters ({len(cross_char_qs)}q), geography ({len(cross_geo_qs)}q), everything ({len(cross_all_qs)}q)")
