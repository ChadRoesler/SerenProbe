"""
seren_probe.runtime.bundles - the inquisition bundle (.iqz): a dataset arrives whole.

Until 21 Oct 2026 a ProbeConfig reached Probe as text, and its Seed and
Questions paths were opened by the Probe PROCESS, relative to wherever it
happened to be running. The viewer on one box sent a config whose paths
meant files on a desktop; the Probe on another box answered 400 and could
not say why. The config travelled; the data did not.

An .iqz bundle is the data and the configs together: Sinew's verified
snapshot tar (one top folder, a manifest.json naming every file with its
sha256) with a manifest of kind "probe" that lists one or more ProbeConfigs
by name, each with a note. That is the grouping: a dataset's easy and hard
configs side by side, or a before and an after over the same questions. A
bundled config refers to its seed and question files by path relative to
the bundle's root, and a path never means anything outside the bundle.

VALIDATE BEFORE COPY. The manifest is read first, straight out of the
archive; each config is compiled; every path-like ref is checked against
the archive's own member list; only then are bytes written, and Sinew's
unpack verifies every sha256 before the folder is kept. A bad bundle costs
one read and leaves nothing behind.

KEPT BY HASH. A bundle unpacks to datasets/<name>-<hash8>/ under the state
dir. The hash is over the content (every file's path and sha256, and the
config names), not the archive bytes, so the same dataset packed twice is
one folder. It is not thrown away when a pod stops: the runs in the ledger
name it, and delete refuses while they do unless forced.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from ..core.seed_dataset import clean_relative
from .docker_env import state_dir

logger = logging.getLogger(__name__)

KIND = "probe"
EXTENSION = ".iqz"
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
_MAX_NAME = 64


class BundleError(ValueError):
    """A bundle that cannot be trusted. Carries every problem at once, so the
    sender fixes them in one pass instead of one upload per problem."""
    def __init__(self, problems):
        self.problems = [str(p) for p in problems]
        super().__init__("bundle refused: " + "; ".join(self.problems[:6]))


def datasets_dir() -> Path:
    return state_dir() / "datasets"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_version() -> str:
    try:
        from .. import __version__
        return str(__version__)
    except Exception:                                      # noqa: BLE001
        return "0.0.0+unknown"


def good_name(name: Any) -> bool:
    """A bundle or config name: letters, digits, dot, dash, underscore; starts
    with a letter or digit; at most 64. It becomes a folder name and half of a
    run's bundle id, so nothing a path could misread."""
    s = str(name or "")
    return (0 < len(s) <= _MAX_NAME and s[0].isalnum()
            and all(c.isalnum() or c in "._-" for c in s) and not s.endswith(".partial"))


def good_id(bundle_id: Any) -> bool:
    s = str(bundle_id or "")
    name, dash, h = s.rpartition("-")
    return bool(dash) and good_name(name) and len(h) == 8 and all(c in "0123456789abcdef" for c in h)


def content_hash(manifest: dict) -> str:
    """What makes two bundles the same: every file's path and sha256, and the
    config names and paths. Not the archive bytes (compression and timestamps
    differ between packs of one dataset) and not the notes."""
    h = hashlib.sha256()
    for f in sorted(manifest.get("files") or [], key=lambda f: str(f.get("path"))):
        if str(f.get("path") or "").startswith("runs/"):
            continue        # baseline runs travel with a bundle; they are not the dataset
        h.update(f"{f.get('path')}\0{f.get('sha256')}\n".encode("utf-8"))
    for c in manifest.get("configs") or []:
        h.update(f"config\0{c.get('name')}\0{c.get('path')}\n".encode("utf-8"))
    return h.hexdigest()


def bundle_id_of(manifest: dict) -> str:
    return f"{manifest.get('name')}-{content_hash(manifest)[:8]}"


