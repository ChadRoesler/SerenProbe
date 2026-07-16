"""
seren_probe.core.linters
=========================
The reachability lint, broken out of the old 700-line question_lint.py monolith.

Deliberately a namespace with NO re-exports: callers import from the specific
submodule (`from ...linters.plan import lint_plan`), not from the package. A
re-export here would be a shim, and the whole point of the split was to stop
hiding where things live.

    text     tokenization primitives (_words, _STOP, _QIDX)
    model    Doc, LintReport, build_docs/build_corpus, max_reachable_hops
    checks   the per-question checks (bait, existence, reachability,
             discriminability, content+rail)
    adapters dataclass -> dict normalizers
    quiet    the quiet-target leak precondition (runs against the plan)
    plan     lint_questions + lint_plan -- the two public entry points
"""
