#!/usr/bin/env python3
"""Stage/verify one fresh Qwen C r1 full Fix Loop, then outer heldout1–50. Runs no experiment."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "code-world-qwen-full-20260924"

spec = importlib.util.spec_from_file_location("qwen_stager", HERE / "support/legacy_stager.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

# The original curated-source constants -- ENGINEERING, A_SOURCE, REFERENCE,
# PACKAGE_SOURCE, VENV, TASKS, PROFILES, SUITE, IGNORE, BASE_COMMIT -- are left
# exactly as the base defines them. A_SOURCE is the judgment study's byte-verified
# Sep-14 reference, not anything in this study.
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
base.CELLS = (("bowl_C_full", "C"),)
base.C_OVERLAY += ("cap/world_model/executable_world.py",
                   "scripts/libero/executable_world_profile.py",
                   "cap/world_model/world_use_audit.py")
# Freeze only runtime support; engineering fixtures and historic inputs stay outside.
base.C_OVERLAY += ("scripts/libero/native_cc_toolchain_probe.py",
                   "env_configs/libero/franka_libero_traced.yaml",
                   "cap/envs/tasks/base.py", "cap/envs/simulators/libero.py")
base.C_OVERLAY = tuple(dict.fromkeys(base.C_OVERLAY))
base.SUPPORT_FILES = ("run_full_cell.py", "cell_read_guard.py", "output_ownership.py",
                      "lineage.py", "native_lineage_r2.py", "native_cc_stream.py",
                      "infra_guard.py", "full_scope.py", "full_import.py",
                      "pilot_assignment_guard.py", "full_render.py", "full_deadlines.py",
                      "heldout_stop_on_infra.py", "dlc-supervisor.py")
sys.path.insert(0, str(HERE / "support"))
import full_deadlines
import full_scope
import output_ownership

V4_LAUNCH = Path("/mnt/home/gewang/experiments/code-world-qwen-debug-20260922-retry4/launch-20260923-v4")
LAUNCH = base.PARENT / "launch-20260924"
LAUNCH_PINS = {
    "dlc-entry.sh": "f97a49d25e70b7811c477d0a8c27cc5a74388dca275a5f1ae545036583796641",
    "qwen-native-compat.py": "7e9cd4509efee2c6da7976767b471b54dac65e755915149f99de494e3284d67b",
    "reference/model-server.json": "22c61284fd39db1310bc7cecbb09ec06ddcb417aeb4156fbea8a163829985c67",
    "reference/native_cc_runtime.py": "36a1d1d118faaec4f30723aff02b804b83fabf3cae94b1891df7d308bb32edda",
}
LAUNCH_FILES = tuple(LAUNCH_PINS) + ("dlc-supervisor.py",)

CELL = "bowl_C_full"
GPU = 6
DEV_SEEDS = list(range(51, 66))
HELDOUT_SEEDS = list(range(1, 51))
MODEL = "qwen3.8-flash-next"
ENDPOINT = "http://127.0.0.1:8121"
CLAUDE_BIN = "/mnt/home/gewang/.local/share/claude/versions/2.1.220"

CONTRACT_SOURCE = base.ENGINEERING / "docs/experiments/code-world-rerun-20260919/baseline-contract.json"
STARTER_SOURCE = base.ENGINEERING / "docs/experiments/code-world-c-opt-ablation-20260920/starters/bowl"
# Verified bytes of the two declared starter programs. Nothing else from that
# study's solutions or results is an input here.
STARTER_PINS = {"fix_code.py": "43edf626beb423612df96e0d3e5d532edf8035b6d8f87cecf616b9a69f5aeb1b",
                "fix_world_program.py": "e26e81549be0d054dcdfadb192b057400ad92f850e90637b68b4aad849bedb22"}
PREFLIGHT_SOURCE = base.STUDY_DIR / "coordination/dsw-preflight"
SKILL_MD_DIR = ".claude/libero/skills"

original_case = base.case_config
original_stage = base.stage_cell
original_external = base.external_inputs


def _support():
    """The frozen support directory. Imported for its module-level constants only."""
    path = str(base.SUPPORT)
    if path not in sys.path:
        sys.path.insert(0, path)
    import full_import
    return full_import


def preflight_target(sim: Path) -> Path:
    """Where the driver's `full_import` resolves the charged diagnostic."""
    relative = _support().SOURCE_REL
    expected = str(base.STUDY / "coordination/dsw-preflight")
    if relative != expected:
        raise SystemExit(f"full_import.SOURCE_REL is {relative!r}, expected {expected!r}")
    return sim / relative


