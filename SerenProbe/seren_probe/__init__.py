"""
seren_probe - RAG evaluation toolkit for Seren's memory architecture.

Evaluates retrieval quality across:
  - SerenLoci (SQLite factstore: exact/lexical/vector)
  - SerenMemory (3-tier Chroma DB: short/near/long)
  - SerenCorpusCallosum (federated RRF merge of both)

Standard metrics: Hit Rate, MRR, Precision@k, Recall@k, NDCG@k.
"""
from __future__ import annotations

# Version flows from the git tag via setuptools-scm (written to _version.py at
# build time, read here). Fallback only fires in a bare source checkout that was
# never built. Mirrors the family so every seren_* exposes __version__ alike.
try:
    from ._version import version as __version__
except Exception:  # noqa: BLE001 - source checkout without a build
    __version__ = "0.0.0+unknown"
    
# NOT exported anymore: EvalDataset / EvalQuery / seed_synthetic_dataset (dataset.py)
# and LociEvaluator / MemoryEvaluator / CorpusCallosumEvaluator (evaluators.py).
#
# dataset.py is the SYNTHETIC corpus generator. Importing it here meant it loaded on
# every `import seren_probe` -- so the fake data was in the import graph of every
# code path that so much as touched an eval, sitting next to a module whose defaults
# named the operator's real stores. That combination is how synthetic content ends up
# inside a live SerenMemory, and it did.
#
# The in-process legacy suite (dataset / evaluators / runner) is in _attic/. Nothing
# in the topology path needs it: seeding is config-driven via seed_dataset.py, and
# evaluation goes through live_eval.run_topology_evaluation, which only ever addresses
# containers SerenProbe spun up itself.

__all__ = [
    "__version__",
    "EvalMetrics",
    "SerenProbeConfig",
    "load_config",
    "DockerEnv",
    "DockerEnvState",
    "launch_and_eval",
    "build_image",
    "start_container",
    "wait_for_healthy",
    "stop_container",
    "container_status",
]
