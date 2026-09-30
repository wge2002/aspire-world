"""Opt-in capture for blind numeric world-model replay.

The existing policy keeps its observations and actions. This collector obtains
additional evaluator evidence at public action API boundaries; a subsequent
numeric monitor sees only evidence released by its broker, never this env.
This is not sparse camera acquisition, a generated world model, or a sandbox.
No simulator or model module is imported here.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from urllib.parse import urlsplit


TASK = "pick_up_the_alphabet_soup_and_place_it_in_the_basket"
SUITE = "libero_object_swap"
ACTIONS = {
    "move_to_joints", "goto_pose", "open_gripper", "close_gripper",
    "goto_home_joint_position",
}
CHILD_ENV_NAMES = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "TMPDIR", "TMP", "TEMP", "ASPIRE_ROOT", "PYTHON_ROOT", "PYTHONPATH",
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
    "LIBRARY_PATH", "CUDA_HOME", "CUDA_PATH", "CUDA_VISIBLE_DEVICES", "MUJOCO_EGL_DEVICE_ID",
    "MUJOCO_GL", "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "XLA_PYTHON_CLIENT_PREALLOCATE",
    "XLA_PYTHON_CLIENT_MEM_FRACTION", "PYTHONUNBUFFERED", "PYTHONNOUSERSITE",
    "VIRTUAL_ENV", "CONDA_PREFIX", "LIBERO_CONFIG_PATH",
}
SERVICE_ENV_NAMES = {"SAM3_SERVICE_URL", "GRASPNET_SERVICE_URL", "PYROKI_SERVICE_URL"}


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numeric(value: Any) -> Any:
    """Restrict action arguments to small numeric payloads for the blind monitor."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)) and len(value) <= 64:
        return [_numeric(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _numeric(item) for key, item in value.items()}
    return None


def load_config(args: Any) -> tuple[dict, dict]:
    """Validate before constructing an environment or creating output files."""
    if getattr(args, "api_key", None):
        raise ValueError("capture does not request a model; do not pass --args.api-key")
    if not args.replay_code or args.interactive:
        raise ValueError("world-model capture requires --args.replay-code and noninteractive mode")
    if args.suite != SUITE or args.task != TASK:
        raise ValueError("world-model capture v0 is restricted to the LIBERO-Pro Alphabet soup task")
    if not 51 <= args.trial <= 65:
        raise ValueError("world-model capture v0 permits development seeds 51–65 only")
    if not Path(args.replay_code).is_file():
        raise ValueError("frozen replay policy does not exist")
    config = json.loads(Path(args.world_model_config).read_text())
    allowed = {"schema_version", "mode", "run_name", "model_provenance", "observation", "output_root",
               "trial_timeout_seconds"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError("capture config contains unsupported keys; do not put credentials in it")
    if config.get("schema_version") != 1 or config.get("mode") != "capture":
        raise ValueError("world-model config requires schema_version=1 and mode='capture'")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", config.get("run_name", "")):
        raise ValueError("run_name must be a single nonempty path component")
    provenance = config.get("model_provenance", {})
    if not isinstance(provenance, dict) or set(provenance) != {"model_id", "open_weights", "source"}:
        raise ValueError("model_provenance permits only model_id, open_weights, and public source")
    if provenance.get("open_weights") is not True:
        raise ValueError("explicit open_weights=true policy-model provenance is required")
    for key in ("model_id", "source"):
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            raise ValueError(f"model_provenance.{key} is required")
    if re.search(r"claude|(?:^|[/_-])gpt(?:$|[/_.-])|anthropic|openai", provenance["model_id"], re.I):
        raise ValueError("capture policy provenance must name an open-weights model, not Claude/GPT")
    source = urlsplit(provenance["source"])
    if (source.scheme not in {"http", "https"} or not source.hostname or source.username
            or source.password or source.query or source.fragment):
        raise ValueError("model_provenance.source must be a public HTTP(S) URL without credentials or query")
    observation = config.get("observation", {})
    if not isinstance(observation, dict) or set(observation) - {"object_id", "segmentation_prompt", "min_score"}:
        raise ValueError("observation permits only object_id, segmentation_prompt, and min_score")
    for key in ("object_id", "segmentation_prompt"):
        if not isinstance(observation.get(key), str) or not observation[key].strip():
            raise ValueError(f"observation.{key} is required")
    minimum = observation.get("min_score", 0.5)
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool) or not 0 <= minimum <= 1:
        raise ValueError("observation.min_score must be between zero and one")
    config = copy.deepcopy(config)
    config["observation"]["min_score"] = float(minimum)
    timeout = config.get("trial_timeout_seconds", 900)
    if (not isinstance(timeout, (float, int)) or isinstance(timeout, bool)
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("trial_timeout_seconds must be a positive finite number")
    config["trial_timeout_seconds"] = timeout
    output_root = config.get("output_root", "./outputs/libero_code_world")
    if not isinstance(output_root, str) or not output_root.strip():
        raise ValueError("output_root must name a separate experiment directory")
    output_root = Path(output_root).expanduser().resolve()
    old_root = Path(args.output_dir).expanduser().resolve()
    if output_root.is_relative_to(old_root) or old_root.is_relative_to(output_root):
        raise ValueError("capture output_root must be separate from the existing replay output tree")
    config["output_root"] = str(output_root)

    import yaml  # Existing replay dependency; imported only for the opt-in path.

    base = yaml.safe_load(Path(args.config).expanduser().read_text())
    cfg = base.get("env", {}).get("cfg", {})
    if cfg.get("privileged", False) or cfg.get("low_level", {}).get("privileged", False):
        raise ValueError("world-model capture refuses privileged configurations")
    apis = cfg.get("apis", [])
    if apis != ["FrankaLiberoApiReducedSkillLibraryTraced"]:
        raise ValueError("capture v0 requires the nonprivileged reduced traced LIBERO API")
    return config, base


class LiberoCapture:
    """One evaluator-side collector; it cannot alter a policy action or result."""

    def __init__(self, directory: Path, config: dict, manifest: dict):
        self.directory = directory
        self.config = config
        self.manifest = manifest
        self.env = None
        self.api = None
        self._restorations: list[tuple[Any, bool, Any]] = []
        self._inside = False
        self._frame = 0
        self._tape = (directory / "numeric_tape.jsonl").open("x")
        self._finished = False

    def attach(self, env: Any) -> None:
        """Wrap this environment instance only, before reset binds its globals."""
        self.env = env
        apis = getattr(env, "_apis", None)
        if not isinstance(apis, dict) or len(apis) != 1:
            raise ValueError("capture expects one reduced API on the code execution environment")
        self.api = next(iter(apis.values()))
        for name in ("get_observation", "segment_sam3_text_prompt", "mask_to_world_points"):
            if not callable(getattr(self.api, name, None)):
                raise ValueError(f"capture API is missing public {name}")
        api = self.api
        original = api.functions
        owned = "functions" in vars(api)
        self._restorations.append((api, owned, vars(api).get("functions")))

        def functions():
            bindings = original()
            return {name: self._wrap(fn, name) if name in ACTIONS else fn
                    for name, fn in bindings.items()}

        api.functions = functions

    def _wrap(self, fn: Any, name: str):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            if self._inside:
                return fn(*args, **kwargs)
            self._inside = True
            action_error = None
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                action_error = type(exc).__name__
                raise
            finally:
                try:
                    try:
                        arguments = {f"arg{i}": _numeric(value) for i, value in enumerate(args)}
                        arguments.update({key: _numeric(value) for key, value in kwargs.items()})
                        self.capture(name, arguments, action_error=action_error)
                    except Exception as exc:
                        self.manifest.setdefault("capture_errors", []).append(type(exc).__name__)
                finally:
                    self._inside = False
        return call

    def _save_sources(self, cam: dict) -> dict:
        """Evaluator-only sensor files for identity and geometry auditing."""
        import numpy as np
        from PIL import Image

        folder = self.directory / "observer_sources" / f"frame_{self._frame:05d}"
        folder.mkdir(parents=True, exist_ok=False)
        Image.fromarray(cam["images"]["rgb"]).save(folder / "rgb.png")
        for name, array in (("depth", cam["images"]["depth"]),
                            ("intrinsics", cam["intrinsics"]), ("extrinsics", cam["pose_mat"])):
            np.save(folder / f"{name}.npy", array, allow_pickle=False)
        return {"directory": str(folder.relative_to(self.directory)), "rgb": "rgb.png",
                "depth": "depth.npy", "intrinsics": "intrinsics.npy", "extrinsics": "extrinsics.npy"}

    def _save_masks(self, masks: list[dict]) -> None:
        import numpy as np

        folder = self.directory / "observer_sources" / f"frame_{self._frame:05d}"
        for index, item in enumerate(masks):
            if item.get("mask") is not None:
                np.save(folder / f"mask_{index}.npy", item["mask"], allow_pickle=False)

    def capture(self, action: str, arguments: dict, *, action_error: str | None = None) -> None:
        """Read public sensors and append numeric evidence; capture failures are unknown."""
        started = time.monotonic()
        observation_cfg = self.config["observation"]
        record = {
            "schema_version": 1, "frame_id": self._frame,
            "action": {"api": action, "args": arguments},
            "robot_state": None,
            "measurement": {"status": "unknown", "object_id": observation_cfg["object_id"],
                            "position": None, "frame": "world", "reason": "capture_not_available",
                            "source": {"camera": "agentview", "frame_id": self._frame,
                                       "prompt": observation_cfg["segmentation_prompt"],
                                       "selection": "unique_nonempty_mask_above_score_threshold",
                                       "min_score": observation_cfg["min_score"],
                                       "identity_verified": False}},
            "capture_cost": {"observation_attempts": 0, "segmentation_attempts": 0,
                             "projection_attempts": 0},
        }
        if action_error is not None:
            record["action_error"] = action_error
        try:
            record["capture_cost"]["observation_attempts"] += 1
            # A public API read, not env unwrapping. Copy views so perception cannot
            # mutate the arrays the fixed policy may have retained.
            obs = copy.deepcopy(self.api.get_observation())
            robot = _numeric(obs["robot_cartesian_pos"])
            if not isinstance(robot, list) or len(robot) != 8 or any(v is None for v in robot):
                raise ValueError("invalid public robot state")
            record["robot_state"] = {"position": robot[:3], "orientation_wxyz": robot[3:7],
                                     "gripper": robot[7]}
            cam = obs["agentview"]
            try:
                record["measurement"]["source"]["files"] = self._save_sources(cam)
            except Exception as exc:
                self.manifest.setdefault("capture_errors", []).append(f"source_write:{type(exc).__name__}")
            record["capture_cost"]["segmentation_attempts"] += 1
            masks = self.api.segment_sam3_text_prompt(
                cam["images"]["rgb"], observation_cfg["segmentation_prompt"])
            try:
                self._save_masks(masks)
            except Exception as exc:
                self.manifest.setdefault("capture_errors", []).append(f"mask_write:{type(exc).__name__}")
            eligible = []
            candidates = []
            for index, item in enumerate(masks):
                score = float(item.get("score", 0.0))
                mask = item.get("mask")
                nonempty = mask is not None and (
                    bool(mask.any()) if hasattr(mask, "any") else any(any(row) for row in mask))
                candidates.append({"mask_index": index, "score": score if math.isfinite(score) else None,
                                   "nonempty": nonempty})
                if nonempty and math.isfinite(score) and score >= observation_cfg["min_score"]:
                    eligible.append((index, item))
            source = record["measurement"]["source"]
            source["candidates"] = candidates
            if len(eligible) != 1:
                record["measurement"]["reason"] = "ambiguous_segmentation" if eligible else "no_eligible_mask"
            else:
                index, selected = eligible[0]
                source.update(mask_index=index, score=float(selected.get("score", 0.0)))
                record["capture_cost"]["projection_attempts"] += 1
                points = self.api.mask_to_world_points(
                    selected["mask"], cam["images"]["depth"], cam["intrinsics"], cam["pose_mat"])
                if hasattr(points, "tolist"):
                    points = points.tolist()
                points = [point for point in points
                          if len(point) == 3 and all(math.isfinite(float(x)) for x in point)]
                source["valid_points"] = len(points)
                source["readout"] = "visible_surface_coordinatewise_median"
                if not points:
                    record["measurement"]["reason"] = "no_valid_depth_points"
                else:
                    record["measurement"].update(
                        status="ok", position=[float(statistics.median(p[axis] for p in points))
                                               for axis in range(3)], reason=None)
        except Exception as exc:
            record["measurement"]["reason"] = f"capture_error:{type(exc).__name__}"
        if record["robot_state"] is None:
            self.manifest.setdefault("capture_errors", []).append(f"missing_proprio:frame_{self._frame}")
        record["capture_cost"]["elapsed_seconds"] = time.monotonic() - started
        self._frame += 1
        try:
            self._tape.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            self._tape.flush()
        except Exception as exc:
            # Keep a failed evidence write from changing the fixed policy's control flow.
            self.manifest.setdefault("capture_errors", []).append(type(exc).__name__)
        self.manifest["frame_count"] = self._frame
        self.manifest["measurement_ok"] += int(record["measurement"]["status"] == "ok")

    def complete(self, **result) -> None:
        self._finished = True
        self.manifest["trial_result"] = result

    def close(self, error: str | None = None) -> None:
        """Restore only this instance's bindings, including when replay failed."""
        for api, owned, old in reversed(self._restorations):
            if owned:
                api.functions = old
            else:
                del api.functions
        self._restorations.clear()
        self._tape.close()
        if self.env is not None and callable(getattr(self.env, "close", None)):
            try:
                self.env.close()
            except Exception as exc:
                self.manifest["cleanup_error"] = type(exc).__name__
        self.manifest["status"] = (
            "complete" if self._finished and error is None and not self.manifest.get("capture_errors")
            else "failed")
        self.manifest["tape_sha256"] = _sha256(self.directory / "numeric_tape.jsonl")
        if error:
            self.manifest["error"] = error
        _write_json(self.directory / "capture_manifest.json", self.manifest)


def _prepare_capture(args: Any, config: dict) -> Path:
    """Parent-owned preparation, exactly once, before the credential-free child."""
    directory = Path(config["output_root"]) / config["run_name"] / f"seed_{args.trial}"
    directory.mkdir(parents=True, exist_ok=False)
    policy_path = directory / "frozen_policy.py"
    policy_path.write_bytes(Path(args.replay_code).read_bytes())
    yaml_path = directory / "source_config.yaml"
    yaml_path.write_bytes(Path(args.config).expanduser().read_bytes())
    _write_json(directory / "capture_config.json", config)
    manifest = {
        "schema_version": 1, "mode": "capture", "status": "running",
        "suite": args.suite, "task": args.task, "seed": args.trial, "partition": "development",
        "output_root": config["output_root"],
        "model_provenance": config["model_provenance"], "provenance_verified_by_capture": False,
        "model_requests": 0, "generated_world": False,
        "scope": "fixed_policy_dense_evaluator_capture_for_later_blind_numeric_replay",
        "frame_count": 0, "measurement_ok": 0, "policy_sha256": _sha256(policy_path),
        "tape": "numeric_tape.jsonl", "policy": policy_path.name, "config": yaml_path.name,
        "limitations": ["Additional evaluator perception has latency; camera acquisition is not reduced.",
                        "SAM identity is a prompt-conditioned hypothesis, not ground truth.",
                        "Visible surface medians are not fixed rigid-body landmarks.",
                        "Child environment is allowlisted; HOME remains for LIBERO config. This is not a filesystem sandbox."],
    }
    _write_json(directory / "capture_manifest.json", manifest)
    # Never serialize vars(args): legacy model fields can contain credentials.
    request = {"schema_version": 1, "args": {
        "suite": args.suite, "task": args.task, "trial": args.trial,
        "world_model_config": None, "output_dir": str(directory / "replay"),
        "replay_code": str(policy_path), "config": str(yaml_path),
        "model": config["model_provenance"]["model_id"],
        "record_video": bool(args.record_video), "debug": bool(args.debug), "interactive": False,
    }}
    request_path = directory / "child_request.json"
    _write_json(request_path, request)
    return request_path


def _run_child_request(request_path: Path, replay_runner: Any) -> None:
    """Private child entry: preparation and environment filtering are parent-owned."""
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    if request.get("schema_version") != 1:
        raise ValueError("unsupported private capture request")
    copied = SimpleNamespace(**request["args"])
    config = json.loads((directory / "capture_config.json").read_text())
    manifest = json.loads((directory / "capture_manifest.json").read_text())
    capture = LiberoCapture(directory, config, manifest)
    error = None
    try:
        replay_runner(copied, _world_capture=capture)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        capture.close(error)
    if capture.manifest["status"] != "complete":
        raise RuntimeError(
            f"world-model capture evidence is incomplete; inspect {directory / 'capture_manifest.json'}")


def child_environment(parent: dict[str, str]) -> dict[str, str]:
    """Keep runtime configuration, not provider keys, proxies, or cloud credentials."""
    child = {name: value for name, value in parent.items() if name in CHILD_ENV_NAMES}
    for name in SERVICE_ENV_NAMES:
        value = parent.get(name)
        if value:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise ValueError(f"{name} must be an HTTP(S) service URL without credentials or query")
            child[name] = value.rstrip("/")
    child["PYTHONUNBUFFERED"] = "1"
    # Defense against common SDK metadata credential discovery; no model SDK is
    # invoked by this mode. This does not create a network or filesystem sandbox.
    child["AWS_EC2_METADATA_DISABLED"] = "true"
    return child


def _stop_process_group(process: subprocess.Popen, grace_seconds: float = 2.0) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    # Kill remaining descendants even if the leader already exited after TERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _launch_child(request_path: Path, environment: dict[str, str], timeout: float) -> dict:
    directory = request_path.parent
    receipt = {"schema_version": 1, "status": "launching", "exit_code": None,
               "timeout_seconds": timeout, "manifest_valid": False,
               "stdout": "child_stdout.log", "stderr": "child_stderr.log"}
    started = time.monotonic()
    process = None
    try:
        with (directory / receipt["stdout"]).open("x") as out, \
             (directory / receipt["stderr"]).open("x") as err:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--child-request", str(request_path)],
                env=environment, stdout=out, stderr=err, start_new_session=True,
            )
            receipt["pid"] = process.pid
            try:
                receipt["exit_code"] = process.wait(timeout=timeout)
                receipt["status"] = "completed" if process.returncode == 0 else "nonzero"
            except subprocess.TimeoutExpired:
                _stop_process_group(process)
                receipt.update(status="timeout", exit_code=process.returncode)
    except BaseException as exc:
        if process is not None and process.poll() is None:
            _stop_process_group(process)
        receipt.update(status="launch_error" if process is None else "interrupted", error=type(exc).__name__)
        raise
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        _write_json(directory / "child_exit.json", receipt)
    return receipt


def run_capture(args: Any) -> None:
    """Launch one bounded child; the normal replay process never executes the policy."""
    config, _ = load_config(args)
    environment = child_environment(dict(os.environ))
    request_path = _prepare_capture(args, config)
    directory = request_path.parent
    manifest_path = directory / "capture_manifest.json"
    prepared_manifest = json.loads(manifest_path.read_text())
    try:
        receipt = _launch_child(request_path, environment, config["trial_timeout_seconds"])
    except BaseException as exc:
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            manifest = prepared_manifest
        manifest.update(status="failed", child_error=type(exc).__name__)
        _write_json(manifest_path, manifest)
        raise
    try:
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
    except (OSError, ValueError) as exc:
        manifest = {**prepared_manifest, "status": "failed", "child_manifest_error": type(exc).__name__}
    tape_path = directory / "numeric_tape.jsonl"
    valid = (manifest.get("status") == "complete" and manifest.get("suite") == args.suite
             and manifest.get("task") == args.task and manifest.get("seed") == args.trial
             and manifest.get("policy_sha256") == _sha256(directory / "frozen_policy.py")
             and tape_path.is_file() and manifest.get("tape_sha256") == _sha256(tape_path))
    receipt["manifest_valid"] = valid
    _write_json(directory / "child_exit.json", receipt)
    if receipt["status"] != "completed" or not valid:
        manifest.update(status="failed", child_status=receipt["status"])
        _write_json(manifest_path, manifest)
        raise RuntimeError(f"world-model capture child {receipt['status']} or incomplete evidence; inspect {directory}")


def _child_main(request_path: Path) -> None:
    # This code path is private to the sanitized subprocess. It has no legacy
    # flag recursion or environment sentinel which changes normal replay.
    import importlib.util

    replay_path = Path(__file__).resolve().parents[2] / "scripts/libero/replay_trial.py"
    spec = importlib.util.spec_from_file_location("_world_capture_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    # These clients historically ignore their service environment variables.
    # Apply explicit endpoint selection only inside this newly created child.
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (os.environ["PYROKI_SERVICE_URL"],)
    _run_child_request(request_path, replay._run_replay)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Private numeric capture child")
    parser.add_argument("--child-request", type=Path, required=True)
    _child_main(parser.parse_args().child_request.resolve())