def case_config(cell, condition):
    """The base case, rewritten to this full study's device, provider, scope and starter.

    Ordinary fields, watchdogs, service URLs, ports and the nonprivileged env
    wiring are the base's. `api_key_helper` and `disable_experimental_betas` are
    removed outright: the local-vllm branch has no credential and never reads the
    beta flag, so leaving either present would be a stale claim about this run.
    """
    case = original_case(cell, condition)
    case.update(
        gpu=GPU, cuda_visible_devices=str(GPU), egl_device_id=GPU,
        dev_seeds=list(DEV_SEEDS), heldout_seeds=list(HELDOUT_SEEDS),
        campaign_timeout=full_deadlines.DEVELOPMENT, condition="C", profile="judgment",
        executable_world_revision="r1", c_arm="full",
        model_provider="local-vllm", inference_endpoint=ENDPOINT,
        model=MODEL, model_tag=MODEL, expected_served_model=MODEL,
        effort="xhigh", context_tokens=1000000, max_output_tokens=64000,
        claude_bin=CLAUDE_BIN,
        full_interface_doc=str(base.INTERFACE_DOC),
        c_starter=str(base.STUDY / "cells" / cell / "prior_c"),
        generation_context="native Qwen C full repair; dev51-65 then frozen heldout1-50")
    case.pop("api_key_helper", None)
    case.pop("disable_experimental_betas", None)
    return case


def stage_cell(cell, condition, contract):
    case, changes = original_stage(cell, condition, contract)
    sim = base.cell_sim(cell)

    # 1. The whole declared common starter, byte-checked on its two programs.
    starter = sim / case["c_starter"]
    shutil.copytree(STARTER_SOURCE, starter)
    for name, expected in STARTER_PINS.items():
        if base.digest(starter / name) != expected:
            raise SystemExit(f"{cell}: staged starter {name} is not the declared bytes")
    starter_files = {}
    for path in base.tree_files(starter):
        relative = str(path.relative_to(sim))
        starter_files[relative] = base.digest(path)
        changes["added"][relative] = starter_files[relative]
    changes["starter"] = {"source": str(STARTER_SOURCE),
                          "tree_sha256": base.tree_digest(starter)[0],
                          "file_count": len(starter_files),
                          "pins": STARTER_PINS,
                          "excluded": ["any other solutions, results, arms or tasks"]}

    # 2. No high-level strategy MD anywhere in this fresh runtime. Only the fresh
    #    staged tree is touched; nothing outside it is modified.
    removed_md = {}
    for path in sorted((sim / SKILL_MD_DIR).glob("*.md")):
        relative = str(path.relative_to(sim))
        removed_md[relative] = base.digest(path)
        changes["removed"].append({"path": relative, "source_sha256": removed_md[relative],
                                   "reason": "no high-level strategy MD in this C full study"})
        path.unlink()
    changes["removed_strategy_md"] = removed_md

    # 3. The already-executed charged DSW diagnostic, at the path the driver's
    #    importer resolves. A copy of existing evidence; nothing is run here.
    target = preflight_target(sim)
    shutil.copytree(PREFLIGHT_SOURCE, target)
    preflight_files = {}
    for path in base.tree_files(target):
        relative = str(path.relative_to(sim))
        preflight_files[relative] = base.digest(path)
        changes["added"][relative] = preflight_files[relative]
    source_sha, source_count = base.tree_digest(PREFLIGHT_SOURCE)
    staged_sha, staged_count = base.tree_digest(target)
    if (staged_sha, staged_count) != (source_sha, source_count):
        raise SystemExit(f"{cell}: staged preflight tree differs from {PREFLIGHT_SOURCE}")
    changes["diagnostic_import_source"] = {
        "source": str(PREFLIGHT_SOURCE), "source_rel": _support().SOURCE_REL,
        "tree_sha256": staged_sha, "file_count": staged_count,
        "declared_sources": json.loads((target / "source-sha256.json").read_text()),
        "note": "copy of one already-executed charged diagnostic; not re-run here"}
    changes["output_ownership"] = output_ownership.verify(case, sim)
    return case, changes


def external_inputs(case):
    """The starter and the imported diagnostic live under docs/, outside the hashed
    source directories, so they are pinned explicitly or they could drift."""
    sim = base.cell_sim(case["id"])
    extra = base.tree_files(sim / case["c_starter"]) + base.tree_files(preflight_target(sim))
    return original_external(case) + extra + [LAUNCH / n for n in (*LAUNCH_FILES, "launch-manifest.json")]


