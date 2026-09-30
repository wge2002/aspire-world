#!/usr/bin/env python3
"""Recorded simulator calls for the native original Fix Loop (A1/B1/C1).

This module has no LLM client, agent loop, or context management: native Claude
Code owns generation, tools, notifications, and compaction. It records what the
solver actually executed, charges each admitted task replay to that seed's three
total attempts, and refuses held-out seeds.

Conditions:
  A  ordinary replay, no world flags at all
  B  the uncapped native world adapter, four pristine strategy MD files
  C  the same world adapter with no high-level strategy MD

An optional case `profile` refines only how a world condition is authored and
run. Absent, everything behaves exactly as the legacy A/B/C protocol:
  (absent)   the legacy native world adapter: world program plus object inventory
  simple     an in-process code world exposing update()/snapshot()
  judgment   an in-process code world the policy also consumes, exposing
             update()/query()/snapshot(), with no object inventory

Both in-process profiles run inside the ordinary replay process, so their
results stay under --args.output-dir; only the legacy adapter relocates them.

A NEW judgment cell may additionally set `world_use_revision` to opt into
bounded world-use feedback (see cap/world_model/world_use_audit.py). Without that
key nothing in this module behaves differently, and the mechanism status it adds
is reported beside the task result: it never enters `errors`, charges no attempt,
and gates no trial, selection or finalization.

The world adapter puts the real simulator results under
output_root/run_name/seed_N/replay, not under the ordinary --args.output-dir, so
result parsing follows the directory the adapter actually used.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_world_fixloop_state import (ATTEMPT_LIMIT, NativeWorldState, ProtocolError,
                                       bundle_identity, code_hash, inventory_errors,
                                       valid_program, world_module_errors)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_freeze import verify_runtime, RuntimeChanged
from native_cc_trial_process import run_replay
from simple_world_profile import is_simple

WORLD_CONDITIONS = {"B", "C"}
CLEANUP_MARGIN_SECONDS = 60
TRIAL_RE_TEMPLATE =r"trial_{seed:02d}_sandboxrc_(\d+)_reward_([\d.]+)_taskcompleted_(\d+)"
JUDGMENT_PROFILE = "judgment"
# Must equal cap.world_model.judgment_world.MODE; replay_trial dispatches on it.
JUDGMENT_MODE = "opus46-judgment-world"
JUDGMENT_ARTIFACT_DIR = "judgment_world"
IN_PROCESS_PROFILES = frozenset({"simple", JUDGMENT_PROFILE})
NON_TRIAL_PHASES = {"snapshot", "diagnostic"}
# Phases whose recorded artifacts are an ordinary development trial of the pair.
TRIAL_PHASES = ("smoke", "initial", "repair")
_world_use_module = None


def is_judgment(case: dict) -> bool:
    """Judgment profile: policy plus a world the policy queries, no inventory."""
    return case.get("profile") == JUDGMENT_PROFILE


def load_world_use():
    """Load the use-feedback reader by location, not by package name.

    Located rather than imported so one protocol file works both in this
    engineering checkout and in a frozen cell tree, and loaded lazily so an
    unflagged A/B/C run never imports it and cannot be changed by it.
    """
    global _world_use_module
    if _world_use_module is None:
        path = Path(__file__).resolve().parents[2] / "cap/world_model/world_use_audit.py"
        spec = importlib.util.spec_from_file_location("aspire_world_use_audit", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _world_use_module = module
    return _world_use_module


def world_use(case: dict):
    """The opt-in use-feedback reader, or None for every unflagged case."""
    if not case.get("world_use_revision"):
        return None
    module = load_world_use()
    return module if module.enabled(case) else None


def in_process_world(case: dict) -> bool:
    """Both code-world profiles load the world inside the ordinary replay."""
    return case.get("profile") in IN_PROCESS_PROFILES


def adapter_world(case: dict, phase: str) -> bool:
    """True only for the legacy out-of-process native world adapter.

    The adapter relocates artifacts and needs a cleanup margin; the in-process
    profiles do neither, so every adapter-specific branch tests this predicate
    rather than re-deriving the condition/profile/phase combination.
    """
    return (case["condition"] in WORLD_CONDITIONS and not in_process_world(case)
            and phase not in NON_TRIAL_PHASES)


def load_case(path: Path) -> tuple[dict, Path, Path]:
    case = json.loads(path.read_text())
    repo = Path(case["sim"]).resolve()
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    return case, repo, task_dir


def identity(case: dict, repo: Path) -> dict:
    """The protocol identity a resume must reproduce exactly."""
    if case.get("executable_world_revision"):
        from executable_world_profile import validate
        validate(case)
    sources = "".join(Path(__file__).with_name(name).read_text() for name in
                      ("native_world_protocol.py", "native_world_fixloop_state.py"))
    if case.get("world_use_revision"):
        # A flagged case is refused before anything runs, and the revision becomes
        # part of the identity a resume must reproduce. Unflagged cases add no key,
        # so their recorded identity is byte-identical to before.
        problems = load_world_use().revision_errors(case)
        if problems:
            raise ProtocolError("; ".join(problems))
    return {
        "harness": "claude-code", "model": case["model"], "condition": case["condition"],
        "cell": case["id"], "suite": case["suite"], "task": case["task"],
        **({"profile": case["profile"]} if "profile" in case else {}),
        "dev_seeds": case["dev_seeds"], "attempt_limit": ATTEMPT_LIMIT,
        "max_steps": case["max_steps"], "trial_timeout": case["trial_timeout"],
        "context_tokens": case["context_tokens"],
        "max_output_tokens": case["max_output_tokens"], "effort": case["effort"],
        **({"world_use_revision": case["world_use_revision"]}
           if case.get("world_use_revision") else {}),
        **({"foundation_revision": case["foundation_revision"]}
           if case.get("foundation_revision") else {}),
        **({"executable_world_revision": case["executable_world_revision"], "c_arm": case["c_arm"]}
           if case.get("executable_world_revision") else {}),
        "world_interface": case["condition"] in WORLD_CONDITIONS,
        "strategy_md": case["condition"] in {"A", "B"},
        "config_sha256": code_hash((repo / case["env_config"]).read_text()),
        "protocol_sha256": code_hash(sources),
        **({"runtime_manifest_sha256": verify_runtime(case, repo)}
           if case.get("runtime_manifest") or case.get("require_runtime_freeze") else {}),
    }


def runtime_env(case: dict, repo: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """Strip credentials, then pin the reviewed device and service wiring."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if not re.search(
        r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|^HF_TOKEN$"
        r"|HUGGING_FACE_HUB_TOKEN|ANTHROPIC", k)}
    env.update(
        ASPIRE_ROOT=str(repo), PYTHON_ROOT=str(repo.parents[1]),
        PYTHONPATH=str(repo.parents[1]), MUJOCO_GL="egl",
        CUDA_VISIBLE_DEVICES=case["cuda_visible_devices"],
        MUJOCO_EGL_DEVICE_ID=str(case["egl_device_id"]),
        TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1",
        SAM3_SERVICE_URL=case["sam3_url"], GRASPNET_SERVICE_URL=case["graspnet_url"],
        PYROKI_SERVICE_URL=case["pyroki_url"],
    )
    if case.get("egl_vendor_config"):
        env["__EGL_VENDOR_LIBRARY_FILENAMES"] = case["egl_vendor_config"]
    env.pop("ASPIRE_SAM3_PROMPTS", None)
    return env