def _refs_of(topo) -> list[str]:
    """Every Seed / Questions ref a compiled topology carries, flattened."""
    out: list[str] = []

    def add(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str))
    add(getattr(topo, "questions_ref", None))
    add(getattr(topo, "default_loci_seed", None))
    add(getattr(topo, "default_memory_seed", None))
    for n in list(getattr(topo, "loci", [])) + list(getattr(topo, "memory", [])) + list(getattr(topo, "corpus", [])):
        add(getattr(n, "seed_ref", None))
        add(getattr(n, "questions_ref", None))
    return out


def path_refs(topo) -> list[str]:
    """The refs that name FILES (inline YAML refs are content and travel in the
    config itself)."""
    from ..core.seed_dataset import _looks_like_a_path
    seen: list[str] = []
    for r in _refs_of(topo):
        if _looks_like_a_path(r) and r not in seen:
            seen.append(r)
    return seen


def check_manifest(man: Any) -> list[str]:
    """What a probe manifest must say before anything else is believed."""
    problems: list[str] = []
    if not isinstance(man, dict):
        return ["manifest.json is not a mapping"]
    if man.get("kind") != KIND:
        problems.append(f"manifest kind is {man.get('kind')!r}, not {KIND!r}: this is not an inquisition bundle")
    if not good_name(man.get("name")):
        problems.append(f"bundle name {man.get('name')!r} is not a name (letters, digits, '.', '-', '_'; up to {_MAX_NAME})")
    configs = man.get("configs")
    if not isinstance(configs, list) or not configs:
        problems.append("the manifest lists no configs")
        configs = []
    names: set[str] = set()
    for i, c in enumerate(configs):
        if not isinstance(c, dict):
            problems.append(f"configs[{i}] is not a mapping")
            continue
        if not good_name(c.get("name")):
            problems.append(f"configs[{i}] name {c.get('name')!r} is not a name")
        elif c["name"] in names:
            problems.append(f"two configs are named {c['name']!r}")
        else:
            names.add(c["name"])
        if not c.get("path") or clean_relative(c.get("path", "")) != c.get("path"):
            problems.append(f"config {c.get('name')!r} path {c.get('path')!r} is not a clean archive-relative path")
    if not isinstance(man.get("files"), list):
        problems.append("the manifest has no files list")
    runs = man.get("runs")
    if runs is not None:
        if not isinstance(runs, list):
            problems.append("the manifest's runs is not a list")
        else:
            for i, r in enumerate(runs):
                if (not isinstance(r, dict) or not r.get("id") or not str(r.get("path") or "").startswith("runs/")
                        or clean_relative(r.get("path")) != r.get("path")):
                    problems.append(f"runs[{i}] is not a baseline run entry (an id and a runs/ path)")
    return problems


def _open(data: Union[bytes, Path]) -> tarfile.TarFile:
    if isinstance(data, (bytes, bytearray)):
        return tarfile.open(fileobj=io.BytesIO(bytes(data)), mode="r:*")
    return tarfile.open(str(data), mode="r:*")


