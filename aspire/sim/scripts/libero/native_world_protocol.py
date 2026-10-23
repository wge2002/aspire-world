#!/usr/bin/env python3
"""Recorded simulator calls for the native original Fix Loop (A1/B1/C1).

This module has no LLM client, agent loop, or context management: native Claude
Code owns generation, tools, notifications, and compaction. It records what the
solver actually executed, counts each admitted task replay as one of that seed's three
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
is reported beside the task result: it never enters `errors`, spends no retry,
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
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_world_fixloop_state import (BUDGET_REVISION, PROBE_PHASE, PROBE_VIOLATION, RETRY_LIMIT,
                                       RESWEEP_PHASE, SWEEP_PHASE, NativeWorldState, ProtocolError,
                                       bundle_identity, code_hash, inventory_errors,
                                       valid_program, world_module_errors)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_freeze import verify_runtime, RuntimeChanged
from native_cc_trial_process import ReplayBatch, parallel_env, run_replay
from simple_world_profile import is_simple
import method_switches

WORLD_CONDITIONS = {"B", "C"}
CLEANUP_MARGIN_SECONDS = 60
TRIAL_RE_TEMPLATE =r"trial_{seed:02d}_sandboxrc_(\d+)_reward_([\d.]+)_taskcompleted_(\d+)"
JUDGMENT_PROFILE = "judgment"
# Must equal cap.world_model.judgment_world.MODE; replay_trial dispatches on it.
JUDGMENT_MODE = "opus46-judgment-world"
JUDGMENT_ARTIFACT_DIR = "judgment_world"
IN_PROCESS_PROFILES = frozenset({"simple", JUDGMENT_PROFILE})
NON_TRIAL_PHASES = {"snapshot", "diagnostic", PROBE_PHASE}
# Phases whose recorded artifacts are an ordinary development trial of the pair.
TRIAL_PHASES = ("smoke", "initial", "repair", "sweep")
#: CC_TASK_14: trial phases plus the outer pre-freeze re-sweep, which grades its
#: candidate on a development seed exactly like a sweep (watchdog, world use,
#: calibration) but is never screened, searched or offered to the solver.
GRADED_PHASES = (*TRIAL_PHASES, RESWEEP_PHASE)
#: A sealed trial's public directory carries the sandbox exit code and nothing else.
SEALED_TRIAL_RE_TEMPLATE = r"trial_{seed:02d}_sandboxrc_(\d+)_sealed"
_world_use_module = None
#: Prediction contracts that run the p2 runtime template. p3-search adds the
#: search-control hooks below; every other contract never reaches them.
RUNTIME_CONTRACTS = ("p2-runtime", "p3-search")
SEARCH_CONTRACT = "p3-search"
# CC_TASK_4: issue codes the generic terminal guard never treats as terminal.
# The solver can always clear them by writing files or by `select` (reports,
# BLOCKED notes, the working copy, pointing fix_code.py at an already tested
# bundle, the selection record), or by the snapshot that spends no retry; an
# infrastructure row is the driver's infrastructure path, not a protocol dead end.
AUTHORABLE_ISSUES = frozenset({"report", "findings_format", "blocked_notes_required", "selection_unrecorded",
                               "selection_mismatch", "selection_untested",
                               "selection_without_success", "snapshot_missing",
                               "infrastructure_errors",
                               # CC_TASK_6 (aspire-v1): cleared by `select` or a note file.
                               "selection_not_phase_b", "selection_not_best",
                               "blocked_mode_notes_required"})
#: CC_TASK_6: the replay's observation-only REPL switch (scripts/libero/replay_trial.py).
OBSERVATION_ONLY_ENV = "ASPIRE_OBSERVATION_ONLY"
#: Motor commands of the public API. A probe may not name them, and the replay's
#: observation-only REPL does not offer them (or anything outside its allow-list).
ACTION_FUNCTIONS = frozenset({"move_to_joints", "open_gripper", "close_gripper", "goto_pose",
                              "goto_home_joint_position", "execute_joint_trajectory",
                              "plan_grasp_trajectory", "plan_with_grasped_object"})
#: Names a probe may not use: ways to reach the environment or the API objects
#: behind the observation functions instead of calling them.
PROBE_FORBIDDEN_NAMES = frozenset({"step", "getattr", "setattr", "delattr", "vars", "globals", "locals",
                                   "eval", "exec", "compile", "__import__", "trace_logger"})
PROBE_FORBIDDEN_MODULES = frozenset({"sys", "gc", "inspect", "ctypes", "importlib", "builtins", "types",
                                     "subprocess", "os"})
SEARCH_REPORT = "search_control_report.json"
TRIAGE_REPORT = "coverage_triage.json"


def gate_module():
    """The opt-in development gate, imported only where a case names one."""
    from aspire.sim.cap.world_model import development_gate
    return development_gate


def sealed_gate(case: dict) -> bool:
    """True only for a cell whose development outcome is sealed from the solver."""
    return bool(case.get("development_gate")) and case["development_gate"] != "oracle"


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
    if not case.get("world_use_revision") and case.get("prediction_contract") not in RUNTIME_CONTRACTS:
        return None
    module = load_world_use()
    return module if module.enabled(case) or case.get("prediction_contract") in RUNTIME_CONTRACTS else None


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
    if (case.get("executable_world_revision") or case.get("closed_loop_revision")
            or case.get("prediction_contract")):
        from executable_world_profile import validate
        validate(case)
    if case.get("development_gate"):
        # Refused before anything runs, and part of the identity a resume must
        # reproduce: an oracle cell can never be resumed as a sealed one.
        gate_module().validate(case)
    sources = "".join(Path(__file__).with_name(name).read_text() for name in
                      ("native_world_protocol.py", "native_world_fixloop_state.py"))
    problems = method_switches.errors(case)
    if problems:
        raise ProtocolError("; ".join(problems))
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
        "dev_seeds": case["dev_seeds"], "retry_limit": RETRY_LIMIT,
        "max_steps": case["max_steps"], "trial_timeout": case["trial_timeout"],
        "context_tokens": case["context_tokens"],
        "max_output_tokens": case["max_output_tokens"], "effort": case["effort"],
        **({"world_use_revision": case["world_use_revision"]}
           if case.get("world_use_revision") else {}),
        **({"foundation_revision": case["foundation_revision"]}
           if case.get("foundation_revision") else {}),
        **({"executable_world_revision": case["executable_world_revision"], "c_arm": case["c_arm"]}
           if case.get("executable_world_revision") else {}),
        # Added only when set: an unflagged cell's identity is byte-identical,
        # and a resume cannot silently switch an observational study online.
        **({"closed_loop_revision": case["closed_loop_revision"]}
           if case.get("closed_loop_revision") else {}),
        **({"development_gate": case["development_gate"]}
           if case.get("development_gate") else {}),
        # "off" is the absent contract; active revisions add a pinned key.
        **({"prediction_contract": case["prediction_contract"]}
           if case.get("prediction_contract") in ("p1", *RUNTIME_CONTRACTS) else {}),
        # CC_TASK_6: added only when set, so every earlier cell's identity is
        # byte-identical and a resume cannot switch a cell onto another budget.
        **({"development_budget": budget_revision(case)} if case.get("development_budget") else {}),
        # CC_TASK_14: the method switches, only when set (legacy identities unchanged).
        **({method_switches.KEY: case[method_switches.KEY]} if method_switches.KEY in case else {}),
        "world_interface": case["condition"] in WORLD_CONDITIONS,
        "strategy_md": case["condition"] in {"A", "B"},
        "config_sha256": code_hash((repo / case["env_config"]).read_text()),
        "protocol_sha256": code_hash(sources),
        **({"runtime_manifest_sha256": verify_runtime(case, repo)}
           if case.get("runtime_manifest") or case.get("require_runtime_freeze") else {}),
    }


def budget_revision(case: dict) -> str | None:
    """The case's development budget (CC_TASK_6), refused unless it is aspire-v1 on p3-search."""
    value = case.get("development_budget")
    if value is None:
        return None
    if value != BUDGET_REVISION:
        raise ProtocolError(f"unknown development_budget {value!r}")
    if case.get("prediction_contract") != SEARCH_CONTRACT:
        raise ProtocolError("the aspire-v1 development budget runs on the p3-search contract "
                            "(its versions are covered by the coverage sweep)")
    return value


