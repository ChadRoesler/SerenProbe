"""
seren_probe.mcp.tools
═════════════════════

MCP tools for SerenProbe. Each tool wraps an evaluator function — the
connected model can run evaluations and inspect results directly via MCP
calls, without needing the HTTP API.

Tool roster:
    run_evaluation          — run full evaluation against live stores
    get_eval_results        — latest evaluation results
    get_store_config        — current store URL configuration
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


class ProbeToolImpl:
    """The actual tool implementations, callable both via FastMCP decoration
    (in production) and directly (in unit tests).

    Each method's return shape is JSON-serialisable — the FastMCP layer
    serialises it on the way out to the MCP client.
    """

    def __init__(self, store_config: dict[str, str],
                 state_ref: dict[str, Any]) -> None:
        self.store_config = store_config
        self.state_ref = state_ref  # shared mutable state dict

    # -- Evaluation tools --------------------------------------------------
    def run_evaluation(self) -> dict:
        """Run a full evaluation against all live stores (Memory, Loci, SCC).
        Returns metrics for each store and aggregate. Results are cached
        for retrieval via get_eval_results.
        """
        from ..live_eval import run_live_evaluation
        results = run_live_evaluation(
            memory_url=self.store_config["memory_url"],
            loci_nv_url=self.store_config["loci_nv_url"],
            loci_v_url=self.store_config["loci_v_url"],
            scc_nv_url=self.store_config["scc_nv_url"],
            scc_v_url=self.store_config["scc_v_url"],
        )
        self.state_ref["eval_results"] = results
        return {"ok": True, "results": results}

    def get_eval_results(self) -> dict:
        """Get the latest evaluation results. Returns empty if no eval has
        been run yet. Use run_evaluation first to generate fresh results."""
        return self.state_ref.get("eval_results") or {
            "stores": {}, "query_count": 0, "date": ""
        }

    # -- Config inspection -------------------------------------------------
    def get_store_config(self) -> dict:
        """Return the current store URL configuration. Shows which URLs the
        eval suite is talking to for each store."""
        return {
            "stores": 5,
            **self.store_config,
        }


def register_tools(mcp: FastMCP, impl: ProbeToolImpl) -> None:
    """Wire every method of a ProbeToolImpl onto a FastMCP instance."""
    mcp.tool(name="run_evaluation")(impl.run_evaluation)
    mcp.tool(name="get_eval_results")(impl.get_eval_results)
    mcp.tool(name="get_store_config")(impl.get_store_config)