def inspect_archive(data: Union[bytes, Path]) -> dict:
    """Read the manifest and the configs straight out of the archive and say
    whether the bundle can be trusted. NOTHING IS WRITTEN. `ok` is the verdict;
    `problems` is why not, all of them."""
    from ..core.topology import load_probe_config, TopologyError
    report: dict[str, Any] = {"ok": False, "problems": [], "configs": [], "files": 0, "bytes": 0}
    problems: list[str] = report["problems"]
    try:
        tar = _open(data)
    except (tarfile.TarError, OSError, EOFError) as exc:
        problems.append(f"not a readable tar archive: {exc}")
        return report
    with tar:
        try:
            members = {m.name: m for m in tar.getmembers() if m.isfile()}
            names = tar.getnames()
        except (tarfile.TarError, EOFError, ValueError) as exc:
            problems.append(f"the archive could not be read: {exc}")
            return report
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) != 1:
            problems.append(f"a bundle holds one top folder, this archive holds {sorted(tops)}")
            return report
        top = tops.pop()
        mpath = f"{top}/manifest.json"
        if mpath not in members:
            problems.append("the archive has no manifest.json at its top")
            return report
        try:
            man = json.loads(tar.extractfile(members[mpath]).read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError, tarfile.TarError) as exc:
            problems.append(f"manifest.json could not be read: {exc}")
            return report
        problems.extend(check_manifest(man))
        if problems:
            return report
        inside = {n.split("/", 1)[1] for n in members if "/" in n}
        for f in man["files"]:
            rel = f.get("path") if isinstance(f, dict) else None
            if not rel or rel not in inside:
                problems.append(f"the manifest names {rel!r}, which the archive does not hold")
        report["runs"] = []
        for r in man.get("runs") or []:
            if r.get("path") not in inside:
                problems.append(f"the manifest lists baseline run {r.get('id')!r} at {r.get('path')!r}, "
                                f"which the archive does not hold")
            report["runs"].append({k: r.get(k) for k in ("id", "kind", "config_name", "finished_at", "origin", "path")})
        for c in man["configs"]:
            entry = {"name": c["name"], "path": c["path"], "note": str(c.get("note") or ""),
                     "loci": [], "memory": [], "corpus": [], "warnings": [], "errors": []}
            report["configs"].append(entry)
            if c["path"] not in inside:
                problems.append(f"config {c['name']!r} is listed at {c['path']!r}, which the archive does not hold")
                continue
            try:
                text = tar.extractfile(members[f"{top}/{c['path']}"]).read().decode("utf-8")
                topo = load_probe_config(text)
            except TopologyError as e:
                entry["errors"] = list(e.errors)
                entry["warnings"] = list(e.warnings)
                problems.append(f"config {c['name']!r} does not compile: {e.errors[0] if e.errors else 'no detail'}")
                continue
            except (UnicodeDecodeError, tarfile.TarError) as exc:
                problems.append(f"config {c['name']!r} could not be read: {exc}")
                continue
            entry.update({"loci": [n.name for n in topo.loci], "memory": [n.name for n in topo.memory],
                          "corpus": [x.name for x in topo.corpus], "warnings": list(topo.warnings)})
            for ref in path_refs(topo):
                rel = clean_relative(ref)
                if rel is None:
                    problems.append(f"config {c['name']!r} refers to {ref!r}, which reaches outside the bundle")
                elif rel not in inside:
                    problems.append(f"config {c['name']!r} refers to {ref!r}, which is not in the bundle")
    report.update({
        "name": man["name"], "note": str(man.get("note") or ""), "hash": content_hash(man),
        "id": bundle_id_of(man), "files": len(man["files"]),
        "bytes": int(man.get("bytes") or 0), "created": man.get("created"),
        "probe_version": man.get("probe_version"),
    })
    report["installed"] = (datasets_dir() / report["id"]).is_dir()
    report["ok"] = not problems
    return report


def install_archive(data: Union[bytes, Path]) -> dict:
    """Validate (inspect_archive) and, only then, unpack under datasets/<id>/.
    Sinew's unpack writes to a .partial and verifies every sha256 before the
    folder exists; a tampered archive leaves nothing. Raises BundleError with
    every problem. The same content already installed is reported, not redone."""
    from seren_sinew.stores import unpack_snapshot
    rep = inspect_archive(data)
    if not rep["ok"]:
        raise BundleError(rep["problems"])
    final = datasets_dir() / rep["id"]
    if final.is_dir():
        # the same content again, maybe carrying baseline runs it did not last time
        imported = _import_runs(_runs_in_archive(data), rep["id"])
        rec = _record(final)
        return {**(rec or {"id": rep["id"]}), "installed": False, "already": True, "runs_imported": imported}
    incoming = datasets_dir() / ".incoming" / uuid.uuid4().hex
    incoming.mkdir(parents=True, exist_ok=True)
    try:
        folder = unpack_snapshot(data, incoming)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():                                   # a second upload of the same content raced us
            shutil.rmtree(folder, ignore_errors=True)
        else:
            os.replace(folder, final)
    except (ValueError, tarfile.TarError, OSError, EOFError) as exc:
        raise BundleError([f"the archive did not verify: {exc}"]) from exc
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    imported = _import_runs(_runs_in_archive(data), rep["id"])
    logger.info("bundle %s installed: %s file(s), %s bytes, %s baseline run(s)",
                rep["id"], rep["files"], rep["bytes"], imported)
    rec = _record(final)
    return {**(rec or {"id": rep["id"]}), "installed": True, "already": False, "runs_imported": imported}


