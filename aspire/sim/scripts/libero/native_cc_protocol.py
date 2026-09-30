#!/usr/bin/env python3
"""Recorded simulator calls and admission checks for native Claude Code.

This module has no LLM client, agent loop, or context management. Claude Code
uses its normal Bash tool to call this CLI; Stage1State accounts for trials.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from fix_loop_state import ProtocolError, Stage1State, code_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_freeze import verify_runtime, RuntimeChanged
from native_cc_trial_process import run_replay


def load_case(path: Path) -> tuple[dict, Path, Path]:
    case = json.loads(path.read_text())
    repo = Path(case["sim"]).resolve()
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    return case, repo, task_dir


def identity(case: dict, repo: Path) -> dict:
    return {
        "harness": "claude-code", "model": case["model"],
        "suite": case["suite"], "task": case["task"],
        "dev_seeds": case["dev_seeds"], "smoke_budget": case["smoke_budget"],
        "repair_limit": 3, "context_tokens": case["context_tokens"],
        "max_output_tokens": case["max_output_tokens"], "effort": case["effort"],
        "config_sha256": code_hash((repo / "env_configs/libero/franka_libero_traced.yaml").read_text()),
        "protocol_sha256": code_hash(Path(__file__).read_text() + Path(__file__).with_name("fix_loop_state.py").read_text()),
        **({"runtime_manifest_sha256": verify_runtime(case, repo)} if case.get("runtime_manifest") or case.get("require_runtime_freeze") else {}),
    }


def runtime_env(case: dict, repo: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in (os.environ if base is None else base).items() if not re.search(
        r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|^HF_TOKEN$|HUGGING_FACE_HUB_TOKEN", k
    )}
    env.update(
        ASPIRE_ROOT=str(repo), PYTHON_ROOT=str(repo.parents[1]),
        PYTHONPATH=str(repo.parents[1]), MUJOCO_GL="egl",
        CUDA_VISIBLE_DEVICES=case["cuda_visible_devices"],
        MUJOCO_EGL_DEVICE_ID=str(case["egl_device_id"]),
        TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1",
        SAM3_SERVICE_URL=case.get("sam3_url", "http://127.0.0.1:8214"),
        GRASPNET_SERVICE_URL=case.get("graspnet_url", "http://127.0.0.1:8215"),
        PYROKI_SERVICE_URL=case.get("pyroki_url", "http://127.0.0.1:8216"),
    )
    env.pop("ASPIRE_SAM3_PROMPTS", None)
    return env


def check_policy(source: str) -> None:
    patterns = [r"env\.handle\.env\b", r"sim\.(?:data|model|forward)\b",
                r"\b(?:body_xpos|get_site_xpos|set_joint_qpos|_eval_predicate|obj_body_id|parsed_problem|_step_once)\b"]
    if any(re.search(pattern, source) for pattern in patterns):
        raise ProtocolError("program references forbidden simulator ground-truth APIs")
    if re.search(r"\b(?:SERVICE_URL|DEFAULT_URL)\s*=|__globals__|\b(?:SAM3|GRASPNET|PYROKI)_SERVICE_URL\b", source):
        raise ProtocolError("program may not override frozen framework service wiring")


def run_trial(case: dict, repo: Path, state: Stage1State, phase: str,
              seed: int, code: Path | None) -> dict:
    with (state.task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("another trial is running; await its result before continuing") from exc
        # Reload under the lock so a concurrent CLI cannot use a stale ledger.
        state.data = json.loads(state.path.read_text())
        return _run_trial(case, repo, state, phase, seed, code)


def _run_trial(case: dict, repo: Path, state: Stage1State, phase: str,
               seed: int, code: Path | None) -> dict:
    verify_runtime(case, repo)
    if phase == "snapshot":
        source = (repo / "scripts/libero/scene_snapshot.py").read_text()
    else:
        if code is None:
            raise ProtocolError("--code is required")
        code = code.resolve()
        if not code.is_relative_to(state.task_dir):
            raise ProtocolError("write replay programs inside this task's output directory")
        if phase == "initial" and code != state.task_dir / "initial_code.py":
            raise ProtocolError("initial trials use TASK_DIR/initial_code.py")
        source = code.read_text()
        check_policy(source)
    if phase == "smoke" and any(r.get("sandbox_rc") == 0 for r in state.records("smoke")):
        raise ProtocolError("a smoke already ran without crashing; proceed to the initial batch")
    record = state.begin_trial(phase, seed, source)
    directory = state.task_dir / record["directory"]
    env = runtime_env(case, repo)
    env["SNAPSHOT_DIR"] = str(state.task_dir)
    command = [str(repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"],
               "--args.trial", str(seed), "--args.model", case["model"],
               "--args.replay-code", str(directory / "code.py"),
               "--args.config", "env_configs/libero/franka_libero_traced.yaml",
               "--args.output-dir", str(directory / "results")]
    (directory / "command.json").write_text(json.dumps(command, indent=2))
    exit_code, error = run_replay(command, repo=repo, env=env, directory=directory,
                                  timeout=case.get("trial_timeout", 900))
    pattern = re.compile(rf"trial_{seed:02d}_sandboxrc_(\d+)_reward_([\d.]+)_taskcompleted_(\d+)")
    matches = [(p, pattern.fullmatch(p.name)) for p in (directory / "results").rglob("trial_*") if p.is_dir()]
    matches = [(p, m) for p, m in matches if m]
    result = None
    if len(matches) == 1:
        path, match = matches[0]
        result = {"sandbox_rc": int(match[1]), "reward": float(match[2]),
                  "task_completed": int(match[3]), "trial_dir": str(path.relative_to(state.task_dir))}
    if phase == "snapshot" and (not result or result["sandbox_rc"] or not all(
        (state.task_dir / name).is_file() for name in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg")
    )):
        result, error = None, "snapshot did not produce both camera images; inspect replay.log"
    try:
        verify_runtime(case, repo)
    except RuntimeChanged as exc:
        error = str(exc)
    if error:
        result = None
    state.finish_trial(record, result=result, exit_code=exit_code, error=error)
    return record


def finalize(case: dict, repo: Path, state: Stage1State, transcripts: list[Path]) -> dict:
    verify_runtime(case, repo)
    working = repo / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"
    errors = state.completion_errors(working_code=working)
    served, usage = {}, {"input_tokens": 0, "output_tokens": 0}
    for path in transcripts:
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "assistant":
                model = record.get("message", {}).get("model")
                if model and not model.startswith("<"):
                    served[model] = served.get(model, 0) + 1
            if record.get("type") == "result":
                for key in usage:
                    usage[key] += record.get("usage", {}).get(key, 0)
    if set(served) != {case["model"]}:
        errors.append(f"native model provenance mismatch: {served}")
    if errors:
        raise ProtocolError("; ".join(errors))
    source = (state.task_dir / "fix_code.py").read_text()
    check_policy(source)
    state.data.update(stage1_complete=True, model_served=served, usage=usage)
    state.save()
    result = {
        "schema_version": 2, "harness": "claude-code", "stage1_complete": True,
        "suite": case["suite"], "task": case["task"], "code_sha256": code_hash(source),
        "model_served": served, "usage": usage,
        "initial_passes": sum(r["task_completed"] for r in state.records("initial")),
        "final_development_coverage": state.final_coverage(source),
        "replay_invocations": len(state.data["trials"]),
        "transcripts": [str(p) for p in transcripts],
    }
    (state.task_dir / "stage1_result.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"))
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("check")
    trial = sub.add_parser("trial")
    trial.add_argument("--phase", choices=["snapshot", "smoke", "initial", "repair"], required=True)
    trial.add_argument("--seed", type=int, required=True)
    trial.add_argument("--code", type=Path)
    final = sub.add_parser("finalize")
    final.add_argument("--transcript", type=Path, action="append", required=True)
    args = parser.parse_args()
    if not args.case:
        parser.error("--case or ASPIRE_NATIVE_CASE is required")
    try:
        case, repo, task_dir = load_case(Path(args.case))
        state = Stage1State(task_dir, identity(case, repo), resume=args.action != "init")
        if args.action == "trial":
            result = run_trial(case, repo, state, args.phase, args.seed, args.code)
        elif args.action == "finalize":
            result = finalize(case, repo, state, args.transcript)
        elif args.action == "check":
            working = repo / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"
            errors = state.completion_errors(working_code=working)
            result = {"ready": not errors, "errors": errors, "progress": state.progress()}
        else:
            result = state.progress()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int((args.action == "check" and not result["ready"]) or
                   (args.action == "trial" and result["status"] != "complete"))
    except (ProtocolError, RuntimeChanged, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