def check_policy(source: str) -> None:
    patterns = [r"env\.handle\.env\b", r"sim\.(?:data|model|forward)\b",
                r"\b(?:body_xpos|get_site_xpos|set_joint_qpos|_eval_predicate|obj_body_id"
                r"|parsed_problem|_step_once)\b"]
    if any(re.search(pattern, source) for pattern in patterns):
        raise ProtocolError("program references forbidden simulator ground-truth APIs")
    if re.search(r"\b(?:SERVICE_URL|DEFAULT_URL)\s*=|__globals__"
                 r"|\b(?:SAM3|GRASPNET|PYROKI)_SERVICE_URL\b", source):
        raise ProtocolError("program may not override frozen framework service wiring")


def authored_path(task_dir: Path, path: Path, label: str) -> Path:
    """Solver-authored inputs stay inside this cell's task directory."""
    resolved = path.resolve()
    if not resolved.is_relative_to(task_dir.resolve()) or not resolved.is_file():
        raise ProtocolError(f"{label} must be an existing file inside this cell's task directory")
    return resolved


def collect_bundle(case: dict, task_dir: Path, phase: str, code: Path | None,
                   world: Path | None, inventory: Path | None) -> tuple[dict, dict]:
    """Candidate identity covers policy plus, for B/C, world program and inventory."""
    if phase == "snapshot":
        source = (Path(case["sim"]) / "scripts/libero/scene_snapshot.py").read_text()
        return {"policy": code_hash(source)}, {"policy": str(Path(case["sim"]) / "scripts/libero/scene_snapshot.py")}
    if code is None:
        raise ProtocolError("--code is required")
    policy = authored_path(task_dir, code, "--code")
    source = policy.read_text()
    if not valid_program(source):
        raise ProtocolError("program must contain executable Python, not an empty file or prose")
    check_policy(source)
    bundle = {"policy": code_hash(source)}
    sources = {"policy": str(policy)}
    if case["condition"] in WORLD_CONDITIONS and phase != "diagnostic":
        if is_judgment(case):
            # Judgment trials are policy + world only. An inventory would
            # reintroduce the framework-supplied object identity this profile
            # deliberately drops, so passing one is a protocol error rather
            # than a silently ignored argument.
            if world is None or inventory is not None:
                raise ProtocolError("judgment world requires --world-program and no inventory")
            if case.get("executable_world_revision"):
                from aspire.sim.cap.world_model.executable_world import module_errors
            else:
                from aspire.sim.cap.world_model.judgment_world import module_errors
            world_path = authored_path(task_dir, world, "--world-program")
            world_source = world_path.read_text()
            problems = module_errors(world_source)
            if case.get("foundation_revision") == "r1":
                # A version declaration is an interface check, not a syntactic
                # judgment about whether a dynamic Python policy uses the world.
                import ast
                declarations = [n for n in ast.parse(world_source).body
                                if isinstance(n, ast.Assign) and any(
                                    isinstance(t, ast.Name) and t.id == "FOUNDATION_REVISION"
                                    for t in n.targets)]
                if not declarations or not isinstance(declarations[-1].value, ast.Constant) or declarations[-1].value.value != "r1":
                    problems.append('foundation world must declare FOUNDATION_REVISION = "r1"')
            check_policy(world_source)
            if problems:
                raise ProtocolError("; ".join(problems))
            bundle["world"] = code_hash(world_source)
            sources["world"] = str(world_path)
            return bundle, sources
        if is_simple(case):
            if world is None or inventory is not None:
                raise ProtocolError("simple world requires --world-program and no inventory")
            from aspire.sim.cap.world_model.simple_world import module_errors
            world_path = authored_path(task_dir, world, "--world-program")
            world_source = world_path.read_text()
            problems = module_errors(world_source)
            check_policy(world_source)
            if problems:
                raise ProtocolError("; ".join(problems))
            bundle["world"] = code_hash(world_source)
            sources["world"] = str(world_path)
            return bundle, sources
        if world is None or inventory is None:
            raise ProtocolError("world conditions require --world-program and --inventory")
        world_path = authored_path(task_dir, world, "--world-program")
        inventory_path = authored_path(task_dir, inventory, "--inventory")
        world_source = world_path.read_text()
        # A valid world module can be pure definitions, so the policy rule
        # "contains some call expression" would reject a correct one. Require its
        # four documented entry points instead. The inventory is validated here,
        # before any record is admitted, so bad JSON or bad roles can never leave
        # a permanently running ledger row.
        problems = world_module_errors(world_source)
        problems += inventory_errors(inventory_path.read_text())
        if problems:
            raise ProtocolError("; ".join(problems))
        check_policy(world_source)
        bundle["world"] = code_hash(world_source)
        bundle["inventory"] = code_hash(inventory_path.read_text())
        sources.update(world=str(world_path), inventory=str(inventory_path))
    elif world is not None or inventory is not None:
        raise ProtocolError("this condition has no world interface")
    return bundle, sources


