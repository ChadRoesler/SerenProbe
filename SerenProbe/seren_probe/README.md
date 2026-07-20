# `seren_probe/` - what lives where

Nineteen files in a flat root is a lot to land in cold. This is the map. It's the
same grouping a `core/` + `runtime/` split would give you - drawn, not moved, so
nothing's import path changes while you find your feet.

The boundary below isn't a suggestion: **`tests/test_layering.py` enforces it.**
Nothing in the PURE layer may import `httpx`, and no module anywhere may name a
live Seren port (7420–7424) in executable code. Both fail the build.

---

## 🧠 PURE - config, parsing, maths. Touches no network, no Docker, no store.

| module | what it is |
|---|---|
| `topology.py` | **the compiler.** ProbeConfig → validated `CompiledTopology`. Owns `REGRADE_KNOBS` + `KNOBS_NEEDING_CAPABILITY`. It's the landmine guard: two corpora that secretly fan the same stores, a decoy wired into a measured SCC - all caught here, before Docker breathes. |
| `topology_emit.py` | `CompiledTopology` → docker-compose + one `seren-corpus-callosum.yaml` per corpus, with correct-by-construction store wiring. |
| `seed_dataset.py` | seed/question **loading + validation**, the seeder (`seed_from_plan`), and `rehydrate_ref_map` (rebuilds the answer key from a live store when a pod was adopted). |
| `resolve.py` | decides which store gets which seed, and which questions get scored. |
| `question_lint.py` | **can this question be answered at all?** Three tiers: expectation-in-corpus, lexical rail (`multihop`), no rail at all (`unbridged`). An unanswerable question looks *exactly* like a retrieval failure on the dashboard. |
| `metrics.py` | HR / MRR / P@k / R@k / nDCG / IoU / P-Ω + docket coverage & density. |
| `docket.py` | the with-edges vs without-edges SCC comparison. |
| `knob_caps.py` | refuses to sweep a knob the SCC doesn't advertise. An ignored knob produces a flatline that reads as a ceiling. |
| `lint_cli.py` | `python -m seren_probe.lint_cli` - non-zero exit gate for CI. |

## 🌐 RUNTIME - everything that speaks to a running thing. `httpx` lives only here.

| module | what it is |
|---|---|
| **`write_guard.py`** | **THE INTERLOCK. Read this first.** SerenProbe cannot write to a store it did not spin up. Fail-closed: no topology → nothing writable, anywhere. |
| `live_eval.py` | `run_topology_evaluation` - evals every store in a topology as its own column. The main event. |
| `live_import.py` | read-only copy of a real store's data into a container. |
| `regrade_live.py` | the **live** SCC knob sweep - reconfigures the running container per combo. This is what the ⚙ Regrades button drives. |
| `regrade.py` | the **capture/replay** sweep - freeze each store's candidate pool once, replay the real Federation in-process over the frozen trace. Read-only. Owns `DEFAULT_GRID`. |
| `docker_env.py` | container lifecycle: compose up/down/ps, health-gating, pod adoption, topology-state persistence. |

## ⚙️ SERVICE

| module | what it is |
|---|---|
| `app.py` | the FastAPI app. Auth, request logging, viewer, route mounts, optional MCP. |
| `config.py` | `seren-probe.yaml` → typed config. Defaults → yaml → env. |
| `__main__.py` | `python -m seren_probe`. |
| `routes/` | `eval.py` (run + regrade) · `docker.py` (start/stop/status/adopt) · `config.py` |
| `viewer/ui/` | the dashboard: `body.html` · `tabs.html` · `scripts.js` · `styles.css` |
| `mcp/` | optional MCP surface (`pip install seren-probe[mcp]`) |
| `dockerfiles/` | the three store images the emitter builds from |

---

## The four rules this package learned the hard way

1. **The summary lies; the rows tell the truth.** Every confident read of an
   aggregate on 2026-07-13 was wrong, and wrong in the direction that made the
   story tidier. Dump the tier. Read every row.
2. **A knob swept in one direction is half a knob.** `loci_weight` was only ever
   swept *down*, toward the other store. The entire win was above 1.0.
3. **Ground truth must be re-resolvable from the live system.** A minted UUID held
   in the seeding process's RAM is not ground truth, it's a receipt. `expect_key`
   survived an adopted pod; `expect_ref` didn't, and reported a perfectly healthy
   store as dead for a full day.
4. **Interlocks catch what discipline doesn't.** Six live-store hazards and eleven
   silent measurement bugs in one day. Discipline caught none of them. Every guard
   we built caught one - including two the guards found *after* we'd stopped looking.