def probe_source_errors(source: str) -> list[str]:
    """Why an authored probe is not observation-only, before anything runs.

    The replay's observation-only REPL is the enforcement: it offers no motor
    command, no `step`, no environment beyond the task language. This static
    check refuses a program that names an action anyway, or reaches for the
    objects behind the observation functions, so the refusal costs nothing.
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"probe does not parse: {exc}"]
    problems = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ACTION_FUNCTIONS:
            problems.add(f"probe is observation-only: it may not call {node.id}()")
        elif isinstance(node, ast.Name) and node.id in PROBE_FORBIDDEN_NAMES:
            problems.add(f"probe may not use {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in ACTION_FUNCTIONS:
                problems.add(f"probe is observation-only: it may not call {node.attr}()")
            if node.attr.startswith("__"):
                problems.add(f"probe may not access dunder attribute {node.attr}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""])
            for name in names:
                if name.split(".")[0] in PROBE_FORBIDDEN_MODULES:
                    problems.add(f"probe may not import {name}")
    for node in ast.walk(tree):
        # env.handle.task_language is the only environment read a probe may make.
        if isinstance(node, ast.Name) and node.id == "env" and not _task_language(tree, node):
            problems.add("probe may read only env.handle.task_language from env")
    return sorted(problems)


def _task_language(tree, name_node) -> bool:
    import ast
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "task_language"
                and isinstance(node.value, ast.Attribute) and node.value.attr == "handle"
                and node.value.value is name_node):
            return True
    return False


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
    for key in method_switches.ENV_KEYS:
        env.pop(key, None)
    env.update(method_switches.api_env(case))  # CC_TASK_14 M4: only when the case sets it.
    return env


def parallel_settings(case: dict, kind: str) -> tuple[int, int]:
    """(replays at once, CPU threads per replay) of the held-out or batch sweep (CC_TASK_7).

    Absent keys are (1, default): the serial sweep, with no thread cap applied.
    """
    from executable_world_profile import DEFAULT_TRIAL_THREADS, parallel_errors
    problems = parallel_errors(case)
    if problems:
        raise ProtocolError("; ".join(problems))
    return case.get(f"{kind}_parallel", 1), case.get("trial_threads", DEFAULT_TRIAL_THREADS)


def check_policy(source: str, *, sealed: bool = False) -> None:
    if sealed:
        # A sealed cell also refuses every route from authored code to the task
        # label: the env object's success/reward methods, the APIS mapping, bound
        # method owners and any handle attribute other than the task language.
        problems = gate_module().source_errors(source)
        if problems:
            raise ProtocolError("; ".join(problems))
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
    check_policy(source, sealed=sealed_gate(case))
    bundle = {"policy": code_hash(source)}
    sources = {"policy": str(policy)}
    if case["condition"] in WORLD_CONDITIONS and phase not in ("diagnostic", PROBE_PHASE):
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
            if case.get("prediction_contract") in RUNTIME_CONTRACTS:
                import ast
                declarations = [n for n in ast.parse(world_source).body
                                if isinstance(n, ast.Assign) and any(
                                    isinstance(t, ast.Name) and t.id == "STATE_TEMPLATE_REVISION"
                                    for t in n.targets)]
                if not declarations or not isinstance(declarations[-1].value, ast.Constant) or declarations[-1].value.value != "p2-runtime":
                    problems.append(f'{case["prediction_contract"]} world must declare STATE_TEMPLATE_REVISION = "p2-runtime"')
            if case.get("prediction_contract") == SEARCH_CONTRACT:
                from aspire.sim.cap.world_model.search_control import contract_errors
                problems += contract_errors(source, world_source)
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
            check_policy(world_source, sealed=sealed_gate(case))
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
            check_policy(world_source, sealed=sealed_gate(case))
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
        check_policy(world_source, sealed=sealed_gate(case))
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
        if sealed_gate(case):
            # Provenance only: the sealing itself is the replay's environment
            # variable, never a value generated code could read from this file.
            config["development_gate"] = case["development_gate"]
        if case.get("closed_loop_revision"):
            # The frozen trial config carries the revision, so development and
            # held-out replays run the same online semantics. Absent otherwise.
            config["closed_loop"] = True
            config["closed_loop_revision"] = case["closed_loop_revision"]
        if case.get("prediction_contract") in ("p1", *RUNTIME_CONTRACTS):
            # Read back by run_executable_world, so development and held-out
            # replays score predictions identically. Absent when off.
            config["prediction_contract"] = case["prediction_contract"]
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
    what broke. An authored failure is a retry-spending model/program failure with usable
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


#: CC_TASK_12 G6: a watchdog-killed policy whose last public API call came at most
#: this many seconds before the trial timeout was still executing when its budget
#: ran out. The nine real overruns of p5/r3/ns made their last call 21-28 s before
#: the kill; a replay quiet for longer than this stalled (a hung service call, a
#: frozen simulator), which stays infrastructure.
POLICY_ACTIVE_WINDOW = 120


def last_api_seconds(directory: Path) -> float | None:
    """`elapsed_seconds` of the last public API event the trial's world tape recorded."""
    tape = directory / JUDGMENT_ARTIFACT_DIR / "events.jsonl"
    try:
        with tape.open("rb") as handle:
            handle.seek(max(0, tape.stat().st_size - 262144))
            lines = handle.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        event = read_json_line(line.decode(errors="replace"))
        if isinstance(event, dict) and isinstance(event.get("elapsed_seconds"), (int, float)):
            return float(event["elapsed_seconds"])
    return None


def watchdog_verdict(case: dict, directory: Path, results: Path, phase: str, seed: int,
                     error: str) -> dict | None:
    """What a replay the trial watchdog killed shows about why (CC_TASK_12 G6).

    None unless `error` is the watchdog's TimeoutExpired on a graded trial phase of
    an oracle-graded cell. Read from the trial's own preserved evidence only:

    * `persistence_cut` (harness): the replay had already written its trial
      directory, so the policy finished inside the budget and its outcome is the
      one recorded there; the kill came while artifacts were being saved. The row
      is that outcome; keyframes/video may be incomplete.
    * `policy_timeout` (policy): no outcome yet, and the policy was still issuing
      public API calls within POLICY_ACTIVE_WINDOW of the budget. A failed trial of
      this candidate on this seed (sandbox_rc 1, not completed), as ASPIRE's own
      runner records a timed-out trial.
    * `stalled` / `unknown` (infrastructure): no API activity near the end, or no
      tape to judge from. Unchanged: an infrastructure row for close-infra.
    """
    if (not str(error).startswith("TimeoutExpired") or phase not in (*GRADED_PHASES, "heldout")
            or sealed_gate(case)):
        return None
    life = read_json(directory / "lifecycle.json") or {}
    started, finished = life.get("started_at"), life.get("finished_at")
    timeout = case["trial_timeout"]
    verdict = {"error": str(error)[:400], "trial_timeout_seconds": timeout,
               "persistence_grace": life.get("persistence_grace"),
               "killed_after_seconds": (round(finished - started, 1)
                                        if isinstance(started, (int, float)) and
                                        isinstance(finished, (int, float)) else None)}
    recorded = parse_result(results, seed)
    summary = Path(recorded["trial_dir"]) / "summary.txt" if recorded is not None else None
    if recorded is not None and not summary.is_file():
        # A trial directory the replay never finished writing: nothing to settle from.
        return {**verdict, "kind": "unknown", "attribution": "infrastructure", "settled": False,
                "note": "a trial directory without its summary; infrastructure evidence for close-infra"}
    if recorded is not None:
        end = (round(summary.stat().st_mtime - started, 1)
               if isinstance(started, (int, float)) else None)
        if end is not None and not 0 < end <= (verdict["killed_after_seconds"] or end):
            end = None  # A copied or touched file: its time says nothing about this run.
        return {**verdict, "kind": "persistence_cut", "attribution": "harness", "settled": True,
                "policy_end_seconds": end, "result": recorded,
                "note": (f"the policy finished{f' at {end} s' if end is not None else ''}, inside the "
                         f"{timeout} s trial budget; the replay was stopped while saving its artifacts "
                         "(keyframes / video / world record may be incomplete). The row is the outcome "
                         "the replay had already recorded; nothing was re-run or refunded.")}
    last = last_api_seconds(directory)
    verdict["last_api_seconds"] = round(last, 1) if last is not None else None
    if last is not None and last >= timeout - POLICY_ACTIVE_WINDOW:
        return {**verdict, "kind": "policy_timeout", "attribution": "policy", "settled": True,
                "note": (f"the policy was still executing (last public API call at {round(last, 1)} s) "
                         f"when its {timeout} s trial budget ran out: a failed trial of this candidate "
                         "on this seed (sandbox_rc 1, task not completed), as ASPIRE records a timed-out "
                         "trial. Nothing is refunded; a policy has to finish within the budget.")}
    return {**verdict, "kind": "stalled" if last is not None else "unknown",
            "attribution": "infrastructure", "settled": False,
            "note": ("no public API activity in the last "
                     f"{POLICY_ACTIVE_WINDOW} s before the watchdog" if last is not None else
                     "no world tape to judge from") + "; infrastructure evidence for close-infra"}


def watchdog_result(state: NativeWorldState, verdict: dict, directory: Path) -> dict:
    """The graded result a settled watchdog verdict stands for."""
    if verdict["kind"] == "persistence_cut":
        return dict(verdict["result"])
    return {"sandbox_rc": 1, "trial_dir": str(directory),
            **state.failure_outcome("policy exceeded the trial timeout")}


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


def search_control(case: dict, state: NativeWorldState) -> dict | None:
    """p3-search: write the search-control report and return its solver summary.

    None for every other contract, so nothing below is ever constructed for them.
    """
    if case.get("prediction_contract") != SEARCH_CONTRACT:
        return None
    from aspire.sim.cap.world_model.search_control import control_report
    if state.budgeted():
        # CC_TASK_6: the budget is versions and modes, not per-seed retries.
        budget = state.budget_status()
        remaining = {"development_budget": {k: budget[k] for k in (
            "revision", "phase", "versions_used", "versions_left", "mode_versions", "blocked_modes")},
            "per_seed": None, "total": None}
    else:
        remaining = {"per_seed": {str(s): state.retries_remaining(s) for s in state.seeds}}
        remaining["total"] = sum(remaining["per_seed"].values())
        remaining["banked_from_deferred_initial"] = state.data.get("search_control", {}).get("banked_attempts", 0)
    report = control_report(state.data, state.task_dir, remaining=remaining)
    path = state.task_dir / SEARCH_REPORT
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)
    return {**report["summary"], "report": str(path),
            "informative_fraction": report["informative"]["fraction"]}


