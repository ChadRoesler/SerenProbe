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

Open **http://127.0.0.1:7430/viewer**, or drive it headless via `POST /eval/run`. Configure the bind and token in `seren-probe.yaml` (or via `SEREN_PROBE_*` env vars) — see [Configuration](#configuration).

### 2. The self-contained Docker harness

Don't have five stores wired up? Declare the shape you want in `ProbeConfig.yml` and let SerenProbe stand the whole thing up, seed it, grade it, and tear it down:

```
ProbeConfig.yml  →  compile  →  emit compose  →  docker up  →  seed  →  eval  →  results
```

The compiler is **correct-by-construction**: it wires inter-container traffic with container-DNS URLs (never `localhost`), publishes host ports only for the eval harness, and — critically — wires each SCC to *its own* Loci. Pointing `scc-nv` and `scc-v` at the same Loci instance is the one silent failure that quietly poisons a whole comparison, so the topology compiler refuses to let you do it by accident and *tells you* when your config drifts toward it.

Kick it off from the dashboard (**▶ Start** on the Docker tab, then **Evaluate** on the Eval tab) or headless: `POST /docker/start`, then `POST /eval/run`.

---

## The ProbeConfig topology compiler

`ProbeConfig.yml` is a declarative description of the stores you want to probe — how many Loci, how many Memory, which flavors, and how the Corpus nodes fan across them. You describe the *shape*; the compiler handles ports, wiring, and the footguns.

```yaml
ProbeConfig:
  StartingPort: 7520
  Loci:
    LociCount: 2
    LociConfigs:
      - { Name: loci-v,  Port: 7521, Flags: [vector] }   # hybrid
      - { Name: loci-nv, Port: 7522 }                     # FTS5-only (nano floor)
  Memory:
    MemoryCount: 1
    MemoryConfigs:
      - { Name: mem, Port: 7520 }
  Corpus:
    CorpusCount: 2
    CorpusConfigs:
      - { Name: scc-v,  Port: 7524, Stores: [{ Store: loci-v },  { Store: mem }] }
      - { Name: scc-nv, Port: 7523, Stores: [{ Store: loci-nv }, { Store: mem }] }
```

What it does for you:

- **Autogen + catch-all.** Declare a count higher than your explicit configs and it fills the gap; any store you don't reference gets swept into a catch-all Corpus so nothing is silently stranded.
- **Compassion-first validation.** Bad flag for a store type, a Corpus that references another Corpus, a duplicate port, a store name that isn't declared, two Corpus nodes with identical store sets — every one of these fails *loud and kind*, naming the exact rule and the exact node, at compile time. Config bugs are the expensive kind; this catches them before a container ever starts.
- **Deterministic, type-grouped ports.** Auto-assigned ports are stable run-to-run and grouped by kind, so a diff of two runs is a diff of your *intent*, not port-assignment noise.

A full working `ProbeConfig.yml` ships in the package, plus a heavily-commented `ProbeConfig.template.yml` to copy from.

---

## Bring your own corpus + questions

The topology says *what stores* to stand up; **flat seed files** and a **question set** say what to put in them and what to ask — and you wire them into the ProbeConfig itself, so one config is self-contained. No separate dataset upload: hand SerenProbe the ProbeConfig and it seeds + scores from what the config points at.

A **seed file** is a flat list of items (the store→data mapping lives in the config, not the file). Shapes are the real write contracts — Loci `{project?, key, value, why?, why_changed?, history?}`, Memory `{content|intent, topic?, tier?, ref?}`:

```yaml
# meridian.loci.yaml
- { project: meridian, key: api_port, value: "8080", why: "the REST API listens here" }
# a fact that changed: its earlier values, oldest first, each set and then superseded
- project: meridian
  key: db_host
  value: db-02.internal
  why: "the primary since the March failover"
  why_changed: "db-01 lost its disk"   # why this value replaced the one before
  history:
    - { value: db-01.internal, why: "the original primary" }
# meridian.memory.yaml
- { tier: short, ref: auth-incident, content: "auth threw 401s after a clock-skew bug", topic: auth }
# a long item can carry its surroundings: satellites, and the earlier core it supersedes
- { tier: long, ref: seat-1, content: "House Vayne holds its seat at Grimsgate" }
- tier: long
  ref: seat-2
  supersedes: seat-1              # an earlier long item in the same store
  content: "House Vayne moved its seat to Blackmere after the fire"
  satellites:
    - { ref: fire, content: "the fire at Grimsgate burned the great hall" }
```

