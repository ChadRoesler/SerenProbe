#!/usr/bin/env python3
"""
Generate large, coherent SerenProbe eval datasets — four domains, ~300+ items
each. Deterministic (fixed RNG seed per domain) so the datasets are stable
run-to-run, which is what makes eval results comparable across runs.

Each domain emits, under datasets/<domain>/:
    loci.yaml       flat Loci facts        {project?, key, value, why?}
    memory.yaml     flat Memory episodes   {content|intent, topic?, tier?, ref?}
    questions.yaml  loci / memory / corpus questions (corpus = multi-fact briefings)
    decoy.yaml      unrelated facts for the NegativeTest (leak) store
    ProbeConfig.yml scc-v / scc-nv comparison + a decoy negative store

Design: a hand-authored CORE the corpus questions target (multi-hop facts across
Loci + Memory), plus templated distractor bulk so retrieval actually has to
discriminate. Corpus expect_content is drawn from real core values, so every
briefing phrase is reachable by construction.

Run from the repo root:  python datasets/generate_datasets.py
"""
from __future__ import annotations
import os
import random
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def _dump_list(items):
    return yaml.safe_dump(items, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)


def _dump_questions(qs):
    return yaml.safe_dump({"questions": qs}, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)


def _probeconfig(domain, start_port=7520, questions_file="questions.yaml", decoy_file="decoy.yaml"):
    """A 3-Loci (v, nv, decoy) / 1-Memory / 2-SCC topology, config-driven seeded."""
    d = domain
    return f"""# Auto-generated eval topology for the '{d}' dataset.
# scc-v vs scc-nv over the SAME corpus, plus a decoy store as a leak test.
ProbeConfig:
  StartingPort: {start_port}

  Questions:         datasets/{d}/{questions_file}
  DefaultLociSeed:   datasets/{d}/loci.yaml
  DefaultMemorySeed: datasets/{d}/memory.yaml

  Memory:
    MemoryCount: 1
    MemoryConfigs:
      - {{ Name: {d}-mem, Port: {start_port} }}

  Loci:
    LociCount: 3
    LociConfigs:
      - {{ Name: {d}-loci-v,  Port: {start_port + 1}, Flags: [vector] }}
      - {{ Name: {d}-loci-nv, Port: {start_port + 2} }}
      - {{ Name: {d}-decoy,   Port: {start_port + 9}, NegativeTest: true, Seed: datasets/{d}/{decoy_file} }}

  Corpus:
    # Regrade sets: named knob sweeps the Regrades button rolls against every
    # corpus (it reconfigures the LIVE SCC container per combo, so keep them
    # COMPACT - a set sweeps only the knobs it names; unnamed knobs stay put).
    # A 'current' baseline row is measured automatically. Edit freely.
    CorpusRegrades:
      # THE TEST. hops=1 is today: coverage ~0.60, and six expectations the
      # linter flagged as having NO lexical bridge from their query. hops=2
      # runs a second retrieval round against a query expanded with round-1
      # hit text, which is the only thing that can reach them.
      #
      # PREDICTION (falsifiable, on the record before we look):
      #   hops=1 -> coverage ~0.600  (baseline)
      #   hops=2 -> coverage climbs toward 1.0, and the gain lands on EXACTLY
      #             the six linter-flagged expectations: Sigma-Aldrich,
      #             2027-06, team-ledger, and their kin. No others.
      # If it moves questions the linter DIDN'T flag, my linter is wrong.
      # If it doesn't move at all, the hop is broken. Either way we learn.
      #
      # ---- RESULT (2026-07-13, orkrail_hard, fair trial, all combos shown) ----
      # FALSIFIED. And the disjunction above was incomplete -- reality took a
      # third branch neither option covered.
      #
      #   hops=1 -> coverage 0.482 (scc-v) / 0.500 (scc-nv)
      #   hops=2 -> coverage 0.482 / 0.500   (delta EXACTLY zero)
      #   hops=3 -> coverage 0.482 / 0.500   (delta EXACTLY zero)
      #
      # "It doesn't move, so the hop is broken" was WRONG. Direct row dump on the
      # live container: hops=1 returns 13 hits, hops=2 returns 18 -- eight NEW
      # document ids. The hop runs. /configure takes it. It retrieves.
      # It just retrieves the WRONG THINGS.
      #
      # Two things fell out of the fair trial that matter more than the prediction:
      #
      # 1. THE HOP APPENDS; IT NEVER REORDERS. The top-10 at hops=2 is byte-identical
      #    to hops=1 -- the new docs land at ranks 11-18. So every @k metric
      #    (hit_rate/mrr/precision/recall/ndcg/iou/prec_omega) is STRUCTURALLY BLIND
      #    to a hop, forever, at any k. docket_coverage scans all hits and is the only
      #    metric in the harness that can see one. Grade hops on coverage or don't
      #    grade them.
      #
      # 2. JUST ASKING FOR MORE BEATS HOPPING. n_results=30 buys +0.161 coverage;
      #    hops=2 buys +0.000 at a whole extra retrieval round. The answers were at
      #    ranks 10-30 of round ONE the entire time. The hop expanded the query and
      #    walked away from them.
      #
      # HYPOTHESIS for (2), and the reason for the two new sets below: hop_terms=4
      # lifts its expansion terms from round-1 hit text. In a corpus where every
      # entry is the SAME TEMPLATE ("<thing> happened at <station> on <time> -- <outcome>"),
      # the highest-signal terms in round-1 text ARE THE TEMPLATE. So the hop expands
      # toward "more sentences shaped like this" instead of toward the bridge fact.
      # Same OOD/templating pathology that has orkrail-mem at HR 0.083, different hat.
      - Name: hop-sweep
        hops: [1, 2, 3]

      # NEVER ACTUALLY TESTED. Every set is a product over ONLY the knobs it names,
      # so hop-sweep ran at n_results=10 -- appending the hop's extra docs to a packet
      # that was ALREADY truncating the answers away. The hop was being graded with its
      # hands tied. This is the honest trial.
      #
      # PREDICTION (on the record, before we look):
      #   hops has a real effect ONLY once the packet is deep enough to hold what it
      #   finds. Expect n_results to carry the gain again (~0.64) and hops to add
      #   ~nothing ON TOP of it -- because the hop's docs aren't the missing ones,
      #   they're just more template. If hops=2 x n_results=30 beats hops=1 x
      #   n_results=30, I'm wrong and the hop DOES reach something depth alone can't.
      - Name: hop-x-packet
        hops: [1, 2]
        n_results: [10, 30]

      # THE REAL SUSPECT. If the hop expands toward the template, the fix isn't MORE
      # hops -- it's better expansion terms. hop_terms controls how many terms it
      # lifts; hop_budget how many docs it may pull back.
      #
      # PREDICTION: fewer, sharper terms beat more. hop_terms=2 should outperform
      # hop_terms=8, because 8 terms on a templated corpus is 8 pieces of boilerplate.
      # If MORE terms wins, the templating story is wrong and I need a new one.
      #
      # ---- RESULT: FALSIFIED. Flat. All six combos identical, both SCCs. Neither
      # branch of my disjunction happened -- same third-branch outcome I'd just
      # finished grading Chad on. But hop_terms IS behaviourally live (18 hits vs 19,
      # different ids on a direct dump), so it steers -- it just steers into the same
      # swamp every time. Which meant the terms were never the problem. The
      # DESTINATION was.
      - Name: hop-terms
        hops: [2]
        hop_terms: [2, 4, 8]
        hop_budget: [5, 10]
      - Name: rrf-sweep         # RRF k: how sharply top ranks dominate the merge
        rrf_k: [30, 60, 100]
      - Name: floor-sweep       # drop weak loci hits (precision / abstention)
        loci_floor: [0.0, 0.1, 0.3]

      # WE ONLY EVER SWEPT THIS KNOB DOWNWARD. [0.3 .. 1.0] is the half of the range
      # that pushes the packet TOWARD memory -- and memory is the store sitting at
      # HR 0.083. Every value below 1.0 hands rank-1 to a dead store and craters MRR
      # to 0.052; we watched that cliff four separate times today and never once
      # turned around and walked the other way.
      #
      # RRF weights are RELATIVE, so loci_weight ABOVE 1.0 is the memory-suppression
      # knob viewed from the other side (there is no memory_weight in REGRADE_KNOBS --
      # see the TODO below). Direct row dump on the live container, hops=2:
      #
      #   loci_weight=1  -> hop added 8 docs,  SIX from mem   (75% templated noise)
      #   loci_weight=3  -> hop added 10 docs, ONE from mem    (90% loci facts)
      #   loci_weight=10 -> identical to 3                     (saturates)
      #
      # The hop budget is allocated AFTER fusion weighting. Starve the corpse and the
      # hop eats FACTS -- depot->supplier->promethium-grade, the exact relation chain
      # a supply-chain query wants. The hop was never lost. It was being strangled.
      #
      # A KNOB SWEPT IN ONE DIRECTION IS HALF A KNOB.
      - Name: weight-sweep      # how hard to lean on the deterministic store
        loci_weight: [0.3, 0.5, 0.7, 1.0, 2.0, 3.0, 5.0, 10.0]

      # THE ACTUAL EXPERIMENT. The hop only ever failed because the fusion fed it a
      # dead store as an equal partner. Give it a starved corpse AND a deep enough
      # packet to hold what it finds, and ask the ONLY metric that can see a hop.
      #
      # PREDICTION (mine, on the record, before we look):
      #   The hop's DIET is fixed -- we watched that in the row dump. Whether that
      #   converts to COVERAGE is a different question and I do NOT know the answer.
      #   The facts it pulled at loci_weight=3 were the right RELATION (supplier,
      #   promethium grade) but I did not see srv-000's OWN depot in there. So:
      #   I expect hops=2 x loci_weight>=3 x n_results=30 to beat every cell in this
      #   grid -- but by LESS than the +0.161 that n_results alone already buys.
      #   If hops=2 still adds exactly 0.000 on top of hops=1 at the same weight and
      #   packet, then the hop is genuinely retrieving relation-correct/entity-WRONG
      #   documents, and the fix is the seed graph, not the fusion.
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
      - {{ Name: {d}-scc-v,  Port: {start_port + 4}, Stores: [{{ Store: {d}-loci-v }},  {{ Store: {d}-mem }}] }}
      - {{ Name: {d}-scc-nv, Port: {start_port + 3}, Stores: [{{ Store: {d}-loci-nv }}, {{ Store: {d}-mem }}] }}
"""


def emit(domain, loci, memory, questions, decoy, start_port=7520):
    d = os.path.join(HERE, domain)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "loci.yaml"), "w", encoding="utf-8").write(_dump_list(loci))
    open(os.path.join(d, "memory.yaml"), "w", encoding="utf-8").write(_dump_list(memory))
    open(os.path.join(d, "questions.yaml"), "w", encoding="utf-8").write(_dump_questions(questions))
    open(os.path.join(d, "decoy.yaml"), "w", encoding="utf-8").write(_dump_list(decoy))
    open(os.path.join(d, "ProbeConfig.yml"), "w", encoding="utf-8").write(_probeconfig(domain, start_port))
    return len(loci), len(memory), len(questions), len(decoy)


# ══════════════════════════════════════════════════════════════════════════
#  Domain 1 — HELIX: a fictional tech-org (services, incidents, runbooks)
# ══════════════════════════════════════════════════════════════════════════
def helix():
    rnd = random.Random(1001)
    proj = "helix"
    loci, memory, questions = [], [], []

    # ── core services (hand-authored, interlocking) ──
    core = {
        "gateway":  {"port": "8080", "lang": "Go",      "db": "none",         "cache": "Redis 7",   "owner": "team-edge",   "deploy": "Kubernetes", "sla": "99.95%", "region": "us-east"},
        "auth":     {"port": "8091", "lang": "Rust",    "db": "PostgreSQL 16","cache": "Redis 7",   "owner": "team-atlas",  "deploy": "Kubernetes", "sla": "99.99%", "region": "us-east"},
        "billing":  {"port": "8092", "lang": "Java 21", "db": "PostgreSQL 16","cache": "none",      "owner": "team-ledger", "deploy": "VMs",        "sla": "99.9%",  "region": "us-west"},
        "search":   {"port": "8093", "lang": "Python",  "db": "Elasticsearch","cache": "Redis 7",   "owner": "team-atlas",  "deploy": "Kubernetes", "sla": "99.5%",  "region": "eu-west"},
        "notify":   {"port": "8094", "lang": "Node 20", "db": "MongoDB",      "cache": "none",      "owner": "team-edge",   "deploy": "Kubernetes", "sla": "99.0%",  "region": "us-east"},
        "ledger":   {"port": "8095", "lang": "Kotlin",  "db": "CockroachDB",  "cache": "none",      "owner": "team-ledger", "deploy": "VMs",        "sla": "99.99%", "region": "us-west"},
    }
    for svc, f in core.items():
        for k, v in f.items():
            why = {
                "port": "the service listens here", "lang": "primary implementation language",
                "db": "system of record", "cache": "hot-path cache", "owner": "owning team",
                "deploy": "deploy target", "sla": "contractual availability", "region": "primary region",
            }[k]
            loci.append({"project": proj, "key": f"{svc}_{k}", "value": v, "why": why})

    deps = [("gateway", "auth"), ("gateway", "search"), ("billing", "auth"),
            ("billing", "ledger"), ("notify", "auth"), ("search", "gateway")]
    for a, b in deps:
        loci.append({"project": proj, "key": f"{a}_depends_on_{b}", "value": f"{a} calls {b}",
                     "why": f"{a} depends on {b} at request time"})

    incidents = [
        ("inc-auth-clockskew", "short", "incident,auth", "The auth service on port 8091 threw a wave of 401s after a JWT clock-skew bug on the 12th; team-atlas fixed it by widening leeway to 30 seconds."),
        ("inc-billing-deadlock", "short", "incident,billing", "billing (Java 21, PostgreSQL 16) deadlocked during the month-end run; team-ledger added a statement_timeout of 30s and a retry."),
        ("inc-search-oom", "short", "incident,search", "search (Python, Elasticsearch) OOM-killed under a query storm; team-atlas capped the result window and added a Redis 7 cache in front."),
        ("dec-ledger-cockroach", "long", "decision,ledger", "Chose CockroachDB for ledger over PostgreSQL for multi-region writes; team-ledger owns it, runs on VMs in us-west."),
        ("inc-gateway-cascade", "short", "incident,gateway", "A gateway (Go, port 8080) retry storm cascaded into auth; team-edge added a circuit breaker with a 2s budget."),
        ("run-notify-failover", "long", "runbook,notify", "notify (Node 20, MongoDB) failover runbook: promote the us-east replica, repoint the gateway, page team-edge."),
    ]
    for ref, tier, topic, content in incidents:
        memory.append({"tier": tier, "ref": ref, "topic": topic, "content": content})

    for ref, intent in [
        ("todo-auth-rotate", "Rotate the auth signing keys before the SOC2 audit and confirm gateway picks up the new JWKS."),
        ("todo-search-shard", "Reshard the search Elasticsearch cluster before the Q3 catalog import (expect 4x docs)."),
        ("todo-billing-region", "Evaluate moving billing from us-west VMs onto Kubernetes to match the rest of the fleet."),
    ]:
        memory.append({"tier": "near", "ref": ref, "topic": "todo", "intent": intent})

    adjectives = ["async", "batch", "edge", "core", "meta", "delta", "north", "south", "aux", "proxy",
                  "relay", "vault", "index", "stream", "cron", "hook", "sync", "fanout", "shard", "probe"]
    nouns = ["worker", "collector", "router", "scheduler", "reducer", "ingester", "compactor",
             "dispatcher", "reconciler", "sweeper", "aggregator", "planner", "sentinel", "warden"]
    langs = ["Go", "Rust", "Python", "Java 21", "Node 20", "Kotlin", "Elixir", "C#"]
    dbs = ["PostgreSQL 16", "MySQL 8", "MongoDB", "DynamoDB", "SQLite", "Cassandra", "none"]
    teams = ["team-edge", "team-atlas", "team-ledger", "team-forge", "team-nova", "team-pulse"]
    port = 8100
    seen = set()
    while len([x for x in loci if x["key"].endswith("_port")]) < 22:
        nm = f"{rnd.choice(adjectives)}-{rnd.choice(nouns)}"
        if nm in seen:
            continue
        seen.add(nm)
        for k, v, why in [
            ("port", str(port), "the service listens here"),
            ("lang", rnd.choice(langs), "primary implementation language"),
            ("db", rnd.choice(dbs), "system of record"),
            ("owner", rnd.choice(teams), "owning team"),
            ("deploy", rnd.choice(["Kubernetes", "VMs", "serverless"]), "deploy target"),
            ("region", rnd.choice(["us-east", "us-west", "eu-west", "ap-south"]), "primary region"),
        ]:
            loci.append({"project": proj, "key": f"{nm.replace('-', '_')}_{k}", "value": v, "why": why})
        port += 1

    verbs = ["deployed", "rolled back", "scaled", "patched", "restarted", "migrated", "throttled"]
    for i in range(140):
        svc = rnd.choice(list(seen)) if seen else "aux-worker"
        v = rnd.choice(verbs)
        memory.append({"tier": rnd.choice(["short", "short", "short", "long"]),
                       "ref": f"log-{i:03d}",
                       "topic": rnd.choice(["deploy", "alert", "decision", "oncall"]),
                       "content": f"{v} {svc} at {rnd.randint(0,23):02d}:{rnd.randint(0,59):02d}; "
                                  f"{rnd.choice(['no impact', 'brief 5xx blip', 'latency spike', 'clean'])}."})

    for svc in ["gateway", "auth", "billing", "search", "notify", "ledger"]:
        questions.append({"asks": "loci", "query": f"what port does the {svc} service listen on?",
                          "expect_key": [f"{proj}/{svc}_port"]})
    for svc in ["auth", "billing", "search"]:
        questions.append({"asks": "loci", "query": f"which database backs the {svc} service?",
                          "expect_key": [f"{proj}/{svc}_db"]})
    questions.append({"asks": "memory", "query": "what caused the auth 401 storm?",
                      "expect_ref": ["inc-auth-clockskew"], "expect_content": ["clock-skew", "401"]})
    questions.append({"asks": "memory", "query": "why did billing deadlock at month-end?",
                      "expect_ref": ["inc-billing-deadlock"], "expect_content": ["deadlock", "statement_timeout"]})
    questions.append({"asks": "memory", "query": "what open work is queued before the audit?",
                      "expect_content": ["signing keys", "SOC2"]})
    for svc in ["auth", "billing", "search", "gateway"]:
        f = core[svc]
        inc = {"auth": "clock-skew", "billing": "deadlock", "search": "OOM", "gateway": "circuit breaker"}[svc]
        questions.append({"asks": "corpus", "query": f"give me a briefing on the {svc} service",
                          "expect_content": [f["lang"], f["db"], f["owner"], f["port"], inc]})

    decoy = []
    breads = ["sourdough", "ciabatta", "focaccia", "baguette", "brioche", "rye", "challah", "pretzel"]
    for i, b in enumerate(breads * 3):
        decoy.append({"project": "bakery", "key": f"{b}_{i}_hydration",
                      "value": f"{rnd.randint(60,85)}%", "why": f"dough hydration for {b}"})
    return proj, loci, memory, questions, decoy


