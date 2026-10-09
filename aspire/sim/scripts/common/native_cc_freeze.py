"""Bind a campaign to its runtime sources; task programs and skills stay writable.

This is an experiment-integrity check, not a security sandbox. Dependency links
and executable metadata are pinned without rehashing model weights per trial.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


SOURCE_DIRS = ("cap", "scripts", "env_configs", ".claude")
SOURCE_FILES = ("CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock")
REQUIRED_FILES = (
    "scripts/libero/native_cc_campaign.py", "scripts/libero/native_cc_protocol.py",
    "scripts/libero/native_cc_recover_trial.py", "scripts/libero/fix_loop_state.py",
    "scripts/libero/replay_trial.py", "scripts/libero/run_fix_loop_validation.py",
    "scripts/libero/record_skill_promotion.py", "scripts/libero/native_cc_toolchain_probe.py",
    "scripts/common/native_cc_freeze.py", "scripts/common/native_cc_guard.py",
    "scripts/common/native_cc_trial_process.py", "scripts/common/native_cc_skill_probe.py",
    "scripts/common/native_cc_stream.py", "scripts/common/native_cc_compat.py",
    "scripts/common/claude_with_local_model.sh",
    # Native original fix loop (A1/B1/C1): runner, ledger, broker and the
    # pristine prompt the worker prompt is rendered from.
    "scripts/libero/native_world_campaign.py", "scripts/libero/native_world_protocol.py",
    "scripts/libero/native_world_fixloop_state.py", "scripts/libero/native_world_heldout.py",
    "cap/world_model/native_world_broker.py", "cap/world_model/live_broker.py",
    "cap/world_model/relational_scene_broker.py",
    ".claude/libero/fix-loop/subagent-prompt.md", ".claude/libero/api-reference.md",
)


class RuntimeChanged(RuntimeError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory(repo: Path) -> dict:
    files, links = {}, {}
    for name in SOURCE_DIRS:
        root = repo / name
        if not root.exists():
            continue
        for folder, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", ".pytest_cache", ".git"})
            for child in dirs + sorted(names):
                path = Path(folder) / child
                relative = str(path.relative_to(repo))
                if path.is_symlink():
                    links[relative] = str(path.resolve())
                elif path.is_file() and path.suffix not in {".pyc", ".pyo"}:
                    files[relative] = digest(path)
    for name in SOURCE_FILES:
        if (repo / name).is_file():
            files[name] = digest(repo / name)
    return {"files": files, "source_links": links}


def dependency_bindings(repo: Path) -> dict:
    links = {}
    for folder, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in {"outputs", "skill_library", ".git", "__pycache__", ".pytest_cache"}]
        for name in dirs + names:
            path = Path(folder) / name
            if path.is_symlink():
                links[str(path.relative_to(repo))] = str(path.resolve(strict=True))
        dirs[:] = [d for d in dirs if not (Path(folder) / d).is_symlink()]
    return links


def build_manifest(repo: Path, *, external_files=(), executables=()) -> dict:
    missing = [name for name in REQUIRED_FILES if not (repo / name).is_file()]
    if missing:
        raise RuntimeChanged("missing runtime dependencies: " + ", ".join(missing))
    return {"schema_version": 1, **source_inventory(repo),
            "dependency_links": dependency_bindings(repo),
            "external_files": {str(p): digest(Path(p)) for p in external_files},
            "executables": {str(p): executable_stamp(Path(p)) for p in executables}}


def executable_stamp(path: Path) -> dict:
    stat = path.stat()
    return dict(target=str(path.resolve()), size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def verify_runtime(case: dict, repo: Path) -> str | None:
    path = case.get("runtime_manifest")
    if not path:
        if case.get("require_runtime_freeze"):
            raise RuntimeChanged("required runtime manifest is missing")
        return None  # Legacy callers remain usable outside frozen campaigns.
    manifest_path = Path(path)
    bound_hash = case.get("runtime_manifest_sha256")
    if not bound_hash or digest(manifest_path) != bound_hash:
        raise RuntimeChanged("runtime manifest hash changed or was not bound")
    expected = json.loads(manifest_path.read_text())
    actual = source_inventory(repo)
    errors = []
    for kind in ("files", "source_links"):
        errors.extend(f"{kind}:{name}" for name in sorted(set(actual[kind]) | set(expected[kind]))
                      if actual[kind].get(name) != expected[kind].get(name))
    if dependency_bindings(repo) != expected["dependency_links"]:
        errors.append("dependency links")
    for name, checksum in expected["external_files"].items():
        if not Path(name).is_file() or digest(Path(name)) != checksum:
            errors.append(name)
    for name, stamp in expected["executables"].items():
        if not Path(name).is_file() or executable_stamp(Path(name)) != stamp:
            errors.append(name)
    if errors:
        raise RuntimeChanged("frozen runtime changed: " + ", ".join(errors[:20]))
    return bound_hash


def verify_from_environment(repo: Path) -> str | None:
    path = os.environ.get("ASPIRE_NATIVE_CASE")
    return verify_runtime(json.loads(Path(path).read_text()), repo) if path else None