A Loci item's `history` is written first, in order, then its live value, so the store holds the past the way set_fact makes it: retired rows under the same key. Each `why_changed` rides with the write it explains, and Loci keeps it on the row that write retires. A Loci that keeps change reasons echoes `why_changed` in its answer; one that does not is named in the result's `ground_truth` notes instead of silently dropping them.

Long items are written the way long-term is actually written: through an approved draft. Refs bind to the ids Memory returns, satellites land on their core, and a supersede demotes the core it names. An unknown key on an item is warned about, never silently dropped.

Wire them into the ProbeConfig — a `DefaultQuestions` set (scored across every store unless a node names its own `Questions:`, which is how a multi-brain dataset points the right questions at the right brain), per-kind default seeds, and optional per-node `Seed` overrides:

```yaml
ProbeConfig:
  DefaultQuestions:   examples/meridian.questions.yaml
  DefaultLociSeed:    examples/meridian.loci.yaml      # every Loci without its own Seed
  DefaultMemorySeed:  examples/meridian.memory.yaml    # every Memory without its own Seed
  Loci:
    LociConfigs:
      - { Name: loci-v, Port: 7521, Flags: [vector] }
      - { Name: decoy,  Port: 7529, NegativeTest: true, Seed: examples/unrelated.yaml }
  # ... Memory / Corpus ...
```

