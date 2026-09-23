"""
seren_probe.mcp.tools
═════════════════════

MCP tools for SerenProbe. A connected model can score the running topology and
read the results back without the HTTP API.

Tool roster:
    run_evaluation          - score the topology SerenProbe spun up (same path
                              as POST /eval/run, same guards)
    get_eval_results        - the latest results, lean
    get_store_config        - what is running, and the operator-typed live URLs

WHAT THIS USED TO DO, so it is not put back: run_evaluation imported
`run_live_evaluation` from live_eval - the retired fixed-five-store evaluator
that SEEDED the operator's real stores if it found them empty. That function
was deleted for exactly that reason; the import stayed, so every MCP call was
an ImportError, and no test touched this class. Both doors now go through
runtime.eval_run, so the topology-only rule and the seed guard hold here by
construction rather than by a second copy of the code.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..runtime.eval_run import (
    EvalFailed, EvalInputError, NoTopologyRunning, configured_store_count, lean,
    run_topology_eval,
)

logger = logging.getLogger(__name__)


class ProbeToolImpl:
    """The tool implementations, callable via FastMCP (in production) and
    directly (in tests). Holds the app's state object - the SAME one the
    routes read and write - so results scored over MCP show on the dashboard
    and results scored from the dashboard are readable over MCP.

    (A private dict of references used to stand in for this. The route
    REPLACES app.state.eval_results on every run rather than mutating it, so
    the dict's reference went stale after the first eval and get_eval_results
    returned the empty seed forever.)
    """

    def __init__(self, app_state: Any) -> None:
        self.app_state = app_state

    # -- Evaluation tools --------------------------------------------------
    async def run_evaluation(self, reseed: bool = False,
                             max_parallel_stores: int = 8,
                             max_parallel_questions: int = 1) -> dict:
        """Score every store in the topology SerenProbe started (POST
        /docker/start) against the ProbeConfig's questions. Refuses when no
        topology is running: SerenProbe never evaluates - or seeds - stores it
        did not spin up itself. Seeding happens on the first run only; pass
        reseed=true to stack another copy of the corpus on purpose. Long: a
        big corpus takes hours. Results are kept for get_eval_results.
        """
        body = {"reseed": bool(reseed),
                "max_parallel_stores": int(max_parallel_stores),
                "max_parallel_questions": int(max_parallel_questions)}
        try:
            results = await run_topology_eval(self.app_state, body)
        except NoTopologyRunning as exc:
            return {"ok": False, "error": str(exc)}
        except EvalInputError as exc:
            return {"ok": False, "error": "eval inputs could not be resolved",
                    "detail": exc.detail}
        except EvalFailed as exc:
            return {"ok": False, "error": f"evaluation failed: {exc}"}
        return {"ok": True, "results": lean(results)}

    def get_eval_results(self) -> dict:
        """The latest evaluation results, lean (per-question detail is on the
        dashboard's drill-down, not here). Empty if nothing has been scored in
        this process yet - run_evaluation first."""
        cached = getattr(self.app_state, "eval_results", None)
        if cached:
            return lean(cached)
        return {"stores": {}, "query_count": 0, "date": ""}

    # -- Config inspection -------------------------------------------------
    def get_store_config(self) -> dict:
        """What the eval would run against: whether a topology is up and which
        stores it holds, plus the operator-typed live-store URLs (usually
        empty; the topology path never reads them)."""
        scfg = dict(getattr(self.app_state, "store_config", None) or {})
        ts = getattr(self.app_state, "topology_state", None) or {}
        url_of = ts.get("url_of") if isinstance(ts, dict) else None
        return {
            "topology_running": bool(ts and getattr(self.app_state, "compiled_topology", None)),
            "topology_project": ts.get("project_name", "") if isinstance(ts, dict) else "",
            "topology_stores": sorted(url_of.keys()) if isinstance(url_of, dict) else [],
            "seeded": bool(ts.get("seeded")) if isinstance(ts, dict) else False,
            "stores": configured_store_count(scfg),
            **scfg,
        }


def register_tools(mcp: FastMCP, impl: ProbeToolImpl) -> None:
    """Wire every method of a ProbeToolImpl onto a FastMCP instance."""
    mcp.tool(name="run_evaluation")(impl.run_evaluation)
    mcp.tool(name="get_eval_results")(impl.get_eval_results)
    mcp.tool(name="get_store_config")(impl.get_store_config)