def write_in_process_config(case: dict, directory: Path, sources: dict,
                            mode: str, profile: str, filename: str) -> Path:
    """Config for a world loaded inside the replay process: policy + world only.

    Both in-process profiles bind the trial by task gate and by the exact bytes
    of the two authored files, and neither carries an object inventory. The
    runtime recomputes both digests and refuses a mismatch, so these hashes are
    the frozen identity of the pair, not a description of it.
    """
    import hashlib
    world = directory / "world_program.py"
    world.write_bytes(Path(sources["world"]).read_bytes())
    config = {"mode": mode, "profile": profile,
              "task_gate": {"suite": case["suite"], "task": case["task"]},
              "world_program": world.name,
              "world_program_sha256": hashlib.sha256(world.read_bytes()).hexdigest(),
              "policy_sha256": hashlib.sha256((directory / "code.py").read_bytes()).hexdigest()}
    path = directory / filename
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def write_world_config(case: dict, directory: Path, sources: dict) -> Path:
    """Materialize this trial's uncapped native world config beside its inputs."""
    if case.get("executable_world_revision"):
        from aspire.sim.cap.world_model.executable_world import MODE
        path = write_in_process_config(case, directory, sources, MODE, JUDGMENT_PROFILE, "executable_world_config.json")
        config = json.loads(path.read_text())
        config["c_arm"] = case["c_arm"]
        path.write_text(json.dumps(config, indent=2) + "\n")
        return path
    if is_judgment(case):
        return write_in_process_config(case, directory, sources, JUDGMENT_MODE,
                                      JUDGMENT_PROFILE, "judgment_world_config.json")
    if is_simple(case):
        return write_in_process_config(case, directory, sources, "opus46-simple-world",
                                      "simple", "simple_world_config.json")
    world = directory / "world_program.py"
    inventory = directory / "inventory.json"
    world.write_bytes(Path(sources["world"]).read_bytes())
    inventory.write_bytes(Path(sources["inventory"]).read_bytes())
    entities = json.loads(inventory.read_text())
    manipulated = [e for e in entities if e["role"] == "manipulated"][0]
    config = {
        "schema_version": 7, "mode": "opus46-native-world-fix-loop",
        "task_gate": {"suite": case["suite"], "task": case["task"]},
        "world_program": "world_program.py",
        "world_program_sha256": code_hash(world.read_text()),
        "policy_sha256": code_hash((directory / "code.py").read_text()),
        "scene_inventory_path": "inventory.json",
        "scene_inventory_sha256": code_hash(inventory.read_text()),
        "observation": {"object_id": manipulated["id"],
                        "segmentation_prompt": manipulated["label"],
                        "min_score": case.get("segmentation_min_score", 0.5)},
        # The word, never a number: this mode is uncapped by construction.
        "query_budget": "unlimited", "max_actions": "unlimited", "max_recovery": "unlimited",
        "tolerance": case.get("world_tolerance", 0.03),
        "trial_timeout_seconds": case["trial_timeout"],
        "identity_binding": "unique_high_score_mask",
        "query_schedule": "close_reference_then_policy_verify",
        "output_root": str(directory / "world"), "run_name": "native_world",
        "model_provenance": {"model_id": case["model"], "policy_generator": "native-cc",
                             "world_program_generator": "native-cc",
                             "generation_context": case["generation_context"]},
    }
    path = directory / "native_config.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def world_evidence(results: Path) -> dict | None:
    """Separate an authored world failure from a real infrastructure failure.

    `run_native_world` raises whenever the child failed OR the evidence is
    incomplete, so a nonzero outer exit alone cannot tell the two apart. The
    child receipt says whether the world child actually ran to completion; the
    tape and manifest say whether the authored world program or inventory was
    what broke. An authored failure is charged model/program failure with usable
    feedback, and the loop continues.
    """
    receipt = read_json(results / "child_exit.json")
    if receipt is None:
        return None  # The child never got far enough to report: infrastructure.
    manifest = read_json(results / "live_manifest.json") or {}
    tape = results / "live_tape.jsonl"
    events = []
    if tape.is_file():
        for line in tape.read_text().splitlines():
            event = read_json_line(line)
            if event is not None:
                events.append(event)
    authored = [e for e in events if e.get("event") in {"program_error", "inconsistent_assimilation",
                                                        "invalid_prediction", "schema_error"}]
    if receipt.get("status") != "completed" and not authored:
        return None  # Interrupted, timed out or killed with no authored fault.
    if not authored and manifest.get("status") == "complete" and receipt.get("manifest_valid"):
        return None  # The world mechanism worked; any failure is elsewhere.
    return {
        "child_status": receipt.get("status"),
        "child_exit_code": receipt.get("exit_code"),
        "manifest_valid": receipt.get("manifest_valid"),
        "manifest_status": manifest.get("status"),
        "authored_errors": authored[:20],
        "authored_error_count": len(authored),
        "mechanism_evidence": manifest.get("mechanism_evidence"),
        "evidence_dir": str(results),
    }


