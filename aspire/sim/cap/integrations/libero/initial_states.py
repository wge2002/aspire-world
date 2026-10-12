# SPDX-License-Identifier: Apache-2.0
"""Trusted initial-state registry for the common LIBERO-Pro environment.

Each of the five protocol tasks ships exactly 50 initial states in its original
LIBERO init file. Under the common environment revision those 50 states belong
to the held-out test seeds only, and the 15 development seeds read 15 separate,
pre-generated development states:

  test seeds 1-50         -> original init file slots 0-49 (file unchanged)
  development seeds 51-65 -> common development states 0-14 (one file each)

There is no modulo and no cross-partition reuse: seed 51 never reads test slot 0.

The registry is an outer, trusted-environment boundary. A trial process finds it
through ``ASPIRE_LIBERO_INITIAL_STATE_MANIFEST`` plus the bound
``..._MANIFEST_SHA256``; every load re-hashes the manifest and the selected
state file and checks the exact suite/task/seed mapping. State contents never
leave the loader: ``LiberoHandle.reset`` passes them to ``set_init_state`` and
reports only partition, slot and digest.

The development states are produced by ``scripts/libero/prepare_common_initial_states.py``
(outer-only; no policy, oracle or held-out access).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

ENVIRONMENT_VERSION = "libero-pro-common-env-v3-statepack-2026-10-10"
MANIFEST_SCHEMA = "aspire-libero-common-initial-states-v1"
MANIFEST_ENV = "ASPIRE_LIBERO_INITIAL_STATE_MANIFEST"
MANIFEST_SHA_ENV = "ASPIRE_LIBERO_INITIAL_STATE_MANIFEST_SHA256"
REQUIRED_ENV = "ASPIRE_LIBERO_INITIAL_STATE_REQUIRED"
TEST_SEEDS = tuple(range(1, 51))
DEV_SEEDS = tuple(range(51, 66))
PARTITIONS = {"test": list(TEST_SEEDS), "development": list(DEV_SEEDS)}
SEED_MAPPING = ("test seeds 1-50 -> original LIBERO init file slots 0-49; development seeds "
                "51-65 -> common development states 0-14 (one file each); no modulo, "
                "no cross-partition reuse")


class InitialStateError(RuntimeError):
    """The registry, a state file or the requested mapping failed verification."""


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_sha256(state: np.ndarray) -> str:
    """Digest of dtype, shape and bytes, so equal digests mean identical states."""
    state = np.ascontiguousarray(state)
    digest = hashlib.sha256(f"{state.dtype.str}|{state.shape}|".encode())
    digest.update(state.tobytes())
    return digest.hexdigest()


def seed_slot(seed: Any) -> tuple[str, int]:
    """Protocol seed -> (partition, slot). Anything outside 1-65 is refused."""
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
        raise InitialStateError(f"protocol seeds are integers 1-65, got {seed!r}")
    seed = int(seed)
    if seed in TEST_SEEDS:
        return "test", seed - 1
    if seed in DEV_SEEDS:
        return "development", seed - DEV_SEEDS[0]
    raise InitialStateError(f"seed {seed} is outside the protocol (test 1-50, development 51-65)")


def load_test_states(path: Path | str) -> np.ndarray:
    """The original init file, loaded the way LIBERO's get_task_init_states does."""
    import torch

    try:
        states = torch.load(str(path), weights_only=False)
    except TypeError:  # older torch without weights_only
        states = torch.load(str(path))
    return np.asarray(states)


def check_state(state: np.ndarray, shape: list[int] | tuple[int, ...], what: str) -> None:
    if state.ndim != 1 or list(state.shape) != list(shape):
        raise InitialStateError(f"{what} has shape {list(state.shape)}, expected {list(shape)}")
    if not np.issubdtype(state.dtype, np.floating) or not np.all(np.isfinite(state)):
        raise InitialStateError(f"{what} is not a finite floating-point state")


def _same_file(a: str | Path, b: str | Path) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