def render_prompts(case):
    """Render through the frozen scope adapter, in a fresh subprocess.

    The base shells `native_world_campaign.py --render-only`, which renders the
    pristine 51-65 scope with its Stage 2 promise and never sees `pilot_scope`.
    The adapter imports the same frozen campaign module from the staged repo,
    applies the same patch the driver applies, and prints the same JSON keys.
    """
    import subprocess
    sim = base.cell_sim(case["id"])
    renderer = base.PARENT / "support/full_render.py"
    if not renderer.is_file():
        raise SystemExit(f"frozen renderer missing: {renderer}")
    command = [str(sim / ".venv-libero/bin/python3"), str(renderer),
               "--case", str(Path(case["control"]) / "case.json")]
    proc = subprocess.run(command, cwd=sim, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: scoped prompt rendering failed:\n{proc.stderr[-4000:]}")
    render = json.loads(proc.stdout)
    render["command"] = command
    return render


base.case_config = case_config
base.stage_cell = stage_cell
base.external_inputs = external_inputs
base.render_prompts = render_prompts


# ---- prepare --------------------------------------------------------------


def campaign_plan() -> dict:
    return {
        "study_id": NAME, "status": "prepared_not_launched",
        "kind": "existing C executable-world r1 repair/continuation; full native Qwen Fix Loop",
        "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
        "cells": [{"id": CELL, "condition": "C", "task": base.TASKS["bowl"], "profile": "judgment"}],
        "development_seeds": DEV_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
        "budgets": {"max_charged_attempts_per_dev_seed": 3,
                    "imported_charged_attempts": {"51": 1},
                    "remaining_attempts": full_scope.remaining_budget({"dev_seeds": DEV_SEEDS})},
        "deadlines": full_deadlines.summary(),
        "common_starter": {"source": str(STARTER_SOURCE), "pins": STARTER_PINS},
        "experiment_model": {"provider": "local-vllm", "endpoint": ENDPOINT,
                             "model": MODEL, "effort": "xhigh", "context_tokens": 1000000,
                             "max_output_tokens": 64000, "claude_bin": CLAUDE_BIN},
        "gpu_mapping": {"model": [0,1,2,3], "sam3": 4, "graspnet": 5, "sim_pyroki": 6, "spare": 7},
        "executable_world_revision": "r1", "c_arm": "full", "no_A_launch": True,
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
        "revision": "full-20260924", "deployment_source": str(V4_LAUNCH),
        "scope": "dev51-65 then immutable outer heldout1-50", "deadlines": full_deadlines.summary()})


def prepare() -> dict:
    refuse_existing(base.PARENT, base.RUNTIME, base.CAMPAIGN_PLAN, base.BASELINE_CONTRACT)
    required = [CONTRACT_SOURCE, STARTER_SOURCE, PREFLIGHT_SOURCE, base.A_SOURCE,
                base.ENGINEERING / base.INTERFACE_DOC]
    required += [base.ENGINEERING / n for n in base.C_OVERLAY]
    required += [base.SUPPORT / n for n in base.SUPPORT_FILES]
    required += [V4_LAUNCH / n for n in LAUNCH_PINS]
    for path in required:
        if not path.exists():
            raise SystemExit(f"required input missing: {path}")
    base.BASELINE_CONTRACT.write_bytes(CONTRACT_SOURCE.read_bytes())
    base.record(base.CAMPAIGN_PLAN, campaign_plan())
    base.record(base.EXPECTED_PROMPTS, {})
    stage_launch()
    frozen = base.freeze_support()
    contract = json.loads(base.BASELINE_CONTRACT.read_text())
    case, differences = stage_cell(CELL, "C", contract)
    case = base.bind_manifest(case)
    render = render_prompts(case)
    prompts = base.verify_prompts(case, {})
    case_path = Path(case["control"]) / "case.json"
    base.record(Path(case["control"]) / "staged-inputs.json", {
        "cell": CELL, "condition": "C", "staged_at": base.now(),
        "source_differences": differences, "frozen_support": frozen,
        "external_inputs": [str(p) for p in external_inputs(case)]})
    entry = {"case": str(case_path), "case_sha256": base.digest(case_path),
             "condition": "C", "profile": "judgment", "task": case["task"],
             "runtime_manifest": case["runtime_manifest"],
             "runtime_manifest_sha256": case["runtime_manifest_sha256"],
             "prompt_sha256": prompts, "source_differences": differences, "render": render}
    frozen_prepare = base.PARENT / "frozen-prepare-full.py"
    prepare_sha = base.freeze_bytes(Path(__file__), frozen_prepare)
    receipt = {"prepared_at": base.now(), "study": str(base.STUDY),
               "runtime_root": str(base.RUNTIME), "result_parent": str(base.PARENT),
               "driver": str(base.PARENT / "support/run_full_cell.py"),
               "frozen_support": frozen, "runtime_sha256": {
                   n: base.digest(base.cell_sim(CELL) / n) for n in base.C_OVERLAY},
               "prepare_source_sha256": prepare_sha,
               "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
               "campaign_plan_sha256": base.digest(base.CAMPAIGN_PLAN),
               "launch_dir": str(LAUNCH), "cells": {CELL: entry}, "launched": False,
               "note": "staged only; verify and platform-preflight before DLC submit"}
    base.record(base.PARENT / "prepare-receipt.json", receipt)
    print(json.dumps({"prepared_at": receipt["prepared_at"], "case": entry["case"],
                      "launch_dir": str(LAUNCH), "launched": False}, indent=2))
    return receipt