A **question set** scores each query against honest ground truth — `expect_key` (Loci's canonical id, retrieval-independent), `expect_ref` (a Memory entry you tagged at seed time), or `expect_content` (a fuzzy substring):

```yaml
questions:
  - { asks: loci,   query: "what port does the API use?", expect_key: ["meridian/api_port"] }
  - { asks: memory, query: "where is House Vayne's seat now?", expect_ref: [seat-2], stale_ref: [seat-1] }
  - { asks: loci,   query: "which host is the database on?", expect_key: ["meridian/db_host"],
      stale_key: ["meridian/db_host"] }
  - { asks: corpus, query: "brief me on the auth setup",
      expect_content: ["JWT bearer tokens", "15 minute", "100 requests per minute", "team-atlas"] }
```

The **corpus** questions are where docket coverage earns its keep: give each *several* expected facts that live across both Loci and Memory, and coverage becomes a real "did the briefing assemble all of it?" fraction instead of a binary hit.

**Negative (decoy) stores.** Mark a store `NegativeTest: true` and give it an unrelated `Seed`, and it's seeded with *only* that decoy — never the defaults — so you can prove a store *stays quiet* on questions it shouldn't answer. It's kept out of the catch-all, and the Eval tab reads it as ✓ *stayed quiet* / ✗ *leaked* instead of a bare zero.

A complete, validated example ships in [`examples/`](examples/): `meridian.loci.yaml` + `meridian.memory.yaml` + `meridian.questions.yaml` — a small interlocking fictional stack with three multi-fact briefing questions — plus `unrelated.yaml`, an off-topic seed for a decoy store. The test suite loads them, so they cannot quietly stop being true. Point the ProbeConfig's `Default*Seed` / `DefaultQuestions` at them (or inline the content for a fully self-contained upload), then just `POST /eval/run` with an empty body.

Validation is **compassion-first**, same as the compiler: an item with the wrong shape, a question with no way to score it, a negative store with no decoy — each fails loud and kind, all at once, naming the fix.

---

## Per-node configuration (defaults + overrides)

The same **default-then-override** pattern as seeds and questions applies to *config* — the knobs each store boots at. Nothing is required; anything you don't set falls through to the service's own installed default. The probe only writes what you specify.

Each section (`Loci`, `Memory`, `Corpus`) may carry a `DefaultConfig`, and each node may carry its own `Config`. The precedence, lowest to highest, is:

```
service installed default   ←   section DefaultConfig   ←   per-node Config
```

For **Loci** and **Memory**, `DefaultConfig` and a node's `Config` are a *partial of that service's own yaml* (nested — `storage`, `lifetimes`, `consolidator`, …), **deep-merged** and mounted into the container as its config file. Set only the keys you care about; the rest default.

```yaml
Memory:
  MemoryCount: 3
  DefaultConfig:                       # every Memory node inherits this
    consolidator: { enabled: false }
  MemoryConfigs:
    - Name: fast-mem
      Config:                          # overrides DefaultConfig for THIS node (deep-merged)
        lifetimes: { short_term_seconds: 3600 }
    - Name: plain-mem                  # no Config → just DefaultConfig
Loci:
  LociCount: 1
  # no DefaultConfig → every Loci boots on its installed defaults
  LociConfigs:
    - Name: only-loci
```

For **Corpus**, the config is the *fusion* layer, which adds a second axis — per-store `weight`/`floor` — so it uses flat knobs plus a per-kind shorthand rather than nested yaml (the probe builds the federation store list for you, container DNS and all). A corpus store's `floor`/`weight` resolves:

```
per-store  Floor:/Weight:  (on the Stores entry)
   ←  per-kind shorthand  loci_floor / loci_weight / mem_floor / mem_weight  (in Config)
   ←  Corpus.DefaultConfig shorthand
   ←  the SCC's installed default
```

`loci_floor` floors *every* loci member of the corpus (and leaves memory alone); `mem_floor` does the same for memory. A per-store `Floor:` on a `Stores` entry is the escape hatch that beats the shorthand for that one store. Federation-level knobs — `authority_margin`, `n_results`, `rrf_k`, `fusion_mode`, `min_per_store`, `hops`, the edge knobs (`satellite_edges_enabled`, `satellite_budget`, `edges_enabled`, `edge_budget`) — live in `Config`/`DefaultConfig` and go straight onto the federation block.

```yaml
Corpus:
  CorpusCount: 1
  DefaultConfig:                       # every corpus inherits these
    authority_margin: 0.035
    loci_floor: 0.5                    # all loci members, unless overridden
  CorpusConfigs:
    - Name: big-scc
      Config:
        loci_floor: 0.6                # this corpus floors its loci at 0.6
      Stores:
        - Store: sharp-loci
          Floor: 0.7                   # …except this one, pinned to 0.7
        - Store: some-mem              # no mem_floor set → SCC's own default
```

The boot config the container mounts **and** the live `/configure` a regrade sweep pushes are resolved through the *same* ladder, so what you sweep and what you boot can't disagree — a value you swept and liked pastes straight into `Config` as the new baseline.

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

**Satellites and marked edges.** Memory's long-term is a core and its surroundings: recall returns cores, and a supporting episode (a *satellite*) surfaces through its core, which carries it as `matched_via`. The Callosum hangs a core's newest satellites, the core it superseded and topic neighbours after the ranked packet as marked edges (`satellite-edge`, `supersedes-edge`, `topic-edge`). An edge is never a rank: the @k metrics read the ranked packet, and an answer an edge carries credits the core it hangs off. A satellite's `expect_ref` is credited to the core that surfaced it. Coverage and the quiet test read everything returned, including the satellite text a core came through. A question with `stale_ref` (the answer that *used to be* right) reports **stale_rate** — how often the superseded core ranked inside the top-k as an answer — with `stale_as_edge` beside it for the times it came back marked as history. `stale_key` is the same test on Loci: a key's retired values (read live from `/fact/history`) must not rank for a "now" question. It counts in the same stale columns, and the linter refuses a `stale_key` the seed never gave a history.

**What the edges bought, and what they cost.** `docket_coverage` reads everything returned, edges included, so every corpus column also carries `surroundings`: coverage of the ranked packet alone (`coverage_packet`), then with each edge family folded in (`coverage_with`), density the same way (`density_with`), and the rows and characters each family adds (`rows`, `chars`) — the reading cost a bigger satellite budget buys. The docket comparison prints it per column. The live knob sweep grades a packet exactly as the eval does (one module, `core/edges.py`), and the edge knobs are sweepable: `satellite_edges_enabled`, `satellite_budget`, `edges_enabled`, `edge_budget` (live only — edges hang off the packet the fusion just made; the capture replay runs every edge family off, so its `docket_coverage` is the packet's and is reported as `docket_coverage_packet` too).

### The docket comparison — vector vs lexical Loci

Run a topology with both a vector SCC and a lexical one (the canonical `scc-v` / `scc-nv` pair) and SerenProbe doesn't just score them independently — it **pairs them and reports the delta**, answering the question an SCC exists for: *when the callosum fans a vector Loci (embeddings) instead of a lexical, FTS5-only one, does the assembled briefing carry more of the relevant info?* (This was called "with vs without edges" until 22 Oct 2026, a name older than the Callosum's marked edges above and easily read as them. Each delta still carries `with_edges` / `without_edges` beside `vector` / `lexical`.)

The comparison rides in every `/eval/run` result under `result["docket"]` and renders in the Eval tab as a side-by-side block:

```
SCC Docket — vector vs lexical Loci     30 questions · k=10
  lexical Loci  scc-nv   coverage 0.5850   density 0.7033   recall 0.9544
  vector Loci   scc-v    coverage 0.5331   density 0.6967   recall 0.9250
  Δ vector − lexical  coverage -0.0519 · density -0.0066 · recall -0.0294
  → the vector Loci COST -0.0519 docket coverage
```

The flavor is read authoritatively off the compiled topology — an SCC's flavor is whether the Loci it fans carries the `[vector]` flag — so the labels are never guessed. And mind the sign: in that run the vector Loci *hurt* coverage. Surfacing the delta is the whole point; two columns side by side make you do that subtraction in your head.

> **On grading honesty:** content relevance is matched fuzzily (so `rate_limit`, `rate-limit`, and `rate limit` compare equal), which means the occasional false-positive on a shared phrase and false-negative on a paraphrase. Trust the *relative* numbers across configs; be cautious with absolutes. The harness never pretends to more precision than it has.

---

## The inquisition bundle (.iqz)

A ProbeConfig's `Seed` and `Questions` paths are opened by the Probe **process**,
on its own box, relative to its working directory. The viewer can send a config
from anywhere; the files it names do not travel with it. An `.iqz` bundle is the
dataset and its configs together: Sinew's verified snapshot tar (one top folder,
a `manifest.json` naming every file with its sha256) with a manifest of kind
`probe` that lists one or more ProbeConfigs by name, each with a note. That is
the grouping: a dataset's easy and hard configs side by side, or a before and an
after over the same questions. Refs inside a bundled config are relative to the
bundle's root and never reach outside it.

    seren-probe bundle pack TestDatasets/aldermoor -o aldermoor.iqz --name aldermoor \
        --probe-config before=ProbeConfig.before.yml::"146 cores, 20 Oct" \
        --probe-config after=ProbeConfig.yml::"165 cores, retold"
    seren-probe bundle inspect aldermoor.iqz