# ══════════════════════════════════════════════════════════════════════════
#  Domain 2 — ALDERMOOR: fictional world / lore (characters, places, events)
# ══════════════════════════════════════════════════════════════════════════
def aldermoor():
    rnd = random.Random(2002)
    proj = "aldermoor"
    loci, memory, questions, decoy = [], [], [], []

    houses = {
        "Vayne":   {"seat": "Grimsgate",   "head": "Lord Corwin Vayne",  "sigil": "a black harrier",  "region": "the Ashfen",   "words": "We Do Not Kneel", "allegiance": "the Iron Pact"},
        "Ostry":   {"seat": "Highmere",     "head": "Lady Serane Ostry",  "sigil": "a silver stag",    "region": "the Reach",    "words": "Truth in Frost",  "allegiance": "the Crown"},
        "Belloc":  {"seat": "Duskwater",    "head": "Ser Aldous Belloc",  "sigil": "a red kraken",     "region": "the Shoals",   "words": "Deep and Patient","allegiance": "the Iron Pact"},
        "Merrow":  {"seat": "Thornhold",    "head": "Lady Wyn Merrow",    "sigil": "a green thorn",    "region": "the Weald",    "words": "None Pass Us",    "allegiance": "the Crown"},
        "Kael":    {"seat": "Sunspear Keep","head": "Lord Tamsin Kael",   "sigil": "a golden sun",     "region": "the Dunes",    "words": "Burn Bright",     "allegiance": "neutral"},
    }
    for h, f in houses.items():
        for k, v in f.items():
            why = {"seat": "ancestral seat", "head": "current head of the house", "sigil": "heraldic sigil",
                   "region": "home region", "words": "house words", "allegiance": "sworn allegiance"}[k]
            loci.append({"project": proj, "key": f"house_{h.lower()}_{k}", "value": v, "why": why})

    rivalries = [("Vayne", "Ostry"), ("Belloc", "Merrow"), ("Kael", "Vayne")]
    for a, b in rivalries:
        loci.append({"project": proj, "key": f"house_{a.lower()}_rival",
                     "value": f"House {b}", "why": f"House {a} and House {b} are sworn rivals"})

    events = [
        ("evt-frostford", "long", "battle", "At the Battle of Frostford, House Ostry broke the Iron Pact line; Lord Corwin Vayne of Grimsgate was captured and ransomed."),
        ("evt-duskwater-siege", "long", "siege", "The Siege of Duskwater lasted a winter; Ser Aldous Belloc held the Shoals with a red kraken banner until the Crown relented."),
        ("evt-thornhold-pact", "long", "treaty", "The Thornhold Pact wed House Merrow to the Crown; Lady Wyn Merrow swore 'None Pass Us' before Lady Serane Ostry."),
        ("evt-sunspear-neutral", "short", "politics", "House Kael of Sunspear Keep declared neutrality in the Dunes, refusing both the Iron Pact and the Crown."),
        ("evt-ashfen-burning", "long", "battle", "The Burning of the Ashfen scattered House Vayne's harriers; they swore 'We Do Not Kneel' amid the ruin of Grimsgate."),
    ]
    for ref, tier, topic, content in events:
        memory.append({"tier": tier, "ref": ref, "topic": topic, "content": content})
    for ref, intent in [
        ("omen-comet", "Watch for the red comet over the Reach — the maesters say it heralds a broken oath."),
        ("plot-kael-marriage", "Broker a marriage between House Kael and House Ostry to pull the Dunes to the Crown."),
    ]:
        memory.append({"tier": "near", "ref": ref, "topic": "prophecy", "intent": intent})

    firsts = ["Bryn", "Cora", "Dax", "Ella", "Finn", "Gwyn", "Hale", "Isolde", "Joss", "Kit",
              "Lyra", "Mabon", "Nell", "Osric", "Petra", "Quill", "Rook", "Senna", "Tibb", "Ulf"]
    trades = ["fisher", "smith", "hedge knight", "septon", "miller", "ferrier", "cooper", "chandler"]
    regions = ["the Ashfen", "the Reach", "the Shoals", "the Weald", "the Dunes"]
    for i in range(120):
        nm = f"{rnd.choice(firsts)} of {rnd.choice(['Grimsgate','Highmere','Duskwater','Thornhold','Sunspear'])}"
        loci.append({"project": proj, "key": f"folk_{i:03d}_trade", "value": rnd.choice(trades),
                     "why": f"{nm} plies their trade in {rnd.choice(regions)}"})
    for i in range(130):
        memory.append({"tier": rnd.choice(["short", "short", "long"]), "ref": f"tale-{i:03d}",
                       "topic": rnd.choice(["rumor", "feast", "raid", "harvest"]),
                       "content": f"In {rnd.choice(regions)}, {rnd.choice(firsts)} spoke of "
                                  f"{rnd.choice(['a wolf in the snow','a sail on the horizon','a fire in the night','a stranger at the gate'])}."})

    for h in houses:
        questions.append({"asks": "loci", "query": f"what is the seat of House {h}?",
                          "expect_key": [f"{proj}/house_{h.lower()}_seat"]})
    for h in ["Vayne", "Ostry", "Belloc"]:
        questions.append({"asks": "loci", "query": f"who heads House {h}?",
                          "expect_key": [f"{proj}/house_{h.lower()}_head"]})
    questions.append({"asks": "memory", "query": "what happened at Frostford?",
                      "expect_ref": ["evt-frostford"], "expect_content": ["Ostry", "captured"]})
    questions.append({"asks": "memory", "query": "how did House Merrow come to the Crown?",
                      "expect_ref": ["evt-thornhold-pact"], "expect_content": ["Thornhold Pact", "None Pass Us"]})
    for h in ["Vayne", "Ostry", "Belloc", "Merrow"]:
        f = houses[h]
        ev = {"Vayne": "captured", "Ostry": "broke the Iron Pact", "Belloc": "Siege of Duskwater", "Merrow": "Thornhold Pact"}[h]
        questions.append({"asks": "corpus", "query": f"tell me everything about House {h}",
                          "expect_content": [f["seat"], f["head"], f["words"], f["allegiance"], ev]})

    creatures = ["mimic flea", "glass toad", "dune wyrm", "fen lantern", "snow shrike"]
    for i, c in enumerate(creatures * 4):
        decoy.append({"project": "bestiary", "key": f"{c.replace(' ','_')}_{i}_habitat",
                      "value": rnd.choice(regions), "why": f"where the {c} is found"})
    return proj, loci, memory, questions, decoy


# ══════════════════════════════════════════════════════════════════════════
#  Domain 3 — LATTICE: a made-up framework's docs (technical KB)
# ══════════════════════════════════════════════════════════════════════════
def lattice():
    rnd = random.Random(3003)
    proj = "lattice"
    loci, memory, questions, decoy = [], [], [], []

    apis = {
        "weave":    {"module": "lattice.core",   "returns": "Fabric",   "since": "0.4", "arg": "nodes: list", "raises": "CycleError"},
        "pin":      {"module": "lattice.core",   "returns": "Handle",   "since": "0.2", "arg": "key: str",    "raises": "PinError"},
        "collapse": {"module": "lattice.reduce", "returns": "Result",   "since": "0.6", "arg": "fabric: Fabric","raises": "EmptyError"},
        "shard":    {"module": "lattice.dist",   "returns": "list",     "since": "0.5", "arg": "n: int",      "raises": "ValueError"},
        "hydrate":  {"module": "lattice.io",     "returns": "Fabric",   "since": "0.3", "arg": "path: str",   "raises": "SchemaError"},
    }
    for fn, f in apis.items():
        for k, v in f.items():
            why = {"module": "defining module", "returns": "return type", "since": "introduced in version",
                   "arg": "primary argument", "raises": "raises on failure"}[k]
            loci.append({"project": proj, "key": f"{fn}_{k}", "value": v, "why": why})

    cfgkeys = {
        "lattice.max_nodes":   {"default": "1024", "type": "int"},
        "lattice.cache_dir":   {"default": "~/.lattice", "type": "path"},
        "lattice.strict":      {"default": "false", "type": "bool"},
        "lattice.shard_count": {"default": "4", "type": "int"},
        "lattice.embedder":    {"default": "minilm", "type": "str"},
    }
    for key, f in cfgkeys.items():
        loci.append({"project": proj, "key": f"cfg_{key.split('.')[1]}_default", "value": f["default"], "why": f"default for {key}"})
        loci.append({"project": proj, "key": f"cfg_{key.split('.')[1]}_type", "value": f["type"], "why": f"type of {key}"})

    changelog = [
        ("chg-0.6-collapse", "long", "changelog", "0.6 added lattice.reduce.collapse(fabric) -> Result; it raises EmptyError on an empty Fabric. Migrate old reduce() calls."),
        ("chg-0.5-shard", "long", "changelog", "0.5 introduced lattice.dist.shard(n) returning a list; lattice.shard_count default rose from 1 to 4."),
        ("gotcha-weave-cycle", "short", "gotcha", "weave(nodes) raises CycleError if the node list forms a loop — dedupe and topo-sort before calling."),
        ("gotcha-strict-hydrate", "short", "gotcha", "With lattice.strict=true, hydrate(path) raises SchemaError on unknown keys instead of warning."),
        ("mig-0.3-io", "long", "migration", "0.3 moved load() to lattice.io.hydrate(path); the old top-level load() is gone. Update imports."),
    ]
    for ref, tier, topic, content in changelog:
        memory.append({"tier": tier, "ref": ref, "topic": topic, "content": content})
    for ref, intent in [
        ("plan-0.7-async", "Ship async collapse in 0.7 so lattice.reduce can stream partial Results."),
        ("plan-typed-cfg", "Add typed config validation so lattice.strict rejects wrong-typed keys at load."),
    ]:
        memory.append({"tier": "near", "ref": ref, "topic": "roadmap", "intent": intent})

    stems = ["map", "fold", "join", "split", "prune", "graft", "flush", "seal", "probe", "trace",
             "bind", "lift", "drop", "merge", "zip", "scan", "walk", "emit", "sink", "tap"]
    mods = ["lattice.core", "lattice.reduce", "lattice.dist", "lattice.io", "lattice.util", "lattice.net"]
    rets = ["Fabric", "Handle", "Result", "list", "dict", "None", "int", "bytes"]
    for i, s in enumerate(stems * 6):
        fn = f"{s}{i}"
        loci.append({"project": proj, "key": f"{fn}_module", "value": rnd.choice(mods), "why": f"module of {fn}()"})
        loci.append({"project": proj, "key": f"{fn}_returns", "value": rnd.choice(rets), "why": f"return type of {fn}()"})
    for i in range(130):
        v = f"0.{rnd.randint(1,6)}.{rnd.randint(0,9)}"
        memory.append({"tier": rnd.choice(["short", "long"]), "ref": f"note-{i:03d}", "topic": rnd.choice(["changelog", "gotcha", "faq"]),
                       "content": f"{v}: {rnd.choice(['fixed','tuned','deprecated','documented'])} "
                                  f"{rnd.choice(stems)}() in {rnd.choice(mods)}."})

    for fn in apis:
        questions.append({"asks": "loci", "query": f"what module is {fn}() in?", "expect_key": [f"{proj}/{fn}_module"]})
    for fn in ["weave", "collapse", "hydrate"]:
        questions.append({"asks": "loci", "query": f"what does {fn}() return?", "expect_key": [f"{proj}/{fn}_returns"]})
    questions.append({"asks": "memory", "query": "what changed with collapse in 0.6?",
                      "expect_ref": ["chg-0.6-collapse"], "expect_content": ["EmptyError", "0.6"]})
    questions.append({"asks": "memory", "query": "why does weave throw a cycle error?",
                      "expect_ref": ["gotcha-weave-cycle"], "expect_content": ["CycleError", "topo-sort"]})
    for fn in ["weave", "collapse", "hydrate", "shard"]:
        f = apis[fn]
        note = {"weave": "CycleError", "collapse": "EmptyError", "hydrate": "SchemaError", "shard": "shard_count"}[fn]
        questions.append({"asks": "corpus", "query": f"how do I use {fn}() in Lattice?",
                          "expect_content": [f["module"], f["returns"], f["since"], f["arg"], note]})

    plants = ["fern", "moss", "lichen", "sedge", "reed", "ivy"]
    for i, p in enumerate(plants * 5):
        decoy.append({"project": "flora", "key": f"{p}_{i}_light", "value": rnd.choice(["shade", "partial", "full sun"]),
                      "why": f"light needs of {p}"})
    return proj, loci, memory, questions, decoy


# ══════════════════════════════════════════════════════════════════════════
#  Domain 4 — HALCYON: sci-fi station / crew ops
# ══════════════════════════════════════════════════════════════════════════
def halcyon():
    rnd = random.Random(4004)
    proj = "halcyon"
    loci, memory, questions, decoy = [], [], [], []

    crew = {
        "vega":   {"role": "Station Chief", "dept": "Command",     "deck": "Deck 1", "clearance": "Omega", "origin": "Ceres",   "species": "human"},
        "orin":   {"role": "Chief Engineer","dept": "Engineering", "deck": "Deck 6", "clearance": "Delta", "origin": "Titan",   "species": "human"},
        "sable":  {"role": "Medical Lead",  "dept": "Medbay",      "deck": "Deck 3", "clearance": "Gamma", "origin": "Luna",    "species": "human"},
        "quell":  {"role": "Nav Officer",   "dept": "Command",     "deck": "Deck 1", "clearance": "Delta", "origin": "Europa",  "species": "synth"},
        "brix":   {"role": "Cargo Master",  "dept": "Logistics",   "deck": "Deck 8", "clearance": "Beta",  "origin": "Mars",    "species": "human"},
    }
    for c, f in crew.items():
        for k, v in f.items():
            why = {"role": "duty role", "dept": "department", "deck": "assigned deck",
                   "clearance": "security clearance", "origin": "homeworld", "species": "species"}[k]
            loci.append({"project": proj, "key": f"crew_{c}_{k}", "value": v, "why": why})

    systems = {
        "reactor":  {"status": "nominal", "deck": "Deck 9", "draw": "40 MW", "owner": "orin"},
        "lifesupport": {"status": "nominal", "deck": "Deck 4", "draw": "6 MW", "owner": "orin"},
        "nav":      {"status": "degraded", "deck": "Deck 1", "draw": "2 MW", "owner": "quell"},
        "comms":    {"status": "nominal", "deck": "Deck 2", "draw": "1 MW", "owner": "vega"},
    }
    for s, f in systems.items():
        for k, v in f.items():
            loci.append({"project": proj, "key": f"sys_{s}_{k}", "value": v, "why": f"{k} of the {s} system"})

    logs = [
        ("log-anomaly-7", "short", "anomaly", "Deck 6 reported a plasma flux anomaly; Chief Engineer Orin (clearance Delta) traced it to a cracked reactor coolant line drawing 40 MW."),
        ("log-nav-degraded", "short", "incident", "Nav went degraded on Deck 1; Quell, the synth Nav Officer, rerouted through the backup gyros and paged Station Chief Vega."),
        ("log-medbay-quarantine", "long", "medical", "Medical Lead Sable (from Luna) quarantined Deck 3 after a cargo pallet from Mars tested positive for spore drift; Brix logged the manifest."),
        ("ord-vega-lockdown", "long", "order", "Station Chief Vega (clearance Omega) ordered a Deck 8 lockdown pending the spore screen; Cargo Master Brix complied."),
        ("log-reactor-drill", "short", "drill", "Orin ran a reactor scram drill; life support on Deck 4 held at 6 MW throughout."),
    ]
    for ref, tier, topic, content in logs:
        memory.append({"tier": tier, "ref": ref, "topic": topic, "content": content})
    for ref, intent in [
        ("todo-nav-parts", "Requisition replacement gyro bearings for the degraded nav system before the Europa transit."),
        ("todo-spore-clear", "Clear the Deck 8 lockdown once Sable signs off on the spore screen."),
    ]:
        memory.append({"tier": "near", "ref": ref, "topic": "todo", "intent": intent})

    firsts = ["Ash", "Bex", "Cyr", "Dov", "Enn", "Fen", "Gale", "Hux", "Iri", "Jax",
              "Kwe", "Lio", "Mko", "Nyx", "Ovo", "Pyr", "Rho", "Syl", "Tae", "Vok"]
    roles = ["technician", "medic", "rating", "specialist", "ensign", "steward", "gunner"]
    decks = [f"Deck {n}" for n in range(1, 11)]
    for i in range(120):
        loci.append({"project": proj, "key": f"crew_r{i:03d}_deck", "value": rnd.choice(decks),
                     "why": f"{rnd.choice(firsts)} the {rnd.choice(roles)} berths here"})
    for i in range(130):
        memory.append({"tier": rnd.choice(["short", "short", "long"]), "ref": f"slog-{i:03d}",
                       "topic": rnd.choice(["routine", "maintenance", "comms", "watch"]),
                       "content": f"{rnd.choice(firsts)} logged {rnd.choice(['a clean sweep','a minor fault','a comms check','a hull ping'])} "
                                  f"on {rnd.choice(decks)} at {rnd.randint(0,23):02d}00."})

    for c in crew:
        questions.append({"asks": "loci", "query": f"what deck is {c} on?", "expect_key": [f"{proj}/crew_{c}_deck"]})
    for c in ["vega", "orin", "sable"]:
        questions.append({"asks": "loci", "query": f"what is {c}'s clearance?", "expect_key": [f"{proj}/crew_{c}_clearance"]})
    for s in ["reactor", "nav"]:
        questions.append({"asks": "loci", "query": f"what is the status of the {s} system?", "expect_key": [f"{proj}/sys_{s}_status"]})
    questions.append({"asks": "memory", "query": "what caused the Deck 6 anomaly?",
                      "expect_ref": ["log-anomaly-7"], "expect_content": ["plasma flux", "coolant"]})
    questions.append({"asks": "memory", "query": "why was Deck 8 locked down?",
                      "expect_ref": ["ord-vega-lockdown"], "expect_content": ["lockdown", "spore"]})
    for c in ["vega", "orin", "sable", "quell"]:
        f = crew[c]
        ev = {"vega": "lockdown", "orin": "plasma flux", "sable": "quarantined", "quell": "degraded"}[c]
        questions.append({"asks": "corpus", "query": f"give me a dossier on crew member {c}",
                          "expect_content": [f["role"], f["dept"], f["deck"], f["clearance"], ev]})

    drinks = ["kaff", "root tea", "ion fizz", "star cider", "brine ale"]
    for i, d in enumerate(drinks * 4):
        decoy.append({"project": "galley", "key": f"{d.replace(' ','_')}_{i}_ration",
                      "value": f"{rnd.randint(1,5)} units", "why": f"galley ration of {d}"})
    return proj, loci, memory, questions, decoy


