"""The post-generation gate: a dataset with an unanswerable question must FAIL
(non-zero exit), or a CI pipeline sails right past it - the port-collision lesson,
applied to datasets. A guard that doesn't block is theater."""
import yaml
from seren_probe.core.lint_cli import main


def _write(d, loci, mem, qs, hard=None):
    (d / "loci.yaml").write_text(yaml.safe_dump(loci))
    (d / "memory.yaml").write_text(yaml.safe_dump(mem))
    (d / "questions.yaml").write_text(yaml.safe_dump({"questions": qs}))
    if hard is not None:
        (d / "questions_hard.yaml").write_text(yaml.safe_dump({"questions": hard}))


def test_clean_dataset_exits_zero(tmp_path):
    _write(tmp_path,
           [{"project": "m", "key": "strain_a1_host", "value": "A. sojae", "why": "host"}],
           [],
           [{"asks": "loci", "query": "what host does strain a1 use?",
             "expect_key": ["m/strain_a1_host"]}])
    assert main([str(tmp_path)]) == 0


def test_unanswerable_question_exits_nonzero(tmp_path):
    _write(tmp_path,
           [{"project": "m", "key": "strain_a1_host", "value": "A. sojae", "why": "host"}],
           [],
           [{"asks": "memory", "query": "reviews?", "expect_content": ["strain history"]}])
    assert main([str(tmp_path)]) == 1          # 'strain history' is in no doc -> block the ship


def test_unbridged_passes_by_default_fails_under_strict(tmp_path):
    """An unbridged (no-rail) question is a WARN by default (ship-able but flagged),
    a FAIL under --strict (the generator wants it gone)."""
    _write(tmp_path,
           [{"project": "m", "key": "strain_asperk1_application", "value": "cellulase production", "why": "product"},
            {"project": "m", "key": "mat_cellulose_feed_supplier", "value": "Sigma-Aldrich", "why": "supplier"}],
           [],
           [{"asks": "corpus", "query": "supply chain for the asperk1 strain",
             "expect_content": ["Sigma-Aldrich"]}])
    assert main([str(tmp_path)]) == 0                    # warn, don't block
    assert main([str(tmp_path), "--strict"]) == 1        # strict: block


def test_missing_questions_file_errors(tmp_path):
    (tmp_path / "loci.yaml").write_text("[]")
    (tmp_path / "memory.yaml").write_text("[]")
    assert main([str(tmp_path)]) == 2
