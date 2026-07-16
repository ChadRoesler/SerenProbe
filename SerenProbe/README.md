# SerenProbe

**A push-button RAG evaluation harness for the Seren memory constellation.**

SerenProbe exists to answer one deceptively hard question: *did that change make retrieval better or worse?* You tweaked an embedder, flipped Loci from lexical to hybrid, retuned the fusion knobs on the callosum — and now you need a number that tells you whether the model is going to get handed a better briefing or a worse one. SerenProbe seeds a known corpus, asks known questions, and scores what comes back, honestly, across every store configuration side by side.

It's the empiricist's tool. You don't reason about whether the change helped. You see it.

---

## What it probes

Seren's memory lives in three services, and SerenProbe knows how to grade all of them:

- **SerenMemory** — the episodic "right brain." Three-tier vector store (short / near / long).
- **SerenLoci** — the deterministic "left brain." SQLite facts, addressable by exact key, searchable by FTS5 or FTS5+vector hybrid.
- **SerenCorpusCallosum (SCC)** — the "callosum." Fans a query across Memory + Loci and fuses the results into one briefing packet.

Each brain has a **no-vector (NV)** and a **vector (V)** flavor, and they're not interchangeable — the whole reason SCC exists is to build a *diverse, provenance-tagged* packet, not to crown a single winner. So SerenProbe scores Loci and Memory with the standard retrieval metrics, and scores SCC with those *plus* docket-quality metrics that measure whether the briefing actually covered the ground. (SCC is a chief-of-staff assembling a packet, not Judge Judy picking rank one. The metrics reflect that.)

---

## Two ways to run it

### 1. Against stores you already have running

Point SerenProbe at the URLs of live services and grade them in place:

```bash
pip install seren-probe
seren-probe                       # boots the operator dashboard on :7430
```

Open **http://127.0.0.1:7430/viewer**, or drive it headless via `POST /eval/run`. Configure the store URLs in `serenprobe.yaml` (or via `SEREN_PROBE_*` env vars) — see [Configuration](#configuration).

### 2. The self-contained Docker harness

Don't have five stores wired up? Declare the shape you want in `ProbeConfig.yml` and let SerenProbe stand the whole thing up, seed it, grade it, and tear it down:

```
ProbeConfig.yml  →  compile  →  emit compose  →  docker up  →  seed  →  eval  →  results
```

The compiler is **correct-by-construction**: it wires inter-container traffic with container-DNS URLs (never `localhost`), publishes host ports only for the eval harness, and — critically — wires each SCC to *its own* Loci. Pointing `scc-nv` and `scc-v` at the same Loci instance is the one silent failure that quietly poisons a whole comparison, so the topology compiler refuses to let you do it by accident and *tells you* when your config drifts toward it.

Kick it off from the dashboard (Docker tab) or `POST /docker/run-eval`.

---

## The ProbeConfig topology compiler

`ProbeConfig.yml` is a declarative description of the stores you want to probe — how many Loci, how many Memory, which flavors, and how the Corpus nodes fan across them. You describe the *shape*; the compiler handles ports, wiring, and the footguns.

```yaml
ProbeConfig:
  StartingPort: 7420
  Loci:
    LociCount: 2
    LociConfigs:
      - { Name: loci-v,  Port: 7421, Flags: [vector] }   # hybrid
      - { Name: loci-nv, Port: 7422 }                     # FTS5-only (nano floor)
  Memory:
    MemoryCount: 1
    MemoryConfigs:
      - { Name: mem, Port: 7420 }
  Corpus:
    CorpusCount: 2
    CorpusConfigs:
      - { Name: scc-v,  Port: 7424, Stores: [{ Store: loci-v },  { Store: mem }] }
      - { Name: scc-nv, Port: 7423, Stores: [{ Store: loci-nv }, { Store: mem }] }
```

What it does for you:

- **Autogen + catch-all.** Declare a count higher than your explicit configs and it fills the gap; any store you don't reference gets swept into a catch-all Corpus so nothing is silently stranded.
- **Compassion-first validation.** Bad flag for a store type, a Corpus that references another Corpus, a duplicate port, a store name that isn't declared, two Corpus nodes with identical store sets — every one of these fails *loud and kind*, naming the exact rule and the exact node, at compile time. Config bugs are the expensive kind; this catches them before a container ever starts.
- **Deterministic, type-grouped ports.** Auto-assigned ports are stable run-to-run and grouped by kind, so a diff of two runs is a diff of your *intent*, not port-assignment noise.

A full working `ProbeConfig.yml` ships in the package, plus a heavily-commented `ProbeConfig.template.yml` to copy from.

---

## Bring your own corpus + questions

