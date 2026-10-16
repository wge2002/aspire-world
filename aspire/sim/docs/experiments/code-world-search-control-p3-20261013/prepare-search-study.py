#!/usr/bin/env python3
"""Prepare and verify the p3 search-control study cells. Never launches a job implicitly.

Derived from the p2 study's `prepare-runtime-study.py` (frozen in the p2 result
root as `frozen-prepare-runtime-study-r3.py`, byte-identical). Every cell is a
fresh C/full/foundation-r1 cell with the oracle development gate, the same
local Qwen3.8-Flash-Next xhigh / 1M context / 64K output recipe, dev seeds 51-65
(3 attempts each), held-out 1-50, max_steps 4000 and the 900 s child watchdog.
The one declared difference from p2 is `prediction_contract = "p3-search"` and
the code it activates (`cap/world_model/search_control.py` plus the p3 hooks in
the already-overlaid protocol/profile/world sources).

Cells: stove x2, drawer x2, bowl x1 (bowl is the should-not-change control).
`--preflight-cell` additionally stages the one-off `stove_p3_preflight` cell under
`<result root>/preflight/`: configured as stove_p3_r1 except the DSW device and
service wiring (the DSW's GPUs 6/7 and ports 8114-8116 belong to another
project). It is not part of the study and is never counted.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "code-world-search-control-p3-20261013"

spec = importlib.util.spec_from_file_location("p3_stager", HERE / "support/legacy_stager.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

# ENGINEERING, A_SOURCE, REFERENCE, PACKAGE_SOURCE, VENV, PROFILES, SUITE,
# IGNORE, BASE_COMMIT, EXPECTED_A_SOURCE_FILES and STRATEGY_MD stay exactly as
# the base defines them (the judgment study's byte-verified Sep-14 reference).
base.__file__ = __file__
ORIGINAL_ENGINEERING = base.ENGINEERING
base.ENGINEERING = HERE.parents[2]
base.STUDY = Path("docs/experiments") / NAME
base.STUDY_DIR = base.ENGINEERING / base.STUDY
RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-" + NAME)
PARENT = Path("/mnt/home/gewang/experiments") / NAME
base.RUNTIME = RUNTIME
base.PARENT = PARENT
base.SUPPORT = base.STUDY_DIR / "support"
base.BASELINE_CONTRACT = base.STUDY_DIR / "baseline-contract.json"
base.CAMPAIGN_PLAN = base.STUDY_DIR / "campaign-plan.json"
base.EXPECTED_PROMPTS = base.STUDY_DIR / "reference/expected-baseline-prompts.json"
base.INTERFACE_DOC = base.STUDY / "NATIVE_WORLD_INTERFACE.md"
#: The interface document is p2's, unchanged: the p3 additions are a prompt
#: section (`executable_world_profile.SEARCH_CONTRACT`), not an interface change.
INTERFACE_DOCS = {"p3": Path("cap/world_model/RUNTIME_INTERFACE.md")}
INTERFACE_SHA256 = {}

TASKS = {"bowl": "put_the_bowl_on_the_plate",
         "drawer": "open_the_middle_drawer_of_the_cabinet",
         "stove": "turn_on_the_stove"}
base.TASKS = TASKS
ARMS = {"p3": "p3-search"}
GATE = "oracle"
#: Claim order of the shared queue: every first repeat before any second one.
CELLS = (("stove_p3_r1", "C"), ("drawer_p3_r1", "C"), ("bowl_p3_r1", "C"),
         ("stove_p3_r2", "C"), ("drawer_p3_r2", "C"))
base.CELLS = CELLS
PREFLIGHT_CELL = "stove_p3_preflight"
PREFLIGHT_PARENT = PARENT / "preflight"
PREFLIGHT_RUNTIME = RUNTIME / "preflight"
#: DSW wiring for the one-off preflight cell only. Study cells keep p2's DLC
#: wiring (sim GPU 6, services 8114-8116), which the supervisor starts itself.
PREFLIGHT_WIRING = {"gpu": 5, "cuda_visible_devices": "5", "egl_device_id": 5,
                    "service_ports": [8134, 8135, 8136],
                    "sam3_url": "http://127.0.0.1:8134", "graspnet_url": "http://127.0.0.1:8135",
                    "pyroki_url": "http://127.0.0.1:8136"}
IMPORT_PREFLIGHT = False


def arm_of(cell):
    arm = cell.split("_")[1]
    if arm not in ARMS:
        raise ValueError(f"unknown arm in cell id {cell!r}")
    return arm


def contract_of(cell):
    return ARMS[arm_of(cell)]


def interface_doc(cell):
    return INTERFACE_DOCS[arm_of(cell)]


def repeat_of(cell):
    tail = cell.split("_")[2]
    # The preflight cell is configured as stove_p3_r1.
    return 1 if tail == "preflight" else int(tail[1:])


#: Applied identically to every cell (all C). Unchanged from p2.
COMMON_OVERLAY = ("scripts/libero/native_world_campaign.py",
                  "scripts/libero/native_world_protocol.py",
                  "scripts/libero/native_world_fixloop_state.py",
                  "scripts/libero/native_world_heldout.py",
                  "scripts/libero/replay_trial.py",
                  "scripts/libero/simple_world_profile.py",
                  "scripts/libero/gate_study_analysis.py",
                  "scripts/libero/prediction_study_analysis.py",
                  "scripts/common/native_cc_stream.py",
                  "cap/envs/tasks/base.py")
#: p2's C overlay plus the search-control library.
C_ONLY_OVERLAY = ("cap/world_model/judgment_world.py",
                  "cap/world_model/simple_world.py",
                  "cap/world_model/executable_world.py",
                  "cap/world_model/evidence_state.py",
                  "cap/world_model/decision_revision.py",
                  "cap/world_model/development_gate.py",
                  "cap/world_model/vlm_judge.py",
                  "cap/world_model/foundation_audit.py",
                  "cap/world_model/world_use_audit.py",
                  "cap/world_model/prediction_contract.py",
                  "cap/world_model/runtime_state.py",
                  "cap/world_model/runtime_predictions.py",
                  "cap/world_model/runtime_mechanism_audit.py",
                  "cap/world_model/search_control.py",
                  "cap/world_model/RUNTIME_INTERFACE.md",
                  "scripts/libero/runtime/native_world_protocol.py",
                  "scripts/libero/runtime/replay_trial.py",
                  "scripts/libero/executable_world_profile.py")
base.C_OVERLAY = COMMON_OVERLAY + C_ONLY_OVERLAY

#: Frozen at <result root>/support. The whole support directory except the
#: stager, which is a prepare-time input recorded by its own digest.
base.SUPPORT_FILES = ("run_two_task_cell.py", "cell_read_guard.py", "output_ownership.py",
                      "lineage.py", "native_lineage_r2.py", "native_cc_stream.py",
                      "infra_guard.py", "two_task_scope.py",
                      "pilot_assignment_guard.py", "two_task_render.py", "full_deadlines.py",
                      "heldout_stop_on_infra.py", "runtime_limits.py", "canonical_assignment.py",
                      "pristine_a_render.py", "dlc-supervisor.py", "dlc-single-node-entry.sh",
                      "schedule.py", "sam3_service_health.py", "foundation_import.py",
                      "qwen-native-compat-r2.py")
sys.path.insert(0, str(HERE / "support"))
import full_deadlines
import output_ownership
import two_task_scope

V4_LAUNCH = Path("/mnt/home/gewang/experiments/code-world-qwen-debug-20260922-retry4"
                 "/launch-20260923-v4")
LAUNCH = PARENT / "launch"
#: The same verified deployment inputs every p2 revision launched from, by hash.
LAUNCH_PINS = {
    "dlc-entry.sh": "f97a49d25e70b7811c477d0a8c27cc5a74388dca275a5f1ae545036583796641",
    "qwen-native-compat.py": "7e9cd4509efee2c6da7976767b471b54dac65e755915149f99de494e3284d67b",
    "reference/model-server.json": "22c61284fd39db1310bc7cecbb09ec06ddcb417aeb4156fbea8a163829985c67",
    "reference/native_cc_runtime.py": "36a1d1d118faaec4f30723aff02b804b83fabf3cae94b1891df7d308bb32edda",
}
#: Frozen from this study's support/reference into LAUNCH: the r9-derived
#: supervisor, single-node entry and scheduling guard, and the SAM3 health probe.
STUDY_LAUNCH_FILES = ("dlc-supervisor.py", "dlc-single-node-entry.sh", "schedule.py",
                      "sam3_service_health.py")
STUDY_LAUNCH_REFERENCE = ("reference/sam3-public-dev51-agentview.jpg",
                          "reference/sam3-public-image-provenance.json")
LAUNCH_FILES = tuple(LAUNCH_PINS) + STUDY_LAUNCH_FILES + STUDY_LAUNCH_REFERENCE

GPU = 6
DEV_SEEDS = list(range(51, 66))
HELDOUT_SEEDS = list(range(1, 51))
MODEL = "qwen3.8-flash-next"
ENDPOINT = "http://127.0.0.1:8121"
CLAUDE_BIN = "/mnt/home/gewang/.local/share/claude/versions/2.1.220"

CONTRACT_SOURCE = (ORIGINAL_ENGINEERING
                   / "docs/experiments/code-world-rerun-20260919/baseline-contract.json")
SKILL_MD_DIR = ".claude/libero/skills"

original_case = base.case_config
original_stage = base.stage_cell
original_external = base.external_inputs


def case_config(cell, condition):
    """p2's case recipe; only the contract (and, for the preflight cell, device wiring) differ."""
    case = original_case(cell, condition)
    case.update(
        gpu=GPU, cuda_visible_devices=str(GPU), egl_device_id=GPU,
        dev_seeds=list(DEV_SEEDS), heldout_seeds=list(HELDOUT_SEEDS),
        campaign_timeout=full_deadlines.DEVELOPMENT,
        model_provider="local-vllm", inference_endpoint=ENDPOINT,
        model=MODEL, model_tag=MODEL, expected_served_model=MODEL,
        effort="xhigh", context_tokens=1000000, max_output_tokens=64000,
        claude_bin=CLAUDE_BIN,
        development_gate=GATE, repeat=repeat_of(cell),
        generation_context=(f"native Qwen {condition} fresh generation, {case['task']}, "
                            f"development gate {GATE}, repeat {repeat_of(cell)}, p3-search; "
                            "dev51-65 then frozen heldout1-50"))
    if cell == PREFLIGHT_CELL:
        case.update(PREFLIGHT_WIRING)
        case["generation_context"] = ("ONE-OFF DSW PREFLIGHT, not a study cell: " + case["generation_context"])
    case.update(executable_world_revision="r1", foundation_revision="r1", world_use_revision="r1",
                c_arm="full", c_lineage="fresh", world_interface_doc=str(interface_doc(cell)),
                prediction_contract=contract_of(cell))
    case.pop("api_key_helper", None)
    case.pop("disable_experimental_betas", None)
    return case


