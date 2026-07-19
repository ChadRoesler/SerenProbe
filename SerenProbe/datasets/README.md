# SerenProbe eval datasets

Four large, coherent eval corpora (~300–400 items each) for stress-testing the
harness — a rich interlocking **core** the corpus briefings target (multi-hop
facts spanning Loci + Memory), plus templated **distractor bulk** so retrieval
actually has to discriminate.

| Domain | Flavor | loci | memory | questions | decoy |
|---|---|---:|---:|---:|---:|
| `helix` | fictional tech-org (services, incidents, runbooks) | 150 | 149 | 16 | 24 |
| `aldermoor` | fantasy world / lore (houses, events) | 153 | 137 | 14 | 20 |
| `lattice` | a made-up framework's docs (APIs, changelog) | 275 | 137 | 14 | 30 |
| `halcyon` | sci-fi station / crew ops (crew, systems, logs) | 166 | 137 | 16 | 20 |

## Generate them

```bash
python datasets/generate_datasets.py      # run from the repo root
```

Deterministic (a fixed RNG seed per domain), so the datasets are **stable
run-to-run** — which is what makes eval numbers comparable across runs. Each
domain emits under `datasets/<domain>/`:

- `loci.yaml` — flat Loci facts `{project?, key, value, why?}`
- `memory.yaml` — flat Memory episodes `{content|intent, topic?, tier?, ref?}` (short / near / long)
- `questions.yaml` — `loci` (expect_key) / `memory` (expect_ref) / `corpus` (multi-fact briefings)
- `decoy.yaml` — unrelated facts for the `NegativeTest` (leak) store
- `ProbeConfig.yml` — a `scc-v` / `scc-nv` comparison over the same corpus + a decoy negative store, config-driven

## Roll one

Paste `datasets/<domain>/ProbeConfig.yml` into the viewer's **Config** tab (Set
Active), start the Docker env, then **▶ Run Eval** — seeds + questions come from
the config, so no separate upload. Headless equivalent:

```bash
# set the topology active, then run (empty body — config-driven)
POST /docker/probeconfig   { "probe_config": "<contents of ProbeConfig.yml>" }
POST /docker/run-eval
```

The `ProbeConfig.yml` paths (`datasets/<domain>/…`) resolve relative to the
process CWD, so run SerenProbe from the repo root.

## What each run tells you

- **loci / memory** questions score retrieval against retrieval-independent
  ground truth (`expect_key` = Loci's canonical id; `expect_ref` = a Memory
  entry tagged at seed time).
- **corpus** briefings expect *several* facts that live across both Loci and
  Memory, so `docket_coverage` is a real "did the packet assemble all of it?"
  fraction — and the Eval tab's docket block shows the with-edges vs
  without-edges delta (`scc-v` − `scc-nv`).
- the **decoy** store is seeded with only unrelated data, so it should surface
  nothing on these questions — the Eval tab reads it as ✓ *stayed quiet* /
  ✗ *leaked*.

Every corpus-briefing phrase is reachable in the seed **by construction**, and
each `ProbeConfig.yml` compiles with **zero warnings** — validated against the
real loaders before shipping. Tweak a domain (add facts, change `range(...)`
bulk sizes, reseed) and re-run; the output stays deterministic.

## Hard test packs (`*_hard`)

Each domain also emits a **harder** variant that exercises what the baselines
don't — pointed at the *same* loci/memory seeds, so it's a difficulty knob, not a
different corpus:

- `questions_hard.yaml` — three sharper flavors:
  - **multi-answer** (`which services depend on auth?` → a *set* of keys) — these
    make precision/recall actually spread, vs the single-target baselines.
  - **paraphrase** (`why were users suddenly unable to log in?` → the clock-skew
    incident) — the query shares no keywords with the seed, so it's the
    vector-vs-lexical money shot: lexical should struggle, vector shouldn't.
  - **cross-store** (`if auth goes down, what else breaks?`) — the answer is
    spread across Loci dependency facts + a Memory incident, so `docket_coverage`
    measures real packet assembly.
- `decoy_hard.yaml` — a **near-miss** decoy: same-domain, plausible-but-fake
  entities whose *names* echo real ones (`shadow-gateway`, House `Vance`≈`Vayne`,
  `dissolve()`≈`collapse()`, crew `Vane`≈`Vega`) but whose *values* never match
  an expected answer. A vocabulary-overlap distractor a good store should still
  stay quiet against. (Every phrase is checked to NOT appear in the decoy, so a
  leak here is a real false-positive, not an accidental match.)
- `ProbeConfig_hard.yml` — same topology, wired to the hard questions + decoy.

Run a baseline and its `_hard` back to back and watch precision/MRR drop and the
vector−lexical delta widen on the narrative domains.

## No-answer questions (`expect_empty`)

Some hard questions ask about entities that **don't exist anywhere** — `vaporize`
in helix, House `Thornwood` in aldermoor, `obliterate()` in lattice, crew `Zane`
in halcyon. They carry `expect_empty: true` instead of an `expect_*`, and the
right answer is **silence**:

```yaml
- { asks: loci, query: "what does obliterate() return?", expect_empty: true }
```

A store **passes** by returning **zero hits** — it abstained. This is scored
*separately* from hit_rate/MRR (a correct silence shouldn't look like a retrieval
miss) and shows in the Eval tab as `∅ passes/total` (green if it stayed quiet on
all of them, red if it hallucinated a match).

It's a real abstention signal: a lexical Loci or a floored SCC *can* return
nothing, so they can pass; a raw-vector store always returns its k nearest
neighbors, so it scores 0 here — which is exactly the point ("can this store say
*I don't know*?"). Tuning the SCC floor up (via regrade, when it lands) should
raise the empty-pass rate — at some cost to coverage. That tradeoff is the fun part.