The topology says *what stores* to stand up; **flat seed files** and a **question set** say what to put in them and what to ask — and you wire them into the ProbeConfig itself, so one config is self-contained. No separate dataset upload: hand SerenProbe the ProbeConfig and it seeds + scores from what the config points at.

A **seed file** is a flat list of items (the store→data mapping lives in the config, not the file). Shapes are the real write contracts — Loci `{project?, key, value, why?}`, Memory `{content|intent, topic?, tier?, ref?}`:

```yaml
# meridian.loci.yaml
- { project: meridian, key: api_port, value: "8080", why: "the REST API listens here" }
# meridian.memory.yaml
- { tier: short, ref: auth-incident, content: "auth threw 401s after a clock-skew bug", topic: auth }
```

Wire them into the ProbeConfig — a **shared** `Questions` (scored across every store, which is what keeps the comparison honest), per-kind default seeds, and optional per-node `Seed` overrides:

```yaml
ProbeConfig:
  Questions:          examples/meridian.questions.yaml
  DefaultLociSeed:    examples/meridian.loci.yaml      # every Loci without its own Seed
  DefaultMemorySeed:  examples/meridian.memory.yaml    # every Memory without its own Seed
  Loci:
    LociConfigs:
      - { Name: loci-v, Port: 7421, Flags: [vector] }
      - { Name: decoy,  Port: 7429, NegativeTest: true, Seed: examples/unrelated.yaml }
  # ... Memory / Corpus ...
```