# ══ HARD test packs — multi-answer / paraphrase / cross-store + near-miss decoys
#    Reachability-validated against the base loci/memory the builders above emit.
def hard_helix(memory=None):
    p = "helix"
    q = [
        {"asks": "loci", "query": "which services depend on auth?",
         "expect_key": [f"{p}/gateway_depends_on_auth", f"{p}/billing_depends_on_auth", f"{p}/notify_depends_on_auth"]},
        {"asks": "loci", "query": "what does the gateway depend on?",
         "expect_key": [f"{p}/gateway_depends_on_auth", f"{p}/gateway_depends_on_search"]},
        {"asks": "loci", "query": "which services does billing rely on?",
         "expect_key": [f"{p}/billing_depends_on_auth", f"{p}/billing_depends_on_ledger"]},
        {"asks": "memory", "query": "why were users suddenly unable to log in?",
         "expect_ref": ["inc-auth-clockskew"], "expect_content": ["clock-skew", "leeway"]},
        {"asks": "memory", "query": "how did we stop the cascading failure between services?",
         "expect_ref": ["inc-gateway-cascade"], "expect_content": ["circuit breaker", "2s budget"]},
        {"asks": "corpus", "query": "if auth goes down, what else breaks?",
         "expect_content": ["gateway calls auth", "billing calls auth", "notify calls auth"]},
        {"asks": "corpus", "query": "give me the blast radius and history of the auth service",
         "expect_content": ["Rust", "team-atlas", "8091", "clock-skew", "circuit breaker"]},
        {"asks": "loci", "query": "what port does the vaporize service listen on?", "expect_empty": True},
        {"asks": "corpus", "query": "brief me on the quantum-teleport service", "expect_empty": True},
    ]
    decoy = [
        {"project": p, "key": "shadow_gateway_port", "value": "9101", "why": "shadow-gateway listens here"},
        {"project": p, "key": "shadow_gateway_lang", "value": "Zig", "why": "shadow-gateway language"},
        {"project": p, "key": "shadow_gateway_owner", "value": "team-void", "why": "shadow-gateway owner"},
        {"project": p, "key": "auth_relay_port", "value": "9102", "why": "auth-relay listens here"},
        {"project": p, "key": "auth_relay_lang", "value": "Haskell", "why": "auth-relay language"},
        {"project": p, "key": "billing_mirror_port", "value": "9103", "why": "billing-mirror listens here"},
        {"project": p, "key": "billing_mirror_db", "value": "Firebird", "why": "billing-mirror datastore"},
        {"project": p, "key": "search_ghost_owner", "value": "team-void", "why": "search-ghost owner"},
    ]
    return q, decoy


def hard_aldermoor(memory=None):
    p = "aldermoor"
    q = [
        {"asks": "loci", "query": "which houses are loyal to the Crown?",
         "expect_key": [f"{p}/house_ostry_allegiance", f"{p}/house_merrow_allegiance"]},
        {"asks": "loci", "query": "which houses back the Iron Pact?",
         "expect_key": [f"{p}/house_vayne_allegiance", f"{p}/house_belloc_allegiance"]},
        {"asks": "memory", "query": "who defeated House Vayne, and where?",
         "expect_ref": ["evt-frostford"], "expect_content": ["Ostry", "Frostford", "captured"]},
        {"asks": "memory", "query": "how did House Merrow come under royal banners?",
         "expect_ref": ["evt-thornhold-pact"], "expect_content": ["Thornhold Pact", "None Pass Us"]},
        {"asks": "corpus", "query": "who is House Vayne's rival and how did their war end?",
         "expect_content": ["House Ostry", "Frostford", "captured"]},
        {"asks": "corpus", "query": "tell me about House Belloc's seat, sigil, and their siege",
         "expect_content": ["Duskwater", "red kraken", "Siege of Duskwater"]},
        {"asks": "loci", "query": "what is the seat of House Thornwood?", "expect_empty": True},
        {"asks": "corpus", "query": "tell me everything about House Blackmoor", "expect_empty": True},
    ]
    decoy = [
        {"project": p, "key": "house_vance_seat", "value": "Blackmere", "why": "seat of House Vance"},
        {"project": p, "key": "house_vance_head", "value": "Lord Aldric Vance", "why": "head of House Vance"},
        {"project": p, "key": "house_vance_allegiance", "value": "the Free Cities", "why": "House Vance allegiance"},
        {"project": p, "key": "house_ostrander_seat", "value": "Palegate", "why": "seat of House Ostrander"},
        {"project": p, "key": "house_ostrander_head", "value": "Lady Miren Ostrander", "why": "head of House Ostrander"},
        {"project": p, "key": "house_merrick_seat", "value": "Greenhollow", "why": "seat of House Merrick"},
        {"project": p, "key": "house_merrick_allegiance", "value": "the Free Cities", "why": "House Merrick allegiance"},
    ]
    return q, decoy


def hard_lattice(memory=None):
    p = "lattice"
    q = [
        {"asks": "loci", "query": "what module is collapse in and what does it raise?",
         "expect_key": [f"{p}/collapse_module", f"{p}/collapse_raises"]},
        {"asks": "loci", "query": "what does hydrate return and what error can it throw?",
         "expect_key": [f"{p}/hydrate_returns", f"{p}/hydrate_raises"]},
        {"asks": "loci", "query": "what are the default and type of the shard count setting?",
         "expect_key": [f"{p}/cfg_shard_count_default", f"{p}/cfg_shard_count_type"]},
        {"asks": "memory", "query": "why does building a fabric blow up when nodes reference each other in a loop?",
         "expect_ref": ["gotcha-weave-cycle"], "expect_content": ["CycleError", "topo-sort"]},
        {"asks": "memory", "query": "what do I change if I was calling the old top-level load function?",
         "expect_ref": ["mig-0.3-io"], "expect_content": ["hydrate", "Update imports"]},
        {"asks": "corpus", "query": "what changed with collapse in 0.6 and what does it raise?",
         "expect_content": ["collapse", "EmptyError", "0.6", "Result"]},
        {"asks": "corpus", "query": "how do I safely call weave and what breaks it?",
         "expect_content": ["lattice.core", "Fabric", "CycleError", "topo-sort"]},
        {"asks": "loci", "query": "what does obliterate() return?", "expect_empty": True},
        {"asks": "corpus", "query": "how do I use teleport() in Lattice?", "expect_empty": True},
    ]
    decoy = [
        {"project": p, "key": "dissolve_module", "value": "lattice.legacy", "why": "module of dissolve()"},
        {"project": p, "key": "dissolve_returns", "value": "Slurry", "why": "return type of dissolve()"},
        {"project": p, "key": "dissolve_raises", "value": "MeltError", "why": "dissolve() failure"},
        {"project": p, "key": "unweave_module", "value": "lattice.legacy", "why": "module of unweave()"},
        {"project": p, "key": "unweave_returns", "value": "Strand", "why": "return type of unweave()"},
        {"project": p, "key": "reshard_module", "value": "lattice.legacy", "why": "module of reshard()"},
    ]
    return q, decoy


def hard_halcyon(memory=None):
    p = "halcyon"
    q = [
        {"asks": "loci", "query": "which crew are in Command?",
         "expect_key": [f"{p}/crew_vega_dept", f"{p}/crew_quell_dept"]},
        {"asks": "loci", "query": "which systems are nominal?",
         "expect_key": [f"{p}/sys_reactor_status", f"{p}/sys_lifesupport_status", f"{p}/sys_comms_status"]},
        {"asks": "loci", "query": "what is nav's status and who owns it?",
         "expect_key": [f"{p}/sys_nav_status", f"{p}/sys_nav_owner"]},
        {"asks": "memory", "query": "what went wrong with the engine cooling?",
         "expect_ref": ["log-anomaly-7"], "expect_content": ["plasma flux", "coolant"]},
        {"asks": "memory", "query": "why did the medbay seal off a deck?",
         "expect_ref": ["log-medbay-quarantine"], "expect_content": ["quarantined", "spore"]},
        {"asks": "corpus", "query": "who owns the degraded system and what happened to it?",
         "expect_content": ["degraded", "Quell", "gyros", "Deck 1"]},
        {"asks": "corpus", "query": "who has Omega clearance and what did they order?",
         "expect_content": ["Vega", "Omega", "lockdown", "Deck 8"]},
        {"asks": "loci", "query": "what deck is Zane on?", "expect_empty": True},
        {"asks": "corpus", "query": "give me a dossier on crew member Kiro", "expect_empty": True},
    ]
    decoy = [
        {"project": p, "key": "crew_vane_role", "value": "Deck Steward", "why": "role of Vane"},
        {"project": p, "key": "crew_vane_dept", "value": "Hospitality", "why": "Vane's department"},
        {"project": p, "key": "crew_vane_clearance", "value": "Tau", "why": "Vane's clearance"},
        {"project": p, "key": "crew_orrin_role", "value": "Hydroponics Tech", "why": "role of Orrin"},
        {"project": p, "key": "crew_orrin_deck", "value": "Deck 5", "why": "Orrin's deck"},
        {"project": p, "key": "crew_sabel_role", "value": "Records Clerk", "why": "role of Sabel"},
    ]
    return q, decoy


