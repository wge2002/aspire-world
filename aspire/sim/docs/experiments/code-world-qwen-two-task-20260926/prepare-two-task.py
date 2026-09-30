#!/usr/bin/env python3
"""Stage/verify four fresh Qwen cells: two tasks x (A original, C executable world).

Runs no model, no simulator and no experiment. `--prepare` writes four isolated
runtimes, cases and prompts; `--verify` re-checks what was staged.

What this study is, in the two places it differs from the Sep-24 full run:

* Four cells, not one. Two `libero_goal_swap` tasks, each in condition A (the
  original Fix Loop with the four high-level strategy documents, no world) and
  condition C (executable world `r1`, no strategy documents). Nothing crosses
  between cells: separate runtime tree, control root, canonical outputs,
  strategy directory, native config directory and rendered prompts.
* Fresh generation. No prior-C starter, no imported charged diagnostic, no old
  solution, tape or result is staged as solver input. Every development seed
  starts with all three attempts, which is what the frozen template already
  states, so nothing about the budget has to be rewritten into the prompt.

Both arms are staged from the same byte-verified Sep-14 A1 reference tree. A
common overlay is applied identically to both, because the pristine Sep-14
renderer cannot reach a local provider and carries the pre-repair protocol,
ledger, held-out and transport modules. That overlay is not taken on trust: for
each A cell the prompts are rendered a second time from the untouched pristine
tree by `support/pristine_a_render.py` and required to match byte for byte. A
C-only overlay adds the executable world on top, for C cells only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "code-world-qwen-two-task-20260926"

spec = importlib.util.spec_from_file_location("qwen_stager", HERE / "support/legacy_stager.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

# ENGINEERING, A_SOURCE, REFERENCE, PACKAGE_SOURCE, VENV, PROFILES, SUITE,
# IGNORE, BASE_COMMIT, EXPECTED_A_SOURCE_FILES and STRATEGY_MD stay exactly as
# the base defines them. A_SOURCE is the judgment study's byte-verified Sep-14
# reference, not anything in this study and not the engineering scripts.
base.__file__ = __file__
base.STUDY = Path("docs/experiments") / NAME
base.STUDY_DIR = base.ENGINEERING / base.STUDY
base.RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-" + NAME)
base.PARENT = Path("/mnt/home/gewang/experiments") / NAME
base.SUPPORT = base.STUDY_DIR / "support"
base.BASELINE_CONTRACT = base.STUDY_DIR / "baseline-contract.json"
base.CAMPAIGN_PLAN = base.STUDY_DIR / "campaign-plan.json"
base.EXPECTED_PROMPTS = base.STUDY_DIR / "reference/expected-baseline-prompts.json"
base.INTERFACE_DOC = base.STUDY / "NATIVE_WORLD_INTERFACE.md"

TASKS = {"bowldrawer": "open_the_top_drawer_and_put_the_bowl_inside",
         "drawer": "open_the_middle_drawer_of_the_cabinet"}
base.TASKS = TASKS
#: Serial order. Both arms of the easier task first, so a systemic staging or
#: provider fault surfaces on the task the paper reports as more tractable.
CELLS = (("bowldrawer_A", "A"), ("bowldrawer_C", "C"),
         ("drawer_A", "A"), ("drawer_C", "C"))
base.CELLS = CELLS

#: Applied identically to A and C. Each entry differs from the Sep-14 reference
#: and is required by one of: the local Qwen provider branch, the repaired
#: protocol/ledger/held-out modules, or the corrected turn-aware transport.
#: `simple_world_profile.py` is here only because the campaign module imports it
#: at import time; its `is_simple` returns False for both of this study's
#: profiles, so no world code is reachable from an A cell.
COMMON_OVERLAY = ("scripts/libero/native_world_campaign.py",
                  "scripts/libero/native_world_protocol.py",
                  "scripts/libero/native_world_fixloop_state.py",
                  "scripts/libero/native_world_heldout.py",
                  "scripts/libero/replay_trial.py",
                  "scripts/libero/simple_world_profile.py",
                  "scripts/common/native_cc_stream.py")
#: C only: the executable world itself. An A cell's tree contains none of it.
C_ONLY_OVERLAY = ("cap/world_model/judgment_world.py",
                  "cap/world_model/simple_world.py",
                  "cap/world_model/executable_world.py",
                  "cap/world_model/world_use_audit.py",
                  "scripts/libero/executable_world_profile.py")
base.C_OVERLAY = COMMON_OVERLAY + C_ONLY_OVERLAY

base.SUPPORT_FILES = ("run_two_task_cell.py", "cell_read_guard.py", "output_ownership.py",
                      "lineage.py", "native_lineage_r2.py", "native_cc_stream.py",
                      "infra_guard.py", "two_task_scope.py",
                      "pilot_assignment_guard.py", "two_task_render.py", "full_deadlines.py",
                      "heldout_stop_on_infra.py", "pristine_a_render.py", "dlc-supervisor.py")
sys.path.insert(0, str(HERE / "support"))
import full_deadlines
import output_ownership
import pristine_a_render
import two_task_scope

V4_LAUNCH = Path("/mnt/home/gewang/experiments/code-world-qwen-debug-20260922-retry4"
                 "/launch-20260923-v4")
LAUNCH = base.PARENT / "launch-20260926"
#: The same verified deployment inputs the Sep-24 run launched from, by hash.
LAUNCH_PINS = {
    "dlc-entry.sh": "f97a49d25e70b7811c477d0a8c27cc5a74388dca275a5f1ae545036583796641",
    "qwen-native-compat.py": "7e9cd4509efee2c6da7976767b471b54dac65e755915149f99de494e3284d67b",
    "reference/model-server.json": "22c61284fd39db1310bc7cecbb09ec06ddcb417aeb4156fbea8a163829985c67",
    "reference/native_cc_runtime.py": "36a1d1d118faaec4f30723aff02b804b83fabf3cae94b1891df7d308bb32edda",
}
LAUNCH_FILES = tuple(LAUNCH_PINS) + ("dlc-supervisor.py",)

GPU = 6
DEV_SEEDS = list(range(51, 66))
HELDOUT_SEEDS = list(range(1, 51))
MODEL = "qwen3.8-flash-next"
ENDPOINT = "http://127.0.0.1:8121"
CLAUDE_BIN = "/mnt/home/gewang/.local/share/claude/versions/2.1.220"

CONTRACT_SOURCE = (base.ENGINEERING
                   / "docs/experiments/code-world-rerun-20260919/baseline-contract.json")
SKILL_MD_DIR = ".claude/libero/skills"

original_case = base.case_config
original_stage = base.stage_cell
original_external = base.external_inputs


def case_config(cell, condition):
    """The base case, rewritten to this study's device, provider, scope and lineage.

    Watchdogs, service URLs, ports, env wiring and the nonprivileged traced
    config stay the base's. `api_key_helper` and `disable_experimental_betas`
    are removed outright: the local-vllm branch has no credential and never
    reads the beta flag, so leaving either present would be a stale claim.
    """
    case = original_case(cell, condition)
    case.update(
        gpu=GPU, cuda_visible_devices=str(GPU), egl_device_id=GPU,
        dev_seeds=list(DEV_SEEDS), heldout_seeds=list(HELDOUT_SEEDS),
        campaign_timeout=full_deadlines.DEVELOPMENT,
        model_provider="local-vllm", inference_endpoint=ENDPOINT,
        model=MODEL, model_tag=MODEL, expected_served_model=MODEL,
        effort="xhigh", context_tokens=1000000, max_output_tokens=64000,
        claude_bin=CLAUDE_BIN,
        # Nothing is imported, so the ledger's uniform three-per-seed budget is
        # the truth and the prompt needs no budget rewrite. Declared explicitly
        # so the gate can check it rather than infer it from an absence.
        imported_charged_attempts={},
        generation_context=(f"native Qwen {condition} fresh generation, {case['task']}; "
                            "dev51-65 then frozen heldout1-50"))
    if condition == "C":
        case.update(executable_world_revision="r1", c_arm="full",
                    # Fresh: no prior C bundle exists for either task, so no
                    # starter is declared and the profile refuses one.
                    c_lineage="fresh",
                    world_interface_doc=str(base.INTERFACE_DOC))
    case.pop("api_key_helper", None)
    case.pop("disable_experimental_betas", None)
    return case


def stage_cell(cell, condition, contract):
    """Stage one cell. The base does the A source, removals, C overlay and links."""
    case, changes = original_stage(cell, condition, contract)
    sim = base.cell_sim(cell)

    # The common overlay, applied to both arms. The base already applied the
    # whole C_OVERLAY for C, so only A still needs this half of it.
    if condition == "A":
        for relative in COMMON_OVERLAY:
            source = base.ENGINEERING / relative
            if not source.is_file():
                raise SystemExit(f"{cell}: missing common overlay source {source}")
            target = sim / relative
            replaced = base.digest(target) if target.is_file() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            changes["overlay"][relative] = {"source": str(source),
                                            "sha256": base.digest(target),
                                            "replaced_sha256": replaced}
    changes["common_overlay"] = list(COMMON_OVERLAY)
    changes["c_only_overlay"] = list(C_ONLY_OVERLAY) if condition == "C" else []

    # Condition C has no high-level strategy library. The four documents are
    # removed from this fresh staged tree only; nothing outside it is touched.
    # Condition A keeps them, which is the difference being measured.
    if condition == "C":
        removed = {}
        for path in sorted((sim / SKILL_MD_DIR).glob("*.md")):
            relative = str(path.relative_to(sim))
            removed[relative] = base.digest(path)
            changes["removed"].append({"path": relative, "source_sha256": removed[relative],
                                       "reason": "condition C has no high-level strategy MD"})
            path.unlink()
        changes["removed_strategy_md"] = removed
    else:
        changes["removed_strategy_md"] = {}
        # The interface document is a C input. An A tree must not contain it,
        # and the base only stages it for C; checked because a leaked copy is
        # exactly the kind of drift a later edit could introduce silently.
        if (sim / base.INTERFACE_DOC).exists():
            raise SystemExit(f"{cell}: the world interface document is staged into an A cell")

    changes["output_ownership"] = output_ownership.verify(case, sim)
    changes["no_prior_inputs"] = {
        "c_starter": None, "imported_diagnostic": None, "imported_tape": None,
        "note": "fresh generation; no prior bundle, diagnostic, tape or result is staged"}
    return case, changes


def external_inputs(case):
    """Pin what lives outside the hashed source directories."""
    extra = [LAUNCH / n for n in (*LAUNCH_FILES, "launch-manifest.json")]
    return original_external(case) + extra


def render_prompts(case):
    """Render through the frozen gate, in a fresh subprocess per cell."""
    import subprocess
    sim = base.cell_sim(case["id"])
    renderer = base.PARENT / "support/two_task_render.py"
    if not renderer.is_file():
        raise SystemExit(f"frozen renderer missing: {renderer}")
    command = [str(sim / ".venv-libero/bin/python3"), str(renderer),
               "--case", str(Path(case["control"]) / "case.json")]
    proc = subprocess.run(command, cwd=sim, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: gated prompt rendering failed:\n{proc.stderr[-4000:]}")
    render = json.loads(proc.stdout)
    render["command"] = command
    return render


def verify_prompts(case, oracles):
    """A must equal the independent pristine render byte for byte; C is recorded.

    The base's version reads a stored oracle file. This study computes the
    oracle instead, by rendering A from the untouched Sep-14 reference tree in a
    fresh subprocess, so the comparison cannot be satisfied by a stale pin.
    """
    import subprocess
    control = Path(case["control"])
    saved = {}
    for name in ("worker-prompt.md", "coordinator-prompt.md"):
        path = control / name
        if not path.is_file():
            raise SystemExit(f"{case['id']}: {name} was not rendered")
        saved[name] = base.digest(path)
    if case["condition"] != "A":
        return saved
    command = [str(base.VENV / "bin/python3"),
               str(base.PARENT / "support/pristine_a_render.py"),
               "--case", str(control / "case.json"),
               "--engineering", str(base.ENGINEERING)]
    proc = subprocess.run(command, cwd=base.ENGINEERING, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: pristine A oracle failed:\n{proc.stderr[-4000:]}")
    oracle = json.loads(proc.stdout)
    for name, expected in oracle["sha256"].items():
        if saved[name] != expected:
            raise SystemExit(f"{case['id']}: {name} differs from the pristine A render; the "
                             "common overlay changed condition A's assignment")
    oracles[case["id"]] = {**oracle, "command": command}
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
        "kind": "fresh two-task paired comparison: original A Fix Loop vs executable-world C r1",
        "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
        "cells": [{"id": cell, "condition": condition,
                   "task": TASKS[cell.split("_")[0]], "profile": base.PROFILES[condition],
                   "runtime": str(base.cell_sim(cell)), "control": str(base.PARENT / cell),
                   "outputs": str(base.PARENT / cell / "outputs")}
                  for cell, condition in CELLS],
        "serial_order": [cell for cell, _ in CELLS],
        "development_seeds": DEV_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
        "budgets": {"max_charged_attempts_per_dev_seed": two_task_scope.ATTEMPT_LIMIT,
                    "imported_charged_attempts": {},
                    "remaining_attempts": two_task_scope.remaining_budget(
                        {"dev_seeds": DEV_SEEDS})},
        "deadlines": full_deadlines.summary(),
        "prior_inputs": {"c_starter": None, "imported_diagnostic": None,
                         "old_solutions": None, "old_tapes": None,
                         "note": "fresh task-specific generation in every cell"},
        "experiment_model": {"provider": "local-vllm", "endpoint": ENDPOINT,
                             "model": MODEL, "effort": "xhigh", "context_tokens": 1000000,
                             "max_output_tokens": 64000, "claude_bin": CLAUDE_BIN,
                             "provider_fallback": None},
        "gpu_mapping": {"model": [0, 1, 2, 3], "sam3": 4, "graspnet": 5,
                        "sim_pyroki": 6, "spare": 7},
        "task_selection_proxy": {"source": "ASPIRE arXiv:2607.00272v1 Appendix D.1 Table 7",
                                 TASKS["bowldrawer"]: "43/50", TASKS["drawer"]: "1/50",
                                 "note": "task-selection proxy only; not a Qwen result "
                                         "and not a prediction"},
        "executable_world_revision": "r1", "c_arm": "full", "c_lineage": "fresh",
        "common_overlay": list(COMMON_OVERLAY), "c_only_overlay": list(C_ONLY_OVERLAY),
        "a_prompt_oracle": "support/pristine_a_render.py against reference/native-A1-source",
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
        base.freeze_bytes(source, LAUNCH / name)
    base.freeze_bytes(base.SUPPORT / "dlc-supervisor.py", LAUNCH / "dlc-supervisor.py")
    base.record(LAUNCH / "launch-manifest.json", {
        "files": {name: base.digest(LAUNCH / name) for name in LAUNCH_FILES},
        "revision": "two-task-20260926", "deployment_source": str(V4_LAUNCH),
        "scope": "per cell: dev51-65 then immutable outer heldout1-50",
        "serial_order": [cell for cell, _ in CELLS],
        "deadlines": full_deadlines.summary()})


def prepare(cells) -> dict:
    refuse_existing(base.PARENT, base.RUNTIME, base.CAMPAIGN_PLAN, base.BASELINE_CONTRACT,
                    base.EXPECTED_PROMPTS)
    required = [CONTRACT_SOURCE, base.A_SOURCE, base.ENGINEERING / base.INTERFACE_DOC]
    required += [base.ENGINEERING / n for n in base.C_OVERLAY]
    required += [base.SUPPORT / n for n in base.SUPPORT_FILES]
    required += [V4_LAUNCH / n for n in LAUNCH_PINS]
    required += [base.ENGINEERING / pristine_a_render.PRISTINE]
    for path in required:
        if not path.exists():
            raise SystemExit(f"required input missing: {path}")
    base.BASELINE_CONTRACT.write_bytes(CONTRACT_SOURCE.read_bytes())
    base.record(base.CAMPAIGN_PLAN, campaign_plan())
    stage_launch()
    frozen = base.freeze_support()
    contract = json.loads(base.BASELINE_CONTRACT.read_text())

    oracles, staged = {}, {}
    for cell, condition in CELLS:
        if cell not in cells:
            continue
        case, differences = stage_cell(cell, condition, contract)
        case = base.bind_manifest(case)
        render = render_prompts(case)
        prompts = verify_prompts(case, oracles)
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
                        "prompt_sha256": prompts,
                        "prompt_oracle": oracles.get(cell),
                        "source_differences": differences, "render": render}
    base.record(base.EXPECTED_PROMPTS, oracles)

    frozen_prepare = base.PARENT / "frozen-prepare-two-task.py"
    prepare_sha = base.freeze_bytes(Path(__file__), frozen_prepare)
    receipt = {"prepared_at": base.now(), "study": str(base.STUDY),
               "runtime_root": str(base.RUNTIME), "result_parent": str(base.PARENT),
               "driver": str(base.PARENT / "support/run_two_task_cell.py"),
               "frozen_support": frozen,
               "runtime_sha256": {cell: {n: base.digest(base.cell_sim(cell) / n)
                                         for n in (COMMON_OVERLAY if condition == "A"
                                                   else base.C_OVERLAY)}
                                  for cell, condition in CELLS if cell in cells},
               "prepare_source_sha256": prepare_sha,
               "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
               "campaign_plan_sha256": base.digest(base.CAMPAIGN_PLAN),
               "expected_prompts_sha256": base.digest(base.EXPECTED_PROMPTS),
               "launch_dir": str(LAUNCH),
               "serial_order": [cell for cell, _ in CELLS if cell in cells],
               "cells": staged, "launched": False,
               "note": "staged only; no model, simulator or job has run. Verify, "
                       "platform-preflight, then submit one cell at a time."}
    base.record(base.PARENT / "prepare-receipt.json", receipt)
    print(json.dumps({"prepared_at": receipt["prepared_at"],
                      "cells": {c: staged[c]["case"] for c in staged},
                      "launch_dir": str(LAUNCH), "launched": False}, indent=2))
    return receipt


# ---- verify ---------------------------------------------------------------


COMMON_EXPECTED = {
    "model_provider": "local-vllm", "inference_endpoint": ENDPOINT,
    "model": MODEL, "model_tag": MODEL, "expected_served_model": MODEL,
    "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000,
    "claude_bin": CLAUDE_BIN, "gpu": GPU, "cuda_visible_devices": str(GPU),
    "egl_device_id": GPU, "dev_seeds": DEV_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
    "suite": "libero_goal_swap", "imported_charged_attempts": {},
    "env_config": "env_configs/libero/franka_libero_traced.yaml",
    "sam3_url": "http://127.0.0.1:8114", "graspnet_url": "http://127.0.0.1:8115",
    "pyroki_url": "http://127.0.0.1:8116", "service_ports": [8114, 8115, 8116],
    "max_steps": 4000, "trial_timeout": 900,
    "campaign_timeout": full_deadlines.DEVELOPMENT,
    "require_runtime_freeze": True,
}
CONDITION_EXPECTED = {
    "A": {"condition": "A", "profile": "legacy_native"},
    "C": {"condition": "C", "profile": "judgment", "executable_world_revision": "r1",
          "c_arm": "full", "c_lineage": "fresh"},
}
#: A carries no world keys; C carries no starter and no credential. Either
#: would be a claim about an input this study does not stage.
FORBIDDEN_CASE_KEYS = {
    "A": ("api_key_helper", "disable_experimental_betas", "c_starter", "c_lineage",
          "c_arm", "executable_world_revision", "world_interface_doc",
          "full_interface_doc", "pilot_interface_doc"),
    "C": ("api_key_helper", "disable_experimental_betas", "c_starter",
          "full_interface_doc", "pilot_interface_doc"),
}


def check_case(cell, condition, case) -> dict:
    expected = {**COMMON_EXPECTED, **CONDITION_EXPECTED[condition],
                "id": cell, "task": TASKS[cell.split("_")[0]]}
    if condition == "C":
        expected["world_interface_doc"] = str(base.INTERFACE_DOC)
    problems = [f"case[{key!r}] is {case.get(key)!r}, expected {value!r}"
                for key, value in expected.items() if case.get(key) != value]
    problems += [f"case still carries {key!r}" for key in FORBIDDEN_CASE_KEYS[condition]
                 if key in case]
    model_keys = sorted(k for k, v in case.items()
                        if "model" in k and k != "model_provider" and isinstance(v, str))
    if model_keys != ["expected_served_model", "model", "model_tag"]:
        problems.append(f"unexpected model-bearing string keys: {model_keys}")
    return {"checked": sorted(expected), "model_keys": model_keys, "problems": problems}


def check_strategy_md(case) -> dict:
    """A exposes exactly the four documents; C exposes none anywhere in its tree."""
    sim = Path(case["sim"])
    strategy = sim / case["skill_library_dir"]
    shared = sorted(p.name for p in (sim / SKILL_MD_DIR).glob("*.md"))
    private = sorted(p.name for p in strategy.glob("*")) if strategy.is_dir() else None
    problems = []
    if case["condition"] == "A":
        if private != sorted(base.STRATEGY_MD):
            problems.append(f"condition A strategy directory is {private}, "
                            f"expected {sorted(base.STRATEGY_MD)}")
    else:
        if shared:
            problems.append(f"high-level strategy MD survived in a C runtime: {shared}")
        if private != ["README.md"]:
            problems.append(f"C notes directory is {private}, expected only README.md")
    return {"shared_skills_md": shared, "private_dir": case["skill_library_dir"],
            "private": private, "problems": problems}


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
    if case["condition"] == "A":
        oracle = entry.get("prompt_oracle")
        if not oracle:
            problems.append("condition A has no recorded pristine prompt oracle")
        else:
            for name, expected in oracle["sha256"].items():
                if hashes[name] != expected:
                    problems.append(f"{name} differs from the pristine A oracle")
    return {"sha256": hashes, "oracle": entry.get("prompt_oracle"), "problems": problems}


def check_isolation(cells) -> dict:
    """No two cells may share a runtime, control root, outputs root or prompt."""
    problems = []
    seen: dict[str, dict] = {}
    for cell, case in cells.items():
        control = Path(case["control"]).resolve()
        canonical = (control / "outputs").resolve()
        link = (Path(case["sim"]) / "outputs")
        for field, value in (("sim", Path(case["sim"]).resolve()), ("control", control),
                             ("outputs", canonical),
                             ("claude_config_dir", Path(case["claude_config_dir"]))):
            owner = seen.setdefault(f"{field}:{value}", {"cell": cell})
            if owner["cell"] != cell:
                problems.append(f"{cell} shares {field} {value} with {owner['cell']}")
        if not link.is_symlink() or link.resolve() != canonical:
            problems.append(f"{cell}: sim/outputs does not resolve to {canonical}")
        # Nothing under this cell's runtime may point into another cell's tree.
        for path in Path(case["sim"]).rglob("*"):
            if not path.is_symlink() or path.name == ".venv-libero":
                continue
            target = str(path.resolve())
            for other, other_case in cells.items():
                if other != cell and (target.startswith(str(Path(other_case["sim"]).resolve()))
                                      or target.startswith(str(Path(other_case["control"]
                                                                    ).resolve()))):
                    problems.append(f"{cell}: symlink {path} points into {other}")
    return {"cells": sorted(cells), "problems": problems}


def check_runtime(cell, case, receipt) -> dict:
    problems = []
    sim = Path(case["sim"])
    for name, expected in receipt["runtime_sha256"][cell].items():
        if base.digest(sim / name) != expected:
            problems.append(f"runtime source changed: {name}")
    if case["condition"] == "A":
        present = [n for n in C_ONLY_OVERLAY if (sim / n).exists()]
        if present:
            problems.append(f"condition A runtime contains world sources: {present}")
    try:
        ownership = output_ownership.verify(case, sim)
    except output_ownership.OwnershipError as exc:
        ownership = None
        problems.append(str(exc))
    allowed = {str(Path(case["skill_library_dir"]) / name)
               for name in (base.STRATEGY_MD if case["condition"] == "A" else ("README.md",))}
    if case["condition"] == "C":
        allowed.add(str(base.INTERFACE_DOC))
    unexpected = [str(p.relative_to(sim)) for p in base.tree_files(sim / "docs")
                  if str(p.relative_to(sim)) not in allowed]
    if unexpected:
        problems.append(f"undeclared documentation/history in solver runtime: {unexpected}")
    launch = json.loads((LAUNCH / "launch-manifest.json").read_text())
    for name in LAUNCH_FILES:
        if launch["files"].get(name) != base.digest(LAUNCH / name):
            problems.append(f"launch file changed: {name}")
    return {"ownership": ownership, "undeclared_docs": unexpected, "problems": problems}


def check_no_prior_input(case) -> dict:
    """Nothing preexecuted, previously solved or previously recorded is staged."""
    sim = Path(case["sim"])
    problems = []
    for pattern in ("**/prior_c", "**/dsw-preflight", "**/starters",
                    "**/*_tape.jsonl", "**/fix_code.py", "**/fix_world_program.py"):
        found = [str(p.relative_to(sim)) for p in sim.glob(pattern)]
        if found:
            problems.append(f"prior-input artifact staged into the runtime: {found}")
    task_dir = sim / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    if task_dir.exists():
        problems.append(f"the task directory already exists before initialization: {task_dir}")
    outputs = Path(case["control"]) / "outputs"
    contents = sorted(p.name for p in outputs.iterdir()) if outputs.is_dir() else None
    if contents != ["working_codes"]:
        problems.append(f"the canonical outputs root is {contents}, expected only working_codes")
    return {"problems": problems, "outputs": contents}


def verify(cells) -> dict:
    receipt = json.loads((base.PARENT / "prepare-receipt.json").read_text())
    resolved, checks = {}, {}
    for cell in [c for c, _ in CELLS if c in cells]:
        entry = receipt["cells"][cell]
        case = base.verify_cell(entry, receipt["frozen_support"])
        resolved[cell] = case
        checks[cell] = {"case": check_case(cell, entry["condition"], case),
                        "strategy_md": check_strategy_md(case),
                        "prompt": check_prompt(case, entry),
                        "no_prior_input": check_no_prior_input(case),
                        "runtime": check_runtime(cell, case, receipt)}
    checks["isolation"] = check_isolation(resolved)
    problems = [f"{name}: {p}" for name, group in checks.items()
                for check in (group.values() if "problems" not in group else [group])
                for p in check["problems"]]
    result = {"verified_at": base.now(), "study": str(base.STUDY),
              "runtime_root": receipt["runtime_root"], "driver": receipt["driver"],
              "frozen_support": receipt["frozen_support"],
              "cells": {cell: {"case": receipt["cells"][cell]["case"],
                               "case_sha256": receipt["cells"][cell]["case_sha256"],
                               "condition": receipt["cells"][cell]["condition"],
                               "task": receipt["cells"][cell]["task"],
                               "sim": resolved[cell]["sim"],
                               "control": resolved[cell]["control"],
                               "runtime_manifest": resolved[cell]["runtime_manifest"],
                               "prompt_sha256": receipt["cells"][cell]["prompt_sha256"]}
                        for cell in resolved},
              "checks": checks, "problems": problems,
              "status": "verified" if not problems else "problems_found"}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true",
                      help="stage the four cells; start no model, simulator or job")
    mode.add_argument("--verify", action="store_true",
                      help="re-verify the already staged cells")
    parser.add_argument("--cells", default=",".join(c for c, _ in CELLS))
    args = parser.parse_args()
    selected = [c.strip() for c in args.cells.split(",") if c.strip()]
    unknown = [c for c in selected if c not in {name for name, _ in CELLS}]
    if unknown:
        raise SystemExit(f"unknown cells: {unknown}")
    if args.prepare:
        prepare(selected)
    else:
        raise SystemExit(0 if verify(selected)["status"] == "verified" else 1)