def coverage_triage(case: dict, state: NativeWorldState) -> dict | None:
    """p4: write `coverage_triage.json` — the sweep's failures grouped by signature.

    None for every other contract. Groups only the candidate's own executed,
    graded development trials, per bundle, so the next repair is aimed at a named
    mode rather than at a re-drawn sample. One repair round is allowed; a second
    triage after it is recorded the same way.
    """
    if case.get("prediction_contract") != SEARCH_CONTRACT:
        return None
    from aspire.sim.cap.world_model.search_control import charged_attempts, triage_table
    attempts = charged_attempts(state.data, state.task_dir, sweeps=True)
    table = triage_table(attempts)
    path = state.task_dir / TRIAGE_REPORT
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(table, indent=2) + "\n")
    temporary.replace(path)
    selected = state.data.get("selected") or {}
    digest = selected.get("bundle_sha256")
    # The modes OF THE FROZEN BYTES are what the next repair acts on; another
    # bundle's failures in the same table are history, and are reported beside it.
    frozen = next((b for b in table["bundles"] if b["bundle_sha256"] == digest), None)
    return {"report": str(path), "bundles": len(table["bundles"]), "empty": table["empty"],
            "mode_count": frozen["mode_count"] if frozen else None,
            "frozen_modes": [{"label": m["label"], "seeds": m["seeds"], "size": len(m["seeds"]),
                              "deciding_field": m["deciding_field"],
                              "signature": m["signature"]} for m in (frozen or {}).get("modes", [])],
            "frozen_failed_seeds": (frozen or {}).get("failed_seeds", [])}


def search_gate(case: dict, state: NativeWorldState, phase: str, seed: int,
                sources: dict, replicate: str | None) -> dict | None:
    """Before a charged p3-search repair: report, then refuse a saturated class.

    A refusal is recorded as a rejected revision and spends no attempt. An
    explicit `--replicate <reason>` admits it and the reason travels with the row.
    """
    if case.get("prediction_contract") != SEARCH_CONTRACT or phase != "repair":
        return None
    from aspire.sim.cap.world_model.search_control import primitive_class
    summary = search_control(case, state)
    declared = primitive_class(sources["policy"])
    summary["candidate_class"] = declared
    if declared in summary["saturated_classes"] and method_switches.enabled(case, "screening_gate") == "record_only":
        # CC_TASK_14 M3(c): the saturation finding is recorded with the row; it refuses nothing.
        summary["would_refuse"] = (f"PRIMITIVE_CLASS {declared!r} is saturated; recorded only "
                                   "(screening_gate record_only)")
        if (replicate or "").strip():
            summary["replicate"] = replicate.strip()
        return summary
    if declared in summary["saturated_classes"]:
        if not (replicate or "").strip():
            reason = (f"PRIMITIVE_CLASS {declared!r} is saturated (see {summary['report']}); declare a "
                      "different contact strategy, or pass --replicate <reason> to re-run it deliberately. "
                      "'Physically infeasible' is not an accepted root cause while untried classes remain")
            state.reject(phase, seed, reason, sources)
            raise ProtocolError(reason)
        summary["replicate"] = replicate.strip()
    return summary


def search_early_stop(case: dict, state: NativeWorldState, record: dict) -> None:
    """After a p3-search initial trial: defer the rest of the batch once it says nothing new."""
    if case.get("prediction_contract") != SEARCH_CONTRACT or record["phase"] != "initial":
        return
    from aspire.sim.cap.world_model.search_control import charged_attempts, initial_early_stop
    if state.deferred_seeds():
        return
    stop = initial_early_stop(charged_attempts(state.data, state.task_dir), 3,
                              seeds=state.seeds, retry_limit=state.RETRY_LIMIT)
    record["search_control"] = {"early_stop": stop}
    if stop["triggered"]:
        state.defer_initial(stop, state.initial_bundle())
    state.save()


def search_after_trial(case: dict, state: NativeWorldState, record: dict, directory: Path) -> None:
    """After a p3-search graded trial: the report that governs the NEXT repair.

    The trial result is what the solver reads from `trial` stdout, so the
    refreshed summary (`search_control_next`) and this trial's own judged
    gripper/object prediction levels reach its context before it writes the
    next bundle, not one trial late. An unobserved trial (no progress() value
    or no decided object-level prediction) carries a hint that it is not
    saturation evidence.
    """
    if case.get("prediction_contract") != SEARCH_CONTRACT or record["phase"] not in TRIAL_PHASES:
        return
    from aspire.sim.cap.world_model.search_control import prediction_levels, read_events, trial_observation
    record["search_prediction_levels"] = prediction_levels(read_events(directory))
    record["search_control_next"] = {**search_control(case, state),
                                     "this_trial": trial_observation(directory)}
    if not record["search_control_next"]["this_trial"]["observed"]:
        record["search_control_next"]["hint"] = record["search_control_next"]["this_trial"]["hint"]
    state.save()


