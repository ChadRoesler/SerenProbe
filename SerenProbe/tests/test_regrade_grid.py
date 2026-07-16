"""Tests for regrade grid parameterization (seren_probe.regrade.build_grid).

We IMPORT regrade. We do not importorskip it.

The old line here was `regrade = pytest.importorskip("seren_probe.regrade")`, and
that is a lie generator. importorskip is for OPTIONAL THIRD-PARTY deps -- `mcp`,
`torch`, things that may legitimately be absent. Pointed at your OWN package, it
converts "this module is broken" into "eh, skip it": when regrade's module-level
`from .dataset import ...` stopped resolving, these nine tests silently stopped
running and pytest printed a GREEN summary with a single quiet `1 skipped`. The
test count dropped from 176 to 167 and nothing shouted.

A first-party module that won't import is a BUG, not an optional feature. If
seren_probe.regrade can't be imported, this file should go RED and say so.

(regrade lazily degrades on SCC's absence all by itself -- build_grid never calls
_require_scc -- so a box without seren_corpus_callosum installed still imports it
fine. That's the module's job, not the test's.)
"""
import itertools

from seren_probe.runtime import regrade
from seren_probe.runtime import regrade_live as rl

build_grid = regrade.build_grid
DEFAULT_GRID = regrade.DEFAULT_GRID
_GRID_ORDER = regrade._GRID_ORDER


def _ncombos(g):
    return len(list(itertools.product(*(g[k] for k in _GRID_ORDER))))


def test_none_is_full_default_grid():
    assert build_grid(None) == DEFAULT_GRID


def test_empty_overrides_is_full_grid():
    assert _ncombos(build_grid({})) == _ncombos(DEFAULT_GRID)


def test_override_narrows_named_knobs_only():
    g = build_grid({"loci_floor": [0.3], "authority_margin": [0.1]})
    assert g["loci_floor"] == [0.3]
    assert g["authority_margin"] == [0.1]
    assert g["rrf_k"] == DEFAULT_GRID["rrf_k"]           # untouched knob keeps default


def test_override_shrinks_combo_count():
    full = _ncombos(DEFAULT_GRID)
    narrowed = _ncombos(build_grid({"loci_floor": [0.3], "authority_margin": [0.1]}))
    assert narrowed < full


def test_empty_list_override_falls_back_to_default():
    assert build_grid({"rrf_k": []})["rrf_k"] == DEFAULT_GRID["rrf_k"]


def test_unknown_knob_dropped():
    assert "bogus" not in build_grid({"bogus": [1]})


def test_build_grid_does_not_mutate_default():
    before = list(DEFAULT_GRID["rrf_k"])
    build_grid({"rrf_k": [60]})
    assert DEFAULT_GRID["rrf_k"] == before


def test_grid_order_matches_default_keys():
    assert set(_GRID_ORDER) == set(DEFAULT_GRID.keys())


def test_live_compact_combos_product_of_named_only():
    """regrade_live's compact grid: product over ONLY the named knobs.

    Also imported, NOT importorskip'd -- regrade_live is the module wired to the
    Regrades button. If it can't import, that is a five-alarm fire, not a skip.
    """
    assert rl.compact_combos({}) == [{}]
    assert rl.compact_combos({"loci_floor": [0.0, 0.1, 0.3]}) == [
        {"loci_floor": 0.0}, {"loci_floor": 0.1}, {"loci_floor": 0.3}]
    assert len(rl.compact_combos({"loci_floor": [0.1, 0.3], "loci_weight": [0.5, 1.0]})) == 4