def read_json_line(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def world_root(directory: Path, seed: int) -> Path:
    """The adapter's own trial directory, which holds its receipt and tape."""
    return directory / "world/native_world" / f"seed_{seed}"


def world_failure(case: dict, directory: Path, phase: str, seed: int) -> dict | None:
    """Distinguish an authored world failure from real infrastructure trouble.

    `run_native_world` raises whenever the child failed OR the evidence was
    incomplete, so mapping every nonzero outer exit to `infrastructure_error`
    would turn a fixable world program bug into a permanent blocker. The child
    receipt says whether the child itself ran to completion; the manifest and
    tape say what the authored world code did. An ordinarily exited child may
    be named `completed` or `nonzero`; positive program-error evidence and a
    readable manifest are still required. Signals and watchdogs stay infra.
    """
    if case["condition"] not in WORLD_CONDITIONS or phase in NON_TRIAL_PHASES:
        return None
    if in_process_world(case):
        # Both in-process profiles write one manifest in the replay's own output
        # tree. `program_error` with recorded errors is the authored-fault signal
        # the runtime already produces; anything else stays infrastructure.
        artifacts = JUDGMENT_ARTIFACT_DIR if is_judgment(case) else "simple_world"
        manifest = read_json(directory / artifacts / "manifest.json") or {}
        return manifest if manifest.get("status") == "program_error" and manifest.get("errors") else None
    root = world_root(directory, seed)
    try:
        receipt = json.loads((root / "child_exit.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None  # No receipt at all: the mechanism never got that far.
    status, exit_code = receipt.get("status"), receipt.get("exit_code")
    if status not in {"completed", "nonzero"}:
        return None  # The child was killed, interrupted or timed out.
    if isinstance(exit_code, int) and exit_code < 0:
        return None  # A signal is infrastructure even with an earlier tape error.
    if status == "nonzero" and (type(exit_code) is not int or exit_code <= 0):
        return None  # Require a real ordinary nonzero exit, not a partial receipt.
    detail = {"child_status": receipt.get("status"),
              "child_exit_code": receipt.get("exit_code"),
              "manifest_valid": receipt.get("manifest_valid"),
              "world_trial_dir": str(root)}
    try:
        manifest = json.loads((root / "live_manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    detail["manifest_status"] = manifest.get("status")
    detail["manifest_error"] = manifest.get("error")
    detail["mechanism_evidence"] = manifest.get("mechanism_evidence")
    tape = root / "live_tape.jsonl"
    if tape.is_file():
        # These are the names the brokers actually emit: `program_error` carries
        # `operation` and `error`; the other three record an authored schedule or
        # identity mistake. Feedback the solver can act on comes from here.
        authored = {"program_error", "measurement_identity_mismatch",
                    "reference_request_off_schedule", "relation_check_not_requested"}
        errors = []
        for line in tape.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") in authored:
                errors.append({k: event.get(k) for k in
                               ("event", "operation", "error", "reason", "frame_id",
                                "prediction_frame", "action")
                               if event.get(k) is not None})
        detail["world_program_events"] = errors[-10:]
        detail["world_program_error_count"] = len(errors)
    detail.setdefault("reason", manifest.get("error")
                      or "the authored world program or its evidence was rejected")
    return detail if any(e["event"] == "program_error"
                         for e in detail.get("world_program_events", [])) else None


def result_root(case: dict, directory: Path, phase: str, seed: int) -> Path:
    """Where the real trial artifacts landed, per condition.

    Only the legacy adapter relocates results to output_root/run_name/seed_N/replay.
    Reading args.output-dir there would find nothing and mislabel a successful
    world execution as an infrastructure error. The in-process profiles run in the
    ordinary replay, so their artifacts stay under `results` like condition A.
    """
    if adapter_world(case, phase):
        return directory / "world/native_world" / f"seed_{seed}" / "replay"
    return directory / "results"


def run_trial(case: dict, repo: Path, state: NativeWorldState, phase: str, seed: int,
              code: Path | None, world: Path | None, inventory: Path | None,
              stdin_code: Path | None) -> dict:
    with (state.task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("another trial is running; await its result") from exc
        state.data = json.loads(state.path.read_text())  # No stale ledger under the lock.
        return _run_trial(case, repo, state, phase, seed, code, world, inventory, stdin_code)


def _run_trial(case: dict, repo: Path, state: NativeWorldState, phase: str, seed: int,
               code: Path | None, world: Path | None, inventory: Path | None,
               stdin_code: Path | None) -> dict:
    verify_runtime(case, repo)
    if 1 <= seed <= 50:
        raise ProtocolError("seeds 1–50 are held out; the outer coordinator evaluates them")
    try:
        bundle, sources = collect_bundle(case, state.task_dir, phase, code, world, inventory)
    except ProtocolError as exc:
        # An invalid generated revision is recorded, and charges nothing: no
        # simulator process started. It stays separate from executed failures.
        state.reject(phase, seed, str(exc), {"code": str(code) if code else None,
                                             "world": str(world) if world else None,
                                             "inventory": str(inventory) if inventory else None})
        raise
    if phase == "diagnostic":
        if stdin_code is None:
            raise ProtocolError("--code holds the authored diagnostic program for the batch REPL")
        stdin_code = authored_path(state.task_dir, stdin_code, "--code")
    screening = None
    if case.get("executable_world_revision") and phase not in NON_TRIAL_PHASES:
        from executable_world_profile import checks
        screening = checks(case, repo, state, sources, runtime_env(case, repo))
        if screening["status"] == "rejected":
            # An authored Python error in the supported part of the screening.
            # That IS the candidate's fault, so it is refused before any
            # simulator runs and, like any refused revision, charges nothing.
            reason = "offline candidate error; inspect " + screening["directory"]
            state.reject(phase, seed, reason, sources)
            raise ProtocolError(reason)
        if screening["status"] == "blocked":
            # The screening itself failed — a watchdog kill or a missing report.
            # Nothing was learned about the candidate, so this must stop here:
            # before begin_trial, before any simulator invocation, consuming zero
            # new attempts. Attaching `blocked` to a charged row instead would
            # make the infrastructure's failure cost a real attempt. Returned as
            # a structured record rather than raised, so the blocker is machine
            # readable and the same candidate can be retried unchanged.
            reason = ("offline screening could not run; this is infrastructure, not "
                      "the candidate. Retry the same unchanged candidate once it "
                      "recovers. No attempt was consumed and none should be spent "
                      "to work around it; inspect " + str(screening.get("directory")))
            detail = {k: screening.get(k) for k in
                      ("status", "conclusions", "retryable", "cached", "directory",
                       # Immutable candidate digests (policy/world/tape), so the
                       # blocker names WHAT was blocked and not just which mutable
                       # paths were passed to the screening at the time.
                       "identity", "reports")}
            blocker = state.record_blocker(phase, seed, reason, sources, detail, bundle=bundle)
            return {**blocker, "status": "screening_blocked", "screening": detail,
                    "attempts_used": state.attempts_used(seed),
                    "attempts_remaining": state.budget_remaining(seed)}
    record = state.begin_trial(phase, seed, bundle, sources)
    if screening is not None:
        # The screening outcome travels with the attempt it preceded. A `blocked`
        # screening cannot reach here — it returned above, before begin_trial —
        # so a charged row always carries a `checked` screening, and the earlier
        # blocker stays its own uncharged record that this admission resolves.
        record["screening"] = {k: screening.get(k) for k in
                               ("status", "conclusions", "retryable", "cached", "directory")}
    directory = state.task_dir / record["directory"]
    (directory / "code.py").write_bytes(Path(sources["policy"]).read_bytes())
    env = runtime_env(case, repo)
    env["SNAPSHOT_DIR"] = str(state.task_dir)
    env["ASPIRE_MAX_STEPS"] = str(case["max_steps"])
    results = result_root(case, directory, phase, seed)
    command = [str(repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"],
               "--args.trial", str(seed), "--args.model", case["model"],
               "--args.config", case["env_config"],
               "--args.output-dir", str(directory / "results")]
    if phase == "diagnostic":
        # Identical API execution semantics, authored code read from a file, one
        # session charged once. Inspection is never silently prohibited.
        command += ["--args.interactive"]
    else:
        command += ["--args.replay-code", str(directory / "code.py")]
    if case["condition"] in WORLD_CONDITIONS and phase not in {"snapshot", "diagnostic"}:
        command += ["--args.world-model-config", str(write_world_config(case, directory, sources))]
    (directory / "command.json").write_text(json.dumps(
        {"command": command, "stdin": str(stdin_code) if stdin_code else None,
         "result_root": str(results)}, indent=2))
    # The world child keeps the real 900 s watchdog. The outer wrapper gets a
    # short cleanup margin on top, because killing it on the same deadline can
    # interrupt child_exit.json persistence. This is grace for cleanup, not
    # extra robot execution budget.
    grace = CLEANUP_MARGIN_SECONDS if adapter_world(case, phase) else 0
    exit_code, error = run_replay(command, repo=repo, env=env, directory=directory,
                                  timeout=case["trial_timeout"] + grace,
                                  stdin_path=stdin_code if phase == "diagnostic" else None)
    result = parse_result(results, seed)
    if phase == "snapshot" and (not result or result["sandbox_rc"] or not all(
            (state.task_dir / n).is_file() for n in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"))):
        result, error = None, "snapshot did not produce both camera images; inspect replay.log"
    diagnostic_error = None
    if phase == "diagnostic":
        # A REPL session grades nothing, so `exit_code == 0` says only that the
        # process ended: the REPL exits 0 whether the authored code ran or every
        # statement raised. The session artifact the console writes is therefore
        # the ONLY classifier, and it is read unconditionally — a `trial_*`
        # directory that happens to sit beside it is not a graded outcome, and
        # reading it as one let a session whose statements all raised be recorded
        # as complete with whatever reward that stray artifact carried.
        session = read_json(results / "diagnostic_session.json")
        observed = (result or {}).get("trial_dir", record["directory"])
        if not isinstance(session, dict) or not isinstance(session.get("errors"), list) \
                or not isinstance(session.get("error_count"), int):
            # Missing or truncated/malformed: the session never reached its own
            # end, so nothing is known about the authored code. Infrastructure.
            result = None
            error = error or ("diagnostic session produced no usable artifact; the "
                              "REPL did not reach its own end")
        elif session["errors"]:
            result = None
            diagnostic_error = {k: session.get(k) for k in
                                ("statements", "error_count", "errors")}
            diagnostic_error["session_dir"] = str(results)
        else:
            # Charged inspection that reached its own end. Never a score: the
            # outcome fields are fixed here rather than taken from any artifact.
            result = ({"sandbox_rc": 0, "reward": 0.0, "task_completed": 0,
                       "trial_dir": observed, "session": "diagnostic"}
                      if exit_code == 0 else None)
    try:
        verify_runtime(case, repo)
    except RuntimeChanged as exc:
        error = str(exc)
    if error:
        result = None
    world_error = None
    if not error and exit_code not in (None, 0):
        # A broken authored world is a charged program failure with feedback, and
        # the raw simulator outcome is preserved separately so a rejected world can
        # never be read back as a valid B/C success.
        world_error = world_failure(case, directory, phase, seed)
    module = world_use(case)
    if module is not None and phase in TRIAL_PHASES:
        # Evidence about the mechanism, read from the policy this trial actually
        # ran and that trial's own recorded query trace. It is attached to the
        # trial record the solver already reads, writes its full audit beside the
        # other artifacts, and is never consulted by `status`, so a missing or
        # inconclusive mechanism costs no attempt and blocks nothing.
        record["world_use"] = module.trial_feedback(directory)
    state.finish_trial(record, result=result, exit_code=exit_code, error=error,
                       world_error=world_error, diagnostic_error=diagnostic_error,
                       raw_result=parse_result(results, seed) if world_error else None)
    if case.get("foundation_revision") == "r1" and phase in TRIAL_PHASES:
        from aspire.sim.cap.world_model.foundation_audit import trial_feedback
        record["foundation_calibration"] = trial_feedback(directory, record)
        state.save()
    return record


def parse_result(results: Path, seed: int) -> dict | None:
    pattern = re.compile(TRIAL_RE_TEMPLATE.format(seed=seed))
    matches = [(p, pattern.fullmatch(p.name)) for p in results.rglob("trial_*") if p.is_dir()]
    matches = [(p, m) for p, m in matches if m]
    if len(matches) != 1:
        return None
    path, match = matches[0]
    return {"sandbox_rc": int(match[1]), "reward": float(match[2]),
            "task_completed": int(match[3]), "trial_dir": str(path)}


def selected_bundle(case: dict, state: NativeWorldState) -> dict:
    """The bundle the cell froze: fix_code.py plus, for B/C, world and inventory."""
    bundle = {"policy": code_hash((state.task_dir / "fix_code.py").read_text())}
    if case["condition"] in WORLD_CONDITIONS:
        # The selected policy and its matching world are one identity, so the pair
        # is hashed together and `select` can only accept a pair that ran.
        bundle["world"] = code_hash((state.task_dir / "fix_world_program.py").read_text())
        if not in_process_world(case):
            bundle["inventory"] = code_hash((state.task_dir / "fix_inventory.json").read_text())
    return bundle


def check(case: dict, repo: Path, state: NativeWorldState) -> dict:
    working = repo / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"
    progress = state.progress()
    fix = state.task_dir / "fix_code.py"
    if not fix.is_file():
        return {"ready": False, "errors": ["missing executable fix_code.py"], "progress": progress}
    try:
        bundle = selected_bundle(case, state)
    except OSError as exc:
        return {"ready": False, "errors": [f"selected bundle is incomplete: {exc}"],
                "progress": progress}
    errors = state.completion_errors(bundle=bundle, working_code=working,
                                     world_required=case["condition"] in WORLD_CONDITIONS)
    selection = state.data.get("selected")
    if not selection or not selection.get("reason", "").strip():
        errors.append("record the selection reason with `select` so coverage stays reviewable")
    elif selection["bundle_sha256"] != bundle_identity(bundle):
        errors.append("the recorded selection does not match the frozen files on disk")
    status = {"ready": not errors, "errors": errors, "progress": progress,
              "selected": selection, "coverage": state.final_coverage(bundle)}
    if case.get("foundation_revision") == "r1":
        from aspire.sim.cap.world_model.foundation_audit import aggregate
        status["foundation_calibration"] = aggregate(state.data["trials"])
    module = world_use(case)
    if module is not None:
        # The selected pair's own executed development trials, audited again as a
        # pair. Deliberately outside `errors` and outside `ready`: an unused or
        # unreadable mechanism is a finding to report with the task result, not an
        # incomplete cell, and there is no repair loop until it passes.
        status["world_use"] = module.selected_pair_report(
            state.task_dir / row["directory"] for row in status["coverage"])
    return status


def finalize(case: dict, repo: Path, state: NativeWorldState, transcripts: list[Path]) -> dict:
    verify_runtime(case, repo)
    status = check(case, repo, state)
    errors = list(status["errors"])
    served, usage, contexts = {}, {"input_tokens": 0, "output_tokens": 0}, set()
    output_limits = set()
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
                for entry in (record.get("modelUsage") or {}).values():
                    if isinstance(entry, dict) and entry.get("contextWindow"):
                        contexts.add(entry["contextWindow"])
                        output_limits.add(entry.get("maxOutputTokens"))
    if set(served) != {case["expected_served_model"]}:
        errors.append(f"native model provenance mismatch: {served}")
    # Claude Code's modelUsage carries the built-in default for a custom Qwen
    # alias, not the request's configured max_tokens. The local request setting
    # is checked in the native probe before the worker starts.
    output_mismatch = (output_limits != {case["max_output_tokens"]}
                       and case.get("model_provider") != "local-vllm")
    if in_process_world(case) and (contexts != {case["context_tokens"]} or output_mismatch):
        errors.append(f"native capacity mismatch: contexts={contexts}, outputs={output_limits}")
    if errors:
        raise ProtocolError("; ".join(errors))
    bundle = selected_bundle(case, state)
    state.data.update(stage1_complete=True, model_served=served, usage=usage,
                      model_context_windows=sorted(contexts))
    state.save()
    result = {
        "schema_version": 1, "harness": "claude-code", "cell": case["id"],
        "condition": case["condition"], "stage1_complete": True,
        "suite": case["suite"], "task": case["task"],
        "selected_bundle": bundle, "selection": state.data["selected"],
        "observation_fallback": state.observation_fallback(
            bundle, (state.task_dir / "fix_code.py").read_text()),
        "model_served": served, "usage": usage,
        # Actual served context window, not only the configured environment.
        "model_context_windows": sorted(contexts),
        "attempt_limit": ATTEMPT_LIMIT,
        "attempts_used_per_seed": status["progress"]["attempts_used_per_seed"],
        "seed_outcomes": status["progress"]["seeds"],
        "tested_bundles": status["progress"]["tested_bundles"],
        "rejected_revisions": state.data["rejected"], "aliases": state.data["aliases"],
        "world_program_errors": status["progress"]["world_program_errors"],
        # Authored REPL failures and uncharged pre-admission blockers, reported
        # separately from graded evidence so neither can be read as a result.
        "diagnostic_program_errors": status["progress"]["diagnostic_program_errors"],
        "screening_blockers": status["progress"]["screening_blockers"],
        "unresolved_screening_blockers": status["progress"]["unresolved_screening_blockers"],
        "graded_executions": status["progress"]["graded_executions"],
        "final_development_coverage": state.final_coverage(bundle),
        # Real simulator invocations. An alias row references a run already
        # counted here, so including it would report a replay that never ran.
        "replay_invocations": status["progress"]["replay_invocations"],
        "aliased_evidence_rows": len(state.data["aliases"]),
        "transcripts": [str(p) for p in transcripts],
    }
    if "world_use" in status:
        # Reported with the accounted outcome, after the protocol errors above have
        # already been allowed to pass. A cell whose mechanism stayed unused or
        # inconclusive still finalizes, and says so.
        result["world_use"] = status["world_use"]
    if "foundation_calibration" in status:
        result["foundation_calibration"] = status["foundation_calibration"]
    (state.task_dir / "stage1_result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"))
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("init", "status", "check"):
        sub.add_parser(name)
    trial = sub.add_parser("trial")
    trial.add_argument("--phase", choices=["snapshot", "smoke", "initial", "repair", "diagnostic"],
                       required=True)
    trial.add_argument("--seed", type=int, required=True)
    trial.add_argument("--code", type=Path)
    trial.add_argument("--world-program", type=Path)
    trial.add_argument("--inventory", type=Path)
    alias = sub.add_parser("alias-smoke")
    alias.add_argument("--seed", type=int, required=True)
    select = sub.add_parser("select")
    select.add_argument("--reason", required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--transcript", type=Path, action="append", required=True)
    args = parser.parse_args()
    if not args.case:
        parser.error("--case or ASPIRE_NATIVE_CASE is required")
    try:
        case, repo, task_dir = load_case(Path(args.case))
        state = NativeWorldState(task_dir, identity(case, repo), resume=args.action != "init")
        if args.action == "trial":
            result = run_trial(case, repo, state, args.phase, args.seed, args.code,
                               args.world_program, args.inventory, args.code)
        elif args.action == "alias-smoke":
            result = state.alias_smoke_as_initial(args.seed)
        elif args.action == "select":
            bundle = selected_bundle(case, state)
            errors = state.selection_errors(
                bundle, (state.task_dir / "fix_code.py").read_text())
            if errors:
                raise ProtocolError("; ".join(errors))
            result = state.select(bundle, args.reason)
        elif args.action == "finalize":
            result = finalize(case, repo, state, args.transcript)
        elif args.action == "check":
            result = check(case, repo, state)
        else:
            result = state.progress()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int((args.action == "check" and not result["ready"])
                   or (args.action == "trial" and result["status"] != "complete"))
    except (ProtocolError, RuntimeChanged, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