# ══════════════════════════════════════════════════════════════════════════
#  Domain 5 — MYCELIUM: synthetic biology / bioengineering R&D corp
# ══════════════════════════════════════════════════════════════════════════
def mycelium():
    rnd = random.Random(5005)
    proj = "mycelium"
    loci, memory, questions, decoy = [], [], [], []

    # ── Core engineered strains (hand-authored, interlocking) ──
    strains_core = {
        "asper-k1":    {"host": "A. sojae",    "vector": "pEX-2A",   "promoter": "glaA",  "marker": "hygB",   "application": "cellulase production",  "stability": "A", "expression": "high",   "titer": "12 g/L"},
        "yeast-e2":    {"host": "S. cerevisiae","vector": "pESC-URA", "promoter": "GAL1",  "marker": "URA3",   "application": "ethanol tolerance",     "stability": "A", "expression": "medium", "titer": "8 g/L"},
        "coli-p3":     {"host": "E. coli",      "vector": "pET-28b",  "promoter": "T7",    "marker": "kanR",   "application": "insulin precursor",     "stability": "B", "expression": "high",   "titer": "5 g/L"},
        "bac-m1":      {"host": "B. subtilis",  "vector": "pHT43",    "promoter": "PaprE", "marker": "catR",   "application": "protease secretion",    "stability": "A", "expression": "high",   "titer": "18 g/L"},
        "pichia-x7":   {"host": "P. pastoris",  "vector": "pGAPzα",   "promoter": "GAP",   "marker": "zeoR",   "application": "single-cell protein",   "stability": "A", "expression": "high",   "titer": "22 g/L"},
        "chlamy-c4":   {"host": "C. reinhardtii","vector": "pChlamy","promoter": "psaD",  "marker": "bleR",   "application": "lipid production",      "stability": "B", "expression": "medium", "titer": "4 g/L"},
        "yeast-o1":    {"host": "Y. lipolytica", "vector": "pJN4522", "promoter": "TEF",  "marker": "nptII",  "application": "omega-3 fatty acids",   "stability": "A", "expression": "high",   "titer": "15 g/L"},
        "coli-s8":     {"host": "E. coli",      "vector": "pBAD33",  "promoter": "araBAD","marker": "cmR",   "application": "silk protein monomer",  "stability": "B", "expression": "medium", "titer": "3 g/L"},
    }
    for s, f in strains_core.items():
        for k, v in f.items():
            why = {"host": "production host organism", "vector": "expression vector backbone",
                   "promoter": "driving transcription", "marker": "selection marker",
                   "application": "intended product", "stability": "genetic stability grade",
                   "expression": "observed expression level", "titer": "measured product titer"}[k]
            loci.append({"project": proj, "key": f"strain_{s}_{k}", "value": v, "why": why})

    # ── Core enzymes (hand-authored) ──
    enzymes_core = {
        "cellB":    {"ec": "3.2.1.4",  "source": "T. reesei",   "opt_temp": "50°C", "opt_ph": "5.0", "substrate": "cellulose",  "product": "glucose",        "turnover": "1200 /s"},
        "lipA":     {"ec": "3.1.1.3",  "source": "C. antarctica","opt_temp": "40°C", "opt_ph": "7.5", "substrate": "triglycerides","product": "fatty acids",    "turnover": "800 /s"},
        "proK":     {"ec": "3.4.21.62","source": "B. subtilis",  "opt_temp": "37°C", "opt_ph": "8.0", "substrate": "casein",       "product": "peptides",       "turnover": "2500 /s"},
        "xynA":     {"ec": "3.2.1.8",  "source": "A. sojae",    "opt_temp": "45°C", "opt_ph": "4.5", "substrate": "xylan",        "product": "xylose",         "turnover": "900 /s"},
        "amyG":     {"ec": "3.2.1.1",  "source": "A. oryzae",    "opt_temp": "55°C", "opt_ph": "6.0", "substrate": "starch",       "product": "maltose",        "turnover": "1800 /s"},
        "phytA":    {"ec": "3.1.3.8",  "source": "E. coli",      "opt_temp": "37°C", "opt_ph": "6.5", "substrate": "phytate",      "product": "phosphate",      "turnover": "400 /s"},
    }
    for e, f in enzymes_core.items():
        for k, v in f.items():
            why = {"ec": "Enzyme Commission number", "source": "source organism",
                   "opt_temp": "optimal temperature", "opt_ph": "optimal pH",
                   "substrate": "catalytic substrate", "product": "reaction product",
                   "turnover": "turnover rate"}[k]
            loci.append({"project": proj, "key": f"enz_{e}_{k}", "value": v, "why": why})

    # ── Core bio-sensors (hand-authored) ──
    sensors_core = {
        "glucSense":   {"target": "glucose",     "range": "0.1-50 mM",  "output": "GFP fluorescence", "rt": "30 s",  "lod": "0.05 mM", "host": "S. cerevisiae"},
        "toxAlert":    {"target": "arsenite",    "range": "0.5-200 ppb","output": "mCherry",           "rt": "60 s",  "lod": "0.1 ppb", "host": "E. coli"},
        "pHmeter":     {"target": "pH",          "range": "4.0-9.0",   "output": "YFP ratio",         "rt": "15 s",  "lod": "0.1 pH",  "host": "B. subtilis"},
        "quorumX":     {"target": "AHL C12",     "range": "1-100 nM",  "output": "luciferase",        "rt": "45 s",  "lod": "0.5 nM",  "host": "E. coli"},
        "metalLock":   {"target": "cadmium",     "range": "10-500 ppb","output": "β-lactamase",       "rt": "120 s", "lod": "5 ppb",   "host": "P. pastoris"},
    }
    for s, f in sensors_core.items():
        for k, v in f.items():
            why = {"target": "analyte detected", "range": "detection range",
                   "output": "reporter output", "rt": "response time",
                   "lod": "limit of detection", "host": "chassis organism"}[k]
            loci.append({"project": proj, "key": f"sense_{s}_{k}", "value": v, "why": why})

    # ── Core plasmids / vectors (hand-authored) ──
    plasmids_core = {
        "pEX-2A":     {"origin": "pUC",     "marker": "hygB",     "promoter": "glaA",    "copy": "high",    "resistance": "hygromycin",   "size": "8.2 kb"},
        "pESC-URA":   {"origin": "2μ",      "marker": "URA3",     "promoter": "GAL1",    "copy": "medium",  "resistance": "none",          "size": "6.5 kb"},
        "pET-28b":    {"origin": "pBR322",  "marker": "kanR",     "promoter": "T7",      "copy": "high",    "resistance": "kanamycin",    "size": "5.4 kb"},
        "pHT43":      {"origin": "pHT",     "marker": "catR",     "promoter": "PaprE",   "copy": "medium",  "resistance": "chloramphenicol","size": "7.1 kb"},
        "pGAPzα":     {"origin": "pUC",     "marker": "zeoR",     "promoter": "GAP",     "copy": "high",    "resistance": "zeocin",       "size": "4.8 kb"},
        "pChlamy":    {"origin": "cpDNA",   "marker": "bleR",     "promoter": "psaD",    "copy": "low",     "resistance": "phleomycin",   "size": "9.3 kb"},
    }
    for p, f in plasmids_core.items():
        for k, v in f.items():
            why = {"origin": "replication origin", "marker": "selection marker",
                   "promoter": "expression promoter", "copy": "copy number regime",
                   "resistance": "antibiotic resistance", "size": "plasmid size"}[k]
            loci.append({"project": proj, "key": f"plasmid_{p}_{k}", "value": v, "why": why})

    # ── Core personnel (hand-authored) ──
    personnel_core = {
        "drm":  {"role": "Director of R&D",       "dept": "R&D",       "clearance": "Omega",   "specialty": "synthetic biology",   "projects": "all",             "origin": "MIT"},
        "ljm":  {"role": "Lead Strain Engineer",  "dept": "Strain Eng", "clearance": "Delta",   "specialty": "yeast engineering",   "projects": "yeast-o1, asper-k1","origin": "Stanford"},
        "rkb":  {"role": "Fermentation Lead",     "dept": "Process Dev", "clearance": "Delta",  "specialty": "scale-up bioreactors","projects": "bac-m1, pichia-x7","origin": "ETH Zurich"},
        "pcs":  {"role": "Analytical Biochemist",  "dept": "Analytics",  "clearance": "Gamma",  "specialty": "mass spec proteomics", "projects": "cellB, proK",     "origin": "Caltech"},
        "tmn":  {"role": "Regulatory Affairs",     "dept": "Compliance", "clearance": "Gamma",  "specialty": "FDA/EMA submissions",  "projects": "all",             "origin": "FDA alum"},
        "vxz":  {"role": "Bioinformatics Lead",    "dept": "Bioinfo",    "clearance": "Delta",  "specialty": "genome assembly",      "projects": "chlamy-c4, coli-s8","origin": "Cambridge"},
        "jrk":  {"role": "Lab Manager",            "dept": "Operations", "clearance": "Beta",   "specialty": "lab automation",       "projects": "all",             "origin": "Industry"},
    }
    for p, f in personnel_core.items():
        for k, v in f.items():
            why = {"role": "job title", "dept": "department", "clearance": "security clearance",
                   "specialty": "area of expertise", "projects": "assigned projects",
                   "origin": "educational background"}[k]
            loci.append({"project": proj, "key": f"person_{p}_{k}", "value": v, "why": why})

    # ── Core regulatory approvals (hand-authored) ──
    reg_core = {
        "FDA-2024-089": {"authority": "FDA",  "status": "approved",    "expires": "2027-06", "scope": "cellB for food processing",            "type": "GRAS"},
        "EPA-2024-012": {"authority": "EPA",  "status": "under review","expires": "2025-12", "scope": "lipA for bioremediation",               "type": "TSCA"},
        "EMA-2024-045": {"authority": "EMA",  "status": "approved",    "expires": "2028-03", "scope": "proK as detergent additive",            "type": "enzyme"},
        "OSHA-2024-3":  {"authority": "OSHA", "status": "certified",   "expires": "2026-09", "scope": "asper-k1 contained use BSL-2",           "type": "containment"},
    }
    for r, f in reg_core.items():
        for k, v in f.items():
            why = {"authority": "regulatory body", "status": "current status",
                   "expires": "expiration date", "scope": "approved scope",
                   "type": "regulatory type"}[k]
            loci.append({"project": proj, "key": f"reg_{r}_{k}", "value": v, "why": why})

    # ── Core raw materials (hand-authored) ──
    mats_core = {
        "cellulose_feed":  {"supplier": "Sigma-Aldrich", "storage": "RT",     "unit_cost": "$12/kg",   "stock": "250 kg",  "purity": "98%"},
        "yeast_extract":   {"supplier": "BD Biosciences","storage": "4°C",    "unit_cost": "$45/kg",   "stock": "80 kg",   "purity": "USP"},
        "peptone":         {"supplier": "ThermoFisher",  "storage": "RT",     "unit_cost": "$28/kg",   "stock": "120 kg",  "purity": "99%"},
        "glucose":         {"supplier": "Cargill",       "storage": "RT",     "unit_cost": "$3/kg",    "stock": "500 kg",  "purity": "dextrose"},
        "IPTG":            {"supplier": "GoldBio",       "storage": "-20°C",  "unit_cost": "$180/10g",  "stock": "50 g",    "purity": ">98% HPLC"},
        "hygromycin":      {"supplier": "InvivoGen",     "storage": "4°C",    "unit_cost": "$220/g",    "stock": "15 g",    "purity": "sterile"},
    }
    for m, f in mats_core.items():
        for k, v in f.items():
            why = {"supplier": "primary supplier", "storage": "storage condition",
                   "unit_cost": "cost per unit", "stock": "current stock level",
                   "purity": "purity specification"}[k]
            loci.append({"project": proj, "key": f"mat_{m}_{k}", "value": v, "why": why})

    # ── Distractor bulk: extra strains ──
    hosts = ["E. coli", "S. cerevisiae", "P. pastoris", "B. subtilis", "A. sojae", "Y. lipolytica", "C. reinhardtii", "K. phaffii"]
    vectors = ["pET-28b", "pBAD33", "pGAPzα", "pESC-URA", "pHT43", "pChlamy", "pJN4522", "pEX-2A"]
    promoters = ["T7", "GAL1", "GAP", "araBAD", "PaprE", "glaA", "psaD", "TEF", "ADH1", "GPD"]
    markers = ["kanR", "hygB", "zeoR", "catR", "URA3", "bleR", "nptII", "cmR", "specR", "tetR"]
    apps = ["cellulase", "protease", "lipase", "phytase", "insulin", "ethanol", "protein", "lipid", "silk", "antibody"]
    stab = ["A", "B", "C"]
    exp_lvls = ["high", "medium", "low"]
    seen_strains = set()
    while len([x for x in loci if x["key"].startswith("strain_") and x["key"].endswith("_host")]) < 80:
        nm = f"{rnd.choice(['alp','bet','gam','del','eps','zet','eta','the','iot','kap','lam','mu','nu','xi','omc','pi','rho','sig','tau','ups','phi','chi','psi','ome'])}-{rnd.choice(['a','b','c','d','e','f','g','h','i','j'])}{rnd.randint(0,9)}"
        if nm in seen_strains:
            continue
        seen_strains.add(nm)
        for k, v, why in [
            ("host", rnd.choice(hosts), "production host organism"),
            ("vector", rnd.choice(vectors), "expression vector backbone"),
            ("promoter", rnd.choice(promoters), "driving transcription"),
            ("marker", rnd.choice(markers), "selection marker"),
            ("application", rnd.choice(apps), "intended product"),
            ("stability", rnd.choice(stab), "genetic stability grade"),
            ("expression", rnd.choice(exp_lvls), "observed expression level"),
        ]:
            loci.append({"project": proj, "key": f"strain_{nm}_{k}", "value": v, "why": why})

    # ── Distractor bulk: extra enzymes ──
    ecs = ["3.2.1.4", "3.1.1.3", "3.4.21.62", "3.2.1.8", "3.2.1.1", "3.1.3.8", "3.4.11.1", "3.5.1.5", "2.3.1.1", "1.1.1.1"]
    sources = ["T. reesei", "A. sojae", "B. subtilis", "C. antarctica", "A. oryzae", "E. coli", "P. pastoris", "S. cerevisiae", "Y. lipolytica", "R. oryzae"]
    temps = ["37°C", "40°C", "45°C", "50°C", "55°C", "60°C"]
    phs = ["4.0", "4.5", "5.0", "5.5", "6.0", "6.5", "7.0", "7.5", "8.0"]
    substrates = ["cellulose", "xylan", "starch", "casein", "triglycerides", "phytate", "pectin", "lactose", "sucrose", "chitin"]
    products_list = ["glucose", "xylose", "maltose", "peptides", "fatty acids", "phosphate", "galactose", "N-acetylglucosamine", "fructose", "ethanol"]
    seen_enzymes = set()
    while len([x for x in loci if x["key"].startswith("enz_") and x["key"].endswith("_ec")]) < 40:
        nm = f"{rnd.choice(['cel','lip','pro','xyn','amy','phyt','pec','lac','suc','chi'])}{rnd.choice(['A','B','C','D','E','F','G','H','I','J'])}"
        if nm in seen_enzymes:
            continue
        seen_enzymes.add(nm)
        for k, v, why in [
            ("ec", rnd.choice(ecs), "Enzyme Commission number"),
            ("source", rnd.choice(sources), "source organism"),
            ("opt_temp", rnd.choice(temps), "optimal temperature"),
            ("opt_ph", rnd.choice(phs), "optimal pH"),
            ("substrate", rnd.choice(substrates), "catalytic substrate"),
            ("product", rnd.choice(products_list), "reaction product"),
            ("turnover", f"{rnd.randint(200,3000)} /s", "turnover rate"),
        ]:
            loci.append({"project": proj, "key": f"enz_{nm}_{k}", "value": v, "why": why})

    # ── Distractor bulk: extra sensors ──
    targets = ["glucose", "arsenite", "pH", "AHL", "cadmium", "mercury", "lactate", "ATP", "H2O2", "nitrate"]
    outputs = ["GFP", "mCherry", "luciferase", "YFP", "CFP", "β-lactamase", "RFP", "lacZ"]
    sensor_hosts = ["E. coli", "S. cerevisiae", "B. subtilis", "P. pastoris", "C. reinhardtii"]
    for i in range(20):
        nm = f"{rnd.choice(['gluc','tox','pH','quor','metal','lac','atp','perox','nit','redox'])}-{rnd.randint(1,9)}"
        loci.append({"project": proj, "key": f"sense_{nm}_target", "value": rnd.choice(targets), "why": "analyte detected"})
        loci.append({"project": proj, "key": f"sense_{nm}_range", "value": f"{rnd.randint(1,100)}-{rnd.randint(200,1000)} {rnd.choice(['mM','ppb','nM'])}", "why": "detection range"})
        loci.append({"project": proj, "key": f"sense_{nm}_output", "value": rnd.choice(outputs), "why": "reporter output"})
        loci.append({"project": proj, "key": f"sense_{nm}_host", "value": rnd.choice(sensor_hosts), "why": "chassis organism"})

    # ── Distractor bulk: extra plasmids ──
    origins = ["pUC", "pBR322", "2μ", "pHT", "cpDNA", "RK2", "R6K", "ColE1"]
    resistances = ["ampicillin", "kanamycin", "hygromycin", "zeocin", "chloramphenicol", "tetracycline", "streptomycin", "gentamicin"]
    copy_nums = ["high", "medium", "low", "very low"]
    for i in range(30):
        nm = f"p{rnd.choice(['EX','ESC','ET','HT','GAP','Chlam','JN','BAD','UC','RK'])}{rnd.randint(1,9)}"
        loci.append({"project": proj, "key": f"plasmid_{nm}_origin", "value": rnd.choice(origins), "why": "replication origin"})
        loci.append({"project": proj, "key": f"plasmid_{nm}_marker", "value": rnd.choice(markers), "why": "selection marker"})
        loci.append({"project": proj, "key": f"plasmid_{nm}_copy", "value": rnd.choice(copy_nums), "why": "copy number regime"})
        loci.append({"project": proj, "key": f"plasmid_{nm}_resistance", "value": rnd.choice(resistances), "why": "antibiotic resistance"})

    # ── Distractor bulk: extra personnel ──
    firsts = ["Ava", "Bao", "Cruz", "Dax", "Elu", "Fyn", "Gia", "Hux", "Ivo", "Jex",
              "Kai", "Lux", "Myo", "Nyx", "Onyx", "Pax", "Qin", "Rex", "Sol", "Taj"]
    roles = ["Scientist I", "Scientist II", "Associate Engineer", "Senior Engineer", "Lab Tech", "Postdoc", "Research Assistant"]
    depts = ["Strain Eng", "Process Dev", "Analytics", "Bioinfo", "Compliance", "Operations", "Media Prep", "QC"]
    specialties = ["molecular cloning", "fermentation", "HPLC", "LC-MS", "genome editing", "protein purification", "bioinformatics", "ELISA"]
    for i in range(40):
        loci.append({"project": proj, "key": f"person_{i:03d}_role", "value": rnd.choice(roles), "why": "job title"})
        loci.append({"project": proj, "key": f"person_{i:03d}_dept", "value": rnd.choice(depts), "why": "department"})
        loci.append({"project": proj, "key": f"person_{i:03d}_clearance", "value": rnd.choice(["Alpha", "Beta", "Gamma", "Delta"]), "why": "security clearance"})
        loci.append({"project": proj, "key": f"person_{i:03d}_specialty", "value": rnd.choice(specialties), "why": "area of expertise"})

    # ── Distractor bulk: extra regulatory ──
    agencies = ["FDA", "EPA", "EMA", "OSHA", "CFIA", "PMDA"]
    statuses = ["approved", "under review", "certified", "expired", "withdrawn"]
    for i in range(25):
        rid = f"{rnd.choice(agencies)}-2024-{rnd.randint(100,999)}"
        loci.append({"project": proj, "key": f"reg_{rid}_authority", "value": rnd.choice(agencies), "why": "regulatory body"})
        loci.append({"project": proj, "key": f"reg_{rid}_status", "value": rnd.choice(statuses), "why": "current status"})
        loci.append({"project": proj, "key": f"reg_{rid}_scope", "value": f"{rnd.choice(['enzyme','strain','containment','sensor','material'])} approval", "why": "approved scope"})

    # ── Distractor bulk: extra materials ──
    suppliers = ["Sigma-Aldrich", "ThermoFisher", "BD Biosciences", "Cargill", "GoldBio", "InvivoGen", "Millipore", "VWR"]
    storages = ["RT", "4°C", "-20°C", "-80°C", "LN2"]
    for i in range(35):
        nm = f"mat_{rnd.choice(['feed','extract','salt','buffer','antibiotic','sugar','amino_acid','vitamin','inducer','metal'])}_{i}"
        loci.append({"project": proj, "key": f"{nm}_supplier", "value": rnd.choice(suppliers), "why": "primary supplier"})
        loci.append({"project": proj, "key": f"{nm}_storage", "value": rnd.choice(storages), "why": "storage condition"})
        loci.append({"project": proj, "key": f"{nm}_stock", "value": f"{rnd.randint(10,500)} {rnd.choice(['kg','g','L'])}", "why": "current stock level"})
        loci.append({"project": proj, "key": f"{nm}_unit_cost", "value": f"${rnd.randint(1,500)}/{rnd.choice(['kg','g','L','10g'])}", "why": "cost per unit"})

    # ══ SHORT-TERM MEMORIES (~400) — small episodic incidents ══
    verbs_short = ["contamination detected", "pH spike", "temperature excursion", "pressure drop",
                   "OD reading anomaly", "DO probe failure", "pump calibration off", "valve stuck",
                   "sample mislabeled", "centrifuge imbalance", "autoclave cycle abort", "power flicker",
                   "network outage", "cold chain break", "alarm triggered"]
    for i in range(400):
        svc = rnd.choice(list(seen_strains)) if seen_strains else "alp-a1"
        memory.append({"tier": "short", "ref": f"evt-{i:03d}",
                       "topic": rnd.choice(["incident", "alarm", "deviation", "QC failure", "equipment"]),
                       "content": f"{rnd.choice(verbs_short)} in {rnd.choice(['strain','enzyme','sensor','media','analytics'])} lab "
                                  f"on {rnd.choice(['Deck 1','Deck 2','Deck 3','Deck 4','Deck 5'])} at "
                                  f"{rnd.randint(0,23):02d}:{rnd.randint(0,59):02d}; "
                                  f"{rnd.choice(['contained','escalated to lead','logged only','required shutdown'])}."})

    # ══ LONG-TERM MEMORIES (~500) — larger overview conglomerates ══
    long_topics = ["project review", "campaign summary", "strain history", "method validation",
                   "patent filing", "collaboration report", "quarterly review", "safety investigation",
                   "scale-up report", "technology transfer", "audit findings", "strategic review"]
    for i in range(500):
        memory.append({"tier": "long", "ref": f"rep-{i:03d}",
                       "topic": rnd.choice(long_topics),
                       "content": f"{rnd.choice(['Q1','Q2','Q3','Q4','FY'])} "
                                  f"{rnd.choice(['2023','2024','2025'])} — "
                                  f"{rnd.choice(['asper-k1','yeast-e2','coli-p3','bac-m1','pichia-x7','chlamy-c4','yeast-o1','coli-s8'])} — "
                                  f"{rnd.choice(['titer improved by','yield reached','contamination rate','productivity gain','cost reduction','regulatory milestone','patent filed','collaboration started'])} "
                                  f"{rnd.choice(['15%','22%','30%','40%','50%','2x','3x','4x'])} — "
                                  f"{rnd.choice(['team-atlas','team-ledger','team-edge','team-forge','team-nova','team-pulse'])} "
                                  f"{rnd.choice(['led','supported','reviewed','audited'])} the effort."})

    # ══ NEAR-TERM MEMORIES (~100) — future tasks ══
    near_tasks = [
        "Prepare the pichia-x7 fed-batch inoculum for the 100 L scale-up next Monday.",
        "Run the QC HPLC on the cellB batch before the FDA inspector arrives Thursday.",
        "Draft the SOP for the new glucSense biosensor deployment in the pilot plant.",
        "Order fresh IPTG and hygromycin — stock below minimum threshold.",
        "Schedule the BSL-2 recertification walkthrough for the asper-k1 suite.",
        "Review the lipA bioremediation data package for the EPA submission deadline.",
        "Complete the yeast-o1 stability assay at 30 generations before the quarterly review.",
        "Prepare the monthly fermentation yield report for Dr. Reyes (drm).",
        "Update the plasmid registry with the new pET-28b variant sequences.",
        "Send the proK samples to the external proteomics lab for activity confirmation.",
        "Calibrate the DO probes on all six bioreactors before the weekend runs.",
        "Finalize the risk assessment for the chlamy-c4 open-pond trial.",
        "Migrate the LIMS database to the new schema — downtime window next Sunday.",
        "Compile the annual enzyme library catalog for the partner distribution deal.",
        "Run the cross-membrane binding assay for the metalLock sensor validation.",
        "Prepare the seed bank cryovials for the bac-m1 master stock replenishment.",
        "Draft the method transfer package for the CMO partner in Singapore.",
        "Order replacement parts for the centrifuge — imbalance vibration worsening.",
        "Review the audit findings from the Q3 compliance walk and assign CAPAs.",
        "Extract RNA from the yeast-e2 stress test time points for RNA-seq.",
        "Validate the new pHmeter calibration curve across the 4.0-9.0 range.",
        "Submit the patent amendment for the proK variant with improved thermostability.",
        "Schedule the monthly one-on-one with each strain engineering team member.",
        "Procure the deuterated substrate for the cellB kinetic isotope study.",
        "Prepare the slide deck for the quarterly board presentation on asper-k1 yields.",
        "Update the BOM for the defined medium formulation — peptone lot change.",
        "Run the in silico docking screen for the new phytase variant library.",
        "Coordinate with the QC lab on the out-of-spec result for the coli-p3 batch.",
        "Plan the contingency for the -80°C freezer alarm — spare unit on Deck 3.",
        "Draft the response to the EMA query on the proK detergent safety dossier.",
    ]
    for i, intent in enumerate(near_tasks):
        memory.append({"tier": "near", "ref": f"todo-{i:03d}", "topic": "todo", "intent": intent})
    # Fill remaining near-term with templated ones
    near_verbs = ["Plan", "Draft", "Review", "Schedule", "Prepare", "Order", "Update", "Validate", "Coordinate", "Finalize"]
    for i in range(len(near_tasks), 100):
        memory.append({"tier": "near", "ref": f"todo-{i:03d}", "topic": "todo",
                       "intent": f"{rnd.choice(near_verbs)} the {rnd.choice(['strain','enzyme','sensor','plasmid','material','regulatory','personnel','equipment'])} "
                                  f"{rnd.choice(['review','update','audit','report','validation','calibration','submission','meeting'])} "
                                  f"for {rnd.choice(['Q1','Q2','Q3','Q4','FY'])} "
                                  f"{rnd.choice(['2024','2025','2026'])}."})

    # ── QUESTIONS ──
    # Loci questions about core strains
    for s in strains_core:
        questions.append({"asks": "loci", "query": f"what host does the {s} strain use?",
                          "expect_key": [f"{proj}/strain_{s}_host"]})
        questions.append({"asks": "loci", "query": f"what is the application of strain {s}?",
                          "expect_key": [f"{proj}/strain_{s}_application"]})
    # Loci questions about core enzymes
    for e in enzymes_core:
        questions.append({"asks": "loci", "query": f"what is the EC number of {e}?",
                          "expect_key": [f"{proj}/enz_{e}_ec"]})
        questions.append({"asks": "loci", "query": f"what is the optimal temperature for {e}?",
                          "expect_key": [f"{proj}/enz_{e}_opt_temp"]})
    # Loci questions about core sensors
    for s in sensors_core:
        questions.append({"asks": "loci", "query": f"what does the {s} sensor detect?",
                          "expect_key": [f"{proj}/sense_{s}_target"]})
    # Loci questions about core personnel
    for p in personnel_core:
        questions.append({"asks": "loci", "query": f"what is {p}'s role?",
                          "expect_key": [f"{proj}/person_{p}_role"]})
    # Loci questions about core plasmids
    for p in plasmids_core:
        questions.append({"asks": "loci", "query": f"what is the origin of {p}?",
                          "expect_key": [f"{proj}/plasmid_{p}_origin"]})
        questions.append({"asks": "loci", "query": f"what marker does {p} carry?",
                          "expect_key": [f"{proj}/plasmid_{p}_marker"]})
    # Loci questions about regulatory
    for r in reg_core:
        questions.append({"asks": "loci", "query": f"what authority approved {r}?",
                          "expect_key": [f"{proj}/reg_{r}_authority"]})

    # Memory questions (short-term)
    questions.append({"asks": "memory", "query": "what was mislabeled in the enzyme lab?",
                      "expect_ref": ["evt-000"], "expect_content": ["sample mislabeled", "contained"]})
    questions.append({"asks": "memory", "query": "what valve issue happened in the enzyme lab?",
                      "expect_ref": ["evt-001"], "expect_content": ["valve stuck", "logged only"]})

    # Memory questions (long-term)
    questions.append({"asks": "memory", "query": "what were the FY 2023 patent results for chlamy-c4?",
                      "expect_ref": ["rep-000"], "expect_content": ["FY", "2023", "chlamy-c4", "patent"]})
    questions.append({"asks": "memory", "query": "how did the Q3 2025 yield for chlamy-c4 go?",
                      "expect_ref": ["rep-001"], "expect_content": ["Q3", "2025", "chlamy-c4", "yield"]})

    # Memory questions (near-term)
    questions.append({"asks": "memory", "query": "what needs to be prepared for the 100 L scale-up?",
                      "expect_ref": ["todo-000"], "expect_content": ["pichia-x7", "fed-batch", "Monday"]})
    questions.append({"asks": "memory", "query": "what needs to be ordered before stock runs out?",
                      "expect_ref": ["todo-003"], "expect_content": ["IPTG", "hygromycin"]})

    # Corpus questions (multi-hop briefings)
    for s, f in strains_core.items():
        questions.append({"asks": "corpus", "query": f"give me a technical briefing on strain {s}",
                          "expect_content": [f["host"], f["vector"], f["promoter"], f["application"], f["titer"]]})
    for e, f in enzymes_core.items():
        questions.append({"asks": "corpus", "query": f"brief me on the {e} enzyme",
                          "expect_content": [f["source"], f["ec"], f["substrate"], f["product"], f["turnover"]]})

    # Extra questions to reach 90 total
    # More loci: materials, regulatory status, sensor range, plasmid resistance
    for m in ["cellulose_feed", "yeast_extract", "peptone", "glucose"]:
        questions.append({"asks": "loci", "query": f"who supplies {m}?",
                          "expect_key": [f"{proj}/mat_{m}_supplier"]})
    questions.append({"asks": "loci", "query": "what is the status of FDA-2024-089?",
                      "expect_key": [f"{proj}/reg_FDA-2024-089_status"]})
    questions.append({"asks": "loci", "query": "what is the detection range of glucSense?",
                      "expect_key": [f"{proj}/sense_glucSense_range"]})
    questions.append({"asks": "loci", "query": "what antibiotic resistance does pEX-2A carry?",
                      "expect_key": [f"{proj}/plasmid_pEX-2A_resistance"]})
    # More memory: short-term incident, long-term review, near-term task
    questions.append({"asks": "memory", "query": "what equipment alarm triggered in the media lab?",
                      "expect_ref": ["evt-002"], "expect_content": ["alarm triggered", "media lab"]})
    questions.append({"asks": "memory", "query": "what did the Q3 2024 review cover for pichia-x7?",
                      "expect_ref": ["rep-002"], "expect_content": ["Q3", "2024", "pichia-x7", "contamination"]})
    questions.append({"asks": "memory", "query": "what needs to be scheduled for the BSL-2 recertification?",
                      "expect_ref": ["todo-004"], "expect_content": ["BSL-2", "recertification", "asper-k1"]})
    # More corpus: key personnel briefings
    for p, f in [("drm", personnel_core["drm"]), ("ljm", personnel_core["ljm"]), ("rkb", personnel_core["rkb"])]:
        questions.append({"asks": "corpus", "query": f"give me a dossier on {p}",
                          "expect_content": [f["role"], f["dept"], f["clearance"], f["specialty"]]})
    questions.append({"asks": "corpus", "query": "brief me on the regulatory status of the cellB enzyme",
                      "expect_content": ["FDA", "GRAS", "food processing", "2027-06"]})

    # ── DECOYS (unrelated facts for NegativeTest store) ──
    decoy_strains = ["pseudo-a1", "pseudo-b2", "pseudo-c3", "pseudo-d4", "pseudo-e5"]
    for s in decoy_strains:
        decoy.append({"project": proj, "key": f"strain_{s}_host", "value": f"{rnd.choice(['E. coli', 'S. cerevisiae', 'P. pastoris'])}", "why": "production host"})
        decoy.append({"project": proj, "key": f"strain_{s}_vector", "value": f"p{rnd.choice(['FAKE','NULL','VOID','GHOST'])}", "why": "expression vector"})
        decoy.append({"project": proj, "key": f"strain_{s}_application", "value": rnd.choice(["nothing", "placeholder", "mock"]), "why": "intended product"})
    # Extra decoys from other domains' style
    for i in range(10):
        decoy.append({"project": proj, "key": f"phantom_enz_{i}_ec", "value": f"{rnd.randint(1,9)}.{rnd.randint(0,99)}.{rnd.randint(0,99)}.{rnd.randint(0,999)}", "why": "phantom enzyme"})
        decoy.append({"project": proj, "key": f"phantom_enz_{i}_source", "value": f"{rnd.choice(['M. tuberculosis', 'P. aeruginosa', 'S. aureus', 'C. botulinum'])}", "why": "source organism"})
    # Extra decoys to reach 50 total
    for i in range(5):
        decoy.append({"project": proj, "key": f"phantom_sense_{i}_target", "value": f"fake-{rnd.choice(['protein','metabolite','toxin'])}", "why": "phantom sensor target"})
        decoy.append({"project": proj, "key": f"phantom_sense_{i}_output", "value": rnd.choice(["GFP", "mCherry", "luciferase"]), "why": "phantom reporter"})
        decoy.append({"project": proj, "key": f"phantom_plasmid_{i}_origin", "value": f"p{rnd.choice(['FAKE','VOID','GHOST'])}", "why": "phantom origin"})

    return proj, loci, memory, questions, decoy


