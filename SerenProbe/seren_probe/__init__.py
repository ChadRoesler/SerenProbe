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

from .dataset import (  # noqa: F401,E402
    EvalDataset, EvalQuery,
    export_dataset_only,
    export_synthetic_dataset_json,
    load_dataset,
    seed_synthetic_dataset,
)
from .metrics import EvalMetrics  # noqa: F401,E402
from .evaluators import LociEvaluator, MemoryEvaluator, CorpusCallosumEvaluator  # noqa: F401,E402
from .config import SerenProbeConfig, load_config  # noqa: F401,E402
from .docker_env import DockerEnv, DockerEnvState, launch_and_eval, \
    build_image, start_container, wait_for_healthy, stop_container, \
    container_status  # noqa: F401,E402

__all__ = [
    "__version__",
    "EvalDataset", "EvalQuery",
    "EvalMetrics",
    "LociEvaluator", "MemoryEvaluator", "CorpusCallosumEvaluator",
    "export_dataset_only",
    "export_synthetic_dataset_json",
    "load_dataset",
    "seed_synthetic_dataset",
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
