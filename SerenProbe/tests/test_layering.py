"""
LAYERING INVARIANTS. Structural guards, not behaviour tests.

Everything else in this suite checks that SerenProbe does the right thing. These
check that it CANNOT easily do the wrong one -- which is a different and, on the
evidence, more valuable kind of test.

The evidence: in one day we found and killed FIVE separate code paths that could
write to (or outright kill) the operator's real, live Seren stores, plus ten
measurement bugs that each made the numbers look calmer than the truth. Not one of
them was caught by discipline. Every single one that got caught early was caught by
an interlock. So: interlocks.

    seed_memory_only.py  -> url = "http://localhost:7420", hardcoded, no flag, wrote
                            the synthetic corpus straight into the real SerenMemory
    live_eval.py         -> MEMORY_URL / LOCI_*_URL constants at module scope, and a
                            run_live_evaluation() that SEEDED them if it found them empty
    routes/eval.py       -> fell through to the above whenever no topology was running
    config.py            -> shipped those addresses as DEFAULTS, so nobody had to type them
    tune_scc.py          -> killed processes by port, rewrote SCC's yaml on disk, and
                            restarted the services. On hardcoded /home/caesar paths.

Every one of these would have failed test_no_live_store_ports_in_source on the day it
was written.

An address you have to TYPE is a decision. An address that arrives as a DEFAULT is an
accident waiting for a tired Tuesday.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "seren_probe"

# The operator's real Seren stack. SerenProbe manufactures synthetic data; it must
# never carry the address of a live brain in its source.
#   7420 SerenMemory · 7421/7422 SerenLoci · 7423/7424 SerenCorpusCallosum
LIVE_PORTS = range(7420, 7425)

# A string is an OFFENCE only if it IS an address -- not if it MENTIONS one.
#
# This distinction is the whole difference between a guard people keep and a guard
# people delete. The first version flagged argparse help text ("DEV SerenMemory base
# URL (e.g. http://127.0.0.1:7420)") and a validation error message ("LiveStoreUrl
# must be ... e.g. http://192.168.0.101:7422"). Both are PROSE. Nobody hands an
# argparse help string to httpx. A guard that cries wolf at its own documentation
# gets switched off within a week, and then it protects nothing.
#
# What we actually fear is an address the code will USE -- and that is always the
# bare thing: "http://localhost:7420". Every single one of the guns was exactly that
# shape:
#     url = "http://localhost:7420"                (seed_memory_only)
#     MEMORY_URL = "http://localhost:7420"         (live_eval)
#     memory_url: str = "http://127.0.0.1:7420"    (config)
#     SCC_NV_URL = "http://localhost:7423"         (tune_scc)
# Ports built by interpolation (f"http://127.0.0.1:{port}") are caught by the INT
# rule instead, which has no such ambiguity: a bare 7420 in code is never prose.
ADDRESS_RE = re.compile(r"^\s*(?:https?://)?[\w.\-]+:742[0-4]/?\s*$")

# Modules allowed to speak HTTP to a store at all. Everything else in the package is
# PURE: config compilation, seed/question parsing, metric maths, compose emission.
# Nothing in that layer should be able to reach a store even by accident.
HTTPX_ALLOWED = {
    "live_eval",       # the topology evaluator (writes go through write_guard)
    "live_import",     # read-only copy from a live store into a container
    "regrade",         # capture/replay CLI -- read-only, POST /search only
    "regrade_live",    # the live SCC knob sweep (writes go through write_guard)
    "docker_env",      # health-gates the containers it started
}


def _py_files() -> list[Path]:
    return [p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id() of every Constant that is a module/class/function DOCSTRING.

    Docstrings and comments are allowed to *mention* a live port -- this very
    package documents, at length, why those defaults were a loaded gun. It's the
    CODE that may not name one. So we parse instead of grepping: comments never
    survive into the AST, and docstrings we identify and skip. A guard that can't
    tell an example from an executable literal gets disabled within a week.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: str(p.relative_to(PKG)))
def test_no_live_store_ports_in_source(path: Path):
    """No module in seren_probe may name a live Seren port in executable code.

    THE test. It would have caught every one of the five guns, on the day each was
    written, without anyone needing to remember to look.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_nodes(tree)
    offences: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in skip:
            continue
        val = node.value
        if isinstance(val, bool):
            continue
        if isinstance(val, int) and val in LIVE_PORTS:
            offences.append(f"line {node.lineno}: integer {val}")
        elif isinstance(val, str) and ADDRESS_RE.match(val):
            offences.append(f"line {node.lineno}: address {val!r}")
    assert not offences, (
        f"\n{path.name} names a LIVE Seren store port in executable code:\n  "
        + "\n  ".join(offences)
        + "\n\nSerenProbe generates SYNTHETIC data. It must only ever address containers "
          "it spun up itself (see write_guard). A live port in the source is how a fake "
          "corpus ends up inside a real SerenMemory -- which has already happened once.\n"
          "If an operator truly needs to reach a live store, they can type the address."
    )


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: str(p.relative_to(PKG)))
def test_only_the_live_layer_speaks_http(path: Path):
    """httpx is confined to the modules that are SUPPOSED to touch a store.

    The pure layer -- topology compilation, seed/question parsing, metric maths,
    compose emission -- cannot reach a store even by accident, because it cannot
    make a request. If a new module starts importing httpx, this test fails and you
    have to say so out loud by adding it to HTTPX_ALLOWED.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports_httpx = any(
        (isinstance(n, ast.Import) and any(a.name.split(".")[0] == "httpx" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "httpx")
        for n in ast.walk(tree)
    )
    if not imports_httpx:
        return
    assert path.stem in HTTPX_ALLOWED, (
        f"\n{path.name} imports httpx but is not in HTTPX_ALLOWED.\n"
        f"Only the live layer may talk to a store: {sorted(HTTPX_ALLOWED)}\n"
        f"If this module genuinely needs it, add it deliberately -- and make sure every "
        f"WRITE it performs goes through write_guard.assert_write_allowed()."
    )


def test_the_guard_itself_is_present():
    """write_guard is load-bearing. If it vanishes, everything above is decoration."""
    assert (PKG / "runtime/write_guard.py").exists()


def test_the_quarantined_guns_are_gone():
    """The five live-store hazards stay in _attic/, out of the importable package."""
    for gun in ("seed_memory_only.py", "tune_scc.py", "dataset.py",
                "clean_stores.py", "runner.py", "evaluators.py"):
        assert not (PKG / gun).exists(), (
            f"{gun} is back in seren_probe/. It was quarantined for a reason -- "
            f"read _attic/README.md before you put it there again."
        )


# ── the reorg guard ───────────────────────────────────
def _runtime_module_stems() -> set[str]:
    """The module names that live in the runtime/ layer."""
    rt = PKG / "runtime"
    return {p.stem for p in rt.glob("*.py") if p.stem != "__init__"}


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: str(p.relative_to(PKG)))
def test_runtime_modules_imported_through_runtime(path: Path):
    """A module that lives in runtime/ must be imported THROUGH runtime/ from outside it.

    docker_env moved from the package root into runtime/. Most call sites followed; two
    didn't -- `from ..docker_env import ...` in routes/docker.py's /validate and
    `from .docker_env import ...` in app.py's shutdown handler. Both were FUNCTION-LOCAL
    imports (inside a handler, inside a try/except), so neither failed at module load the
    way a top-of-file import would -- they only blew up when that exact path ran, which no
    test exercised. They rotted silently through an entire reorg.

    routes/eval.py has the whole horror inline: the stale path raised ImportError, a bare
    except ATE it, the seeded flag never persisted, and a restart RESEEDED an already-full
    pod -- stacking a second copy of the corpus and quietly walking around the seed guard
    twenty lines above it.

    A top-of-file import to a vanished module dies loud on collection. A function-local one
    dies at 2am. So we don't rely on the import ever running -- we walk the AST: any import
    of a runtime module, from a file OUTSIDE runtime/, must name `runtime` in its path. A
    runtime sibling reaching for `from .docker_env import X` is correct and exempt.
    """
    if "runtime" in path.relative_to(PKG).parts:
        return  # a runtime sibling importing `from .docker_env import X` is right
    stems = _runtime_module_stems()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        parts = (node.module or "").split(".") if node.module else []
        dots = "." * node.level
        # `from ..docker_env import X` -- module path ENDS in a runtime stem but never
        # passes through runtime.
        if parts and parts[-1] in stems and "runtime" not in parts:
            offences.append(f"line {node.lineno}: from {dots}{node.module} import ...")
            continue
        # `from .. import docker_env` -- the runtime module is an imported NAME and the
        # path it's pulled from doesn't route through runtime.
        if "runtime" not in parts:
            for a in node.names:
                if a.name in stems:
                    offences.append(
                        f"line {node.lineno}: from {dots}{node.module or ''} import {a.name}")
    assert not offences, (
        f"\n{path.name} imports a runtime/ module by its OLD pre-reorg path:\n  "
        + "\n  ".join(offences)
        + "\n\nModules under runtime/ must be imported THROUGH runtime "
          "(e.g. `from ..runtime.docker_env import ...`), not the flat path they lived at "
          "before the reorg. A stale flat import inside a function body doesn't fail until "
          "that code runs -- which is how docker_env's /validate and shutdown handlers "
          "stayed broken through a whole refactor, and how a swallowed ImportError once "
          "double-seeded a live pod."
    )