class per_cell_interface:
    """Point the base's single INTERFACE_DOC at this cell's arm document."""

    def __init__(self, cell):
        self.arm, self.doc = arm_of(cell), interface_doc(cell)

    def __enter__(self):
        expected = INTERFACE_SHA256.get(self.arm)
        if expected and base.digest(base.ENGINEERING / self.doc) != expected:
            raise SystemExit(f"the {self.arm} arm's interface document changed: {self.doc}")
        self.saved, base.INTERFACE_DOC = base.INTERFACE_DOC, self.doc

    def __exit__(self, *exc):
        base.INTERFACE_DOC = self.saved


def stage_cell(cell, condition, contract):
    """Stage one C cell. The base does the A source, removals, C overlay and links."""
    with per_cell_interface(cell):
        case, changes = original_stage(cell, condition, contract)
    sim = base.cell_sim(cell)
    changes["common_overlay"] = list(COMMON_OVERLAY)
    changes["c_only_overlay"] = list(C_ONLY_OVERLAY)
    # Condition C has no high-level strategy library.
    removed = {}
    for path in sorted((sim / SKILL_MD_DIR).glob("*.md")):
        relative = str(path.relative_to(sim))
        removed[relative] = base.digest(path)
        changes["removed"].append({"path": relative, "source_sha256": removed[relative],
                                   "reason": "condition C has no high-level strategy MD"})
        path.unlink()
    changes["removed_strategy_md"] = removed
    changes["output_ownership"] = output_ownership.verify(case, sim)
    changes["no_prior_inputs"] = {"c_starter": None, "imported_diagnostic": None, "imported_tape": None,
                                  "note": "fresh generation; no diagnostic imported; no prior solution or tape"}
    return case, changes