# ══════════════════════════════════════════════════════════════════════════
#  Domain 6 — ORKRAIL: grimdark orkish rail network with explicit LINKING
#  facts for multi-hop questions.  ~3.8k Loci, 1k memory, 90 questions, 50
#  decoys.  Every multi-hop question is answerable from the corpus because
#  the linking facts (service→loco→depot→supplier, station→warboss→clan,
#  line→warboss→clan, depot→warboss→clan) are all present as Loci keys.
# ══════════════════════════════════════════════════════════════════════════
def orkrail():
    proj = "orkrail"
    rnd = random.Random(5005)

    loci = []
    memory = []
    questions = []
    decoy = []

    # ── Entity name pools ──
    station_names = [
        "gorkamork-central", "dakka-junction", "teef-exchange", "squig-market",
        "promethium-depot", "armor-yard", "grot-halt", "mekboy-forge",
        "waagh-gate", "kustom-kutter", "battle-wagon-stop", "zogwort-folly",
        "bad-moon-basin", "blood-axe-bend", "evil-sunz-spur", "goff-rock",
        "snakebite-siding", "deff-dread-halt", "killa-kan-crossing", "warboss-terrace",
        "dakka-plains", "teef-tower", "squig-pen", "promethium-pit",
        "armor-gate", "grot-warren", "mekboy-station", "kustom-keep",
        "battlefront", "supply-ridge", "zogwort-zoo", "bad-moon-bazaar",
        "blood-axe-barracks", "evil-sunz-speedway", "goff-armory", "snakebite-market",
        "deff-dread-garage", "killa-kan-pen", "warboss-palace", "dakka-dome",
    ]

    line_ids = [
        "dakka-line", "teef-railway", "waagh-express", "squig-belt",
        "promethium-main", "armored-corridor", "grot-shuttle", "mekboy-local",
        "kustom-kutter-line", "battlefront-rail", "supply-wagon-way", "zogwort-tunnel",
    ]

    loco_classes = [
        "big-choppa", "dakka-jet", "teef-train", "squig-hauler",
        "promethium-puller", "armor-wagon", "grot-mover", "mekboy-special",
        "kustom-kroozer", "battle-barge", "supply-sled", "zogwort-zogger",
    ]
    loco_ids = []
    for cls in loco_classes:
        for i in range(5):
            loco_ids.append(f"{cls}-{i}")

    depot_names = [
        "gork-forge", "mork-foundry", "mekboy-yard", "teef-smelter",
        "armor-plate", "promethium-refuel", "kustom-workshop", "deff-dread-garage",
        "killa-kan-stable", "battle-bunker", "squig-feed-mill", "grot-labor-pen",
        "dakka-arsenal", "teef-vault", "bad-moon-smithy", "blood-axe-forge",
        "evil-sunz-speedshop", "goff-armory-works", "snakebite-tannery", "zogwort-lab",
        "mekboy-foundry", "kustom-kombat-works", "battle-wagon-bay", "supply-depot",
        "promethium-tank", "gork-furnace", "mork-smelter", "deff-dread-pit",
        "killa-kan-kennel", "warboss-armory",
    ]

    supplier_names = [
        "squig-feeders", "teef-traders", "promethium-peddlers", "grot-labor",
        "mekboy-parts", "dakka-dealers", "armor-acquirers", "kustom-katalog",
        "squig-ranchers", "teef-smugglers", "promethium-refiners", "grot-herders",
        "mekboy-design", "dakka-imports", "armor-smiths", "kustom-gubbinz",
        "bad-moon-supply", "blood-axe-trade", "evil-sunz-speedparts", "goff-armor-supply",
        "snakebite-feed", "zogwort-oddments", "deff-dread-parts", "killa-kan-kits",
        "warboss-commissary", "gork-supply", "mork-trade", "waagh-logistics",
        "dakka-distributors", "teef-collectors", "squig-butchers", "promethium-haulers",
        "mekboy-catalog", "kustom-kombat-supply", "battle-wagon-parts", "armor-plate-co",
        "grot-rentals", "deff-dread-armor", "killa-kan-feed", "warboss-gear",
    ]

    warboss_names = [
        "boss-gitsnik", "boss-skragbad", "boss-mogrok", "boss-zogwort",
        "boss-snaggletooth", "boss-gutfang", "boss-rottenjaw", "boss-blackfang",
        "boss-ironhide", "boss-gnashrag", "boss-skullkrak", "boss-bloodfang",
        "boss-dakkaface", "boss-teefsnatcha", "boss-squigherder", "boss-mekboss",
        "boss-kustomkutter", "boss-battleaxe", "boss-supplymaster", "boss-promethium",
        "boss-armorplate", "boss-grotkeeper", "boss-deffdread", "boss-killakan",
        "boss-waaghleader", "boss-dakka-king", "boss-teef-lord", "boss-squig-king",
        "boss-promethium-lord", "boss-armor-lord", "boss-mekboy-overlord", "boss-kustom-king",
        "boss-battle-lord", "boss-supply-lord", "boss-zogwort-lord", "boss-bad-moon",
        "boss-blood-axe", "boss-evil-sunz", "boss-goff", "boss-snakebite",
        "boss-gork", "boss-mork", "boss-waaghboss", "boss-high-dakka",
        "boss-teef-master", "boss-squig-master", "boss-promethium-master", "boss-armor-master",
        "boss-kustom-master", "boss-battle-master",
    ]

    # ── Generate STATIONS (40 × 8 = 320 loci) ──
    zones = ["norf", "souf", "east", "west", "centr-al", "bad-moon", "blood-axe",
             "evil-sunz", "goff", "snakebite"]
    conditions = ["workin' good", "fixin' up", "grot overrun", "kustomized",
                   "fortified real 'ard", "a bit krumpled"]
    dakka_ratings = ["lotsa dakka", "'eavy dakka", "enuff dakka", "not enuff dakka", "needz moar dakka"]

    station_warboss_map = {}   # station → warboss for linking
    for s in station_names:
        line = rnd.choice(line_ids)
        zone = rnd.choice(zones)
        platforms = rnd.randint(2, 8)
        interchange = rnd.choice([True, False])
        warboss = rnd.choice(warboss_names)
        condition = rnd.choice(conditions)
        dakka = rnd.choice(dakka_ratings)
        grotcount = rnd.randint(10, 500)
        station_warboss_map[s] = warboss
        loci.append({"project": proj, "key": f"station_{s}_line",      "value": line,      "why": "serving line"})
        loci.append({"project": proj, "key": f"station_{s}_zone",      "value": zone,      "why": "territory zone"})
        loci.append({"project": proj, "key": f"station_{s}_platforms",  "value": str(platforms), "why": "platform count"})
        loci.append({"project": proj, "key": f"station_{s}_interchange","value": str(interchange),"why": "interchange capability"})
        loci.append({"project": proj, "key": f"station_{s}_warboss",   "value": warboss,   "why": "warboss in charge"})
        loci.append({"project": proj, "key": f"station_{s}_condition", "value": condition, "why": "current condition"})
        loci.append({"project": proj, "key": f"station_{s}_dakka",     "value": dakka,     "why": "dakka rating"})
        loci.append({"project": proj, "key": f"station_{s}_grotcount", "value": str(grotcount), "why": "grot count"})

    # ── Generate LINES (12 × 6 = 72 loci) ──
    gauges = ["orkish-wide", "orkish-standard", "orkish-narrow", "orkish-broad"]
    colors = ["red", "black", "yellow", "purple", "green", "blue", "orange", "checkered"]
    line_statuses = ["runnin'", "buildin'", "mothballed", "warzone"]
    line_warboss_map = {}
    for lid in line_ids:
        cls = rnd.choice(["primary", "secondary", "feeder", "military"])
        gauge = rnd.choice(gauges)
        color = rnd.choice(colors)
        stops = rnd.randint(3, 12)
        status = rnd.choice(line_statuses)
        warboss = rnd.choice(warboss_names)
        line_warboss_map[lid] = warboss
        loci.append({"project": proj, "key": f"line_{lid}_class",   "value": cls,     "why": "line classification"})
        loci.append({"project": proj, "key": f"line_{lid}_gauge",   "value": gauge,   "why": "track gauge"})
        loci.append({"project": proj, "key": f"line_{lid}_color",   "value": color,   "why": "heraldic color"})
        loci.append({"project": proj, "key": f"line_{lid}_stops",   "value": str(stops),"why": "number of stops"})
        loci.append({"project": proj, "key": f"line_{lid}_status",  "value": status,  "why": "operational status"})
        loci.append({"project": proj, "key": f"line_{lid}_warboss", "value": warboss, "why": "warboss overseeing line"})

    # ── Generate LOCO/WAGONS (60 × 8 = 480 loci) ──
    fuel_sources = ["promethium", "teef-burner", "squig poop", "dakka backblast", "grot battery"]
    gubbinz_list = ["extra armor", "dakka turret", "grot cannon", "spiky bits",
                     "loud horn", "smoke launcher", "cargo claw", "teef safe"]
    loco_depot_map = {}    # loco_id → depot
    loco_line_map = {}     # loco_id → line
    for loco_id in loco_ids:
        cls_name = loco_id.rsplit("-", 1)[0]   # e.g. big-choppa
        depot = rnd.choice(depot_names)
        assigned_line = rnd.choice(line_ids)
        armor = rnd.choice(["real 'eavy", "medium", "light", "reinforced", "kustom"])
        gubbinz = rnd.choice(gubbinz_list)
        fuel = rnd.choice(fuel_sources)
        kustomization = rnd.choice(["recent", "old", "none", "partial", "full"])
        killcount = rnd.randint(0, 500)
        loco_depot_map[loco_id] = depot
        loco_line_map[loco_id] = assigned_line
        loci.append({"project": proj, "key": f"loco_{loco_id}_class",         "value": cls_name,       "why": "locomotive class"})
        loci.append({"project": proj, "key": f"loco_{loco_id}_depot",        "value": depot,          "why": "assigned depot"})
        loci.append({"project": proj, "key": f"loco_{loco_id}_line",         "value": assigned_line,  "why": "assigned line"})
        loci.append({"project": proj, "key": f"loco_{loco_id}_armor",        "value": armor,          "why": "armor rating"})
        loci.append({"project": proj, "key": f"loco_{loco_id}_gubbinz",      "value": gubbinz,        "why": "special equipment"})
        loci.append({"project": proj, "key": f"loco_{loco_id}_fuel",         "value": fuel,           "why": "fuel source"})
        loci.append({"project": proj, "key": f"loco_{loco_id}_kustomization","value": kustomization,  "why": "last kustomization"})
        loci.append({"project": proj, "key": f"loco_{loco_id}_killcount",    "value": str(killcount), "why": "kill count"})

    # ── Generate SCHEDULED SERVICES (200 × 6 = 1200 loci) ──
    cargo_types = ["dakka", "teef", "squigs", "promethium", "armor plates",
                    "grots", "gubbinz", "mekboy parts", "kustom krok", "battle wagon"]
    srv_loco_map = {}
    srv_ids = []
    for i in range(200):
        sid = f"srv-{i:03d}"
        srv_ids.append(sid)
        origin = rnd.choice(station_names)
        dest = rnd.choice(station_names)
        while dest == origin:
            dest = rnd.choice(station_names)
        line = rnd.choice(line_ids)
        loco = rnd.choice(loco_ids)
        warband = rnd.choice(warboss_names)
        cargo = rnd.choice(cargo_types)
        srv_loco_map[sid] = loco
        loci.append({"project": proj, "key": f"sched_{sid}_origin",  "value": origin, "why": "origin station"})
        loci.append({"project": proj, "key": f"sched_{sid}_dest",    "value": dest,   "why": "destination station"})
        loci.append({"project": proj, "key": f"sched_{sid}_line",    "value": line,   "why": "operating line"})
        loci.append({"project": proj, "key": f"sched_{sid}_loco",    "value": loco,   "why": "assigned locomotive"})
        loci.append({"project": proj, "key": f"sched_{sid}_warband", "value": warband,"why": "warband in charge"})
        loci.append({"project": proj, "key": f"sched_{sid}_cargo",   "value": cargo,  "why": "cargo type"})

    # ── Generate DEPOTS/FORGES (30 × 7 = 210 loci) ──
    depot_types = ["forge", "foundry", "garage", "stable", "workshop",
                    "smelter", "refuel", "armory", "labor-pen", "arsenal"]
    depot_statuses = ["workin'", "growin'", "fixin' up", "mothballed"]
    depot_supplier_map = {}
    depot_warboss_map = {}
    for dep in depot_names:
        dtype = rnd.choice(depot_types)
        dline = rnd.choice(line_ids)
        capacity = rnd.randint(5, 50)
        warboss = rnd.choice(warboss_names)
        equipment = rnd.choice(["mekboy toolz", "grot labor", "kustom rig", "dakka forge",
                                "teef smelter", "armor press", "promethium pump", "squig grinder"])
        supplier = rnd.choice(supplier_names)
        status = rnd.choice(depot_statuses)
        depot_supplier_map[dep] = supplier
        depot_warboss_map[dep] = warboss
        loci.append({"project": proj, "key": f"depot_{dep}_type",      "value": dtype,    "why": "depot type"})
        loci.append({"project": proj, "key": f"depot_{dep}_line",      "value": dline,    "why": "serving line"})
        loci.append({"project": proj, "key": f"depot_{dep}_capacity",  "value": str(capacity),"why": "vehicle capacity"})
        loci.append({"project": proj, "key": f"depot_{dep}_warboss",   "value": warboss,  "why": "warboss in charge"})
        loci.append({"project": proj, "key": f"depot_{dep}_equipment", "value": equipment, "why": "primary equipment"})
        loci.append({"project": proj, "key": f"depot_{dep}_supplier",  "value": supplier, "why": "primary supplier"})
        loci.append({"project": proj, "key": f"depot_{dep}_status",    "value": status,   "why": "depot status"})

    # ── Generate CONTRACTORS/SUPPLY (40 × 6 = 240 loci) ──
    parts_list = ["squig feed", "teef ingots", "promethium drumz", "grot harnesses",
                   "mekboy gearz", "dakka shellz", "armor platez", "kustom circuitz"]
    zones_list = ["norf", "souf", "east", "west", "centr-al", "bad-moon",
                   "blood-axe", "evil-sunz", "goff", "snakebite"]
    supp_warboss_map = {}
    for supp in supplier_names:
        part = rnd.choice(parts_list)
        teef = rnd.randint(100, 5000)
        promethium = rnd.choice(["proppa-grade", "low-grade", "refined", "krude"])
        squig = rnd.choice(["feed", "breedin' stock", "meat", "hides"])
        warboss = rnd.choice(warboss_names)
        zone = rnd.choice(zones_list)
        supp_warboss_map[supp] = warboss
        loci.append({"project": proj, "key": f"supp_{supp}_part",       "value": part,        "why": "supplied part"})
        loci.append({"project": proj, "key": f"supp_{supp}_teef",       "value": str(teef),   "why": "teef cost per unit"})
        loci.append({"project": proj, "key": f"supp_{supp}_promethium", "value": promethium,  "why": "promethium grade"})
        loci.append({"project": proj, "key": f"supp_{supp}_squig",      "value": squig,       "why": "squig product"})
        loci.append({"project": proj, "key": f"supp_{supp}_warboss",    "value": warboss,     "why": "warboss overseeing"})
        loci.append({"project": proj, "key": f"supp_{supp}_zone",       "value": zone,        "why": "operating zone"})

    # ── Generate WARBOSSES/CREW (50 × 6 = 300 loci) ──
    clans = ["bad-moon", "blood-axe", "evil-sunz", "goff", "snakebite", "gork", "mork"]
    ranks = ["warboss", "big-mek", "warpsight", "nob", "mekboy", "weirdboy", "painboy"]
    domains = ["station", "line", "depot", "supply", "loco", "waagh"]
    boss_clan_map = {}
    for boss in warboss_names:
        clan = rnd.choice(clans)
        rank = rnd.choice(ranks)
        domain = rnd.choice(domains)
        loco = rnd.choice(loco_ids)
        depot = rnd.choice(depot_names)
        teef = rnd.randint(100, 10000)
        boss_clan_map[boss] = clan
        loci.append({"project": proj, "key": f"boss_{boss}_clan",    "value": clan,   "why": "ork clan"})
        loci.append({"project": proj, "key": f"boss_{boss}_rank",    "value": rank,   "why": "rank"})
        loci.append({"project": proj, "key": f"boss_{boss}_domain",  "value": domain, "why": "domain of control"})
        loci.append({"project": proj, "key": f"boss_{boss}_loco",    "value": loco,   "why": "preferred locomotive"})
        loci.append({"project": proj, "key": f"boss_{boss}_depot",   "value": depot,  "why": "home depot"})
        loci.append({"project": proj, "key": f"boss_{boss}_teef",    "value": str(teef),"why": "teef wealth"})

    # ── MEMORY ──
    short_verbs = ["train crashed", "bridge fell down", "loco caught fire", "grots escaped", "cargo spilled",
                    "signals busted", "track broke", "warband fought", "teef got stolen",
                    "squigs stampeded", "promethium leaked", "dakka went BOOM", "tunnel fell in",
                    "station riot", "mekboy exploded"]
    for i in range(800):
        memory.append({"tier": "short", "ref": f"evt-{i:03d}",
                       "topic": rnd.choice(["incident", "alarm", "skirmish", "accident", "equipment failure"]),
                       "content": f"{rnd.choice(short_verbs)} at {rnd.choice(station_names)} "
                                  f"on {rnd.choice(['Dawn','Midday','Dusk','Night','WAAAGH! hour'])} — "
                                  f"{rnd.choice(['sorted it','got bigger','sorted proppa','still goin','lookin into it'])}."})

    long_topics = ["kampaign summary", "build project", "waagh report", "line expansion",
                    "depot construction", "kustomization project", "supply audit",
                    "territory survey", "inventory review", "war report"]
    for i in range(1000):
        memory.append({"tier": "long", "ref": f"rep-{i:03d}",
                       "topic": rnd.choice(long_topics),
                       "content": f"{rnd.choice(['Q1','Q2','Q3','Q4','FY'])} "
                                  f"{rnd.choice(['234.M41','235.M41','236.M41','237.M41','238.M41'])} — "
                                  f"{rnd.choice(station_names)} — "
                                  f"{rnd.choice(['dakka shipment','teef tribute','squig harvest','promethium delivery','armor consignment','grot draft','kustom krok','waagh muster'])} "
                                  f"{rnd.choice(['15%','22%','30%','40%','50%','2x','3x','4x'])} — "
                                  f"{rnd.choice(warboss_names)} "
                                  f"{rnd.choice(['led','supported','reviewed','audited'])} da effort."})

    near_verbs = ["Raid", "Fortify", "Kustomize", "Get more", "Scout", "Build", "Fix", "Muster"]
    near_targets = ["station", "line", "depot", "supply route", "loco", "waagh camp", "teef vault", "squig pen"]
    for i in range(200):
        intent = f"{rnd.choice(near_verbs)} the {rnd.choice(near_targets)} at {rnd.choice(station_names)} for {rnd.choice(warboss_names)}."
        memory.append({"tier": "near", "ref": f"todo-{i:03d}", "topic": "todo",
                       "intent": intent, "content": intent})

    # ── QUESTIONS with labeled hop-depth ──
    # All expect_key chains are built from actual linking maps above, so every
    # hop is answerable from the corpus.

    # 1-hop: direct loci lookup (5 questions)
    questions.append({"asks": "loci", "query": "wot line runs ta gorkamork-central station?",
                      "expect_key": [f"{proj}/station_gorkamork-central_line"], "hops": 1})
    questions.append({"asks": "loci", "query": "ow much dakka at dakka-junction station?",
                      "expect_key": [f"{proj}/station_dakka-junction_dakka"], "hops": 1})
    questions.append({"asks": "loci", "query": "wot fuel does big-choppa-0 guzzle?",
                      "expect_key": [f"{proj}/loco_big-choppa-0_fuel"], "hops": 1})
    questions.append({"asks": "loci", "query": "wot cargo does srv-000 carry?",
                      "expect_key": [f"{proj}/sched_srv-000_cargo"], "hops": 1})
    questions.append({"asks": "loci", "query": "wot kinda depot type is gork-forge?",
                      "expect_key": [f"{proj}/depot_gork-forge_type"], "hops": 1})

    # 2-hop: station → warboss → clan
    s2 = "gorkamork-central"
    wb2 = station_warboss_map[s2]
    c2 = boss_clan_map[wb2]
    questions.append({"asks": "loci", "query": f"gimme da warboss of {s2} station — {wb2} — an da clan {c2}",
                      "expect_key": [f"{proj}/station_{s2}_warboss", f"{proj}/boss_{wb2}_clan"], "hops": 2})
    s2b = "dakka-junction"
    wb2b = station_warboss_map[s2b]
    c2b = boss_clan_map[wb2b]
    questions.append({"asks": "loci", "query": f"gimme da warboss of {s2b} station — {wb2b} — an da clan {c2b}",
                      "expect_key": [f"{proj}/station_{s2b}_warboss", f"{proj}/boss_{wb2b}_clan"], "hops": 2})

    # 2-hop: line → warboss → clan
    l2 = "dakka-line"
    wbl2 = line_warboss_map[l2]
    cl2 = boss_clan_map[wbl2]
    questions.append({"asks": "loci", "query": f"gimme da warboss of {l2} line — {wbl2} — an da clan {cl2}",
                      "expect_key": [f"{proj}/line_{l2}_warboss", f"{proj}/boss_{wbl2}_clan"], "hops": 2})
    l2b = "teef-railway"
    wbl2b = line_warboss_map[l2b]
    cl2b = boss_clan_map[wbl2b]
    questions.append({"asks": "loci", "query": f"gimme da warboss of {l2b} line — {wbl2b} — an da clan {cl2b}",
                      "expect_key": [f"{proj}/line_{l2b}_warboss", f"{proj}/boss_{wbl2b}_clan"], "hops": 2})

    # 2-hop: depot → warboss → clan
    d2 = "gork-forge"
    wbd2 = depot_warboss_map[d2]
    cd2 = boss_clan_map[wbd2]
    questions.append({"asks": "loci", "query": f"gimme da warboss of {d2} depot — {wbd2} — an da clan {cd2}",
                      "expect_key": [f"{proj}/depot_{d2}_warboss", f"{proj}/boss_{wbd2}_clan"], "hops": 2})
    d2b = "mork-foundry"
    wbd2b = depot_warboss_map[d2b]
    cd2b = boss_clan_map[wbd2b]
    questions.append({"asks": "loci", "query": f"gimme da warboss of {d2b} depot — {wbd2b} — an da clan {cd2b}",
                      "expect_key": [f"{proj}/depot_{d2b}_warboss", f"{proj}/boss_{wbd2b}_clan"], "hops": 2})

    # 3-hop: service → loco → depot → type
    sid3 = "srv-000"
    loco3 = srv_loco_map[sid3]
    dep3 = loco_depot_map[loco3]
    dt3 = [d["value"] for d in loci if d["key"] == f"depot_{dep3}_type"][0]
    questions.append({"asks": "loci", "query": f"gimme da loco {loco3} on {sid3}, depot {dep3} — type {dt3}",
                      "expect_key": [f"{proj}/sched_{sid3}_loco", f"{proj}/loco_{loco3}_depot", f"{proj}/depot_{dep3}_type"], "hops": 3})
    sid3b = "srv-001"
    loco3b = srv_loco_map[sid3b]
    dep3b = loco_depot_map[loco3b]
    dt3b = [d["value"] for d in loci if d["key"] == f"depot_{dep3b}_type"][0]
    questions.append({"asks": "loci", "query": f"gimme da loco {loco3b} on {sid3b}, depot {dep3b} — type {dt3b}",
                      "expect_key": [f"{proj}/sched_{sid3b}_loco", f"{proj}/loco_{loco3b}_depot", f"{proj}/depot_{dep3b}_type"], "hops": 3})

    # 3-hop: service → loco → depot → supplier
    sid3c = "srv-002"
    loco3c = srv_loco_map[sid3c]
    dep3c = loco_depot_map[loco3c]
    supp3c = depot_supplier_map[dep3c]
    questions.append({"asks": "loci", "query": f"gimme da loco {loco3c} on {sid3c}, depot {dep3c} — supplier {supp3c}",
                      "expect_key": [f"{proj}/sched_{sid3c}_loco", f"{proj}/loco_{loco3c}_depot", f"{proj}/depot_{dep3c}_supplier"], "hops": 3})

    # 4-hop: service → loco → depot → supplier → part — split into 1-hop questions
    sid4 = "srv-003"
    loco4 = srv_loco_map[sid4]
    dep4 = loco_depot_map[loco4]
    supp4 = depot_supplier_map[dep4]
    part4 = [d["value"] for d in loci if d["key"] == f"supp_{supp4}_part"][0]
    questions.append({"asks": "loci", "query": f"wot loco runs {sid4} service?",
                      "expect_key": [f"{proj}/sched_{sid4}_loco"], "hops": 1})
    questions.append({"asks": "loci", "query": f"wot depot keeps {loco4}?",
                      "expect_key": [f"{proj}/loco_{loco4}_depot"], "hops": 1})
    questions.append({"asks": "loci", "query": f"wot supplier serves {dep4}?",
                      "expect_key": [f"{proj}/depot_{dep4}_supplier"], "hops": 1})
    questions.append({"asks": "loci", "query": f"wot part does {supp4} supply?",
                      "expect_key": [f"{proj}/supp_{supp4}_part"], "hops": 1})

    # 4-hop: service → loco → line → line_warboss → clan
    sid4d = "srv-005"
    loco4d = srv_loco_map[sid4d]
    line4d = loco_line_map[loco4d]
    wb_line4d = line_warboss_map[line4d]
    clan4d = boss_clan_map[wb_line4d]
    questions.append({"asks": "loci", "query": f"gimme da loco {loco4d} on {sid4d}, line {line4d}, warboss {wb_line4d} — clan {clan4d}",
                      "expect_key": [f"{proj}/sched_{sid4d}_loco", f"{proj}/loco_{loco4d}_line", f"{proj}/line_{line4d}_warboss", f"{proj}/boss_{wb_line4d}_clan"], "hops": 4})

    # 5-hop: service → loco → depot → supplier → warboss → clan
    sid5 = "srv-006"
    loco5 = srv_loco_map[sid5]
    dep5 = loco_depot_map[loco5]
    supp5 = depot_supplier_map[dep5]
    wb_supp5 = supp_warboss_map[supp5]
    clan5 = boss_clan_map[wb_supp5]
    questions.append({"asks": "loci", "query": f"gimme da loco {loco5} on {sid5}, depot {dep5}, supplier {supp5}, warboss {wb_supp5} — clan {clan5}",
                      "expect_key": [f"{proj}/sched_{sid5}_loco", f"{proj}/loco_{loco5}_depot", f"{proj}/depot_{dep5}_supplier", f"{proj}/supp_{supp5}_warboss", f"{proj}/boss_{wb_supp5}_clan"], "hops": 5})

    # Memory questions — expect_ref only; content is random-generated.
    questions.append({"asks": "memory", "query": "gimme evt-000 — bridge fell down at deff-dread-halt on Night",
                      "expect_ref": ["evt-000"], "hops": 1})
    questions.append({"asks": "memory", "query": "gimme rep-000 — Q2 235.M41 goff-rock promethium delivery",
                      "expect_ref": ["rep-000"], "hops": 1})
    questions.append({"asks": "memory", "query": "gimme todo-000 — Fix loco at goff-rock",
                      "expect_ref": ["todo-000"], "hops": 1})

    # Corpus questions — expect_content uses values from linking maps.
    wb_gork = station_warboss_map["gorkamork-central"]
    clan_gork = boss_clan_map[wb_gork]
    questions.append({"asks": "corpus", "query": "tell me about gorkamork-central station — warboss an clan",
                      "expect_content": [wb_gork, clan_gork, "station"], "hops": 2})
    wb_dakka = line_warboss_map["dakka-line"]
    clan_dakka = boss_clan_map[wb_dakka]
    questions.append({"asks": "corpus", "query": "tell me about da dakka-line — warboss an clan",
                      "expect_content": [wb_dakka, clan_dakka, "line"], "hops": 2})
    wb_gork_forge = depot_warboss_map["gork-forge"]
    clan_gork_forge = boss_clan_map[wb_gork_forge]
    supp_gork_forge = depot_supplier_map["gork-forge"]
    questions.append({"asks": "corpus", "query": "tell me about gork-forge depot — warboss, clan, an supplier",
                      "expect_content": [wb_gork_forge, clan_gork_forge, supp_gork_forge, "forge"], "hops": 2})
    clan_gitsnik = boss_clan_map["boss-gitsnik"]
    questions.append({"asks": "corpus", "query": "tell me about boss-gitsnik — clan, rank, an domain",
                      "expect_content": [clan_gitsnik, "warboss", "station"], "hops": 1})
    loco_srv0 = srv_loco_map["srv-000"]
    cargo_srv0 = [d["value"] for d in loci if d["key"] == f"sched_srv-000_cargo"][0]
    questions.append({"asks": "corpus", "query": "tell me about srv-000 — loco an cargo",
                      "expect_content": [loco_srv0, cargo_srv0], "hops": 2})

    # Fill remaining questions to reach 90 — 1-hop with attribute name included
    for s in station_names[:10]:
        questions.append({"asks": "loci", "query": f"wot zone is {s} station in?",
                          "expect_key": [f"{proj}/station_{s}_zone"], "hops": 1})
    for lid in line_ids[:6]:
        questions.append({"asks": "loci", "query": f"wot gauge is da {lid} line?",
                          "expect_key": [f"{proj}/line_{lid}_gauge"], "hops": 1})
    for loco_id in loco_ids[:10]:
        questions.append({"asks": "loci", "query": f"wot armor does {loco_id} loco have?",
                          "expect_key": [f"{proj}/loco_{loco_id}_armor"], "hops": 1})
    for dep in depot_names[:5]:
        questions.append({"asks": "loci", "query": f"ow big capacity is {dep} depot?",
                          "expect_key": [f"{proj}/depot_{dep}_capacity"], "hops": 1})
    for supp in supplier_names[:8]:
        questions.append({"asks": "loci", "query": f"wot zone does {supp} supplier operate in?",
                          "expect_key": [f"{proj}/supp_{supp}_zone"], "hops": 1})
    for boss in warboss_names[:8]:
        questions.append({"asks": "loci", "query": f"wot rank is {boss} boss?",
                          "expect_key": [f"{proj}/boss_{boss}_rank"], "hops": 1})
    for i in range(10):
        sid = f"srv-{i:03d}"
        questions.append({"asks": "loci", "query": f"wot origin station does {sid} start from?",
                          "expect_key": [f"{proj}/sched_{sid}_origin"], "hops": 1})

    # Memory questions about specific incidents — include content words to single out
    questions.append({"asks": "memory", "query": "tell me about incident evt-000 — bridge fell down at deff-dread-halt",
                      "expect_ref": ["evt-000"], "hops": 1})
    questions.append({"asks": "memory", "query": "tell me about incident evt-001 — squigs stampeded at promethium-pit",
                      "expect_ref": ["evt-001"], "hops": 1})
    questions.append({"asks": "memory", "query": "tell me about incident evt-002 — grots escaped at zogwort-folly",
                      "expect_ref": ["evt-002"], "hops": 1})
    questions.append({"asks": "memory", "query": "tell me about incident evt-003 — track broke at kustom-keep",
                      "expect_ref": ["evt-003"], "hops": 1})
    questions.append({"asks": "memory", "query": "tell me about incident evt-004 — squigs stampeded at mekboy-forge",
                      "expect_ref": ["evt-004"], "hops": 1})

    # ── DECOYS (unrelated facts for NegativeTest store) ──
    # Heritage / closed / never-built decoys
    heritage_lines = ["old-dwarven-way", "squig-track", "grot-express", "teef-tramway", "dakka-tram"]
    for hl in heritage_lines:
        decoy.append({"project": proj, "key": f"line_{hl}_status",  "value": "preserved heritage", "why": "heritage status"})
        decoy.append({"project": proj, "key": f"line_{hl}_gauge",   "value": "dwarven-narrow",     "why": "track gauge"})
        decoy.append({"project": proj, "key": f"line_{hl}_warboss", "value": "none (museum piece)", "why": "warboss"})

    collapsed_tunnels = ["zogwort-tunnel-old", "bad-moon-bore", "gork-crater", "mork-fissure", "dakka-shaft"]
    for ct in collapsed_tunnels:
        decoy.append({"project": proj, "key": f"station_{ct}_condition", "value": "collapsed", "why": "tunnel condition"})
        decoy.append({"project": proj, "key": f"station_{ct}_line",     "value": "none",      "why": "serving line"})
        decoy.append({"project": proj, "key": f"station_{ct}_warboss",  "value": "none",      "why": "warboss"})

    never_built_projects = ["grand-waagh-spur", "teef-mountain-railway", "promethium-pipeline-rail", "mekboy-maglev", "dakka-orbital"]
    for nbp in never_built_projects:
        decoy.append({"project": proj, "key": f"line_{nbp}_status", "value": "never built",  "why": "project status"})
        decoy.append({"project": proj, "key": f"line_{nbp}_class",  "value": "grand project", "why": "classification"})
        decoy.append({"project": proj, "key": f"line_{nbp}_stops",  "value": "0",             "why": "number of stops"})

    # Extra decoys to reach 50 total
    phantom_warbosses = ["boss-phantom-1", "boss-phantom-2"]
    for pb in phantom_warbosses:
        decoy.append({"project": proj, "key": f"boss_{pb}_clan", "value": "unknown", "why": "phantom clan"})
        decoy.append({"project": proj, "key": f"boss_{pb}_rank", "value": "impostor", "why": "phantom rank"})
    decoy.append({"project": proj, "key": "loco_phantom-hauler_depot", "value": "nowhere", "why": "phantom loco depot"})

    return proj, loci, memory, questions, decoy