`pack` carries the configs and every file they reference (`--all` for the whole
folder) and refuses a config that does not compile or a ref that is missing or
climbs out of the folder. Without `--probe-config`, every `ProbeConfig*.yml` at
the top of the folder is carried, named by its suffix (`ProbeConfig_hard.yml` is
`hard`; `ProbeConfig.yml` is `default`).

| route | what |
|---|---|
| `POST /bundles` (the archive as the body) | validate, then install. The manifest is read out of the archive first, each config compiled, every ref checked against the archive's own file list; only then are bytes written, and every sha256 is verified before the folder is kept. A bad bundle costs one read and leaves nothing behind; 400 carries every problem. |
| `POST /bundles/inspect` | the same checks, nothing written |
| `GET /bundles` | what is installed, with how many runs in the ledger name each |
| `GET /bundles/{id}` | one bundle, its configs compiled (store names, warnings, errors) |
| `GET /bundles/{id}/configs/{name}` | a config's YAML and summary |
| `POST /bundles/{id}/activate` `{"config": name}` | make that config the active ProbeConfig, exactly as an upload would, with the bundle's folder as the base for its refs; a running pod of the same structure is hot-swapped |
| `DELETE /bundles/{id}?force=true` | refuses (409) while anything holds it, naming every holder with a reason: `running_pod` (a pod was started from it; Stop it), `active_config` (clear it with `DELETE /docker/probeconfig`), `runs` (they keep their numbers either way). `force` deletes anyway and clears the active config with it |
| `DELETE /docker/probeconfig` | clear the active ProbeConfig (the **Clear** button on the Config tab): nothing is active and Start falls back to the shipped example. A running pod keeps the config it was started with until it is stopped |

