"""
seren_probe.config
════════════════════════════════════════════════════════════════════════

Typed configuration for the SerenProbe evaluation viewer. Same pattern as
the family: dataclass-based (like SCC), adopting the shared ServerConfig
and TlsConfig from SerenMeninges so the whole family shares one definition.

Resolution order (later wins):
    1. Defaults (this file)
    2. seren-probe.yaml (path from --config or ./seren-probe.yaml)
    3. Environment variables (SEREN_PROBE_*)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# Shared server/tls config blocks from Meninges — ONE definition for the family.
from seren_meninges import ServerConfig, TlsConfig

log = logging.getLogger(__name__)


@dataclass
class StoreUrlsConfig:
    """URLs for the live stores the eval suite talks to."""
    memory_url: str = "http://127.0.0.1:7420"
    loci_nv_url: str = "http://127.0.0.1:7422"
    loci_v_url: str = "http://127.0.0.1:7421"
    scc_nv_url: str = "http://127.0.0.1:7423"
    scc_v_url: str = "http://127.0.0.1:7424"
    capture_path: str = "/tmp/scc_capture2.json"

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "StoreUrlsConfig":
        d = d or {}
        return cls(
            memory_url=str(d.get("memory_url", "http://127.0.0.1:7420")),
            loci_nv_url=str(d.get("loci_nv_url", "http://127.0.0.1:7422")),
            loci_v_url=str(d.get("loci_v_url", "http://127.0.0.1:7421")),
            scc_nv_url=str(d.get("scc_nv_url", "http://127.0.0.1:7423")),
            scc_v_url=str(d.get("scc_v_url", "http://127.0.0.1:7424")),
            capture_path=str(d.get("capture_path", "/tmp/scc_capture2.json")),
        )


@dataclass
class SerenProbeConfig:
    """The whole service: server + tls + store URLs."""
    server: ServerConfig = field(default_factory=lambda: ServerConfig(port=7430))
    tls: TlsConfig = field(default_factory=TlsConfig)
    stores: StoreUrlsConfig = field(default_factory=StoreUrlsConfig)


def _apply_env_overrides(cfg: SerenProbeConfig) -> SerenProbeConfig:
    """SEREN_PROBE_* env wins last, same precedence as the family's SEREN_<X>_*."""
    env = os.environ
    if v := env.get("SEREN_PROBE_HOST"):
        cfg.server.host = v
    if v := env.get("SEREN_PROBE_PORT"):
        cfg.server.port = int(v)
    if v := env.get("SEREN_PROBE_BEARER_TOKEN"):
        cfg.server.bearer_token = v
    if v := env.get("SEREN_PROBE_BEARER_TOKEN_ENV"):
        cfg.server.bearer_token_env = v
    if v := env.get("SEREN_PROBE_BEARER_TOKEN_KEYRING"):
        cfg.server.bearer_token_keyring = v
    if v := env.get("SEREN_PROBE_TRUST_SYSTEM_STORE"):
        cfg.tls.trust_system_store = v.lower() in ("1", "true", "yes", "on")
    if v := env.get("SEREN_PROBE_MEMORY_URL"):
        cfg.stores.memory_url = v
    if v := env.get("SEREN_PROBE_LOCI_NV_URL"):
        cfg.stores.loci_nv_url = v
    if v := env.get("SEREN_PROBE_LOCI_V_URL"):
        cfg.stores.loci_v_url = v
    if v := env.get("SEREN_PROBE_SCC_NV_URL"):
        cfg.stores.scc_nv_url = v
    if v := env.get("SEREN_PROBE_SCC_V_URL"):
        cfg.stores.scc_v_url = v
    if v := env.get("SEREN_PROBE_CAPTURE_PATH"):
        cfg.stores.capture_path = v
    return cfg


def load_config(path: Optional[str] = None) -> SerenProbeConfig:
    """Defaults -> yaml -> env (later wins). A missing file is fine — defaults
    + env is a valid zero-config run. YAML is imported lazily so the core
    import path stays light."""
    data: dict[str, Any] = {}
    candidate = path or os.environ.get("SEREN_PROBE_CONFIG") or "seren-probe.yaml"
    cfg_path = Path(os.path.expanduser(candidate))
    if cfg_path.is_file():
        try:
            with open(cfg_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception:  # noqa: BLE001 — unreadable degrades to defaults
            data = {}

    server = ServerConfig.from_dict(data.get("server"), default_port=7430)
    tls = TlsConfig.from_dict(data.get("tls"))
    stores = StoreUrlsConfig.from_dict(data.get("stores"))

    cfg = SerenProbeConfig(server=server, tls=tls, stores=stores)
    return _apply_env_overrides(cfg)
