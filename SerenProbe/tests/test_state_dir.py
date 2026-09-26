"""
Where SerenProbe keeps its own state, and that it can be moved.

Topology state, eval results and corpus captures used to be Path.home() /
".seren-probe" frozen into module constants at import, and the docker configs
dir was created under ~/.serenprobe on every import. Fine for one Probe per box;
wrong for two clusters on one host (2026-09-25, per-install roots): they would
share one topology_state.json and each adopt the other's pod.

These pin the contract: the default is unchanged, storage.state_dir moves all of
it, SEREN_PROBE_STATE_DIR beats the yaml, and docker configs saved in the old
~/.serenprobe location are not stranded. Everything runs against a fake HOME in
tmp_path - nothing here touches the real ~/.seren-probe.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from seren_probe.config import load_config
from seren_probe.runtime import docker_env
from seren_probe.runtime.docker_env import (
    captures_file, configure_state_dir, docker_config_dir, results_file,
    state_dir, state_file,
)


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """A throwaway home, no state-dir env, no configured state dir - and the
    configured value reset afterwards so no later test inherits it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))          # POSIX expanduser
    monkeypatch.setenv("USERPROFILE", str(home))   # Windows expanduser
    for name in ("SEREN_PROBE_STATE_DIR", "SERENPROBE_DOCKER_CONFIG_DIR", "SEREN_PROBE_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    configure_state_dir("")
    yield home
    configure_state_dir("")


def _yaml(tmp_path: Path, state: Path | str) -> str:
    p = tmp_path / "seren-probe.yaml"
    p.write_text(f"storage:\n  state_dir: {state}\n", encoding="utf-8")
    return str(p)


# ── the default is exactly what it always was ────────────────────────────────

def test_a_bare_install_keeps_state_in_home_dot_seren_probe(fake_home, tmp_path):
    cfg = load_config(str(tmp_path / "no-such-file.yaml"))
    assert cfg.storage.state_dir == ""          # unset, not "the cwd"
    root = fake_home / ".seren-probe"
    assert state_dir() == root
    assert state_file() == root / "topology_state.json"
    assert results_file() == root / "eval_results.json"
    assert captures_file() == root / "corpus_captures.json"
    # No legacy dir on this box -> docker configs join the rest of the state.
    assert docker_config_dir() == root / "docker_configs"


def test_the_default_round_trips_through_the_real_writers(fake_home):
    docker_env.save_topology_state({"project_name": "p", "work_dir": "/w"})
    assert (fake_home / ".seren-probe" / "topology_state.json").is_file()
    assert docker_env.load_topology_state()["project_name"] == "p"


# ── storage.state_dir moves all of it ────────────────────────────────────────

def test_the_config_key_moves_every_state_file_and_the_docker_configs(fake_home, tmp_path):
    target = tmp_path / "seren" / "alpha" / "stores" / "probe"
    cfg = load_config(_yaml(tmp_path, target))
    assert cfg.storage.state_dir == str(target)
    configure_state_dir(cfg.storage.state_dir)

    assert state_dir() == target
    docker_env.save_topology_state({"project_name": "p", "work_dir": "/w"})
    docker_env.save_eval_results("p", {"scc": {}})
    docker_env.save_corpus_captures("p", {"Characters": {}}, "qh")
    assert (target / "topology_state.json").is_file()
    assert (target / "eval_results.json").is_file()
    assert (target / "corpus_captures.json").is_file()
    assert docker_config_dir() == target / "docker_configs"
    # And nothing leaked to the old home location.
    assert not (fake_home / ".seren-probe").exists()


def test_create_app_applies_the_configured_state_dir(tmp_path):
    """The wiring, not just the helper: a config handed to create_app is what
    decides where the adopt check looks."""
    from seren_probe.app import create_app
    target = tmp_path / "beta-state"
    create_app(load_config(_yaml(tmp_path, target)))
    assert state_dir() == target


def test_tilde_is_expanded_and_absolute_paths_are_used_as_is(fake_home, tmp_path):
    configure_state_dir("~/seren/gamma/stores/probe")
    assert state_dir() == fake_home / "seren" / "gamma" / "stores" / "probe"
    configure_state_dir(str(tmp_path / "abs"))
    assert state_dir() == tmp_path / "abs"


def test_paths_follow_the_config_after_import_rather_than_freezing(tmp_path):
    """The old constants were computed at import. Reconfiguring later has to move
    the files, or config applied at startup would be silently ignored."""
    configure_state_dir(str(tmp_path / "one"))
    first = state_file()
    configure_state_dir(str(tmp_path / "two"))
    assert first != state_file() == tmp_path / "two" / "topology_state.json"


# ── env beats config ─────────────────────────────────────────────────────────

def test_env_var_beats_the_config_key(tmp_path, monkeypatch):
    from_yaml = tmp_path / "from-yaml"
    from_env = tmp_path / "from-env"
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(from_env))
    cfg = load_config(_yaml(tmp_path, from_yaml))
    assert cfg.storage.state_dir == str(from_env)   # cfg tells the truth
    # Even if something configures the yaml value directly, env still wins.
    configure_state_dir(str(from_yaml))
    assert state_dir() == from_env
    assert docker_config_dir() == from_env / "docker_configs"


# ── docker configs: old override and old location both still honoured ───────

def test_legacy_docker_configs_dir_is_kept_when_no_state_dir_is_configured(fake_home):
    legacy = fake_home / ".serenprobe" / "docker_configs"
    (legacy / "mine").mkdir(parents=True)
    (legacy / "mine" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    assert docker_config_dir() == legacy
    assert [c.name for c in docker_env._discover_configs()] == ["mine"]
    assert docker_env.get_config_path("mine") == legacy / "mine" / "Dockerfile"
    # The other state files are NOT dragged to the legacy spelling.
    assert state_dir() == fake_home / ".seren-probe"


def test_a_configured_state_dir_beats_the_legacy_docker_configs_dir(fake_home, tmp_path):
    """Per-install isolation is the point of configuring one; reaching back into
    the shared home-dir location would undo it."""
    (fake_home / ".serenprobe" / "docker_configs").mkdir(parents=True)
    configure_state_dir(str(tmp_path / "delta"))
    assert docker_config_dir() == tmp_path / "delta" / "docker_configs"


def test_the_old_docker_config_env_override_still_wins_outright(fake_home, tmp_path, monkeypatch):
    explicit = tmp_path / "explicit-configs"
    monkeypatch.setenv("SERENPROBE_DOCKER_CONFIG_DIR", str(explicit))
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(tmp_path / "epsilon"))
    (fake_home / ".serenprobe" / "docker_configs").mkdir(parents=True)
    assert docker_config_dir() == explicit
    assert state_dir() == tmp_path / "epsilon"      # only docker configs are pinned


def test_importing_the_module_does_not_create_directories(fake_home):
    """The old import-time mkdir planted ~/.serenprobe on every box that merely
    imported the package. Resolving a path must not create it either. A fresh
    interpreter, so the import really happens under the fake home."""
    import os
    import subprocess
    import sys
    env = {**os.environ, "HOME": str(fake_home), "USERPROFILE": str(fake_home)}
    r = subprocess.run(
        [sys.executable, "-c",
         "from seren_probe.runtime import docker_env as d; "
         "d.docker_config_dir(); d.state_dir(); print('ok')"],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr
    assert not (fake_home / ".serenprobe").exists()
    assert not (fake_home / ".seren-probe").exists()
