"""Generate the 15 common development initial states per protocol task (outer-only).

Common environment revision ``libero-pro-common-env-v3-statepack-2026-10-10``:
each of the five tasks keeps its original 50-state LIBERO init file, byte for
byte, for the held-out test seeds 1-50. This script adds 15 independent
development states for seeds 51-65, one file each, plus a manifest that the
trusted loader (``cap/integrations/libero/initial_states.py``) verifies on every
trial reset. It is run once by the outer operator; the statepack is shared by
every method and cell of the line as a pre-declared common environment input.

What it does, per task and development seed, and nothing else:

  1. build the task's environment from the suite's own registration (the same
     OffScreenRenderEnv arguments FrankaLiberoEnv uses: JOINT_POSITION
     controller, 800x512 cameras with depth, control_freq 20, horizon 4000);
  2. seed numpy's global RNG (LIBERO's placement samplers use it) and the env
     with a declared sampling seed derived from (environment version, suite,
     task, seed);
  3. call env.reset() once - LIBERO resamples the scene placement and runs
     forward kinematics;
  4. capture env.get_sim_state() immediately, with ZERO settling steps (the
     convention of the original init files). The trial reset later applies the
     same set_init_state + 10 settling steps to test and development states.

No action is stepped, no policy or world model runs, no success predicate or
oracle is queried and no held-out trajectory is produced: the env is wrapped so
any other attribute access fails. Every state is checked for shape/finiteness
and against all 50 test states and all recorded development states; a
duplicate is recorded and fails the run - another seed is never substituted.

Resume (``--resume``) requires the identical identity (sources, versions,
task files, sampling spec), never rewrites a recorded state, and re-verifies
each recorded file. ``--output-root`` must otherwise be fresh and outside the
source checkout.

Usage (outer host, LIBERO venv, LIBERO_CONFIG_PATH set):
  python scripts/libero/prepare_common_initial_states.py \\
      --output-root /mnt/.../runtime-assets/common-initial-states-v3 \\
      --expect-asset-manifest /mnt/.../five-task-asset-manifest.preflight.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Callable

import numpy as np

SIM_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SIM_ROOT.parents[1]))
from aspire.sim.cap.integrations.libero import initial_states as ist  # noqa: E402

SUITE = "libero_goal_swap"
TASKS = ("put_the_bowl_on_the_plate", "open_the_top_drawer_and_put_the_bowl_inside",
         "open_the_middle_drawer_of_the_cabinet", "put_the_wine_bottle_on_the_rack",
         "turn_on_the_stove")
# Same arguments FrankaLiberoEnv passes to load_libero_task (max_steps 4000).
ENV_ARGS = {"camera_heights": 512, "camera_widths": 800, "controller": "JOINT_POSITION",
            "horizon": 4000, "control_freq": 20, "camera_depths": True}
SAMPLING = {
    "env_args": ENV_ARGS,
    "seeding": "np.random.seed(s); env.seed(s); s = int(sha256('<environment_version>|<suite>|"
               "<task>|<seed>')[:8], 16)",
    "resets": 1,
    "settling_steps_before_capture": 0,
    "capture": "env.get_sim_state() immediately after the single env.reset()",
    "trial_load": "single reset + set_init_state + 10 settling steps (FrankaLiberoEnv.reset)",
}
MANIFEST = "initial_state_manifest.json"
ALLOWED_ENV_CALLS = ("seed", "reset", "get_sim_state", "close")
PACKAGES = ("mujoco", "robosuite", "numpy", "torch", "libero")


class GenerationError(RuntimeError):
    """The run stopped; the manifest records how far it got."""


class SamplingOnlyEnv:
    """Exposes only seed/reset/get_sim_state/close: no step, no success check."""

    def __init__(self, env):
        object.__setattr__(self, "_env", env)

    def __getattr__(self, name):
        if name not in ALLOWED_ENV_CALLS:
            raise GenerationError(f"state generation may not use env.{name}")
        return getattr(self._env, name)


def sampling_seed(task: str, seed: int) -> int:
    key = f"{ist.ENVIRONMENT_VERSION}|{SUITE}|{task}|{seed}".encode()
    return int(hashlib.sha256(key).hexdigest()[:8], 16)


# --- real LIBERO bindings (replaced by stubs in CPU tests) -------------------------

def _libero():
    vendor = SIM_ROOT / "cap/third_party/LIBERO-PRO"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.append(str(vendor))
    from libero import benchmark  # type: ignore[import-not-found]
    from libero.envs import OffScreenRenderEnv  # type: ignore[import-not-found]
    from libero.utils import get_libero_path  # type: ignore[import-not-found]
    return benchmark, OffScreenRenderEnv, get_libero_path


def resolve_tasks(suite: str, tasks: tuple[str, ...]) -> dict:
    """Definition and init-file paths exactly as the suite registers them."""
    benchmark, _, get_libero_path = _libero()
    task_suite = benchmark.get_benchmark_dict(help=False)[suite]()
    names = [task_suite.get_task(i).name for i in range(task_suite.n_tasks)]
    resolved = {}
    for name in tasks:
        if names.count(name) != 1:
            raise GenerationError(f"{suite} does not register exactly one task {name}")
        index = names.index(name)
        task = task_suite.get_task(index)
        resolved[name] = {
            "task_index": index,
            "definition_path": os.path.join(get_libero_path("bddl_files"), task.problem_folder,
                                            task.bddl_file),
            "test_path": os.path.join(get_libero_path("init_states"), task.problem_folder,
                                      task.init_states_file)}
    return resolved


def libero_env_factory(definition_path: str):
    _, OffScreenRenderEnv, _ = _libero()
    return OffScreenRenderEnv(bddl_file_name=definition_path, **ENV_ARGS)


def versions() -> dict:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = None
    return {"packages": found, "python": sys.version.split()[0]}


def source_identity() -> dict:
    try:
        head = subprocess.run(["git", "-C", str(SIM_ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30).stdout.strip() or None
    except OSError:
        head = None
    files = ("scripts/libero/prepare_common_initial_states.py",
             "cap/integrations/libero/initial_states.py", "cap/integrations/libero/__init__.py",
             "cap/envs/simulators/libero.py")
    return {"git_head": head, "files": {f: ist.file_sha256(SIM_ROOT / f) for f in files}}


# --- generation ----------------------------------------------------------------------

def atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def build_identity(resolved: dict, test_loader, expected: dict | None,
                   versions_: dict, source: dict) -> tuple[dict, dict]:
    tasks, test_digests = {}, {}
    for name, task in resolved.items():
        states = np.asarray(test_loader(Path(task["test_path"])))
        if states.ndim != 2 or states.shape[0] != len(ist.TEST_SEEDS):
            raise GenerationError(f"{name}: original init file must hold 50 flat states, "
                                  f"got {list(states.shape)}")
        for slot, state in enumerate(states):
            ist.check_state(np.asarray(state), states.shape[1:], f"{name} test slot {slot}")
        digests = [ist.array_sha256(np.asarray(s)) for s in states]
        test_digests[name] = digests
        tasks[name] = {
            "suite": SUITE, "task_index": task["task_index"],
            "definition_path": task["definition_path"],
            "definition_sha256": ist.file_sha256(task["definition_path"]),
            "test": {"path": task["test_path"], "sha256": ist.file_sha256(task["test_path"]),
                     "count": len(ist.TEST_SEEDS), "state_shape": list(states.shape[1:]),
                     "state_dtype": states.dtype.str, "state_sha256": digests,
                     "seed_to_slot": {str(s): s - 1 for s in ist.TEST_SEEDS}}}
        if expected is not None:
            check_preflight(name, tasks[name], expected)
    identity = {"schema": ist.MANIFEST_SCHEMA, "environment_version": ist.ENVIRONMENT_VERSION,
                "suite": SUITE, "seed_mapping": ist.SEED_MAPPING, "partitions": ist.PARTITIONS,
                "sampling": SAMPLING, "versions": versions_, "source": source,
                "tasks": tasks}
    return identity, test_digests


def check_preflight(name: str, task: dict, expected: dict) -> None:
    rows = [t for t in expected.get("tasks", []) if t.get("task") == name]
    if len(rows) != 1:
        raise GenerationError(f"asset preflight has no single row for {name}")
    row = rows[0]
    wanted = {"task_index": task["task_index"], "suite": task["suite"],
              "definition_sha256": task["definition_sha256"],
              "initial_state_sha256": task["test"]["sha256"],
              "initial_states": task["test"]["count"]}
    wrong = {k: {"preflight": row.get(k), "suite": v} for k, v in wanted.items() if row.get(k) != v}
    if wrong:
        raise GenerationError(f"{name} differs from the asset preflight: {wrong}")


def generate(output_root: Path, *, resume: bool = False, tasks: tuple[str, ...] = TASKS,
             resolver: Callable = resolve_tasks, env_factory: Callable = libero_env_factory,
             test_loader: Callable = ist.load_test_states, expect: dict | None = None,
             versions_: Callable = versions, source: Callable = source_identity) -> dict:
    output_root = Path(output_root).resolve()
    if output_root.is_relative_to(SIM_ROOT.parents[1]):
        raise GenerationError("runtime assets belong outside the source checkout")
    manifest_path = output_root / MANIFEST
    # Output-root checks come first: they need no LIBERO import.
    if resume and not manifest_path.is_file():
        raise GenerationError("nothing to resume: no manifest in --output-root")
    if not resume and output_root.exists() and any(output_root.iterdir()):
        raise GenerationError("--output-root must be fresh (missing or empty); use --resume")
    resolved = resolver(SUITE, tuple(tasks))
    identity, test_digests = build_identity(resolved, test_loader, expect, versions_(), source())
    if resume:
        manifest = json.loads(manifest_path.read_text())
        drift = sorted(k for k in identity if manifest.get("identity", {}).get(k) != identity[k])
        if drift:
            raise GenerationError(f"resume identity differs in {drift}; use a fresh output root")
    else:
        output_root.mkdir(parents=True, exist_ok=True)
        manifest = {"identity": identity, "status": "incomplete",
                    "created_at": datetime.now(timezone.utc).isoformat(), "tasks": {},
                    "outer_only": {"policy_runs": 0, "env_steps": 0, "oracle_queries": 0,
                                   "heldout_trajectories": 0,
                                   "state_contents_provided_to_solver": False}}
        manifest.update({k: identity[k] for k in ("schema", "environment_version", "suite",
                                                  "seed_mapping", "partitions")})
        atomic_json(manifest_path, manifest)
    manifest["status"] = "incomplete"
    try:
        for name in tasks:
            _generate_task(name, manifest, manifest_path, test_digests, env_factory)
        for name in tasks:  # the original 50-state files stayed byte-identical
            test = identity["tasks"][name]["test"]
            if ist.file_sha256(test["path"]) != test["sha256"]:
                raise GenerationError(f"{name}: original test init file changed during generation")
    except BaseException:
        atomic_json(manifest_path, manifest)
        raise
    manifest["status"] = "complete"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(manifest_path, manifest)
    return manifest


def _generate_task(name, manifest, manifest_path, test_digests, env_factory) -> None:
    identity = manifest["identity"]["tasks"][name]
    entry = manifest["tasks"].setdefault(name, {
        **{k: identity[k] for k in ("suite", "task_index", "definition_path",
                                    "definition_sha256", "test")},
        "development": {"states": {}}})
    states = entry["development"]["states"]
    root = manifest_path.parent
    shape = identity["test"]["state_shape"]
    env = None
    try:
        for seed in ist.DEV_SEEDS:
            record = states.get(str(seed))
            relative = Path("development") / name / f"seed_{seed}.npy"
            path = root / relative
            if record and record["status"] == "complete":
                if ist.file_sha256(path) != record["sha256"]:
                    raise GenerationError(f"{name} seed {seed}: recorded state file was modified")
                continue
            if record and record["status"] == "duplicate":
                raise GenerationError(f"{name} seed {seed}: recorded duplicate "
                                      f"({record['duplicate_of']}); not resampled")
            if path.exists():
                raise GenerationError(f"{name} seed {seed}: unrecorded state file {path}; "
                                      "inspect it, nothing is overwritten")
            s = sampling_seed(name, seed)
            states[str(seed)] = {"status": "sampling", "slot": seed - ist.DEV_SEEDS[0],
                                 "sampling_seed": s}
            atomic_json(manifest_path, manifest)
            if env is None:
                env = SamplingOnlyEnv(env_factory(identity["definition_path"]))
            np.random.seed(s)
            env.seed(s)
            env.reset()
            state = np.array(env.get_sim_state(), dtype=np.float64, copy=True)
            ist.check_state(state, shape, f"{name} development seed {seed}")
            digest = ist.array_sha256(state)
            seen = {d: f"test seed {i + 1}" for i, d in enumerate(test_digests[name])}
            for other, task in manifest["tasks"].items():
                for other_seed, r in task["development"]["states"].items():
                    if r.get("array_sha256"):
                        seen.setdefault(r["array_sha256"], f"{other} development seed {other_seed}")
            if digest in seen:
                states[str(seed)].update(status="duplicate", array_sha256=None,
                                         duplicate_of=seen[digest])
                atomic_json(manifest_path, manifest)
                raise GenerationError(f"{name} seed {seed} duplicates {seen[digest]}; "
                                      "recorded and stopped, no other seed is substituted")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("wb") as handle:
                np.save(handle, state, allow_pickle=False)
            os.replace(tmp, path)
            states[str(seed)].update(status="complete", path=str(relative),
                                     sha256=ist.file_sha256(path), array_sha256=digest,
                                     shape=list(state.shape), dtype=state.dtype.str, finite=True)
            atomic_json(manifest_path, manifest)
    finally:
        if env is not None:
            env.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--expect-asset-manifest", type=Path,
                        help="outer asset preflight; task index and file hashes must match")
    args = parser.parse_args(argv)
    expect = json.loads(args.expect_asset_manifest.read_text()) if args.expect_asset_manifest else None
    try:
        manifest = generate(args.output_root, resume=args.resume, expect=expect)
    except GenerationError as exc:
        print(f"prepare_common_initial_states: {exc}", file=sys.stderr)
        return 3
    path = Path(args.output_root).resolve() / MANIFEST
    print(json.dumps({"manifest": str(path), "manifest_sha256": ist.file_sha256(path),
                      "status": manifest["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
