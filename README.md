# SerenProbe

**A push-button RAG evaluation harness for the Seren memory constellation.**

SerenProbe exists to answer one deceptively hard question: *did that change make retrieval better or worse?* You tweaked an embedder, flipped Loci from lexical to hybrid, retuned the fusion knobs on the callosum - and now you need a number that tells you whether the model is going to get handed a better briefing or a worse one. SerenProbe seeds a known corpus, asks known questions, and scores what comes back, honestly, across every store configuration side by side.

It's the empiricist's tool. You don't reason about whether the change helped. You see it.

---

## What it probes

Seren's memory lives in three services, and SerenProbe knows how to grade all of them:

- **SerenMemory** - the episodic "right brain." Three-tier vector store (short / near / long).
- **SerenLoci** - the deterministic "left brain." SQLite facts, addressable by exact key, searchable by FTS5 or FTS5+vector hybrid.
- **SerenCorpusCallosum (SCC)** - the "callosum." Fans a query across Memory + Loci and fuses the results into one briefing packet.

Each brain has a **no-vector (NV)** and a **vector (V)** flavor, and they're not interchangeable - the whole reason SCC exists is to build a *diverse, provenance-tagged* packet, not to crown a single winner. So SerenProbe scores Loci and Memory with the standard retrieval metrics, and scores SCC with those *plus* docket-quality metrics that measure whether the briefing actually covered the ground. (SCC is a chief-of-staff assembling a packet, not Judge Judy picking rank one. The metrics reflect that.)

---

## Two ways to run it

### 1. Against stores you already have running

Point SerenProbe at the URLs of live services and grade them in place:

```bash
pip install seren-probe
seren-probe                       # boots the operator dashboard on :7430
```

Open **http://127.0.0.1:7430/viewer**, or drive it headless via `POST /eval/run`. Configure the store URLs in `serenprobe.yaml` (or via `SEREN_PROBE_*` env vars) - see [Configuration](#configuration).

### 2. The self-contained Docker harness

Don't have five stores wired up? Declare the shape you want in `ProbeConfig.yml` and let SerenProbe stand the whole thing up, seed it, grade it, and tear it down:

```
ProbeConfig.yml  →  compile  →  emit compose  →  docker up  →  seed  →  eval  →  results
```

The compiler is **correct-by-construction**: it wires inter-container traffic with container-DNS URLs (never `localhost`), publishes host ports only for the eval harness, and - critically - wires each SCC to *its own* Loci. Pointing `scc-nv` and `scc-v` at the same Loci instance is the one silent failure that quietly poisons a whole comparison, so the topology compiler refuses to let you do it by accident and *tells you* when your config drifts toward it.

Kick it off from the dashboard (Docker tab) or `POST /docker/run-eval`.

---

## The ProbeConfig topology compiler

`ProbeConfig.yml` is a declarative description of the stores you want to probe - how many Loci, how many Memory, which flavors, and how the Corpus nodes fan across them. You describe the *shape*; the compiler handles ports, wiring, and the footguns.

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
- **Compassion-first validation.** Bad flag for a store type, a Corpus that references another Corpus, a duplicate port, a store name that isn't declared, two Corpus nodes with identical store sets - every one of these fails *loud and kind*, naming the exact rule and the exact node, at compile time. Config bugs are the expensive kind; this catches them before a container ever starts.
- **Deterministic, type-grouped ports.** Auto-assigned ports are stable run-to-run and grouped by kind, so a diff of two runs is a diff of your *intent*, not port-assignment noise.

A full working `ProbeConfig.yml` ships in the package.

---

## What the numbers mean

Every store gets the standard retrieval battery, computed at top-*k*:

| Metric | Reads as |
|---|---|
| **HitRate@k** | Did *any* relevant doc land in the top-k? (0 or 1) |
| **MRR@k** | How high was the *first* relevant hit? (rank 1 → 1.0, rank 3 → 0.333) |
| **Precision@k** | Fraction of the top-k that were relevant |
| **Recall@k** | Fraction of all relevant docs the top-k caught |
| **NDCG@k** | Rank-aware quality - right answers near the top score higher |
| **IoU@k** | Jaccard overlap of retrieved vs. relevant |
| **PΩ@k** | Rank-weighted precision - top ranks count for more |

SCC additionally reports **docket_coverage** (fraction of expected facts found *anywhere* in the packet - did the briefing cover the ground?) and **docket_density** (fraction of the top-k that carry expected content - how much of the packet is signal?). These are the metrics that actually reflect SCC's job.

> **On grading honesty:** content relevance is matched fuzzily (so `rate_limit`, `rate-limit`, and `rate limit` compare equal), which means the occasional false-positive on a shared phrase and false-negative on a paraphrase. Trust the *relative* numbers across configs; be cautious with absolutes. The harness never pretends to more precision than it has.

---

## Tuning the callosum without a hundred restarts

`regrade.py` is the SCC fusion tuner, and it's built on one insight: the fusion knobs (RRF *k*, per-store weight, floor, `authority_margin`, `min_per_store`, packet size) all operate on candidates the stores *already returned*. So you query each store **once** per question, freeze the raw responses to a trace, and then replay the **real** Federation against that frozen capture for every knob combination - in-process. A 300-point grid that used to be 300 service bounces becomes a sub-second sweep.

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

Resolution order, later wins: **built-in defaults → `serenprobe.yaml` → `SEREN_PROBE_*` env vars.** A missing config file is fine - defaults plus env is a valid zero-config run.

Copy `serenprobe.yaml.sample` to `serenprobe.yaml` and edit, or set env vars (`SEREN_PROBE_PORT`, `SEREN_PROBE_MEMORY_URL`, `SEREN_PROBE_BEARER_TOKEN`, …). The default store layout follows the Seren family port convention:

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

The tests are transport-injected - they prove the compiler, the compose emitter, the seeder, and the grading math without needing a live store stack, so `pytest` runs green anywhere:

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
  seed_dataset.py    # pool-based seeding, ref→id capture, honest ground truth
  live_eval.py       # topology-driven + legacy live-store evaluation
  metrics.py         # the retrieval + docket scoring math
  regrade.py         # capture-once / replay-many SCC fusion sweep
  runner.py          # in-process per-store evaluation runner
  app.py             # FastAPI dashboard: /eval, /docker, /viewer, /mcp
  viewer/            # the operator dashboard UI
tests/               # transport-injected, no live stack required
```

---

## License

GPL-3.0-or-later. Part of the [Seren](https://github.com/ChadRoesler) project - a fully self-hosted, local-first AI companion stack built to run gracefully on cheap hardware. The floor is a $250 Jetson, not a data center. Take it, probe your own stores, and show us what you find.