def run_count(bundle_id: str) -> int:
    from . import ledger
    return len(ledger.list_runs(bundle=bundle_id, limit=1_000_000))


def _record(folder: Path, *, with_runs: bool = True) -> Optional[dict]:
    try:
        man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(man, dict) or man.get("kind") != KIND:
        return None
    rec: dict[str, Any] = {
        "id": folder.name, "name": man.get("name"), "hash": content_hash(man),
        "note": str(man.get("note") or ""), "created": man.get("created"),
        "installed_at": _iso(folder.stat().st_mtime), "probe_version": man.get("probe_version"),
        "configs": [{"name": c.get("name"), "path": c.get("path"), "note": str(c.get("note") or "")}
                    for c in (man.get("configs") or []) if isinstance(c, dict)],
        "files": len(man.get("files") or []), "bytes": int(man.get("bytes") or 0), "path": str(folder),
        "baseline_runs": len(man.get("runs") or []),       # the runs that came with it
        "exported_by": man.get("exported_by"),
    }
    if with_runs:
        rec["runs"] = run_count(folder.name)
    return rec


def bundle_dir(bundle_id: str) -> Optional[Path]:
    if not good_id(bundle_id):
        return None
    d = datasets_dir() / str(bundle_id)
    return d if (d / "manifest.json").is_file() else None


def list_bundles() -> list[dict]:
    """Every installed bundle, newest first, with how many runs name each."""
    root = datasets_dir()
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        if d.is_dir() and not d.name.startswith(".") and good_id(d.name):
            rec = _record(d)
            if rec:
                out.append(rec)
    out.sort(key=lambda r: r["installed_at"], reverse=True)
    return out


def get_bundle(bundle_id: str, *, details: bool = False) -> Optional[dict]:
    """One bundle. With details, each config is compiled (from the folder) for
    its store names, warnings and errors."""
    d = bundle_dir(bundle_id)
    if d is None:
        return None
    rec = _record(d)
    if rec is None:
        return None
    if details:
        from ..core.topology import load_probe_config, TopologyError
        for c in rec["configs"]:
            c.update({"loci": [], "memory": [], "corpus": [], "warnings": [], "errors": []})
            try:
                topo = load_probe_config((d / c["path"]).read_text(encoding="utf-8"), base_dir=d)
                c.update({"loci": [n.name for n in topo.loci], "memory": [n.name for n in topo.memory],
                          "corpus": [x.name for x in topo.corpus], "warnings": list(topo.warnings)})
            except TopologyError as e:
                c["errors"], c["warnings"] = list(e.errors), list(e.warnings)
            except OSError as exc:
                c["errors"] = [f"could not read {c['path']}: {exc}"]
    return rec


def config_text(bundle_id: str, name: str) -> Optional[tuple[str, dict]]:
    """A bundled config's YAML and its manifest entry, or None."""
    d = bundle_dir(bundle_id)
    rec = _record(d, with_runs=False) if d else None
    if not rec:
        return None
    for c in rec["configs"]:
        if c["name"] == name:
            try:
                return (d / c["path"]).read_text(encoding="utf-8"), c
            except OSError:
                return None
    return None


def delete_bundle(bundle_id: str, *, force: bool = False) -> dict:
    """Remove a bundle's folder. Refuses while runs in the ledger name it,
    unless forced: the runs keep their numbers and lose their re-run."""
    d = bundle_dir(bundle_id)
    if d is None:
        raise KeyError(bundle_id)
    runs = run_count(bundle_id)
    if runs and not force:
        raise BundleError([f"{runs} run(s) in the ledger name bundle {bundle_id!r}; delete with force to drop it "
                           f"anyway (the runs keep their numbers and lose their re-run)"])
    shutil.rmtree(d)
    return {"deleted": bundle_id, "runs": runs}