def hard_mycelium(memory=None):
    p = "mycelium"
    q = [
        {"asks": "loci", "query": "which strains are hosted in S. cerevisiae?",
         "expect_key": [f"{p}/strain_yeast-e2_host", f"{p}/strain_yeast-o1_host"]},
        {"asks": "loci", "query": "which enzymes target cellulose as substrate?",
         "expect_key": [f"{p}/enz_cellB_substrate", f"{p}/enz_xynA_substrate"]},
        {"asks": "loci", "query": "who works in Strain Eng and what is their specialty?",
         "expect_key": [f"{p}/person_ljm_role", f"{p}/person_ljm_specialty", f"{p}/person_ljm_dept"]},
        {"asks": "memory", "query": "what incidents happened in the analytics lab?",
         "expect_ref": ["evt-002"], "expect_content": ["alarm triggered", "media lab"]},
        {"asks": "memory", "query": "how did the Q3 2024 review for pichia-x7 go?",
         "expect_ref": ["rep-002"], "expect_content": ["Q3", "2024", "pichia-x7", "contamination"]},
        {"asks": "corpus", "query": "which strain produces cellulase and what enzyme does it use?",
         "expect_content": ["asper-k1", "cellB", "cellulase", "A. sojae"]},
        {"asks": "corpus", "query": "brief me on the regulatory landscape for cellB",
         "expect_content": ["FDA", "GRAS", "food processing", "2027-06"]},
        {"asks": "loci", "query": "what host does the strain phony-z9 use?", "expect_empty": True},
        {"asks": "corpus", "query": "brief me on the fictional strain xeno-7", "expect_empty": True},
        # Extra 11 hard questions to reach 20
        {"asks": "loci", "query": "which sensors detect heavy metals?",
         "expect_key": [f"{p}/sense_toxAlert_target", f"{p}/sense_metalLock_target"]},
        {"asks": "loci", "query": "what are the clearance levels of the R&D leads?",
         "expect_key": [f"{p}/person_drm_clearance", f"{p}/person_ljm_clearance", f"{p}/person_rkb_clearance"]},
        {"asks": "loci", "query": "which materials need cold storage?",
         "expect_key": [f"{p}/mat_IPTG_storage", f"{p}/mat_hygromycin_storage"]},
        {"asks": "memory", "query": "what long-term reviews cover the bac-m1 strain?",
         "expect_ref": ["rep-018"], "expect_content": ["bac-m1", "collaboration started"]},
        {"asks": "memory", "query": "what near-term tasks involve the analytical lab?",
         "expect_ref": ["todo-001"], "expect_content": ["QC HPLC", "cellB", "FDA"]},
        {"asks": "corpus", "query": "brief me on the full supply chain for the asper-k1 strain",
         "expect_content": ["A. sojae", "pEX-2A", "glaA", "cellulase", "Sigma-Aldrich"]},
        {"asks": "corpus", "query": "who is responsible for the lipA project and what enzyme do they use?",
         "expect_content": ["team-ledger", "lipA", "C. antarctica", "bioremediation"]},
        {"asks": "loci", "query": "what plasmid does the fake strain zeta-null use?", "expect_empty": True},
        {"asks": "memory", "query": "what happened with the phantom enzyme calibration?", "expect_empty": True},
        {"asks": "corpus", "query": "brief me on the non-existent bio-sensor xenoScan", "expect_empty": True},
        {"asks": "corpus", "query": "dossier on the fictional person Dr. Null", "expect_empty": True},
    ]
    decoy = [
        {"project": p, "key": "strain_ghost_a1_host", "value": "Mycoplasma laboratorium", "why": "production host"},
        {"project": p, "key": "strain_ghost_a1_vector", "value": "pMYCO-1", "why": "expression vector"},
        {"project": p, "key": "strain_ghost_a1_application", "value": "synthetic minimal genome", "why": "intended product"},
        {"project": p, "key": "enz_none_ec", "value": "0.0.0.0", "why": "null enzyme"},
        {"project": p, "key": "enz_none_source", "value": "Unknown", "why": "source organism"},
        {"project": p, "key": "enz_none_substrate", "value": "nothing", "why": "substrate"},
        {"project": p, "key": "person_void_role", "value": "None", "why": "role"},
        {"project": p, "key": "person_void_dept", "value": "None", "why": "department"},
        # Extra 12 hard decoys to reach 20
        {"project": p, "key": "strain_ghost_b2_host", "value": "D. radiodurans", "why": "production host"},
        {"project": p, "key": "strain_ghost_b2_vector", "value": "pRAD-1", "why": "expression vector"},
        {"project": p, "key": "strain_ghost_b2_application", "value": "radiation resistance", "why": "intended product"},
        {"project": p, "key": "sense_phantom_pH_target", "value": "mercury", "why": "phantom sensor target"},
        {"project": p, "key": "sense_phantom_pH_output", "value": "UV fluorescence", "why": "phantom reporter"},
        {"project": p, "key": "plasmid_null_origin", "value": "pNONE", "why": "origin of null plasmid"},
        {"project": p, "key": "plasmid_null_marker", "value": "noneR", "why": "marker of null plasmid"},
        {"project": p, "key": "reg_FAKE-2025-000_authority", "value": "Fictitious Bureau", "why": "fake authority"},
        {"project": p, "key": "reg_FAKE-2025-000_status", "value": "nonexistent", "why": "fake status"},
        {"project": p, "key": "mat_void_stock", "value": "0 units", "why": "phantom material stock"},
        {"project": p, "key": "mat_void_supplier", "value": "Nowhere Corp", "why": "phantom supplier"},
        {"project": p, "key": "mat_void_storage", "value": "void", "why": "phantom storage"},
    ]
    return q, decoy


