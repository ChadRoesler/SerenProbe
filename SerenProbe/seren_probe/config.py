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

# Shared server/tls config blocks from Meninges - ONE definition for the family.
from seren_meninges import ServerConfig, TlsConfig

log = logging.getLogger(__name__)


@dataclass
class StoreUrlsConfig:
    """URLs for live stores the operator may point the viewer at.

    THE DEFAULTS ARE DELIBERATELY EMPTY. They used to be:

        memory_url  = "http://127.0.0.1:7420"   <- the operator's REAL SerenMemory
        loci_v_url  = "http://127.0.0.1:7421"   <- the operator's REAL SerenLoci
        loci_nv_url = "http://127.0.0.1:7422"
        scc_nv_url  = "http://127.0.0.1:7423"
        scc_v_url   = "http://127.0.0.1:7424"

    A tool whose job is to MANUFACTURE SYNTHETIC DATA shipped with the addresses of
    the operator's live brain preloaded into its config. Those defaults fed
    app.state.store_config, which fed the legacy /eval/run fallback, which SEEDED
    those stores if it found them empty. Exactly one thing stood between that and a
    contaminated memory store: the store happening to be non-empty.

    An address you have to TYPE is a decision. An address that arrives as a default
    is an accident waiting for a tired Tuesday.

    Nothing in the topology path reads these anymore (eval and regrade only ever
    address containers SerenProbe spun up itself, and write_guard refuses anything
    else at the transport). They survive only as an operator-settable field on
    /eval/config. tests/test_layering.py enforces that no module in this package
    contains a live-store port literal -- do not put them back.
    """
    memory_url: str = ""
    loci_nv_url: str = ""
    loci_v_url: str = ""
    scc_nv_url: str = ""
    scc_v_url: str = ""
    capture_path: str = ""

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "StoreUrlsConfig":
        d = d or {}
        return cls(
            memory_url=str(d.get("memory_url", "")),
            loci_nv_url=str(d.get("loci_nv_url", "")),
            loci_v_url=str(d.get("loci_v_url", "")),
            scc_nv_url=str(d.get("scc_nv_url", "")),
            scc_v_url=str(d.get("scc_v_url", "")),
            capture_path=str(d.get("capture_path", "")),
        )


@dataclass
class UpdatesConfig:
    """\"Is there a newer seren-probe\" checking. Cosmetic, opt-outable.

    Needs seren-meninges[updates]. Without it the check reports
    status="unavailable" rather than silently reading as "you're current" -
    see seren_meninges/updates.py for why that distinction is load-bearing.
    """
    enabled: bool = True
    check_interval_hours: float = 6.0
    index_url: str = "https://pypi.org/pypi/{distribution}/json"
    allow_prerelease: bool = False

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "UpdatesConfig":
        d = d or {}
        default = cls()
        raw = d.get("check_interval_hours")
        try:
            hours = float(raw) if raw is not None else default.check_interval_hours
        except (TypeError, ValueError):
            hours = default.check_interval_hours
        return cls(
            enabled=bool(d.get("enabled", True)),
            check_interval_hours=hours if hours > 0 else default.check_interval_hours,
            index_url=str(d.get("index_url", "") or default.index_url),
            allow_prerelease=bool(d.get("allow_prerelease", False)),
        )


@dataclass
class SerenProbeConfig:
    """The whole service: server + tls + store URLs."""
    server: ServerConfig = field(default_factory=lambda: ServerConfig(port=7430))
    tls: TlsConfig = field(default_factory=TlsConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
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
    """Defaults -> yaml -> env (later wins). A missing file is fine - defaults
    + env is a valid zero-config run. YAML is imported lazily so the core
    import path stays light."""
    data: dict[str, Any] = {}
    candidate = path or os.environ.get("SEREN_PROBE_CONFIG") or "seren-probe.yaml"
    cfg_path = Path(os.path.expanduser(candidate))
    if cfg_path.is_file():
        try:
            # encoding= IS NOT OPTIONAL. Without it Python uses the LOCALE
            # codec - cp1252 on Windows - and the yaml sample opens with a
            # `# ═══` banner (U+2550 -> E2 95 90), so byte 0x90 raises
            # UnicodeDecodeError at position 4. The except below then swallows
            # it and hands back {}, meaning a Windows operator's ENTIRE config
            # is silently ignored while they stare at the values they just
            # set. Leniency is right for a MALFORMED file; it must not be what
            # hides a file we simply failed to read.
            with open(cfg_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:  # noqa: BLE001 - unreadable degrades to defaults
            data = {}

    server = ServerConfig.from_dict(data.get("server"), default_port=7430)
    tls = TlsConfig.from_dict(data.get("tls"))
    updates = UpdatesConfig.from_dict(data.get("updates"))
    stores = StoreUrlsConfig.from_dict(data.get("stores"))

    cfg = SerenProbeConfig(server=server, tls=tls, stores=stores, updates=updates)
    return _apply_env_overrides(cfg)