# ---- verify ---------------------------------------------------------------


EXPECTED_CASE = {
    "id": CELL, "condition": "C", "profile": "judgment", "c_arm": "full",
    "executable_world_revision": "r1", "task": "put_the_bowl_on_the_plate",
    "model_provider": "local-vllm", "inference_endpoint": ENDPOINT,
    "model": MODEL, "model_tag": MODEL, "expected_served_model": MODEL,
    "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000,
    "claude_bin": CLAUDE_BIN, "gpu": GPU, "cuda_visible_devices": str(GPU),
    "egl_device_id": GPU, "dev_seeds": DEV_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
    "full_interface_doc": str(base.INTERFACE_DOC),
    "c_starter": str(base.STUDY / "cells" / CELL / "prior_c"),
    "sam3_url": "http://127.0.0.1:8114", "graspnet_url": "http://127.0.0.1:8115",
    "pyroki_url": "http://127.0.0.1:8116", "service_ports": [8114, 8115, 8116],
    "max_steps": 4000, "trial_timeout": 900, "campaign_timeout": full_deadlines.DEVELOPMENT,
}
FORBIDDEN_CASE_KEYS = ("api_key_helper", "disable_experimental_betas")


def check_case(case: dict) -> dict:
    problems = []
    for key, expected in EXPECTED_CASE.items():
        if case.get(key) != expected:
            problems.append(f"case[{key!r}] is {case.get(key)!r}, expected {expected!r}")
    for key in FORBIDDEN_CASE_KEYS:
        if key in case:
            problems.append(f"case still carries {key!r}")
    model_keys = sorted(k for k, v in case.items()
                        if "model" in k and k != "model_provider" and isinstance(v, str))
    if model_keys != ["expected_served_model", "model", "model_tag"]:
        problems.append(f"unexpected model-bearing string keys: {model_keys}")
    return {"checked": sorted(EXPECTED_CASE) + list(FORBIDDEN_CASE_KEYS),
            "model_keys": model_keys, "problems": problems}


def check_no_md(case: dict) -> dict:
    sim = Path(case["sim"])
    strategy = sim / case["skill_library_dir"]
    stray = sorted(str(p.relative_to(sim)) for p in (sim / SKILL_MD_DIR).glob("*.md"))
    notes = sorted(p.name for p in strategy.glob("*")) if strategy.is_dir() else None
    problems = []
    if stray:
        problems.append(f"high-level strategy MD survived in the runtime: {stray}")
    if notes != ["README.md"]:
        problems.append(f"strategy notes directory is {notes}, expected only README.md")
    return {"skills_dir": SKILL_MD_DIR, "skills_md": stray,
            "notes_dir": case["skill_library_dir"], "notes": notes,
            "problems": problems}


def check_prompt(case: dict) -> dict:
    problems = []
    hashes = {}
    for role, name in (("worker", "worker-prompt.md"), ("coordinator", "coordinator-prompt.md")):
        path = Path(case["control"]) / name
        text = path.read_text()
        try:
            full_scope._verify(text, case, role=role)
        except full_scope.ScopeError as exc:
            problems.append(str(exc))
        if role == "worker":
            for required in (case["full_interface_doc"], "seed 51 has **2**", "seeds 52-65 have **3**"):
                if required not in text:
                    problems.append(f"missing worker scope/budget: {required}")
        hashes[name] = base.digest(path)
    return {"sha256": hashes, "problems": problems}