def emit_hard(domain, hard_questions, hard_decoy, start_port):
    d = os.path.join(HERE, domain)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "questions_hard.yaml"), "w", encoding="utf-8").write(_dump_questions(hard_questions))
    open(os.path.join(d, "decoy_hard.yaml"), "w", encoding="utf-8").write(_dump_list(hard_decoy))
    open(os.path.join(d, "ProbeConfig_hard.yml"), "w", encoding="utf-8").write(
        _probeconfig(domain, start_port, "questions_hard.yaml", "decoy_hard.yaml"))
    return len(hard_questions), len(hard_decoy)


# ══════════════════════════════════════════════════════════════════════════
#  Hard questions for orkrail  —  20 questions, 20 decoys
# ══════════════════════════════════════════════════════════════════════════
def hard_orkrail(memory=None):
    p = "orkrail"
    # Build a lookup from memory content for dynamic queries
    mem_lookup = {}
    if memory:
        for m in memory:
            ref = m.get('ref', '')
            content = m.get('content', '') or ''
            topic = m.get('topic', '') or ''
            tier = m.get('tier', '')
            words = content.split()
            sig = [w for w in words if w not in ('at', 'on', 'the', 'a', 'an', 'in', '\u2014', '-', 'da', 'an', 'wot', 'fer', 'night', 'dusk', 'dawn', 'midday', 'hour')]
            intent = m.get('intent', '') or ''
            intent_words = intent.split()
            intent_sig = [w for w in intent_words if w not in ('at', 'on', 'the', 'a', 'an', 'in', '\u2014', '-', 'da', 'an', 'wot', 'fer', 'the', 'for')]
            mem_lookup[ref] = {'content': content, 'sig': sig[:4], 'intent_sig': intent_sig[:3], 'topic': topic, 'tier': tier}
    
    def _mem_q(refs, extra_text=''):
        """Build a memory query with ref IDs and content words."""
        parts = []
        for r in refs:
            info = mem_lookup.get(r, {})
            sig = info.get('sig', [])
            tier = info.get('tier', '')
            if tier == 'short':
                qpart = f"gimme {r} — {' '.join(sig)}"
            elif tier == 'long':
                # Use content sig words (quarter, location, metric) instead of topic
                qpart = f"gimme {r} — {' '.join(sig)}" if sig else f"gimme {r} — report"
            else:
                extra = info.get('intent_sig', [])
                qpart = f"gimme {r} — {' '.join(extra)}" if extra else f"gimme {r}"
            parts.append(qpart)
        return ' plus '.join(parts) + (' ' + extra_text if extra_text else '')
    
    q = [
        # Hard questions — independent expect_key sets (no chain continuity needed),
        # corpus briefings with actual known values, and expect_empty for decoys.
        # All queries in proper Ork speak (40k pidgin English).
        {"asks": "loci", "query": "wot interchange an zone fer gorkamork-central, dakka-junction, an teef-exchange?",
         "expect_key": [f"{p}/station_gorkamork-central_interchange", f"{p}/station_gorkamork-central_zone",
                        f"{p}/station_dakka-junction_interchange", f"{p}/station_dakka-junction_zone",
                        f"{p}/station_teef-exchange_interchange", f"{p}/station_teef-exchange_zone"], "hops": 2},
        {"asks": "loci", "query": "wot fuel an armor fer dakka-jet-0 an teef-train-0?",
         "expect_key": [f"{p}/loco_dakka-jet-0_fuel", f"{p}/loco_dakka-jet-0_armor",
                        f"{p}/loco_teef-train-0_fuel", f"{p}/loco_teef-train-0_armor"], "hops": 2},
        {"asks": "loci", "query": "wot zone, line, an condition fer gorkamork-central an teef-exchange?",
         "expect_key": [f"{p}/station_gorkamork-central_zone", f"{p}/station_gorkamork-central_line",
                        f"{p}/station_gorkamork-central_condition",
                        f"{p}/station_teef-exchange_zone", f"{p}/station_teef-exchange_line",
                        f"{p}/station_teef-exchange_condition"], "hops": 2},
        {"asks": "loci", "query": "wot cargo an warband fer srv-000, srv-001, an srv-002?",
         "expect_key": [f"{p}/sched_srv-000_cargo", f"{p}/sched_srv-000_warband",
                        f"{p}/sched_srv-001_cargo", f"{p}/sched_srv-001_warband",
                        f"{p}/sched_srv-002_cargo", f"{p}/sched_srv-002_warband"], "hops": 2},
        {"asks": "loci", "query": "wot type an equipment fer gork-forge, mork-foundry, an mekboy-yard?",
         "expect_key": [f"{p}/depot_gork-forge_type", f"{p}/depot_gork-forge_equipment",
                        f"{p}/depot_mork-foundry_type", f"{p}/depot_mork-foundry_equipment",
                        f"{p}/depot_mekboy-yard_type", f"{p}/depot_mekboy-yard_equipment"], "hops": 2},
        {"asks": "corpus", "query": "tell me about da full supply chain fer srv-000",
         "expect_content": ["teef-train-3"], "hops": 4},
        {"asks": "corpus", "query": "tell me about da warboss chain fer gorkamork-central station",
         "expect_content": ["boss-promethium-lord"], "hops": 3},
        {"asks": "memory", "query": _mem_q(["evt-000"]),
         "expect_ref": ["evt-000"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["evt-001"]),
         "expect_ref": ["evt-001"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["todo-000"]),
         "expect_ref": ["todo-000"], "hops": 2},
        {"asks": "corpus", "query": "tell me about da never-built grand-waagh-spur line", "expect_empty": True},
        {"asks": "corpus", "query": "tell me about da collapsed zogwort-tunnel-old station", "expect_empty": True},
        # Extra 8 hard questions to reach 20
        {"asks": "loci", "query": "wot zone fer gorkamork-central an teef-exchange?",
         "expect_key": [f"{p}/station_gorkamork-central_zone",
                        f"{p}/station_teef-exchange_zone"], "hops": 2},
        {"asks": "loci", "query": "wot line fer gorkamork-central an teef-exchange?",
         "expect_key": [f"{p}/station_gorkamork-central_line",
                        f"{p}/station_teef-exchange_line"], "hops": 2},
        {"asks": "loci", "query": "wot armor an fuel fer big-choppa-0 an dakka-jet-0?",
         "expect_key": [f"{p}/loco_big-choppa-0_armor", f"{p}/loco_big-choppa-0_fuel",
                        f"{p}/loco_dakka-jet-0_armor", f"{p}/loco_dakka-jet-0_fuel"], "hops": 2},
        {"asks": "loci", "query": "what warboss runs gork-forge depot and what is its type?",
         "expect_key": [f"{p}/depot_gork-forge_warboss", f"{p}/depot_gork-forge_type"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["evt-002", "evt-003"]),
         "expect_ref": ["evt-002", "evt-003"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["rep-001"]),
         "expect_ref": ["rep-001"], "hops": 2},
        {"asks": "corpus", "query": "tell me about da full chain from srv-003 ta its supplier's part",
         "expect_content": ["battle-barge-0"], "hops": 4},
        {"asks": "corpus", "query": "tell me about da warboss chain fer da dakka-line",
         "expect_content": ["boss-grotkeeper"], "hops": 3},
        # Extra 30 hard questions to reach 50
        {"asks": "loci", "query": "wot line runs ta wot zone — compare gorkamork-central an dakka-junction?",
         "expect_key": [f"{p}/station_gorkamork-central_line", f"{p}/station_gorkamork-central_zone",
                        f"{p}/station_dakka-junction_line", f"{p}/station_dakka-junction_zone"], "hops": 2},
        {"asks": "loci", "query": "wot condition an dakka at gorkamork-central, dakka-junction, an teef-exchange?",
         "expect_key": [f"{p}/station_gorkamork-central_condition", f"{p}/station_gorkamork-central_dakka",
                        f"{p}/station_dakka-junction_condition", f"{p}/station_dakka-junction_dakka",
                        f"{p}/station_teef-exchange_condition", f"{p}/station_teef-exchange_dakka"], "hops": 2},
        {"asks": "loci", "query": "wot gauge an color fer da dakka-line, teef-railway, an waagh-express?",
         "expect_key": [f"{p}/line_dakka-line_gauge", f"{p}/line_dakka-line_color",
                        f"{p}/line_teef-railway_gauge", f"{p}/line_teef-railway_color",
                        f"{p}/line_waagh-express_gauge", f"{p}/line_waagh-express_color"], "hops": 2},
        {"asks": "loci", "query": "wot armor an gubbinz on big-choppa-0, dakka-jet-0, an teef-train-0?",
         "expect_key": [f"{p}/loco_big-choppa-0_armor", f"{p}/loco_big-choppa-0_gubbinz",
                        f"{p}/loco_dakka-jet-0_armor", f"{p}/loco_dakka-jet-0_gubbinz",
                        f"{p}/loco_teef-train-0_armor", f"{p}/loco_teef-train-0_gubbinz"], "hops": 2},
        {"asks": "loci", "query": "wot depot fer big-choppa-0, dakka-jet-0, an teef-train-0?",
         "expect_key": [f"{p}/loco_big-choppa-0_depot", f"{p}/loco_dakka-jet-0_depot",
                        f"{p}/loco_teef-train-0_depot"], "hops": 2},
        {"asks": "loci", "query": "wot line fer big-choppa-0, dakka-jet-0, an teef-train-0?",
         "expect_key": [f"{p}/loco_big-choppa-0_line", f"{p}/loco_dakka-jet-0_line",
                        f"{p}/loco_teef-train-0_line"], "hops": 2},
        {"asks": "loci", "query": "wot cargo an warband on srv-000, srv-001, srv-002, srv-003, srv-004?",
         "expect_key": [f"{p}/sched_srv-000_cargo", f"{p}/sched_srv-000_warband",
                        f"{p}/sched_srv-001_cargo", f"{p}/sched_srv-001_warband",
                        f"{p}/sched_srv-002_cargo", f"{p}/sched_srv-002_warband",
                        f"{p}/sched_srv-003_cargo", f"{p}/sched_srv-003_warband",
                        f"{p}/sched_srv-004_cargo", f"{p}/sched_srv-004_warband"], "hops": 2},
        {"asks": "loci", "query": "wot type, equipment, an supplier fer gork-forge, mork-foundry, an mekboy-yard?",
         "expect_key": [f"{p}/depot_gork-forge_type", f"{p}/depot_gork-forge_equipment", f"{p}/depot_gork-forge_supplier",
                        f"{p}/depot_mork-foundry_type", f"{p}/depot_mork-foundry_equipment", f"{p}/depot_mork-foundry_supplier",
                        f"{p}/depot_mekboy-yard_type", f"{p}/depot_mekboy-yard_equipment", f"{p}/depot_mekboy-yard_supplier"], "hops": 2},
        {"asks": "loci", "query": "wot part an zone fer squig-feeders, teef-traders, an promethium-peddlers?",
         "expect_key": [f"{p}/supp_squig-feeders_part", f"{p}/supp_squig-feeders_zone",
                        f"{p}/supp_teef-traders_part", f"{p}/supp_teef-traders_zone",
                        f"{p}/supp_promethium-peddlers_part", f"{p}/supp_promethium-peddlers_zone"], "hops": 2},
        {"asks": "loci", "query": "wot clan an rank fer boss-gitsnik, boss-skragbad, an boss-mogrok?",
         "expect_key": [f"{p}/boss_boss-gitsnik_clan", f"{p}/boss_boss-gitsnik_rank",
                        f"{p}/boss_boss-skragbad_clan", f"{p}/boss_boss-skragbad_rank",
                        f"{p}/boss_boss-mogrok_clan", f"{p}/boss_boss-mogrok_rank"], "hops": 2},
        {"asks": "corpus", "query": "tell me about da full supply chain fer srv-001 — loco, depot, supplier, part",
         "expect_content": ["big-choppa-4"], "hops": 4},
        {"asks": "corpus", "query": "tell me about da full supply chain fer srv-002 — loco, depot, supplier, part",
         "expect_content": ["kustom-kroozer-2"], "hops": 4},
        {"asks": "corpus", "query": "tell me about da full supply chain fer srv-004 — loco, depot, supplier, part",
         "expect_content": ["big-choppa-0"], "hops": 4},
        {"asks": "corpus", "query": "tell me about da full supply chain fer srv-007 — loco, depot, supplier, part",
         "expect_content": ["battle-barge-1"], "hops": 4},
        {"asks": "corpus", "query": "tell me about da full supply chain fer srv-008 — loco, depot, supplier, part",
         "expect_content": ["squig-hauler-2"], "hops": 4},
        {"asks": "corpus", "query": "tell me about da warboss chain fer teef-exchange station",
         "expect_content": ["boss-rottenjaw"], "hops": 3},
        {"asks": "corpus", "query": "tell me about da warboss chain fer da teef-railway",
         "expect_content": ["boss-high-dakka"], "hops": 3},
        {"asks": "corpus", "query": "tell me about da warboss chain fer mork-foundry depot",
         "expect_content": ["boss-teefsnatcha"], "hops": 3},
        {"asks": "corpus", "query": "tell me about boss-skragbad — clan, rank, an domain",
         "expect_content": ["boss-skragbad", "mork", "weirdboy", "line"], "hops": 2},
        {"asks": "corpus", "query": "tell me about boss-mogrok — clan, rank, an domain",
         "expect_content": ["boss-mogrok", "goff", "mekboy", "station"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["evt-004", "evt-005"]),
         "expect_ref": ["evt-004", "evt-005"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["rep-002"]),
         "expect_ref": ["rep-002"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["rep-003"]),
         "expect_ref": ["rep-003"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["todo-001"]),
         "expect_ref": ["todo-001"], "hops": 2},
        {"asks": "memory", "query": _mem_q(["todo-002"]),
         "expect_ref": ["todo-002"], "hops": 2},
        {"asks": "loci", "query": "wot loco does da fake srv-999 use?", "expect_empty": True},
        {"asks": "corpus", "query": "tell me about da never-built promethium-pipeline-rail", "expect_empty": True},
        {"asks": "corpus", "query": "tell me about da collapsed gork-crater station", "expect_empty": True},
        {"asks": "corpus", "query": "dossier on da phantom warboss boss-phantom-1", "expect_empty": True},
        {"asks": "memory", "query": _mem_q(["evt-006"]),
         "expect_ref": ["evt-006"], "hops": 2},
    ]
    decoy = [
        # Phantom heritage decoys (20 existing + 30 extra = 50)
        {"project": p, "key": "line_old-dwarven-way_status", "value": "preserved heritage", "why": "heritage"},
        {"project": p, "key": "line_old-dwarven-way_gauge", "value": "dwarven-narrow", "why": "gauge"},
        {"project": p, "key": "line_squig-track_status", "value": "preserved heritage", "why": "heritage"},
        {"project": p, "key": "line_squig-track_gauge", "value": "dwarven-narrow", "why": "gauge"},
        {"project": p, "key": "station_zogwort-tunnel-old_condition", "value": "collapsed", "why": "collapsed tunnel"},
        {"project": p, "key": "station_bad-moon-bore_condition", "value": "collapsed", "why": "collapsed tunnel"},
        {"project": p, "key": "line_grand-waagh-spur_status", "value": "never built", "why": "grand project"},
        {"project": p, "key": "line_grand-waagh-spur_class", "value": "grand project", "why": "classification"},
        {"project": p, "key": "line_teef-mountain-railway_status", "value": "never built", "why": "grand project"},
        {"project": p, "key": "line_teef-mountain-railway_class", "value": "grand project", "why": "classification"},
        {"project": p, "key": "boss_fake-warboss_clan", "value": "unknown", "why": "fake boss"},
        {"project": p, "key": "boss_fake-warboss_rank", "value": "impostor", "why": "fake rank"},
        {"project": p, "key": "loco_phantom-engine-0_class", "value": "myth", "why": "phantom loco"},
        {"project": p, "key": "loco_phantom-engine-0_depot", "value": "nowhere", "why": "phantom depot"},
        {"project": p, "key": "depot_ghost-forge_type", "value": "haunted", "why": "ghost depot"},
        {"project": p, "key": "depot_ghost-forge_supplier", "value": "spectral", "why": "ghost supplier"},
        {"project": p, "key": "supp_phantom-parts_part", "value": "imaginary", "why": "phantom part"},
        {"project": p, "key": "supp_phantom-parts_teef", "value": "0", "why": "phantom cost"},
        {"project": p, "key": "sched_fake-service-999_loco", "value": "none", "why": "fake service"},
        {"project": p, "key": "sched_fake-service-999_cargo", "value": "nothing", "why": "fake cargo"},
        # Extra 30 decoys to reach 50
        {"project": p, "key": "line_grot-express_status", "value": "preserved heritage", "why": "heritage"},
        {"project": p, "key": "line_grot-express_gauge", "value": "dwarven-narrow", "why": "gauge"},
        {"project": p, "key": "line_teef-tramway_status", "value": "preserved heritage", "why": "heritage"},
        {"project": p, "key": "line_teef-tramway_gauge", "value": "dwarven-narrow", "why": "gauge"},
        {"project": p, "key": "line_dakka-tram_status", "value": "preserved heritage", "why": "heritage"},
        {"project": p, "key": "line_dakka-tram_gauge", "value": "dwarven-narrow", "why": "gauge"},
        {"project": p, "key": "station_gork-crater_condition", "value": "collapsed", "why": "collapsed tunnel"},
        {"project": p, "key": "station_mork-fissure_condition", "value": "collapsed", "why": "collapsed tunnel"},
        {"project": p, "key": "station_dakka-shaft_condition", "value": "collapsed", "why": "collapsed tunnel"},
        {"project": p, "key": "line_promethium-pipeline-rail_status", "value": "never built", "why": "grand project"},
        {"project": p, "key": "line_promethium-pipeline-rail_class", "value": "grand project", "why": "classification"},
        {"project": p, "key": "line_mekboy-maglev_status", "value": "never built", "why": "grand project"},
        {"project": p, "key": "line_mekboy-maglev_class", "value": "grand project", "why": "classification"},
        {"project": p, "key": "line_dakka-orbital_status", "value": "never built", "why": "grand project"},
        {"project": p, "key": "line_dakka-orbital_class", "value": "grand project", "why": "classification"},
        {"project": p, "key": "boss_phantom-warboss-1_clan", "value": "unknown", "why": "phantom clan"},
        {"project": p, "key": "boss_phantom-warboss-1_rank", "value": "impostor", "why": "phantom rank"},
        {"project": p, "key": "boss_phantom-warboss-2_clan", "value": "unknown", "why": "phantom clan"},
        {"project": p, "key": "boss_phantom-warboss-2_rank", "value": "impostor", "why": "phantom rank"},
        {"project": p, "key": "loco_phantom-hauler-0_class", "value": "ghost", "why": "phantom class"},
        {"project": p, "key": "loco_phantom-hauler-0_depot", "value": "void", "why": "phantom depot"},
        {"project": p, "key": "depot_wraith-works_type", "value": "spectral", "why": "phantom type"},
        {"project": p, "key": "depot_wraith-works_supplier", "value": "ethereal", "why": "phantom supplier"},
        {"project": p, "key": "supp_shadow-parts_part", "value": "nothing", "why": "phantom part"},
        {"project": p, "key": "supp_shadow-parts_teef", "value": "0", "why": "phantom cost"},
        {"project": p, "key": "sched_ghost-service-000_loco", "value": "none", "why": "ghost service"},
        {"project": p, "key": "sched_ghost-service-000_cargo", "value": "void", "why": "ghost cargo"},
        {"project": p, "key": "station_phantom-halt_condition", "value": "does not exist", "why": "phantom station"},
        {"project": p, "key": "station_phantom-halt_zone", "value": "void", "why": "phantom zone"},
        {"project": p, "key": "line_ghost-line_status", "value": "never existed", "why": "ghost line"},
    ]
    return q, decoy


HARD = {"helix": hard_helix, "aldermoor": hard_aldermoor,
        "lattice": hard_lattice, "halcyon": hard_halcyon,
        "mycelium": hard_mycelium, "orkrail": hard_orkrail}


BUILDERS = [helix, aldermoor, lattice, halcyon, mycelium, orkrail]

if __name__ == "__main__":
    print(f"{'domain':<12}{'loci':>6}{'memory':>8}{'questions':>11}{'decoy':>7}")
    print("-" * 44)
    for i, b in enumerate(BUILDERS):
        proj, loci, memory, questions, decoy = b()
        sp = 7520 + i * 20
        nl, nm, nq, nd = emit(proj, loci, memory, questions, decoy, start_port=sp)
        hq, hd = HARD[proj](memory)
        nhq, nhd = emit_hard(proj, hq, hd, start_port=sp)
        print(f"{proj:<12}{nl:>6}{nm:>8}{nq:>11}{nd:>7}   +hard: {nhq}q/{nhd}decoy")
    print("\nwrote datasets/<domain>/{loci,memory,questions,decoy}.yaml + ProbeConfig.yml")