class InitialStateRegistry:
    """One task's view of a hash-bound common initial-state manifest."""

    def __init__(self, manifest_path: str | Path, manifest_sha256: str, suite: str, task: str,
                 *, test_loader: Callable[[Path], np.ndarray] = load_test_states) -> None:
        if not manifest_sha256:
            raise InitialStateError("the initial-state manifest must be bound by its sha256")
        self.manifest_path = Path(manifest_path)
        self.manifest_sha256 = manifest_sha256
        self.suite, self.task = suite, task
        self.test_loader = test_loader

    @classmethod
    def from_environment(cls, suite: str, task: str, *, definition_path: str | None = None,
                         test_states_path: str | None = None,
                         environ: Mapping[str, str] = os.environ) -> "InitialStateRegistry | None":
        """The trial-process entry point. ``None`` only for legacy, non-required runs."""
        path = environ.get(MANIFEST_ENV)
        if not path:
            if environ.get(REQUIRED_ENV) == "1":
                raise InitialStateError(f"{REQUIRED_ENV}=1 but {MANIFEST_ENV} is not set; a "
                                        "formal run never falls back to modulo init states")
            return None
        registry = cls(path, environ.get(MANIFEST_SHA_ENV, ""), suite, task)
        registry.verify(definition_path=definition_path, test_states_path=test_states_path)
        return registry

    # --- verification --------------------------------------------------------------

    def manifest(self) -> dict:
        if not self.manifest_path.is_file():
            raise InitialStateError(f"initial-state manifest is missing: {self.manifest_path}")
        raw = self.manifest_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.manifest_sha256:
            raise InitialStateError("initial-state manifest hash does not match the bound sha256")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InitialStateError(f"initial-state manifest is not JSON: {exc}") from exc
        expected = {"schema": MANIFEST_SCHEMA, "environment_version": ENVIRONMENT_VERSION,
                    "suite": self.suite, "seed_mapping": SEED_MAPPING, "partitions": PARTITIONS,
                    "status": "complete"}
        wrong = {k: data.get(k) for k, v in expected.items() if data.get(k) != v}
        if wrong:
            raise InitialStateError(f"initial-state manifest disagrees with {ENVIRONMENT_VERSION}: "
                                    f"{sorted(wrong)}")
        return data

    def entry(self, data: dict | None = None) -> dict:
        data = data if data is not None else self.manifest()
        entry = data.get("tasks", {}).get(self.task)
        if not isinstance(entry, dict) or entry.get("suite") != self.suite:
            raise InitialStateError(f"initial-state manifest has no {self.suite}/{self.task} entry")
        test, dev = entry.get("test", {}), entry.get("development", {})
        if test.get("count") != len(TEST_SEEDS) or len(test.get("state_sha256", [])) != len(TEST_SEEDS):
            raise InitialStateError("the test partition must record exactly the original 50 states")
        states = dev.get("states", {})
        if sorted(states, key=int) != [str(s) for s in DEV_SEEDS]:
            raise InitialStateError("the development partition must record exactly seeds 51-65")
        for seed, record in states.items():
            if record.get("status") != "complete" or record.get("slot") != int(seed) - DEV_SEEDS[0]:
                raise InitialStateError(f"development seed {seed} is not a complete, exact slot")
        digests = list(test["state_sha256"]) + [r["array_sha256"] for r in states.values()]
        if len(set(digests)) != len(digests):
            raise InitialStateError("duplicate initial states across or within partitions")
        return entry

    def verify(self, *, definition_path: str | None = None,
               test_states_path: str | None = None) -> dict:
        """Check manifest, task binding and the suite's own files; returns a summary."""
        data = self.manifest()
        entry = self.entry(data)
        if definition_path is not None:
            if not _same_file(definition_path, entry["definition_path"]):
                raise InitialStateError("the suite's task definition is not the registered one")
            if file_sha256(definition_path) != entry["definition_sha256"]:
                raise InitialStateError("task definition hash differs from the registry")
        if test_states_path is not None and not _same_file(test_states_path, entry["test"]["path"]):
            raise InitialStateError("the suite's init file is not the registered test file")
        if file_sha256(entry["test"]["path"]) != entry["test"]["sha256"]:
            raise InitialStateError("original test init file hash differs from the registry")
        return {"manifest": str(self.manifest_path), "manifest_sha256": self.manifest_sha256,
                "environment_version": data["environment_version"],
                "seed_mapping": data["seed_mapping"], "suite": self.suite, "task": self.task,
                "test_sha256": entry["test"]["sha256"],
                "development_sha256": {s: r["sha256"]
                                       for s, r in entry["development"]["states"].items()}}

    # --- loading -------------------------------------------------------------------

    def state_for(self, seed: Any) -> tuple[np.ndarray, dict]:
        """Verify, then load the state for ``seed``. Info carries no state content."""
        partition, slot = seed_slot(seed)
        entry = self.entry()
        shape = entry["test"]["state_shape"]
        if partition == "test":
            path = Path(entry["test"]["path"])
            sha = file_sha256(path)
            if sha != entry["test"]["sha256"]:
                raise InitialStateError("original test init file hash differs from the registry")
            states = np.asarray(self.test_loader(path))
            if states.shape[0] != len(TEST_SEEDS):
                raise InitialStateError(f"test init file holds {states.shape[0]} states, not 50")
            state, expected = np.asarray(states[slot]), entry["test"]["state_sha256"][slot]
        else:
            record = entry["development"]["states"][str(int(seed))]
            relative = Path(record["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise InitialStateError("development state paths stay inside the statepack")
            path = self.manifest_path.parent / relative
            if not path.is_file():
                raise InitialStateError(f"development state for seed {seed} is missing")
            sha = file_sha256(path)
            if sha != record["sha256"]:
                raise InitialStateError(f"development state for seed {seed} was modified")
            state, expected = np.load(path, allow_pickle=False), record["array_sha256"]
        check_state(state, shape, f"{partition} state for seed {seed}")
        if array_sha256(state) != expected:
            raise InitialStateError(f"{partition} state for seed {seed} differs from its digest")
        return state, {"init_state_partition": partition, "init_state_slot": slot,
                       "init_state_sha256": expected, "environment_version": ENVIRONMENT_VERSION}