def check_runtime(case: dict, receipt: dict) -> dict:
    problems = []
    sim = Path(case["sim"])
    for name, expected in receipt["runtime_sha256"].items():
        if base.digest(sim / name) != expected:
            problems.append(f"runtime source changed: {name}")
    try:
        ownership = output_ownership.verify(case, sim)
    except output_ownership.OwnershipError as exc:
        ownership = None
        problems.append(str(exc))
    allowed = {str(base.INTERFACE_DOC), str(Path(case["skill_library_dir"]) / "README.md")}
    allowed.update(str(p.relative_to(sim)) for p in base.tree_files(sim / case["c_starter"]))
    allowed.update(str(p.relative_to(sim)) for p in base.tree_files(preflight_target(sim)))
    unexpected = [str(p.relative_to(sim)) for p in base.tree_files(sim / "docs")
                  if str(p.relative_to(sim)) not in allowed]
    if unexpected:
        problems.append(f"undeclared documentation/history in solver runtime: {unexpected}")
    launch = json.loads((LAUNCH / "launch-manifest.json").read_text())
    for name in LAUNCH_FILES:
        if launch["files"].get(name) != base.digest(LAUNCH / name):
            problems.append(f"launch file changed: {name}")
    return {"ownership": ownership, "runtime_sha256": receipt["runtime_sha256"],
            "undeclared_docs": unexpected, "problems": problems}


def check_preflight(case: dict) -> dict:
    """The five declared sources of the charged diagnostic, against the staged tree."""
    sim = Path(case["sim"])
    staged = preflight_target(sim)
    declared = json.loads((staged / "source-sha256.json").read_text())
    checked, problems = {}, []
    for relative, expected in declared.items():
        target = sim / relative
        actual = base.digest(target) if target.is_file() else None
        checked[relative] = {"expected": expected, "staged": actual,
                             "match": actual == expected}
        if actual != expected:
            problems.append(f"diagnostic source {relative} is {actual}, declared {expected}")
    if len(declared) != 5:
        problems.append(f"expected five declared diagnostic sources, found {len(declared)}")
    source_sha, source_count = base.tree_digest(PREFLIGHT_SOURCE)
    staged_sha, staged_count = base.tree_digest(staged)
    if (staged_sha, staged_count) != (source_sha, source_count):
        problems.append("staged diagnostic tree differs from the study copy")
    return {"staged_root": str(staged), "source_rel": _support().SOURCE_REL,
            "tree_sha256": staged_sha, "file_count": staged_count,
            "sources": checked, "problems": problems}


def check_starter(case: dict) -> dict:
    sim = Path(case["sim"])
    starter = sim / case["c_starter"]
    problems = []
    for name, expected in STARTER_PINS.items():
        actual = base.digest(starter / name) if (starter / name).is_file() else None
        if actual != expected:
            problems.append(f"starter {name} is {actual}, declared {expected}")
    source_sha, source_count = base.tree_digest(STARTER_SOURCE)
    staged_sha, staged_count = base.tree_digest(starter)
    if (staged_sha, staged_count) != (source_sha, source_count):
        problems.append("staged starter tree differs from the declared source")
    return {"staged_root": str(starter), "source": str(STARTER_SOURCE),
            "tree_sha256": staged_sha, "file_count": staged_count,
            "pins": STARTER_PINS, "problems": problems}


def verify() -> dict:
    receipt = json.loads((base.PARENT / "prepare-receipt.json").read_text())
    entry = receipt["cells"][CELL]
    case = base.verify_cell(entry, receipt["frozen_support"])
    checks = {"case": check_case(case), "no_strategy_md": check_no_md(case),
              "prompt_scope": check_prompt(case), "starter": check_starter(case),
              "diagnostic_sources": check_preflight(case), "runtime": check_runtime(case, receipt)}
    problems = [p for check in checks.values() for p in check["problems"]]
    result = {"verified_at": base.now(), "cell": CELL,
              "case_path": entry["case"], "case_sha256": entry["case_sha256"],
              "runtime_root": receipt["runtime_root"], "sim": case["sim"],
              "control": case["control"],
              "driver": receipt["driver"],
              "frozen_support": receipt["frozen_support"],
              "runtime_manifest": case["runtime_manifest"],
              "runtime_manifest_sha256": case["runtime_manifest_sha256"],
              "prompt_sha256": entry["prompt_sha256"], "render": entry["render"],
              "verify_cell": "base.verify_cell(receipt['cells'][cell], receipt['frozen_support'])",
              "checks": checks, "problems": problems,
              "status": "verified" if not problems else "problems_found"}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true",
                      help="stage the one cell; start no model, simulator or queue")
    mode.add_argument("--verify", action="store_true",
                      help="re-verify the already staged cell")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        raise SystemExit(0 if verify()["status"] == "verified" else 1)