def external_inputs(case):
    extra = [LAUNCH / n for n in (*LAUNCH_FILES, "launch-manifest.json")]
    with per_cell_interface(case["id"]):
        return original_external(case) + extra


def render_prompts(case):
    """Render through the frozen gate, in a fresh subprocess per cell."""
    import subprocess
    sim = base.cell_sim(case["id"])
    renderer = base.PARENT / "support/two_task_render.py"
    if not renderer.is_file():
        raise SystemExit(f"frozen renderer missing: {renderer}")
    command = [str(sim / ".venv-libero/bin/python3"), "-B", str(renderer),
               "--case", str(Path(case["control"]) / "case.json")]
    proc = subprocess.run(command, cwd=sim, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: gated prompt rendering failed:\n{proc.stderr[-4000:]}")
    render = json.loads(proc.stdout)
    render["command"] = command
    return render


def verify_prompts(case, oracles):
    """C only: record the rendered prompts' digests (there is no A oracle)."""
    saved = {}
    for name in ("worker-prompt.md", "coordinator-prompt.md"):
        path = Path(case["control"]) / name
        if not path.is_file():
            raise SystemExit(f"{case['id']}: {name} was not rendered")
        saved[name] = base.digest(path)
    return saved


base.case_config = case_config
base.stage_cell = stage_cell
base.external_inputs = external_inputs
base.render_prompts = render_prompts
base.verify_prompts = verify_prompts


# ---- plan and launch ------------------------------------------------------


def campaign_plan() -> dict:
    return {
        "study_id": NAME, "status": "prepared_not_launched",
        "kind": "p3 search-control fresh C study (signature early stop + class saturation)",
        "parent_study": "code-world-runtime-p2-20261011 (r9 deployment chain)",
        "arms": dict(ARMS), "development_gate": GATE,
        "interface_docs": {arm: str(doc) for arm, doc in INTERFACE_DOCS.items()},
        "tasks": TASKS,
        "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
        "cells": [{"id": cell, "condition": condition, "development_gate": GATE,
                   "arm": arm_of(cell), "prediction_contract": contract_of(cell),
                   "world_interface_doc": str(interface_doc(cell)), "repeat": repeat_of(cell),
                   "task": TASKS[cell.split("_")[0]], "profile": base.PROFILES[condition],
                   "runtime": str(base.cell_sim(cell)), "control": str(PARENT / cell),
                   "outputs": str(PARENT / cell / "outputs")}
                  for cell, condition in CELLS],
        "serial_order": [cell for cell, _ in CELLS],
        "development_seeds": DEV_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
        "budgets": {"max_retries_per_dev_seed": two_task_scope.RETRY_LIMIT, "imported_retries": {},
                    "remaining_retries": two_task_scope.remaining_budget({"dev_seeds": DEV_SEEDS})},
        "deadlines": full_deadlines.summary(),
        "prior_inputs": {"c_starter": None, "imported_diagnostic": None, "old_solutions": None,
                         "old_tapes": None, "note": "fresh task-specific generation in every cell"},
        "experiment_model": {"provider": "local-vllm", "endpoint": ENDPOINT, "model": MODEL,
                             "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000,
                             "claude_bin": CLAUDE_BIN, "provider_fallback": None},
        "gpu_mapping": {"model": [0, 1, 2, 3], "sam3": 4, "graspnet": 5, "sim_pyroki": 6, "spare": 7},
        "launch_shape": {"mode": "shared serial queue served by independent 1x8-GPU jobs",
                         "entry": "launch/dlc-single-node-entry.sh --node-id N --node-count M --node-window-minutes W"},
        "search_control": {"early_stop_k": 3, "saturation": {"K": 5, "N": 8, "frac": 0.3, "gripper_match": 0.8},
                           "signature": "prediction-status counts + rounded object residuals + outcome",
                           "per_trial_prediction_levels": "gripper >= 1 and object >= 1, judged on the rehearsal"},
        "heldout_judge": None, "executable_world_revision": "r1", "c_arm": "full", "c_lineage": "fresh",
        "common_overlay": list(COMMON_OVERLAY), "c_only_overlay": list(C_ONLY_OVERLAY),
    }


def refuse_existing(*paths):
    for path in paths:
        if path.exists() or path.is_symlink():
            raise SystemExit(f"{path} exists; preserve it and use fresh roots")


def stage_launch():
    for name, expected in LAUNCH_PINS.items():
        source = V4_LAUNCH / name
        if base.digest(source) != expected:
            raise SystemExit(f"verified-v4 launch input changed: {source}")
        if name == "qwen-native-compat.py":
            source = base.SUPPORT / "qwen-native-compat-r2.py"
        base.freeze_bytes(source, LAUNCH / name)
    for name in STUDY_LAUNCH_FILES:
        base.freeze_bytes(base.SUPPORT / name, LAUNCH / name)
    for name in STUDY_LAUNCH_REFERENCE:
        base.freeze_bytes(base.STUDY_DIR / name, LAUNCH / name)
    base.record(LAUNCH / "launch-manifest.json", {
        "files": {name: base.digest(LAUNCH / name) for name in LAUNCH_FILES},
        "revision": "search-control-p3-20261014-queue",
        "derived_from": "/mnt/home/gewang/experiments/code-world-runtime-p2-20261011/launch-20261011-r9",
        "deployment_source": str(V4_LAUNCH),
        "scope": "per cell: dev51-65 then immutable outer heldout1-50; cells served serially from a shared queue",
        "entries": {"single_cell": "dlc-entry.sh --case <cell>/case.json",
                    "queue": "dlc-single-node-entry.sh --node-id N --node-count M --node-window-minutes W"},
        "serial_order": [cell for cell, _ in CELLS],
        "single_cell_watchdogs": full_deadlines.summary(),
        "not_carried_from_r9": ["continuation_driver*.py", "recover-native-gate*.py", "recovery_guard*.py",
                                "startup_gate*.py", "successor_*_r1.py", "recover_successor_r1.py",
                                "stage-deployment-r8/r9.py", "submit-runtime-study-r8.py",
                                "verify-staging-successor-r8.py",
                                "reason: p2 recovery/successor/old-job-replacement patches; the r9 supervisor "
                                "reached them only through p2 recovery checkpoints a fresh p3 cell never has"]})


def write_support_manifest(frozen):
    stager = base.STUDY_DIR / "support/legacy_stager.py"
    base.record(base.PARENT / "support-freeze-manifest.json", {
        "frozen_at": base.now(), "support_dir": str(base.PARENT / "support"),
        "files": frozen, "stager": {"path": str(stager), "sha256": base.digest(stager),
                                    "frozen": False, "note": "prepare-time input only"}})


def prepare(cells, *, parent=PARENT) -> dict:
    preflight = parent == PREFLIGHT_PARENT
    base.STUDY_DIR.mkdir(parents=True, exist_ok=True)
    base.EXPECTED_PROMPTS.parent.mkdir(parents=True, exist_ok=True)
    if not preflight:
        refuse_existing(RUNTIME / "cells", base.CAMPAIGN_PLAN, base.BASELINE_CONTRACT,
                        base.EXPECTED_PROMPTS, PARENT / "prepare-receipt.json")
    required = [CONTRACT_SOURCE, base.A_SOURCE]
    required += [base.ENGINEERING / doc for doc in INTERFACE_DOCS.values()]
    required += [base.ENGINEERING / n for n in base.C_OVERLAY]
    required += [base.SUPPORT / n for n in base.SUPPORT_FILES]
    required += [V4_LAUNCH / n for n in LAUNCH_PINS]
    required += [base.STUDY_DIR / n for n in STUDY_LAUNCH_REFERENCE]
    for path in required:
        if not path.exists():
            raise SystemExit(f"required input missing: {path}")
    if preflight:
        if not (PARENT / "prepare-receipt.json").is_file():
            raise SystemExit("stage the study first; the preflight cell reuses its launch and contract")
    else:
        base.BASELINE_CONTRACT.write_bytes(CONTRACT_SOURCE.read_bytes())
        base.record(base.CAMPAIGN_PLAN, campaign_plan())
        stage_launch()
    frozen = base.freeze_support()
    write_support_manifest(frozen)
    contract = json.loads(base.BASELINE_CONTRACT.read_text())

    staged = {}
    order = [(PREFLIGHT_CELL, "C")] if preflight else CELLS
    for cell, condition in order:
        if cell not in cells:
            continue
        case, differences = stage_cell(cell, condition, contract)
        case = base.bind_manifest(case)
        render = render_prompts(case)
        prompts = verify_prompts(case, {})
        case_path = Path(case["control"]) / "case.json"
        base.record(Path(case["control"]) / "staged-inputs.json", {
            "cell": cell, "condition": condition, "profile": base.PROFILES[condition],
            "task": case["task"], "staged_at": base.now(), "base_commit": base.BASE_COMMIT,
            "source_differences": differences, "frozen_support": frozen,
            "external_inputs": [str(p) for p in external_inputs(case)]})
        staged[cell] = {"case": str(case_path), "case_sha256": base.digest(case_path),
                        "condition": condition, "profile": base.PROFILES[condition],
                        "task": case["task"], "gpu": case["gpu"],
                        "runtime_manifest": case["runtime_manifest"],
                        "runtime_manifest_sha256": case["runtime_manifest_sha256"],
                        "prompt_sha256": prompts, "prompt_oracle": None,
                        "source_differences": differences, "render": render}
    if not preflight:
        base.record(base.EXPECTED_PROMPTS, {})
    frozen_prepare = base.PARENT / "frozen-prepare-search-study.py"
    prepare_sha = base.freeze_bytes(Path(__file__), frozen_prepare)
    receipt = {"prepared_at": base.now(), "study": str(base.STUDY),
               "runtime_root": str(base.RUNTIME), "result_parent": str(base.PARENT),
               "driver": str(base.PARENT / "support/run_two_task_cell.py"),
               "frozen_support": frozen,
               "support_freeze_manifest": str(base.PARENT / "support-freeze-manifest.json"),
               "runtime_sha256": {cell: {n: base.digest(base.cell_sim(cell) / n) for n in base.C_OVERLAY}
                                  for cell in staged},
               "prepare_source_sha256": prepare_sha,
               "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
               "campaign_plan_sha256": base.digest(base.CAMPAIGN_PLAN),
               "launch_dir": str(LAUNCH),
               "launch_manifest_sha256": base.digest(LAUNCH / "launch-manifest.json"),
               "serial_order": list(staged), "cells": staged, "launched": False,
               "preflight_only": preflight,
               "note": ("ONE-OFF DSW preflight cell; not a study cell and never counted" if preflight else
                        "staged only; verify environment and actual native work, then submit once.")}
    base.record(base.PARENT / "prepare-receipt.json", receipt)
    print(json.dumps({"prepared_at": receipt["prepared_at"],
                      "cells": {c: staged[c]["case"] for c in staged},
                      "launch_dir": str(LAUNCH), "launched": False}, indent=2))
    return receipt


class preflight_roots:
    """Point the stager at <result root>/preflight and <runtime root>/preflight."""

    def __enter__(self):
        self.saved = base.PARENT, base.RUNTIME
        base.PARENT, base.RUNTIME = PREFLIGHT_PARENT, PREFLIGHT_RUNTIME

    def __exit__(self, *exc):
        base.PARENT, base.RUNTIME = self.saved


# ---- verify ---------------------------------------------------------------


COMMON_EXPECTED = {
    "model_provider": "local-vllm", "inference_endpoint": ENDPOINT,
    "model": MODEL, "model_tag": MODEL, "expected_served_model": MODEL,
    "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000,
    "claude_bin": CLAUDE_BIN, "gpu": GPU, "cuda_visible_devices": str(GPU),
    "egl_device_id": GPU, "dev_seeds": DEV_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
    "suite": "libero_goal_swap",
    "env_config": "env_configs/libero/franka_libero_traced.yaml",
    "sam3_url": "http://127.0.0.1:8114", "graspnet_url": "http://127.0.0.1:8115",
    "pyroki_url": "http://127.0.0.1:8116", "service_ports": [8114, 8115, 8116],
    "max_steps": 4000, "trial_timeout": 900,
    "campaign_timeout": full_deadlines.DEVELOPMENT,
    "require_runtime_freeze": True,
    "condition": "C", "profile": "judgment", "executable_world_revision": "r1",
    "c_arm": "full", "c_lineage": "fresh", "foundation_revision": "r1", "world_use_revision": "r1",
    "development_gate": GATE,
}
FORBIDDEN_CASE_KEYS = ("api_key_helper", "disable_experimental_betas", "c_starter",
                       "full_interface_doc", "pilot_interface_doc", "closed_loop_revision",
                       "imported_retries", "diagnostic_import", "judge")


def check_case(cell, case) -> dict:
    expected = {**COMMON_EXPECTED, "id": cell, "task": TASKS[cell.split("_")[0]],
                "world_interface_doc": str(interface_doc(cell)), "repeat": repeat_of(cell),
                "prediction_contract": contract_of(cell)}
    if cell == PREFLIGHT_CELL:
        expected.update(PREFLIGHT_WIRING)
    problems = [f"case[{key!r}] is {case.get(key)!r}, expected {value!r}"
                for key, value in expected.items() if case.get(key) != value]
    problems += [f"case still carries {key!r}" for key in FORBIDDEN_CASE_KEYS if key in case]
    model_keys = sorted(k for k, v in case.items()
                        if "model" in k and k != "model_provider" and isinstance(v, str))
    if model_keys != ["expected_served_model", "model", "model_tag"]:
        problems.append(f"unexpected model-bearing string keys: {model_keys}")
    return {"checked": sorted(expected), "model_keys": model_keys, "problems": problems,
            "preflight_wiring": PREFLIGHT_WIRING if cell == PREFLIGHT_CELL else None}


def check_strategy_md(case) -> dict:
    sim = Path(case["sim"])
    strategy = sim / case["skill_library_dir"]
    shared = sorted(p.name for p in (sim / SKILL_MD_DIR).glob("*.md"))
    private = sorted(p.name for p in strategy.glob("*")) if strategy.is_dir() else None
    problems = []
    if shared:
        problems.append(f"high-level strategy MD survived in a C runtime: {shared}")
    if private != ["README.md"]:
        problems.append(f"C notes directory is {private}, expected only README.md")
    return {"shared_skills_md": shared, "private": private, "problems": problems}


def check_prompt(case, entry) -> dict:
    problems, hashes = [], {}
    control = Path(case["control"])
    for role, name in (("worker", "worker-prompt.md"), ("coordinator", "coordinator-prompt.md")):
        path = control / name
        text = path.read_text()
        try:
            if role == "worker":
                two_task_scope.verify_worker(text, case, Path(case["sim"]))
            else:
                two_task_scope.verify_coordinator(text, case)
        except two_task_scope.ScopeError as exc:
            problems.append(f"{name}: {exc}")
        hashes[name] = base.digest(path)
        if hashes[name] != entry["prompt_sha256"][name]:
            problems.append(f"{name} changed since --prepare")
    worker = (control / "worker-prompt.md").read_text()
    for marker in ("PRIMITIVE_CLASS", "progress()", "--replicate", "search_control_report.json",
                   "gripper-level AND at least one", "Physically infeasible"):
        if marker not in worker:
            problems.append(f"worker prompt lacks the p3 marker {marker!r}")
    return {"sha256": hashes, "problems": problems}


def check_isolation(cells) -> dict:
    problems = []
    seen: dict[str, dict] = {}
    for cell, case in cells.items():
        control = Path(case["control"]).resolve()
        canonical = (control / "outputs").resolve()
        link = Path(case["sim"]) / "outputs"
        for field, value in (("sim", Path(case["sim"]).resolve()), ("control", control),
                             ("outputs", canonical),
                             ("claude_config_dir", Path(case["claude_config_dir"]))):
            owner = seen.setdefault(f"{field}:{value}", {"cell": cell})
            if owner["cell"] != cell:
                problems.append(f"{cell} shares {field} {value} with {owner['cell']}")
        if not link.is_symlink() or link.resolve() != canonical:
            problems.append(f"{cell}: sim/outputs does not resolve to {canonical}")
    return {"cells": sorted(cells), "problems": problems}


def check_runtime(cell, case, receipt) -> dict:
    problems = []
    sim = Path(case["sim"])
    for name, expected in receipt["runtime_sha256"][cell].items():
        if base.digest(sim / name) != expected:
            problems.append(f"runtime source changed: {name}")
        engineering = base.ENGINEERING / name
        if engineering.is_file() and base.digest(engineering) != expected:
            problems.append(f"engineering source drifted after staging: {name}")
    try:
        ownership = output_ownership.verify(case, sim)
    except output_ownership.OwnershipError as exc:
        ownership = None
        problems.append(str(exc))
    allowed = {str(Path(case["skill_library_dir"]) / "README.md"), str(interface_doc(cell))}
    unexpected = [str(p.relative_to(sim)) for p in base.tree_files(sim / "docs")
                  if str(p.relative_to(sim)) not in allowed]
    if unexpected:
        problems.append(f"undeclared documentation/history in solver runtime: {unexpected}")
    launch = json.loads((LAUNCH / "launch-manifest.json").read_text())
    for name in LAUNCH_FILES:
        if launch["files"].get(name) != base.digest(LAUNCH / name):
            problems.append(f"launch file changed: {name}")
    if receipt.get("launch_manifest_sha256") != base.digest(LAUNCH / "launch-manifest.json"):
        problems.append("launch manifest changed since --prepare")
    return {"ownership": ownership, "undeclared_docs": unexpected, "problems": problems}


def check_no_prior_input(case) -> dict:
    sim, control = Path(case["sim"]), Path(case["control"])
    problems = []
    for pattern in ("**/prior_c", "**/dsw-preflight", "**/starters", "**/*_tape.jsonl",
                    "**/fix_code.py", "**/fix_world_program.py"):
        found = [str(p.relative_to(sim)) for p in sim.glob(pattern)]
        if found:
            problems.append(f"prior-input artifact staged into runtime: {found}")
    outputs = control / "outputs"
    contents = sorted(p.name for p in outputs.iterdir()) if outputs.is_dir() else None
    started = [n for n in ("campaign_state.json", "native_smoke_state.json", "queue-result.json",
                           "probe.json") if (control / n).exists()]
    if contents != ["working_codes"] or started:
        problems.append(f"cell is not fresh: outputs={contents}, started={started}")
    return {"problems": problems, "outputs": contents, "started": started}


def verify(cells, *, parent=PARENT) -> dict:
    receipt = json.loads((parent / "prepare-receipt.json").read_text())
    resolved, checks = {}, {}
    fresh_required = parent == PARENT
    for cell in [c for c in receipt["serial_order"] if c in cells]:
        entry = receipt["cells"][cell]
        case = base.verify_cell(entry, receipt["frozen_support"])
        resolved[cell] = case
        checks[cell] = {"case": check_case(cell, case),
                        "strategy_md": check_strategy_md(case),
                        "prompt": check_prompt(case, entry),
                        "runtime": check_runtime(cell, case, receipt)}
        if fresh_required:
            checks[cell]["no_prior_input"] = check_no_prior_input(case)
    checks["isolation"] = check_isolation(resolved)
    manifests = {"support_freeze_manifest": parent / "support-freeze-manifest.json",
                 "launch_manifest": LAUNCH / "launch-manifest.json"}
    checks["manifests"] = {"paths": {k: str(v) for k, v in manifests.items()},
                           "problems": [f"missing {k}: {v}" for k, v in manifests.items() if not v.is_file()]}
    problems = [f"{name}: {p}" for name, group in checks.items()
                for check in (group.values() if "problems" not in group else [group])
                for p in check["problems"]]
    result = {"verified_at": base.now(), "study": str(base.STUDY),
              "result_parent": str(parent), "runtime_root": receipt["runtime_root"],
              "driver": receipt["driver"], "frozen_support": receipt["frozen_support"],
              "cells": {cell: {"case": receipt["cells"][cell]["case"],
                               "case_sha256": receipt["cells"][cell]["case_sha256"],
                               "task": receipt["cells"][cell]["task"],
                               "sim": resolved[cell]["sim"], "control": resolved[cell]["control"],
                               "prompt_sha256": receipt["cells"][cell]["prompt_sha256"]}
                        for cell in resolved},
              "verified_cells": len(resolved), "checks": checks, "problems": problems,
              "status": "verified" if not problems and resolved else "problems_found"}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true",
                      help="stage the selected cells; start no model, simulator or job")
    mode.add_argument("--verify", action="store_true", help="re-verify the already staged cells")
    parser.add_argument("--cells", default=",".join(c for c, _ in CELLS))
    parser.add_argument("--preflight-cell", action="store_true",
                        help=f"operate on the one-off {PREFLIGHT_CELL} under <result root>/preflight")
    args = parser.parse_args()
    if args.preflight_cell:
        with preflight_roots():
            if args.prepare:
                prepare([PREFLIGHT_CELL], parent=PREFLIGHT_PARENT)
            else:
                raise SystemExit(0 if verify([PREFLIGHT_CELL], parent=PREFLIGHT_PARENT)["status"] == "verified" else 1)
        raise SystemExit(0)
    selected = [c.strip() for c in args.cells.split(",") if c.strip()]
    unknown = [c for c in selected if c not in {name for name, _ in CELLS}]
    if unknown:
        raise SystemExit(f"unknown cells: {unknown}")
    if args.prepare:
        prepare(selected)
    else:
        raise SystemExit(0 if verify(selected)["status"] == "verified" else 1)