def discover_configs(folder: Path) -> list[dict]:
    """Every ProbeConfig*.yml at the top of a folder, named by its suffix:
    ProbeConfig.yml is 'default', ProbeConfig_hard.yml is 'hard',
    ProbeConfig.before.yml is 'before'."""
    out = []
    for f in sorted(Path(folder).glob("ProbeConfig*.y*ml")):
        if not f.is_file():
            continue
        suffix = f.stem[len("ProbeConfig"):].lstrip("_-.")
        out.append({"name": suffix or "default", "path": f.name, "note": ""})
    return out


def pack_dataset(src: Path, out: Path, *, name: str, configs: list[dict], note: str = "",
                 include_all: bool = False) -> dict:
    """Build an .iqz from a folder. `configs`: [{name, path (relative to src),
    note}]. Packs the configs and every file they reference (their refs are
    relative to src, as they will be to the bundle's root), or everything under
    src with include_all. Refuses, with every problem, a config that does not
    compile or a ref that is missing or reaches outside the folder."""
    from seren_sinew.stores import pack_snapshot_to
    from ..core.topology import load_probe_config, TopologyError
    src = Path(src)
    problems: list[str] = []
    if not src.is_dir():
        raise BundleError([f"{src} is not a folder"])
    if not good_name(name):
        problems.append(f"bundle name {name!r} is not a name (letters, digits, '.', '-', '_'; up to {_MAX_NAME})")
    if not configs:
        problems.append("no configs to carry (no --probe-config, and no ProbeConfig*.yml at the top of the folder)")
    wanted: dict[str, Path] = {}
    entries: list[dict] = []
    seen: set[str] = set()
    for c in configs:
        cname, cnote = str(c.get("name") or ""), str(c.get("note") or "")
        rel = clean_relative(str(c.get("path") or ""))
        if not good_name(cname):
            problems.append(f"config name {cname!r} is not a name")
            continue
        if cname in seen:
            problems.append(f"two configs are named {cname!r}")
            continue
        seen.add(cname)
        if rel is None or not (src / rel).is_file():
            problems.append(f"config {cname!r}: {c.get('path')!r} is not a file under {src}")
            continue
        try:
            topo = load_probe_config((src / rel).read_text(encoding="utf-8"))
        except TopologyError as e:
            problems.append(f"config {cname!r} does not compile: {e.errors[0] if e.errors else 'no detail'}")
            continue
        wanted[rel] = src / rel
        for ref in path_refs(topo):
            r = clean_relative(ref)
            if r is None:
                problems.append(f"config {cname!r} refers to {ref!r}, which reaches outside the folder")
            elif not (src / r).is_file():
                problems.append(f"config {cname!r} refers to {ref!r}, which is not under {src}")
            else:
                wanted[r] = src / r
        entries.append({"name": cname, "path": rel, "note": cnote})
    if include_all:
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src).as_posix()
                if rel != "manifest.json":
                    wanted[rel] = f
    if problems:
        raise BundleError(problems)
    stage = Path(tempfile.mkdtemp(prefix="iqz-"))
    try:
        folder = stage / name
        folder.mkdir()
        files = []
        for rel, f in sorted(wanted.items()):
            dst = folder / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            files.append({"path": rel, "bytes": dst.stat().st_size, "sha256": _sha256(dst)})
        now = time.time()
        manifest = {"format": 1, "kind": KIND, "service": "seren-probe", "name": name, "note": note,
                    "created_at": now, "created": _iso(now), "probe_version": _probe_version(),
                    "configs": entries, "files": files, "bytes": sum(f["bytes"] for f in files)}
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        out = Path(out)
        pack_snapshot_to(folder, out, compress="gz")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    h = content_hash(manifest)
    return {"path": str(out), "id": f"{name}-{h[:8]}", "hash": h, "name": name, "note": note,
            "configs": entries, "files": len(files), "bytes": manifest["bytes"]}