def run_trial(case: dict, repo: Path, state: NativeWorldState, phase: str, seed: int,
              code: Path | None, world: Path | None, inventory: Path | None,
              stdin_code: Path | None, *, replicate: str | None = None,
              declaration: dict | None = None) -> dict:
    with (state.task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("another trial is running; await its result") from exc
        state.data = json.loads(state.path.read_text())  # No stale ledger under the lock.
        return _run_trial(case, repo, state, phase, seed, code, world, inventory, stdin_code,
                          replicate=replicate, declaration=declaration)


def run_sweep_batch(case: dict, repo: Path, state: NativeWorldState, seeds,
                    code: Path | None, world: Path | None, inventory: Path | None) -> dict:
    """`trial --phase sweep --seeds all|N,M,...`: one candidate on many seeds (CC_TASK_7).

    Under the same trial lock as a single trial, so this process is the only
    writer of the ledger for the whole batch.
    """
    with (state.task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("another trial is running; await its result") from exc
        state.data = json.loads(state.path.read_text())  # No stale ledger under the lock.
        return _run_sweep_batch(case, repo, state, seeds, code, world, inventory)


def _run_sweep_batch(case: dict, repo: Path, state: NativeWorldState, requested,
                     code: Path | None, world: Path | None, inventory: Path | None) -> dict:
    """Sweep the candidate on every requested seed, up to `sweep_parallel` at once.

    Each seed is exactly the trial `trial --phase sweep --seed N` would run:
    same admission (every requested seed is admitted-checked before anything
    runs), same row, same replay, same settlement. With sweep_parallel 1 the
    seeds simply run one after another. With N > 1 up to N replays run at once
    in the slot's own card, each with capped CPU threads, while rows are begun
    and settled by this one thread strictly in seed order, so every row and
    every report written after it reads the ledger a serial batch would have
    had. The sweep rules are the ledger's and are unchanged: only a tested
    candidate, each seed at most once per candidate, no retry or version spent.
    Like the infrastructure guard between separate calls, the batch starts no
    further seed after an infrastructure error, a screening blocker, changed
    candidate files, a refused admission or a signal; seeds already running
    finish (a signal ends them) and are recorded first.
    """
    if "sweep_parallel" not in case:
        raise ProtocolError("the batch sweep (`--seeds`) is an opt-in of the case (sweep_parallel); "
                            "sweep one seed per call with `--seed N`")
    parallel, threads = parallel_settings(case, "sweep")
    verify_runtime(case, repo)
    try:
        bundle, _ = collect_bundle(case, state.task_dir, SWEEP_PHASE, code, world, inventory)
    except ProtocolError as exc:
        first = requested[0] if requested != "all" and requested else state.seeds[0]
        state.reject(SWEEP_PHASE, first, str(exc), {"code": str(code) if code else None,
                                                    "world": str(world) if world else None,
                                                    "inventory": str(inventory) if inventory else None})
        raise
    digest = bundle_identity(bundle)
    seeds = (state.coverage_gate(bundle)["sweepable"] if requested == "all"
             else sorted(set(requested)))
    if not seeds:
        gate = state.coverage_gate(bundle)
        raise ProtocolError(f"nothing to sweep for this candidate: covered {gate['covered_seeds']}, "
                            f"unsweepable {gate['unsweepable']}")
    for seed in seeds:
        state.validate_admission(SWEEP_PHASE, seed, digest)  # Nothing runs unless every seed may.

    def same_candidate() -> bool:
        try:
            return bundle_identity(collect_bundle(case, state.task_dir, SWEEP_PHASE, code, world,
                                                  inventory)[0]) == digest
        except ProtocolError:
            return False

    records, stopped = [], None
    if parallel == 1:
        for seed in seeds:
            if not same_candidate():
                stopped = "the candidate files changed during the batch"
                break
            try:
                record = _run_trial(case, repo, state, SWEEP_PHASE, seed, code, world, inventory, code)
            except (ProtocolError, RuntimeChanged, OSError) as exc:
                stopped = f"seed {seed} not admitted: {exc}"
                break
            records.append(record)
            stopped = batch_stop(record)
            if stopped:
                break
    else:
        records, stopped = _sweep_parallel(case, repo, state, seeds, code, world, inventory,
                                           parallel, threads, same_candidate)
    ran = [r["seed"] for r in records]
    complete = (not stopped and len(records) == len(seeds)
                and all(r["status"] == "complete" for r in records))
    return {"phase": SWEEP_PHASE, "batch": True, "bundle_sha256": digest, "seeds": seeds,
            "sweep_parallel": parallel, "trial_threads": threads if parallel > 1 else None,
            "status": "complete" if complete else "incomplete", "stopped": stopped,
            "not_run": [s for s in seeds if s not in ran], "records": records,
            "coverage": state.coverage_gate(bundle)}


def batch_stop(record: dict) -> str | None:
    """Why a batch sweep starts no further seed after this row (None: it continues)."""
    if record.get("status") == "infrastructure_error":
        return (f"infrastructure error on seed {record['seed']}: {record.get('error') or 'no result'}; "
                "inspect it before sweeping further")
    if record.get("status") == "screening_blocked":
        return f"offline screening blocked on seed {record['seed']}; retry the unchanged candidate"
    return None


def _sweep_parallel(case: dict, repo: Path, state: NativeWorldState, seeds: list[int],
                    code: Path | None, world: Path | None, inventory: Path | None,
                    parallel: int, threads: int, same_candidate,
                    phase: str = SWEEP_PHASE) -> tuple[list[dict], str | None]:
    """Up to `parallel` sweep replays at once; rows begun and settled in seed order."""
    pending, launched, finished, records, stopped = list(seeds), [], {}, [], None
    with ReplayBatch(parallel) as batch:
        while pending or launched:
            while launched and launched[0].seed in finished:
                launch = launched.pop(0)
                record = _settle_trial(case, repo, state, launch, *finished.pop(launch.seed))
                records.append(record)
                stopped = stopped or batch_stop(record)
            if batch.stopping and stopped is None:
                stopped = batch.interrupt.reason
            while pending and stopped is None and batch.free():
                seed = pending.pop(0)
                if not same_candidate():
                    stopped = "the candidate files changed during the batch"
                    break
                try:
                    launch = _admit_trial(case, repo, state, phase, seed, code, world, inventory,
                                          code, in_flight=frozenset(l.record["directory"] for l in launched))
                except (ProtocolError, RuntimeChanged, OSError) as exc:
                    stopped = f"seed {seed} not admitted: {exc}"
                    break
                if not isinstance(launch, TrialLaunch):
                    records.append(launch)  # A screening blocker; nothing was admitted.
                    stopped = batch_stop(launch) or f"seed {seed} was not admitted"
                    break
                launched.append(launch)
                # `run_replay` is looked up now, so a runtime entry's patch applies.
                batch.submit(seed, run_replay, launch.command, repo=repo,
                             env=parallel_env(launch.env, threads), directory=launch.directory,
                             timeout=launch.timeout, stdin_path=launch.stdin_path)
            if stopped is not None:
                pending = []
            for seed, outcome in batch.wait():
                finished[seed] = outcome
    return records, stopped


class TrialLaunch(SimpleNamespace):
    """An admitted trial ready to start: its begun ledger row, command, env and paths."""


def _run_trial(case: dict, repo: Path, state: NativeWorldState, phase: str, seed: int,
               code: Path | None, world: Path | None, inventory: Path | None,
               stdin_code: Path | None, *, replicate: str | None = None,
               declaration: dict | None = None) -> dict:
    launch = _admit_trial(case, repo, state, phase, seed, code, world, inventory, stdin_code,
                          replicate=replicate, declaration=declaration)
    if not isinstance(launch, TrialLaunch):
        return launch  # A screening blocker: nothing was admitted.
    exit_code, error = run_replay(launch.command, repo=repo, env=launch.env,
                                  directory=launch.directory, timeout=launch.timeout,
                                  stdin_path=launch.stdin_path)
    return _settle_trial(case, repo, state, launch, exit_code, error)


def _admit_trial(case: dict, repo: Path, state: NativeWorldState, phase: str, seed: int,
                 code: Path | None, world: Path | None, inventory: Path | None,
                 stdin_code: Path | None, *, replicate: str | None = None,
                 declaration: dict | None = None,
                 in_flight: frozenset = frozenset()) -> "TrialLaunch | dict":
    """Everything before the replay starts; the row is begun, the command written."""
    verify_runtime(case, repo)
    if 1 <= seed <= 50:
        raise ProtocolError("seeds 1–50 are held out; the outer coordinator evaluates them")
    try:
        bundle, sources = collect_bundle(case, state.task_dir, phase, code, world, inventory)
    except ProtocolError as exc:
        # An invalid generated revision is recorded, and spends no retry: no
        # simulator process started. It stays separate from executed failures.
        state.reject(phase, seed, str(exc), {"code": str(code) if code else None,
                                             "world": str(world) if world else None,
                                             "inventory": str(inventory) if inventory else None})
        raise
    if phase in ("diagnostic", PROBE_PHASE):
        if stdin_code is None:
            raise ProtocolError("--code holds the authored diagnostic program for the batch REPL")
        stdin_code = authored_path(state.task_dir, stdin_code, "--code")
        try:
            # The REPL runs the file as one script; one that cannot compile
            # would execute nothing, so it is refused here and spends no retry.
            compile(stdin_code.read_bytes(), str(stdin_code), "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            reason = f"diagnostic program does not compile: {type(exc).__name__}: {exc}"
            state.reject(phase, seed, reason, sources)
            raise ProtocolError(reason) from exc
        if phase == PROBE_PHASE:
            # CC_TASK_6: refused before anything runs, spending nothing; the
            # observation-only REPL enforces the same boundary at run time.
            problems = probe_source_errors(stdin_code.read_text())
            if problems:
                reason = "; ".join(problems)
                state.reject(phase, seed, reason, sources)
                raise ProtocolError(reason)
    # Pre-admission validation before any offline screening: a candidate that
    # would be refused at begin_trial must not spend screening time. This
    # is non-mutating, so a ProtocolError/TerminalBlocker here spends no retry.
    digest = bundle_identity(bundle)
    # Only a batch sweep names rows in flight; a single trial calls the ledger as before.
    in_flight = {"in_flight": in_flight} if in_flight else {}
    state.validate_admission(phase, seed, digest, declaration, **in_flight)
    # p3-search only, and before the (expensive) offline screening.
    search = search_gate(case, state, phase, seed, sources, replicate)
    # Baseline initial exception: the exact previously-executed initial bundle
    # may run as a REAL retry-spending grading trial even if offline screening
    # reproduces an authored program_error, because the simulator already ran it
    # and its real result must be recorded — a claimed screen pass would be a
    # fiction.  Conditions: initial phase, exact hash match, no prior settled
    # initial result on this seed, and all infrastructure checks successful.
    # A mixed report (authored error AND infrastructure failure) still blocks.
    _run_as_baseline_initial = False
    screening = None
    if case.get("executable_world_revision") and phase not in NON_TRIAL_PHASES and phase != RESWEEP_PHASE:
        from executable_world_profile import checks, screening_infrastructure_ok
        screening = checks(case, repo, state, sources, runtime_env(case, repo))
        baseline_initial = (
            phase == "initial" and state.initial_bundle() == digest
            # A legitimate smoke alias freezes a bundle without another replay;
            # require its actual graded smoke, never the alias row alone.
            and any(r["phase"] in {"initial", "smoke"} and r.get("executed") is True
                    and r["bundle_sha256"] == digest for r in state.graded())
        )
        mixed_or_missing = (screening["status"] == "rejected" and baseline_initial
                            and not screening_infrastructure_ok(screening.get("reports")))
        if screening["status"] == "blocked" or mixed_or_missing:
            # The screening itself failed — a watchdog kill or a missing report.
            # Nothing was learned about the candidate, so this must stop here:
            # before begin_trial, before any simulator invocation, consuming zero
            # new retries. Attaching `blocked` to a retry-spending row instead would
            # make the infrastructure's failure cost a real retry. Returned as
            # a structured record rather than raised, so the blocker is machine
            # readable and the same candidate can be retried unchanged.
            reason = ("offline screening could not run; this is infrastructure, not "
                      "the candidate. Retry the same unchanged candidate once it "
                      "recovers. No retry was spent and none should be spent "
                      "to work around it; inspect " + str(screening.get("directory")))
            detail = {k: screening.get(k) for k in
                      ("status", "conclusions", "retryable", "cached", "directory",
                       # Immutable candidate digests (policy/world/tape), so the
                       # blocker names WHAT was blocked and not just which mutable
                       # paths were passed to the screening at the time.
                       "identity", "reports")}
            blocker = state.record_blocker(phase, seed, reason, sources, detail, bundle=bundle)
            return {**blocker, "status": "screening_blocked", "screening": detail,
                    "retries_used": state.retries_used(seed),
                    "retries_remaining": state.retries_remaining(seed)}
        if screening["status"] == "rejected":
            # An authored Python error in the supported part of the screening.
            # Normally refused before any simulator runs and spends no retry.
            # Exception: the frozen initial bundle that was ALREADY graded on
            # another development seed is known to have run; its real
            # result is what matters, not a screening reproduction.  Record the
            # screening exception explicitly so the trial row names both facts.
            if baseline_initial:
                _run_as_baseline_initial = True
            else:
                reason = "offline candidate error; inspect " + screening["directory"]
                state.reject(phase, seed, reason, sources)
                raise ProtocolError(reason)
    record = state.begin_trial(phase, seed, bundle, sources, declaration, **in_flight)
    if search is not None:
        record["search_control"] = search
        state.save()
    if _run_as_baseline_initial and screening is not None:
        # Mark the row explicitly so reviewers know the real trial ran despite
        # the authored error that the screening reproduced.
        record["screening_baseline_exception"] = {
            "screening_status": "rejected",
            "note": ("exact previously-executed initial bundle admitted for real retry-spending "
                     "grading; screening-reproduced error kept for audit"),
            "screening_conclusions": screening.get("conclusions", [])}
    if screening is not None:
        # The screening outcome travels with the attempt it preceded. A `blocked`
        # screening cannot reach here — it returned above, before begin_trial —
        # The exact frozen-initial exception retains `rejected`; it never claims
        # a screening pass. Earlier blockers remain separate records that spent no retry.
        record["screening"] = {k: screening.get(k) for k in
                               ("status", "conclusions", "retryable", "cached", "directory",
                                "identity", "reports")}
    directory = state.task_dir / record["directory"]
    (directory / "code.py").write_bytes(Path(sources["policy"]).read_bytes())
    env = runtime_env(case, repo)
    env["SNAPSHOT_DIR"] = str(state.task_dir)
    env["ASPIRE_MAX_STEPS"] = str(case["max_steps"])
    if phase == PROBE_PHASE:
        env[OBSERVATION_ONLY_ENV] = "1"
    sealed_dir = None
    if sealed_gate(case):
        # Every phase of a sealed cell runs sealed: the replay writes the real
        # outcome under the control root, outside the solver's read boundary,
        # names its public artifacts without it, and the diagnostic REPL gets
        # the read-only env view. The gate verdict is computed below.
        sealed_dir = gate_module().sealed_trial_dir(case, record["directory"])
        sealed_dir.mkdir(parents=True, exist_ok=True)
        env[gate_module().SEALED_ENV] = str(sealed_dir / "outcome.json")
    results = result_root(case, directory, phase, seed)
    command = [str(repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"],
               "--args.trial", str(seed), "--args.model", case["model"],
               "--args.config", case["env_config"],
               "--args.output-dir", str(directory / "results")]
    if phase in ("diagnostic", PROBE_PHASE):
        # Identical API execution semantics, authored code read from a file, one
        # session spends one retry. Inspection is never silently prohibited.
        # A probe runs the same REPL observation-only and spends nothing.
        command += ["--args.interactive"]
    else:
        command += ["--args.replay-code", str(directory / "code.py")]
    if case["condition"] in WORLD_CONDITIONS and phase not in NON_TRIAL_PHASES:
        command += ["--args.world-model-config", str(write_world_config(case, directory, sources))]
    (directory / "command.json").write_text(json.dumps(
        {"command": command, "stdin": str(stdin_code) if stdin_code else None,
         "result_root": str(results)}, indent=2))
    # The world child keeps the real 900 s watchdog. The outer wrapper gets a
    # short cleanup margin on top, because killing it on the same deadline can
    # interrupt child_exit.json persistence. This is grace for cleanup, not
    # extra robot execution budget.
    grace = CLEANUP_MARGIN_SECONDS if adapter_world(case, phase) else 0
    return TrialLaunch(phase=phase, seed=seed, record=record, directory=directory, results=results,
                       sealed_dir=sealed_dir, command=command, env=env,
                       timeout=case["trial_timeout"] + grace,
                       stdin_path=stdin_code if phase in ("diagnostic", PROBE_PHASE) else None)


def _settle_trial(case: dict, repo: Path, state: NativeWorldState, launch: TrialLaunch,
                  exit_code: int | None, error: str) -> dict:
    """Everything after the replay ended: grade, settle the row, write the reports."""
    phase, seed, record = launch.phase, launch.seed, launch.record
    directory, results, sealed_dir = launch.directory, launch.results, launch.sealed_dir
    oracle = None
    if sealed_dir is not None:
        oracle = parse_sealed_result(results, seed, sealed_dir / "outcome.json")
        result = None if oracle is None else gate_module().public_result(
            oracle, gate_module().not_evaluated(case, f"{phase} grades nothing"))
    else:
        result = parse_result(results, seed)
    if phase == "snapshot" and (not result or result["sandbox_rc"] or not all(
            (state.task_dir / n).is_file() for n in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"))):
        result, error = None, "snapshot did not produce both camera images; inspect replay.log"
    diagnostic_error = None
    probe_violation = None
    if phase in ("diagnostic", PROBE_PHASE):
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
        elif phase == PROBE_PHASE and (session.get("observation_only") is not True
                                       or session.get("robot_state_unchanged") is not True):
            # The REPL did not run observation-only, or the robot moved anyway:
            # whatever this was, it was no probe (CC_TASK_6).
            result = None
            probe_violation = {k: session.get(k) for k in
                               ("observation_only", "robot_state_unchanged", "robot_state_change",
                                "refused_actions", "statements", "error_count")}
            probe_violation["session_dir"] = str(results)
        elif session["errors"]:
            result = None
            diagnostic_error = {k: session.get(k) for k in
                                ("statements", "error_count", "errors", "refused_actions")
                                if k in session}
            diagnostic_error["session_dir"] = str(results)
        else:
            # Retry-spending inspection that reached its own end. Never a score: the
            # outcome fields are fixed here rather than taken from any artifact.
            result = ({"sandbox_rc": 0, "trial_dir": observed, "session": phase,
                       **state.failure_outcome(f"{phase} session grades nothing")}
                      if exit_code == 0 else None)
    try:
        verify_runtime(case, repo)
    except RuntimeChanged as exc:
        error = str(exc)
    # CC_TASK_12 G6: a watchdog kill is judged from the trial's own evidence.
    watchdog = watchdog_verdict(case, directory, results, phase, seed, error)
    if watchdog and watchdog["settled"]:
        result, error = watchdog_result(state, watchdog, directory), ""
    if error:
        result = None
    gate_report = None
    if (sealed_dir is not None and result is not None and phase in TRIAL_PHASES
            and exit_code == 0):
        # The only grade this cell sees. Computed after the simulator exited and
        # before the row is settled, so the ledger row carries it from the start.
        gate_report = gate_module().evaluate(case, directory, trial_dir=Path(oracle["trial_dir"]))
        result = gate_module().public_result(oracle, gate_report)
    world_error = None
    if not error and exit_code not in (None, 0) and watchdog is None:
        # A broken authored world is a retry-spending program failure with feedback, and
        # the raw simulator outcome is preserved separately so a rejected world can
        # never be read back as a valid B/C success.
        world_error = world_failure(case, directory, phase, seed)
    module = world_use(case)
    if module is not None and phase in GRADED_PHASES:
        # Evidence about the mechanism, read from the policy this trial actually
        # ran and that trial's own recorded query trace. It is attached to the
        # trial record the solver already reads, writes its full audit beside the
        # other artifacts, and is never consulted by `status`, so a missing or
        # inconclusive mechanism costs no attempt and blocks nothing.
        record["world_use"] = module.trial_feedback(directory)
    raw_result = None
    if world_error:
        # The raw simulator outcome of a rejected world stays with the row in an
        # oracle cell; in a sealed cell it goes to the sealed ledger only.
        raw_result = oracle if sealed_dir is not None else parse_result(results, seed)
    if probe_violation is not None and not error:
        record.update(process_exit_code=exit_code, error="", status=PROBE_VIOLATION,
                      probe_violation=probe_violation, sandbox_rc=1,
                      **state.failure_outcome("probe moved the robot; grades nothing"),
                      trial_dir=record["directory"], session=PROBE_PHASE)
        state.save()
    else:
        state.finish_trial(record, result=result, exit_code=exit_code, error=error,
                           world_error=world_error, diagnostic_error=diagnostic_error,
                           raw_result=None if sealed_dir is not None else raw_result,
                           watchdog=watchdog)
    if case.get("foundation_revision") == "r1" and phase in GRADED_PHASES and sealed_dir is None:
        from aspire.sim.cap.world_model.foundation_audit import trial_feedback
        record["foundation_calibration"] = trial_feedback(directory, record)
        state.save()
    if (method_switches.enabled(case, "judgment_calibration") and phase in TRIAL_PHASES
            and sealed_dir is None):
        # CC_TASK_14 M3(b): where the world's own goal judgment disagreed with the
        # simulator so far, per seed, read by the worker with this trial's result.
        from aspire.sim.cap.world_model.judgment_calibration import trial_note
        record["judgment_calibration"] = trial_note(state, record)
        state.save()
    if sealed_dir is not None:
        # Oracle beside gate, outside the solver boundary. The public row says
        # only that the outcome is sealed and where the outer analysis reads it.
        module = gate_module()
        self_eval = module.final_self_evaluation(directory) if phase in TRIAL_PHASES else None
        gate_report = record.get("gate") or module.not_evaluated(case, record["status"])
        sealed_row = module.seal(case, record, oracle, gate_report, self_eval=self_eval,
                                 judge_report=gate_report if gate_report.get("source") == "vlm_judge" else None)
        record["sealed"] = {"outcome": "sealed", "ledger": str(module.sealed_ledger(case)),
                            "self_eval_verdict": sealed_row.get("self_eval_verdict")}
        state.save()
    search_early_stop(case, state, record)
    search_after_trial(case, state, record, directory)
    if case.get("closed_loop_revision") and phase in TRIAL_PHASES:
        # Online decisions the policy made, reported apart from the framework's
        # final shadow judgment. Never raises and never touches the retry count.
        from aspire.sim.cap.world_model.decision_revision import feedback
        record["closed_loop"] = feedback(directory)
        state.save()
    return record


def parse_sealed_result(results: Path, seed: int, outcome_path: Path) -> dict | None:
    """A sealed trial's real outcome, read from the protocol's sealed file.

    The public directory is named without the label. The file the replay wrote
    outside the solver boundary must exist and agree on the directory and the
    sandbox exit code; otherwise the trial is unresolved infrastructure evidence.
    """
    pattern = re.compile(SEALED_TRIAL_RE_TEMPLATE.format(seed=seed))
    matches = [(p, pattern.fullmatch(p.name)) for p in results.rglob("trial_*") if p.is_dir()]
    matches = [(p, m) for p, m in matches if m]
    if len(matches) != 1:
        return None
    path, match = matches[0]
    outcome = gate_module().read_sealed_outcome(outcome_path)
    if (outcome is None or Path(outcome["trial_dir"]).resolve() != path.resolve()
            or int(outcome["sandbox_rc"]) != int(match[1])):
        return None
    return {"sandbox_rc": int(match[1]), "reward": float(outcome["reward"]),
            "task_completed": int(bool(outcome["task_completed"])), "trial_dir": str(path),
            "terminated": outcome.get("terminated"), "truncated": outcome.get("truncated")}


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
    """Assess cell readiness.

    Every returned dict carries a ``decision_type`` key so a machine-readable
    driver never has to parse the ``errors`` list to distinguish the three
    situations that surface here:

    ``"terminal"``   — irrecoverable: a TerminalBlocker applies and no amount of
                       repair work can make this cell ready.  ``ready`` is False.
    ``"incomplete"`` — recoverable: at least one error exists but it is not
                       terminal; more development work or a missing file can fix it.
                       ``ready`` is False.
    ``"ready"``      — no errors, the cell may be finalized.  ``ready`` is True.
    """
    working = repo / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"
    progress = state.progress()
    terminal_seeds = progress.get("seeds_exhausted_without_graded_evidence", [])
    # Structured reasons a driver stops on (CC_TASK_2): one entry per way the
    # cell can no longer reach `ready` whatever further work is done.
    terminal_reasons = ([{"code": "seeds_exhausted_without_graded_evidence", "seeds": terminal_seeds}]
                        if terminal_seeds else [])
    # Present on every check output of a sealed cell, ready or not, so the
    # solver always reads what grades its progress.
    gate_status = ({"source": case["development_gate"], "sealed": True,
                    "note": "progress, triage and selection use each trial's recorded gate verdict; "
                            "the simulator's task label is sealed from this cell until the outer evaluation"}
                   if sealed_gate(case) else None)
    incomplete = {"ready": False, "progress": progress,
                  "decision_type": "terminal" if terminal_reasons else "incomplete",
                  "terminal_seeds": terminal_seeds, "terminal_reasons": terminal_reasons,
                  **({"development_gate": gate_status} if gate_status else {})}
    if state.budgeted():
        incomplete["development_budget"] = state.budget_status()
    if method_switches.enabled(case, "judgment_calibration") and not sealed_gate(case):
        from aspire.sim.cap.world_model.judgment_calibration import summary
        incomplete["judgment_calibration"] = summary(state)  # CC_TASK_14 M3(b), every check
    fix = state.task_dir / "fix_code.py"
    if not fix.is_file():
        return {**incomplete, "errors": ["missing executable fix_code.py"]}
    try:
        bundle = selected_bundle(case, state)
    except OSError as exc:
        return {**incomplete, "errors": [f"selected bundle is incomplete: {exc}"]}
    # Check for terminal blockers (exhausted-without-graded seeds) before
    # computing completion_errors, so the decision_type is accurate even when
    # completion_errors also returns those seeds in its own list.
    # CC_TASK_4: issues are structured ({code, severity, message}); only the
    # substantive ones are `errors`. An advisory all-fail tie-break (see
    # `NativeWorldState.tie_break`) is reported under `advisories` and never
    # blocks ready.
    issues = state.completion_issues(bundle=bundle, working_code=working,
                                     world_required=case["condition"] in WORLD_CONDITIONS)
    selection = state.data.get("selected")
    # p4 coverage gate: the frozen bundle is only evidence once its OWN executed
    # trials cover every development seed. A sweep that cannot be paid for is a
    # blocker in `errors`, never a silently relaxed gate. Scoped to the
    # p3-search contract, whose early stop is what defers seeds in the first
    # place: a legacy A/B/C cell has no banked-attempt notion to sweep against,
    # and applying the gate there would retroactively invalidate cells that the
    # protocol never asked to sweep.
    searching = case.get("prediction_contract") == SEARCH_CONTRACT
    coverage_gate = state.coverage_gate(bundle)
    if searching:
        issues += [{"code": "coverage_gate", "severity": "substantive", "message": message}
                   for message in state.coverage_errors(bundle)]
        reach = state.coverage_reachability(bundle)
        coverage_gate["reachability"] = reach
        if not coverage_gate["complete"] and not reach["reachable"]:
            # Every repair retry is spent (so nothing remains to repair or test)
            # and no tested candidate is covered or sweepable: the gate cannot be
            # met by construction. Terminal, never another solver round.
            terminal_reasons.append({
                "code": "coverage_unreachable", "missing": coverage_gate["missing"],
                "unsweepable": coverage_gate["unsweepable"],
                "retries_remaining_total": reach["retries_remaining_total"],
                "seeds_pending_repair": progress.get("seeds_pending_repair", []),
                "reason": reach["reason"]})
    if not selection or not selection.get("reason", "").strip():
        issues.append({"code": "selection_unrecorded", "severity": "substantive",
                       "message": "record the selection reason with `select` so coverage stays reviewable"})
    elif selection["bundle_sha256"] != bundle_identity(bundle):
        issues.append({"code": "selection_mismatch", "severity": "substantive",
                       "message": "the recorded selection does not match the frozen files on disk"})
    errors = [i["message"] for i in issues if i["severity"] == "substantive"]
    advisories = [i for i in issues if i["severity"] == "advisory"]
    actions = state.legal_actions(bundle)
    blocking = sorted({i["code"] for i in issues if i["severity"] == "substantive"})
    if errors and not terminal_reasons and not actions["any"] and not set(blocking) & AUTHORABLE_ISSUES:
        # CC_TASK_4 generic guard: errors remain, but no admissible action can
        # change them — no charged retry, no pending repair, no sweep, no better
        # freezable selection, and nothing the solver could fix by writing a
        # file. Another coordinator round could only repeat this check, so the
        # cell is terminal with a structured reason instead of `incomplete`
        # (goaltaskbowldrawer_p4n_r1 sat on `incomplete` until the stall stop).
        terminal_reasons.append({"code": "no_legal_action", "blocking_issues": blocking,
                                 "legal_actions": actions,
                                 "reason": "the check still has errors and no admissible protocol "
                                           "action can change them"})
    if errors and terminal_reasons:
        decision_type = "terminal"
    elif errors:
        decision_type = "incomplete"
    else:
        decision_type = "ready"
    status = {"ready": not errors, "decision_type": decision_type,
              "terminal_reasons": terminal_reasons if errors else [],
              "errors": errors, "advisories": advisories, "legal_actions": actions,
              "progress": progress,
              "selected": selection, "coverage": state.final_coverage(bundle),
              "coverage_gate": coverage_gate}
    if selection:
        # p3's selection reason overclaimed its pass count and nothing recomputed
        # it. The ledger's own figures are reported either way; a disagreement is
        # a warning beside them, never an editable claim.
        evidence = state.reason_evidence(selection.get("reason", ""))
        status["selection_evidence"] = evidence
        if evidence["warnings"]:
            status["warnings"] = list(status.get("warnings", [])) + evidence["warnings"]
    if terminal_seeds:
        status["terminal_seeds"] = terminal_seeds
    if state.budgeted():
        # CC_TASK_6: phase, versions used / left, versions per mode, blocked
        # modes and the freeze conditions, judged on the bundle on disk.
        status["development_budget"] = state.budget_status(bundle_identity(bundle))
    if case.get("foundation_revision") == "r1" and not sealed_gate(case):
        from aspire.sim.cap.world_model.foundation_audit import aggregate
        status["foundation_calibration"] = aggregate(state.data["trials"])
    if method_switches.enabled(case, "judgment_calibration") and not sealed_gate(case):
        from aspire.sim.cap.world_model.judgment_calibration import summary
        status["judgment_calibration"] = summary(state)
    if gate_status:
        status["development_gate"] = gate_status
    search = search_control(case, state)
    if search is not None:
        status["search_control"] = search
        # p4: the sweep's failure modes, beside the coverage gate that produced
        # them. This is the table the next repair round is aimed at.
        status["coverage_triage"] = coverage_triage(case, state)
    if case.get("closed_loop_revision"):
        from aspire.sim.cap.world_model.decision_revision import aggregate as closed_loop_aggregate
        status["closed_loop"] = closed_loop_aggregate(state.data["trials"])
    module = world_use(case)
    if module is not None:
        # The selected pair's own executed development trials, audited again as a
        # pair. Deliberately outside `errors` and outside `ready`: an unused or
        # unreadable mechanism is a finding to report with the task result, not an
        # incomplete cell, and there is no repair loop until it passes.
        status["world_use"] = module.selected_pair_report(
            state.task_dir / row["directory"] for row in status["coverage"])
    return status


#: Why the outer driver ended development (CC_TASK_8; `terminal` CC_TASK_9).
DEVELOPMENT_END_REASONS = ("deadline", "no_progress", "solver_exit", "terminal")
#: CC_TASK_14: a cell with the pre-freeze re-sweep also ends development through the
#: outer selection when the solver reached `ready` itself, so the re-sweep can follow.
READY_END_REASON = "ready"


def closeout_infrastructure(state: NativeWorldState, best: list[str]) -> tuple[list[dict], list[dict]]:
    """Unresolved infrastructure evidence at development end: (blocking, tolerated).

    Blocking: a row or screening blocker of a best complete candidate (its own
    executions or admissions failed for a reason nobody resolved, so the bytes to
    be frozen have an unexplained run), the snapshot, or one naming no candidate.
    Tolerated: evidence about another candidate. It changes neither the selected
    candidate's executed coverage nor its passes/crashes, refunds nothing and grades
    nothing; it is reported, and the row stays exactly as recorded.
    """
    rows = {r["directory"]: r for r in state.data["trials"]}
    evidence = [{"kind": "trial", "directory": d, "seed": rows[d]["seed"], "phase": rows[d]["phase"],
                 "bundle_sha256": rows[d].get("bundle_sha256"), "error": str(rows[d].get("error", ""))[:300]}
                for d in state.progress()["infrastructure_errors"] if d in rows]
    evidence += [{"kind": "screening_blocker", "seed": b.get("seed"), "phase": b.get("phase"),
                  "bundle_sha256": b.get("bundle_sha256"), "reason": str(b.get("reason", ""))[:300]}
                 for b in state.unresolved_blockers()]
    blocking, tolerated = [], []
    for item in evidence:
        if item["phase"] == "snapshot" or item["bundle_sha256"] is None or item["bundle_sha256"] in best:
            blocking.append(item)
        else:
            tolerated.append(dict(item, reason_tolerated=(
                "evidence about a candidate other than the best complete one; the selected "
                "candidate's executed coverage and score do not depend on it")))
    return blocking, tolerated


def end_development(case: dict, repo: Path, state: NativeWorldState, reason: str,
                    cleanup_receipt: Path | None = None) -> dict:
    """Outer coordinator only: settle a stopped phase B using development evidence.

    The nonblocking trial lock is shared with serial/parallel admission and held
    through selection. A killed batch's running rows refuse even after lock release.
    No trial is created, refunded, recovered, or swept by this operation.

    CC_TASK_9: `terminal` is the phase-B check's structured terminal state, settled
    here before the driver blocks. Unresolved infrastructure evidence (an
    `infrastructure_error` row not closed by close-infra, an unresolved screening
    blocker) is judged per candidate, see `closeout_infrastructure`: evidence about
    a best complete candidate, the snapshot, or a row/blocker naming no candidate
    still refuses; evidence about any other candidate is listed under
    `unresolved_not_blocking` and stays in the ledger untouched.
    """
    if reason not in DEVELOPMENT_END_REASONS and not (
            reason == READY_END_REASON and method_switches.resweep_settings(case)):
        raise ProtocolError("invalid development end reason")
    with (state.task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("development trials are still active") from exc
        state.data = json.loads(state.path.read_text())
        verify_runtime(case, repo)
        if not state.budgeted() or state.budget_phase() != "B":
            raise ProtocolError("development end requires phase B; no freeze before first seed success")
        interrupted = []
        if reason == "deadline" and cleanup_receipt is not None:
            import socket
            receipt = json.loads(cleanup_receipt.read_text())
            if receipt.get("verified_no_live_work") is not True:
                raise ProtocolError("deadline cleanup is not verified")
            for row in state.data["trials"]:
                if row["status"] != "running" and not (row["status"] == "infrastructure_error"
                        and row.get("error", "").startswith("TrialInterrupted:")):
                    continue
                path = state.task_dir / row["directory"] / "lifecycle.json"
                life = json.loads(path.read_text()) if path.exists() else {}
                if (life.get("host") != socket.gethostname()
                        or life.get("pid") not in receipt.get("tracked_pids", [])):
                    raise ProtocolError("unfinished row is not covered by owned-process cleanup")
                import psutil
                try:
                    process = psutil.Process(life["pid"])
                    stat = Path(f"/proc/{life['pid']}/stat")
                    same = (not stat.exists() or life.get("proc_start_ticks") is None
                            or stat.read_text().rsplit(")", 1)[1].split()[19] == life["proc_start_ticks"])
                    if same and process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        raise ProtocolError("unfinished replay remains alive after claimed cleanup")
                except psutil.NoSuchProcess:
                    pass
                # No invented grade or refunded attempt. Preserve the entire original row.
                interrupted.append(row.copy())
            for original in interrupted:
                row = next(r for r in state.data["trials"] if r["directory"] == original["directory"])
                row.update(status="development_interrupted", interrupted_record=original,
                           error="development deadline; owned process cleanup verified; no grade",
                           cleanup_receipt=str(cleanup_receipt))
        if state.unresolved():
            raise ProtocolError("unsettled trials prevent development end")
        best = state.best_complete()
        if not best:
            raise ProtocolError("no complete phase-B candidate covers all development seeds")
        blocking, tolerated = closeout_infrastructure(state, best["bundle_sha256"])
        if blocking:
            raise ProtocolError("unresolved infrastructure evidence of a best complete candidate, the "
                                "snapshot or no attributable candidate prevents development end: "
                                + json.dumps(blocking, sort_keys=True))
        if interrupted:
            state.save()
        # Candidate bytes come from executed trial snapshots, never mutable sources
        # or held-out paths. Validate every supporting snapshot's identity.
        names = {"policy": "code.py", "world": "world_program.py", "inventory": "inventory.json"}
        candidates = state.candidates()
        snapshots = {}
        for digest in best["bundle_sha256"]:
            rows = state.final_coverage(candidates[digest]["bundle"])
            for row in rows:
                directory = state.task_dir / row["directory"]
                if not directory.resolve().is_relative_to((state.task_dir / "development").resolve()):
                    raise ProtocolError("candidate evidence is outside development")
                files = {k: directory / names[k] for k in row["bundle"]}
                if any(not p.is_file() or code_hash(p.read_text()) != row["bundle"][k]
                       for k, p in files.items()):
                    raise ProtocolError("candidate snapshot bytes do not match executed bundle")
            snapshots[digest] = files
        selected = state.data.get("selected") or {}
        if selected.get("bundle_sha256") in snapshots and selected.get("reason", "").strip():
            chosen = selected["bundle_sha256"]
            tie = "retained solver's documented choice among equal best complete candidates"
        else:
            # Existing simplicity rule is qualitative. Use executable AST size as
            # its deterministic fallback when the solver left no eligible choice.
            import ast
            def simplicity(digest):
                files = snapshots[digest]
                return (sum(sum(1 for _ in ast.walk(ast.parse(p.read_text())))
                            for k, p in files.items() if k != "inventory"), digest)
            chosen = min(snapshots, key=simplicity)
            tie = "fewest policy+world Python AST nodes; bundle digest breaks exact ties"
        files = snapshots[chosen]
        # Re-run the ordinary API/static checks on the immutable executed bytes.
        bundle, _ = collect_bundle(case, state.task_dir, SWEEP_PHASE, files["policy"],
                                   files.get("world"), files.get("inventory"))
        if bundle_identity(bundle) != chosen:
            raise ProtocolError("candidate snapshot identity changed")
        import copy
        before = copy.deepcopy(state.data)
        state.data["development_end"] = {"reason": reason, "tie_break": tie,
                                         "best_complete": best, "selected": chosen,
                                         "unresolved_not_blocking": tolerated}
        targets = {"policy": "fix_code.py", "world": "fix_world_program.py", "inventory": "fix_inventory.json"}
        paths = {state.task_dir / targets[k]: p.read_bytes() for k, p in files.items()}
        paths[repo / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"] = files["policy"].read_bytes()
        previous = {p: p.read_bytes() if p.exists() else None for p in paths}
        try:
            for path, content in paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            reason_text = (f"Development ended: {reason}; best complete candidate: "
                           f"{best['score'][0]}/{len(state.seeds)} passes, {-best['score'][1]} crashes. {tie}.")
            if chosen == selected.get("bundle_sha256"):
                reason_text += " Prior solver rationale: " + selected["reason"]
            state.select(bundle, reason_text)
            status = check(case, repo, state)
            if not status["ready"]:
                raise ProtocolError("development end failed substantive checks: " + "; ".join(status["errors"]))
        except BaseException:
            state.data = before
            state.save()
            for path, content in previous.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            raise
        return {"development_end": state.data["development_end"], "check": status}


def candidate_snapshot(state: NativeWorldState, digest: str) -> dict[str, Path]:
    """The executed bytes of a candidate: policy/world/inventory of one of its sweep rows."""
    names = {"policy": "code.py", "world": "world_program.py", "inventory": "inventory.json"}
    rows = [r for r in state.graded() if r["bundle_sha256"] == digest]
    rows.sort(key=lambda r: r["phase"] != SWEEP_PHASE)
    for row in rows:
        directory = state.task_dir / row["directory"]
        if not directory.resolve().is_relative_to((state.task_dir / "development").resolve()):
            continue
        files = {k: directory / names[k] for k in row["bundle"]}
        if all(p.is_file() and code_hash(p.read_text()) == row["bundle"][k] for k, p in files.items()):
            return files
    raise ProtocolError(f"no executed snapshot matches candidate {digest[:12]}")


def adopt_candidate(case: dict, repo: Path, state: NativeWorldState, digest: str, reason_text: str) -> dict:
    """Make `digest` the selection: fix files from its executed bytes, `select`, `check` ready.

    end_development's file handling for one chosen candidate; rolled back as a whole
    when the check refuses it. Returns the check.
    """
    import copy
    files = candidate_snapshot(state, digest)
    bundle, _ = collect_bundle(case, state.task_dir, SWEEP_PHASE, files["policy"],
                               files.get("world"), files.get("inventory"))
    if bundle_identity(bundle) != digest:
        raise ProtocolError("candidate snapshot identity changed")
    before = copy.deepcopy(state.data)
    targets = {"policy": "fix_code.py", "world": "fix_world_program.py", "inventory": "fix_inventory.json"}
    paths = {state.task_dir / targets[k]: p.read_bytes() for k, p in files.items()}
    paths[repo / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"] = files["policy"].read_bytes()
    previous = {p: p.read_bytes() if p.exists() else None for p in paths}
    try:
        for path, content in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        state.select(bundle, reason_text)
        status = check(case, repo, state)
        if not status["ready"]:
            raise ProtocolError("re-sweep selection failed substantive checks: " + "; ".join(status["errors"]))
    except BaseException:
        state.data = before
        state.save()
        for path, content in previous.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    return status


def prefreeze_resweep(case: dict, repo: Path, state: NativeWorldState) -> dict:
    """Outer coordinator only (CC_TASK_14 M2(b)): re-sweep, then select on both runs.

    After `end-development` and before `finalize`. `resweep_plan` fixes the
    candidates once (complete phase-B candidates within `margin` sweep passes of
    the best, at most `max_candidates`, the current selection always included);
    each runs every development seed again as `resweep` rows (no version, no
    retry, never shown to the solver). A candidate whose re-sweep is complete is
    compared on sweep + re-sweep passes, then crashes; between equal ones the
    current selection is kept, else the fewest policy+world AST nodes. With no
    complete re-sweep the end-development selection stands, and the result says so.
    Resumable: settled re-sweep rows are never re-run.
    """
    if not method_switches.resweep_settings(case):
        raise ProtocolError("this cell has no pre-freeze re-sweep (method switch prefreeze_resweep)")
    from datetime import datetime, timezone
    with (state.task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("development trials are still active") from exc
        state.data = json.loads(state.path.read_text())
        verify_runtime(case, repo)
        if state.data.get("stage1_complete"):
            raise ProtocolError("this cell is frozen")
        end = state.data.get("development_end")
        if not end:
            raise ProtocolError("the pre-freeze re-sweep runs after the outer end-development")
        if state.data.get("prefreeze_resweep") is None:
            plan = state.resweep_plan()
            plan.update(planned_at=datetime.now(timezone.utc).isoformat(),
                        selected_before=end.get("selected"))
            state.data["prefreeze_resweep"] = plan
            state.save()
        plan = state.data["prefreeze_resweep"]
        parallel, threads = parallel_settings(case, "sweep")
        runs = []
        for digest in plan["candidates"]:
            done = {r["seed"] for r in state.records(RESWEEP_PHASE)
                    if r["bundle_sha256"] == digest and r["status"] in ("complete", "world_program_error")}
            pending = [s for s in state.seeds if s not in done]
            if not pending:
                runs.append({"bundle_sha256": digest, "seeds": [], "stopped": None})
                continue
            files = candidate_snapshot(state, digest)
            records, stopped = _sweep_parallel(case, repo, state, pending, files["policy"], files.get("world"),
                                               files.get("inventory"), parallel, threads, lambda: True,
                                               phase=RESWEEP_PHASE)
            runs.append({"bundle_sha256": digest, "seeds": pending, "stopped": stopped,
                         "statuses": {str(r["seed"]): r.get("status") for r in records}})
        scores = state.resweep_scores()
        best = state.best_complete()
        current = (state.data.get("selected") or {}).get("bundle_sha256") or end.get("selected")
        result = {"plan": plan, "runs": runs, "scores": scores, "best": best, "selected_before": current}
        if not best or best.get("evidence") != "sweep + pre-freeze re-sweep":
            result.update(selected=current, changed=False,
                          note="no candidate completed its re-sweep; the end-development selection stands")
        else:
            if current in best["bundle_sha256"]:
                chosen, tie = current, "kept the end-development selection among equal merged scores"
            else:
                import ast
                def simplicity(digest):
                    files = candidate_snapshot(state, digest)
                    return (sum(sum(1 for _ in ast.walk(ast.parse(p.read_text())))
                                for k, p in files.items() if k != "inventory"), digest)
                chosen = min(best["bundle_sha256"], key=simplicity)
                tie = "fewest policy+world Python AST nodes; bundle digest breaks exact ties"
            record = scores[chosen]
            reason_text = (f"Pre-freeze re-sweep ({plan['labels'].get(chosen)}): merged passes "
                           f"{record['merged_score'][0]} over {2 * len(state.seeds)} development trials "
                           f"(sweep {record['sweep_score'][0]}, re-sweep {len(record['resweep_passes'])}), "
                           f"{-record['merged_score'][1]} crashes. {tie}.")
            status = adopt_candidate(case, repo, state, chosen, reason_text)
            result.update(selected=chosen, changed=chosen != current, tie_break=tie,
                          check_ready=status["ready"])
        state.data["prefreeze_resweep"]["result"] = {k: result[k] for k in
                                                     ("selected", "changed", "selected_before", "best")}
        state.data["prefreeze_resweep"]["result"]["tie_break"] = result.get("tie_break")
        state.save()
        return result


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
        "retry_limit": RETRY_LIMIT,
        "retries_used_per_seed": status["progress"]["retries_used_per_seed"],
        "seed_outcomes": status["progress"]["seeds"],
        "tested_bundles": status["progress"]["tested_bundles"],
        "rejected_revisions": state.data["rejected"], "aliases": state.data["aliases"],
        "world_program_errors": status["progress"]["world_program_errors"],
        # Authored REPL failures and pre-admission blockers that spent no retry, reported
        # separately from graded evidence so neither can be read as a result.
        "diagnostic_program_errors": status["progress"]["diagnostic_program_errors"],
        "screening_blockers": status["progress"]["screening_blockers"],
        "unresolved_screening_blockers": status["progress"]["unresolved_screening_blockers"],
        "graded_executions": status["progress"]["graded_executions"],
        "final_development_coverage": state.final_coverage(bundle),
        "coverage_gate": status["coverage_gate"],
        # CC_TASK_4: findings that did not block ready (an all-fail tie-break
        # against partially covered candidates), kept with the result.
        "advisories": status["advisories"],
        **({"development_budget": status["development_budget"]} if "development_budget" in status else {}),
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
    result["development_gate"] = case.get("development_gate") or "oracle"
    (state.task_dir / "stage1_result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"))
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("init", "status", "check"):
        sub.add_parser(name)
    trial = sub.add_parser("trial")
    trial.add_argument("--phase", choices=["snapshot", "smoke", "initial", "repair", "diagnostic",
                                           SWEEP_PHASE, PROBE_PHASE], required=True)
    trial.add_argument("--seed", type=int)
    trial.add_argument("--seeds", help="batch sweep (only --phase sweep, case sweep_parallel): `all` "
                                       "missing development seeds of the candidate, or e.g. 52,53")
    trial.add_argument("--code", type=Path)
    trial.add_argument("--world-program", type=Path)
    trial.add_argument("--inventory", type=Path)
    trial.add_argument("--replicate", metavar="REASON",
                       help="p3-search only: deliberately re-run a saturated PRIMITIVE_CLASS")
    trial.add_argument("--mode", help="aspire-v1 phase B: the failure mode this version targets")
    trial.add_argument("--mode-seeds", help="aspire-v1 phase B: that mode's failing seeds, e.g. 53,57")
    alias = sub.add_parser("alias-smoke")
    alias.add_argument("--seed", type=int, required=True)
    select = sub.add_parser("select")
    select.add_argument("--reason", required=True)
    end = sub.add_parser("end-development")
    end.add_argument("--cleanup-receipt", type=Path)
    end.add_argument("--reason", choices=(*DEVELOPMENT_END_REASONS, READY_END_REASON), required=True)
    sub.add_parser("resweep")
    final = sub.add_parser("finalize")
    final.add_argument("--transcript", type=Path, action="append", required=True)
    args = parser.parse_args()
    if not args.case:
        parser.error("--case or ASPIRE_NATIVE_CASE is required")
    if args.action == "trial" and args.seed is None and args.seeds is None:
        parser.error("the following arguments are required: --seed")
    if args.action == "trial" and args.seed is not None and args.seeds is not None:
        parser.error("--seed runs one trial and --seeds a batch sweep; pass one of them")
    try:
        case, repo, task_dir = load_case(Path(args.case))
        state = NativeWorldState(task_dir, identity(case, repo), resume=args.action != "init")
        if args.action == "trial":
            if args.replicate and case.get("prediction_contract") != SEARCH_CONTRACT:
                raise ProtocolError("--replicate exists only under the p3-search contract")
            if args.phase == SWEEP_PHASE and case.get("prediction_contract") != SEARCH_CONTRACT:
                raise ProtocolError("the coverage sweep exists only under the p3-search contract")
            declaration = None
            if args.mode is not None or args.mode_seeds is not None:
                try:
                    seeds = [int(s) for s in (args.mode_seeds or "").split(",") if s.strip()]
                except ValueError as exc:
                    raise ProtocolError(f"--mode-seeds takes comma-separated seeds: {exc}") from exc
                declaration = {"mode": args.mode or "", "target_seeds": seeds}
            if args.seeds is not None:
                if args.phase != SWEEP_PHASE or declaration or args.replicate:
                    raise ProtocolError("--seeds is the batch sweep: only `--phase sweep`, without "
                                        "--mode/--mode-seeds/--replicate")
                try:
                    batch = ("all" if args.seeds.strip() == "all" else
                             [int(s) for s in args.seeds.split(",") if s.strip()])
                except ValueError as exc:
                    raise ProtocolError(f"--seeds takes `all` or comma-separated seeds: {exc}") from exc
                result = run_sweep_batch(case, repo, state, batch, args.code, args.world_program,
                                         args.inventory)
            else:
                result = run_trial(case, repo, state, args.phase, args.seed, args.code,
                                   args.world_program, args.inventory, args.code,
                                   replicate=args.replicate, declaration=declaration)
        elif args.action == "alias-smoke":
            result = state.alias_smoke_as_initial(args.seed)
        elif args.action == "select":
            bundle = selected_bundle(case, state)
            errors = state.selection_errors(
                bundle, (state.task_dir / "fix_code.py").read_text())
            if errors:
                raise ProtocolError("; ".join(errors))
            result = state.select(bundle, args.reason)
        elif args.action == "end-development":
            if os.environ.get("ASPIRE_OUTER_DEVELOPMENT_END") != "1":
                raise ProtocolError("end-development is reserved for the outer driver")
            result = end_development(case, repo, state, args.reason, args.cleanup_receipt)
        elif args.action == "resweep":
            if os.environ.get("ASPIRE_OUTER_DEVELOPMENT_END") != "1":
                raise ProtocolError("the pre-freeze re-sweep is reserved for the outer driver")
            result = prefreeze_resweep(case, repo, state)
        elif args.action == "finalize":
            result = finalize(case, repo, state, args.transcript)
        elif args.action == "check":
            result = check(case, repo, state)
        else:
            result = state.progress()
            search = search_control(case, state)
            if search is not None:
                result["search_control"] = search
            if method_switches.enabled(case, "judgment_calibration") and not sealed_gate(case):
                from aspire.sim.cap.world_model.judgment_calibration import summary
                result["judgment_calibration"] = summary(state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int((args.action == "check" and not result["ready"])
                   or (args.action == "trial" and result["status"] != "complete"))
    except (ProtocolError, RuntimeChanged, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