A **question set** scores each query against honest ground truth — `expect_key` (Loci's canonical id, retrieval-independent), `expect_ref` (a Memory entry you tagged at seed time), or `expect_content` (a fuzzy substring):

```yaml
questions:
  - { asks: loci,   query: "what port does the API use?", expect_key: ["meridian/api_port"] }
  - { asks: corpus, query: "brief me on the auth setup",
      expect_content: ["JWT bearer tokens", "15 minute", "100 requests per minute", "team-atlas"] }
```

The **corpus** questions are where docket coverage earns its keep: give each *several* expected facts that live across both Loci and Memory, and coverage becomes a real "did the briefing assemble all of it?" fraction instead of a binary hit.

**Negative (decoy) stores.** Mark a store `NegativeTest: true` and give it an unrelated `Seed`, and it's seeded with *only* that decoy — never the defaults — so you can prove a store *stays quiet* on questions it shouldn't answer. It's kept out of the catch-all, and the Eval tab reads it as ✓ *stayed quiet* / ✗ *leaked* instead of a bare zero.

A complete, validated example ships in [`examples/`](examples/): `meridian.loci.yaml` + `meridian.memory.yaml` + `meridian.questions.yaml` — a small interlocking fictional stack with three multi-fact briefing questions. Point the ProbeConfig's `Default*Seed` / `Questions` at them (or inline the content for a fully self-contained upload), then just `POST /eval/run` with an empty body.

Validation is **compassion-first**, same as the compiler: an item with the wrong shape, a question with no way to score it, a negative store with no decoy — each fails loud and kind, all at once, naming the fix.

---

## What the numbers mean

Every store gets the standard retrieval battery, computed at top-*k*:

| Metric | Reads as |
|---|---|
| **HitRate@k** | Did *any* relevant doc land in the top-k? (0 or 1) |
| **MRR@k** | How high was the *first* relevant hit? (rank 1 → 1.0, rank 3 → 0.333) |
| **Precision@k** | Fraction of the top-k that were relevant |
| **Recall@k** | Fraction of all relevant docs the top-k caught |
| **NDCG@k** | Rank-aware quality — right answers near the top score higher |
| **IoU@k** | Jaccard overlap of retrieved vs. relevant |
| **PΩ@k** | Rank-weighted precision — top ranks count for more |

SCC additionally reports **docket_coverage** (fraction of expected facts found *anywhere* in the packet — did the briefing cover the ground?) and **docket_density** (fraction of the top-k that carry expected content — how much of the packet is signal?). These are the metrics that actually reflect SCC's job.

### The docket comparison — with vs without edges

Run a topology with both a vector SCC and a lexical one (the canonical `scc-v` / `scc-nv` pair) and SerenProbe doesn't just score them independently — it **pairs them and reports the delta**, answering the question an SCC exists for: *when the callosum fans a vector Loci (semantic edges between nodes) instead of a lexical, FTS5-only one (no edges), does the assembled briefing carry more of the relevant info?*

The comparison rides in every `/eval/run` result under `result["docket"]` and renders in the Eval tab as a side-by-side block:

```
SCC Docket — with vs without edges     30 questions · k=10
  without edges  scc-nv   coverage 0.5850   density 0.7033   recall 0.9544
  with edges     scc-v    coverage 0.5331   density 0.6967   recall 0.9250
  Δ edges (with − without)  coverage -0.0519 · density -0.0066 · recall -0.0294
  → edges COST -0.0519 docket coverage
```

"Edges" is read authoritatively off the compiled topology — an SCC's flavor is whether the Loci it fans carries the `[vector]` flag — so the labels are never guessed. And mind the sign: in that run the semantic edges *hurt* coverage. Surfacing the delta is the whole point; two columns side by side make you do that subtraction in your head.

> **On grading honesty:** content relevance is matched fuzzily (so `rate_limit`, `rate-limit`, and `rate limit` compare equal), which means the occasional false-positive on a shared phrase and false-negative on a paraphrase. Trust the *relative* numbers across configs; be cautious with absolutes. The harness never pretends to more precision than it has.

---

## Tuning the callosum without a hundred restarts

`regrade.py` is the SCC fusion tuner, and it's built on one insight: the fusion knobs (RRF *k*, per-store weight, floor, `authority_margin`, `min_per_store`, packet size) all operate on candidates the stores *already returned*. So you query each store **once** per question, freeze the raw responses to a trace, and then replay the **real** Federation against that frozen capture for every knob combination — in-process. A 300-point grid that used to be 300 service bounces becomes a sub-second sweep.

It's **read-only** (only ever `POST /search`, never seeds or mutates), refuses to run without explicit dev-store URLs, and can sweep a saved trace fully offline with nothing live attached. Capture once on the dev rig, then tune all day on a plane.

```bash
# capture on the dev rig + sweep, save the trace
python -m seren_probe.regrade \
    --memory-url http://127.0.0.1:7420 \
    --loci-nv-url http://127.0.0.1:7422 \
    --loci-v-url  http://127.0.0.1:7421 \
    --save-capture /tmp/scc_capture.json

# later, re-sweep the frozen trace, nothing live attached
python -m seren_probe.regrade --load-capture /tmp/scc_capture.json
```

---

## Configuration

Resolution order, later wins: **built-in defaults → `seren-probe.yaml` → `SEREN_PROBE_*` env vars.** A missing config file is fine — defaults plus env is a valid zero-config run.

Copy `serenprobe.yaml.sample` to `seren-probe.yaml` and edit, or set env vars (`SEREN_PROBE_PORT`, `SEREN_PROBE_MEMORY_URL`, `SEREN_PROBE_BEARER_TOKEN`, …). The default store layout follows the Seren family port convention:

| Service | Port |
|---|---|
| SerenMemory | 7420 |
| SerenLoci (vector) | 7421 |
| SerenLoci (no-vector) | 7422 |
| SCC (no-vector) | 7423 |
| SCC (vector) | 7424 |
| **SerenProbe dashboard** | **7430** |

---

## Install

```bash
pip install seren-probe            # core: eval + dashboard against live stores
pip install "seren-probe[mcp]"     # + MCP surface for connected models
pip install "seren-probe[corp]"    # + OS-trust-store TLS for intercepting proxies
pip install "seren-probe[dev]"     # + pytest for hacking on the harness
```

Requires Python 3.10+.

**On a corporate network** that does TLS interception (Zscaler, Netskope, and friends): install the `corp` extra and set `tls.trust_system_store: true`. That routes Python's TLS through your OS trust store so outbound calls to the store services stop failing on an unknown root CA.

---

## Testing

The tests are transport-injected — they prove the compiler, the compose emitter, the seeder, and the grading math without needing a live store stack, so `pytest` runs green anywhere:

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

CI runs the full matrix on every push and publishes to PyPI (tokenless Trusted Publishing) on a `v*` tag.

---

## Project layout

```
seren_probe/
  topology.py        # ProbeConfig → validated CompiledTopology (the compiler)
  topology_emit.py   # CompiledTopology → docker compose + per-corpus wiring
  seed_dataset.py    # flat seed items + questions, ref→id capture, honest ground truth
  resolve.py         # ProbeConfig refs → per-store seed plan + shared questions
  live_eval.py       # topology-driven + legacy live-store evaluation
  metrics.py         # the retrieval + docket scoring math
  docket.py          # pairs the SCC columns → the with/without-edges delta
  regrade.py         # capture-once / replay-many SCC fusion sweep
  runner.py          # in-process per-store evaluation runner
  app.py             # FastAPI dashboard: /eval, /docker, /viewer, /mcp
  viewer/            # the operator dashboard UI
tests/               # transport-injected, no live stack required
examples/            # a ready-to-upload seed dataset + question set
```

---

## License

GPL-3.0-or-later. Part of the [Seren](https://github.com/ChadRoesler) project — a fully self-hosted, local-first AI companion stack built to run gracefully on cheap hardware. The floor is a $250 Jetson, not a data center. Take it, probe your own stores, and show us what you find.