# ── baseline runs: a bundle carries the runs that name it, so it can be passed on ──
def _runs_in_archive(data: Union[bytes, Path]) -> list[dict]:
    """The run records a bundle carries (manifest.runs), read out of the archive."""
    out: list[dict] = []
    try:
        with _open(data) as tar:
            members = {m.name: m for m in tar.getmembers() if m.isfile()}
            top = next(iter({n.split("/", 1)[0] for n in members}), None)
            man = json.loads(tar.extractfile(members[f"{top}/manifest.json"]).read().decode("utf-8"))
            for r in man.get("runs") or []:
                m = members.get(f"{top}/{r.get('path')}")
                if m is None:
                    continue
                try:
                    rec = json.loads(tar.extractfile(m).read().decode("utf-8"))
                except (ValueError, UnicodeDecodeError, tarfile.TarError) as exc:
                    logger.warning("baseline run %s could not be read: %s", r.get("id"), exc)
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except (tarfile.TarError, OSError, EOFError, KeyError, ValueError) as exc:
        logger.warning("could not read the baseline runs: %s", exc)
    return out


def _import_runs(records: list[dict], bundle_id: str) -> int:
    """Keep the baseline runs in this Probe's ledger, each naming the bundle it
    came with. A run already here (same id) is left alone."""
    from . import ledger
    n = 0
    for rec in records:
        if ledger.import_run({**rec, "bundle": bundle_id}, origin="imported", imported_from=bundle_id):
            n += 1
    return n


def export_bundle(bundle_id: str, out: Path, *, with_runs: bool = True) -> dict:
    """Pack an installed bundle to hand on: the dataset and its configs as they
    were installed, plus (with_runs) every run in the ledger that names it, as
    runs/<id>.json. The runs ride OUTSIDE the content hash, so whoever installs
    the archive gets the same bundle id, with these runs in their ledger as the
    baseline their own runs group with and compare against."""
    from seren_sinew.stores import pack_snapshot_to
    from . import ledger
    d = bundle_dir(bundle_id)
    if d is None:
        raise KeyError(bundle_id)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    stage = Path(tempfile.mkdtemp(prefix="iqz-"))
    runs: list[dict] = []
    try:
        folder = stage / str(man.get("name"))
        folder.mkdir()
        files = []
        for f in man.get("files") or []:
            rel = str(f.get("path") or "")
            if not rel or rel.startswith("runs/") or not (d / rel).is_file():
                continue
            dst = folder / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(d / rel, dst)
            files.append({"path": rel, "bytes": dst.stat().st_size, "sha256": _sha256(dst)})
        if with_runs:
            for row in ledger.list_runs(bundle=bundle_id, limit=1_000_000):
                rec = ledger.load_run(row["id"])
                if not rec:
                    continue
                rel = f"runs/{rec['id']}.json"
                dst = folder / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
                files.append({"path": rel, "bytes": dst.stat().st_size, "sha256": _sha256(dst)})
                runs.append({"id": rec["id"], "kind": rec.get("kind"), "config_name": rec.get("config_name"),
                             "config_edited": bool(rec.get("config_edited")), "finished_at": rec.get("finished_at"),
                             "question_hash": rec.get("question_hash"), "origin": rec.get("origin") or "native",
                             "path": rel})
        now = time.time()
        manifest = {**{k: v for k, v in man.items() if k not in ("files", "runs", "bytes")},
                    "files": files, "runs": runs, "bytes": sum(f["bytes"] for f in files),
                    "exported_at": now, "exported": _iso(now), "exported_by": _probe_version()}
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        pack_snapshot_to(folder, Path(out), compress="gz")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"path": str(out), "id": bundle_id_of(manifest), "runs": len(runs), "files": len(files),
            "bytes": manifest["bytes"]}