A bundle unpacks to `datasets/<name>-<hash8>/` under the state dir. The hash is
over the content (every file's path and sha256, the config names), not the
archive bytes, so the same dataset packed twice is one folder. Nothing is thrown
away when a pod stops; only the upload's temp file is. Uploads are capped at
512 MB. A run started from a bundled config names the bundle and the config in
the ledger, and `/eval/runs?bundle=` lists them. In the viewer, the **Bundles**
block on the Config tab does the same: inspect, upload, activate a config by
name, delete (with the force path when runs name it). A bundled config that is
active survives a Probe restart (the choice is kept beside the state, and the
bundle is on disk); an uploaded config lives in the process, as before, until a
pod is started on it.

### Passing an inquisition on

`GET /bundles/{id}/export` (the **Export** button on a bundle) packs the
installed dataset and its configs again, plus every run in the ledger that
names the bundle, as `runs/<id>.json`. Whoever installs that archive gets the
same bundle id (the runs ride outside the content hash) and your runs in their
ledger marked `baseline`, grouped with the config they ran on: install,
activate the config, Start, Evaluate, and Compare theirs against yours. "Here
is a baseline of a world; check it against your stores and fiddle with your
knobs." Editing a bundled config in the editor and setting it active keeps it
tied to the bundle (the checkbox under the editor): its paths still mean files
inside the bundle, and its runs group as `<config> (edited)`, so the knobs
change and the data they run on does not. The edited text survives a restart
with the choice.

## The run ledger

Every eval and every regrade sweep is kept, not just the last one. A run lands
as `runs/<stamp>-<id>.json` under the state dir with what makes it comparable
later: the topology fingerprint, when the stores were seeded, the hash of the
question set, the version pins, the Probe version, how long it took, and the
results. `/eval/results` stays "latest" and now carries its `run_id`.

| route | what |
|---|---|
| `GET /eval/runs` | newest first; filter by `kind` (eval / regrade), `question_hash`, `config`, `bundle` |
| `GET /eval/runs/{id}` | one run, results leaned for the wire, with its per-tag table |
| `DELETE /eval/runs/{id}` | throw one away |
| `GET /eval/compare?a=&b=` | two runs side by side: per-store metric deltas, per-tag within-k deltas, the movers first, and whether the question sets match |

The per-tag table comes from the questions' `tags`, which ride on every
drill-down row. Editing the question set starts a new lineage on purpose: the
hash is on every row, and a compare across hashes says so.

In the viewer, the **Runs** tab is this ledger: filter by kind and bundle, group
by config (a group lends its latest run to A or B with one click, so before
against after is two clicks and Compare), see a run's own numbers, compare two
and copy the compare as markdown, throw a run away.

## Tuning the callosum without a hundred restarts

`regrade.py` is the SCC fusion tuner, and it's built on one insight: the fusion knobs (RRF *k*, per-store weight, floor, `authority_margin`, `min_per_store`, packet size) all operate on candidates the stores *already returned*. So you query each store **once** per question, freeze the raw responses to a trace, and then replay the **real** Federation against that frozen capture for every knob combination — in-process. A 300-point grid that used to be 300 service bounces becomes a sub-second sweep.

It's **read-only** (only ever `POST /search`, never seeds or mutates), refuses to run without explicit dev-store URLs, and can sweep a saved trace fully offline with nothing live attached. Capture once on the dev rig, then tune all day on a plane.

```bash
# capture from the harness pod (or a dev rig - never your live stack), save the trace
python -m seren_probe.regrade \
    --memory-url http://127.0.0.1:7520 \
    --loci-nv-url http://127.0.0.1:7522 \
    --loci-v-url  http://127.0.0.1:7521 \
    --save-capture /tmp/scc_capture.json

# later, re-sweep the frozen trace, nothing live attached
python -m seren_probe.regrade --load-capture /tmp/scc_capture.json
```

---

## Configuration

Resolution order, later wins: **built-in defaults → `seren-probe.yaml` → `SEREN_PROBE_*` env vars.** A missing config file is fine — defaults plus env is a valid zero-config run.

Copy `seren-probe.yaml.sample` to `seren-probe.yaml` and edit, or set env vars (`SEREN_PROBE_PORT`, `SEREN_PROBE_BEARER_TOKEN`, …). The `stores:` block is empty on purpose: SerenProbe scores the containers it starts, never your live stack, and the harness publishes them in its own band so the two can never be confused:

| | Port |
|---|---|
| **SerenProbe dashboard** | **7430** |
| Harness containers (from your ProbeConfig; the template starts at) | 7520+ |
| Your live stack, for reference: Lodestar 6361, Memory 7420, Margin 7421, Loci 7422, Callosum 7423, Workbench 7425, Observatory 7777 | never targeted |

One Loci per box: "vector" is a flag on it, not a second service. The `loci-v` / `loci-nv` pairs you see in a ProbeConfig are two harness *containers* of the same store, stood up side by side so the flag can be measured.

**Where Probe keeps its state.** Topology state (so a restarted Probe adopts a running pod instead of rebuilding it), the last eval's results, regrade corpus captures, and saved docker deployment configs all live under one state dir: `~/.seren-probe` by default. When one host runs more than one cluster, give each install its own with `storage.state_dir` in `seren-probe.yaml` (e.g. `~/seren/<install>/stores/probe`) or `SEREN_PROBE_STATE_DIR`, which beats the yaml. Two Probes sharing a state dir will each adopt the other's pod. `~` is expanded; absolute paths are used as-is.

Docker configs land in `<state_dir>/docker_configs`, with two exceptions: `SERENPROBE_DOCKER_CONFIG_DIR` still overrides outright, and with no state dir configured an existing `~/.serenprobe/docker_configs` (the old location) keeps being used, so configs already saved there are not stranded.

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
  core/                # the PURE layer: nothing here can reach a store
    topology.py        #   ProbeConfig → validated CompiledTopology (the compiler)
    topology_emit.py   #   CompiledTopology → docker compose + per-corpus wiring
    seed_dataset.py    #   flat seed items + questions, ref→id capture, honest ground truth
    resolve.py         #   ProbeConfig refs → per-store seed plan + questions
    metrics.py         #   the retrieval + docket scoring math
    docket.py          #   pairs the SCC columns → the with/without-edges delta
  runtime/             # the LIVE layer: the only modules allowed to speak httpx
    live_eval.py       #   topology-driven evaluation
    eval_run.py        #   the one "run the eval" both the route and the MCP tool call
    regrade.py         #   capture-once / replay-many SCC fusion sweep
    docker_env.py      #   compile → emit → compose up → health-gate, adopt, teardown
    write_guard.py     #   refuses any write that isn't to a container SerenProbe started
  routes/              # /eval, /docker, /eval/config
  mcp/                 # the MCP surface (run_evaluation, get_eval_results, get_store_config)
  app.py               # FastAPI dashboard wiring
  viewer/              # the operator dashboard UI
tests/                 # transport-injected, no live stack required
examples/              # meridian.{loci,memory,questions}.yaml + unrelated.yaml (a decoy seed)
```

---

## License

AGPL-3.0-or-later. Part of the [Seren](https://github.com/ChadRoesler) project — a fully self-hosted, local-first AI companion stack built to run gracefully on cheap hardware. The floor is a $250 Jetson, not a data center. Take it, probe your own stores, and show us what you find.
