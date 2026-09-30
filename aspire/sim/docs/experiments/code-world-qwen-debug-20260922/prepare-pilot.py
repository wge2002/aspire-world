#!/usr/bin/env python3
"""Stage the one native-Qwen C development pilot cell. Stages only; runs nothing.

One cell, `bowl_C_full`: condition C, judgment profile, executable-world r1, the
common `bowl` starter from the C optimization ablation, debug seeds 51/52/53 and
no held-out stage at all. The solver is the loopback vLLM server, so the case
carries no credential reference of any kind.

Three differences against the curated staging base, all recorded as inputs:

  1. the entire declared `../code-world-c-opt-ablation-20260920/starters/bowl`
     tree is copied into the cell's `c_starter`;
  2. `.claude/libero/skills/*.md` is removed from the fresh staged runtime, so no
     high-level strategy document is reachable (the notes directory stays);
  3. this study's already-charged DSW preflight diagnostic is copied to the exact
     relative path the driver's `pilot_import.SOURCE_REL` resolves.

Prompts are rendered through `support/pilot_render.py`, not the pristine
`--render-only` path, because the latter would render the 51-65 scope and a
Stage 2 promise this pilot does not have.

`--prepare` stages. `--verify` re-verifies what was staged. Neither starts a
model, a simulator or a queue; no launch mode exists in this file.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "code-world-qwen-debug-20260922"

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
# The frozen shared outer inputs of this pilot. `evaluate.py` is deliberately
# absent: this cell has no held-out stage and must not be able to reach one.
base.SUPPORT_FILES = ("legacy_stager.py", "run_cell.py", "cell_read_guard.py",
                      "lineage.py", "native_lineage_r2.py", "native_cc_stream.py",
                      "infra_guard.py", "pilot_scope.py", "pilot_import.py",
                      "pilot_assignment_guard.py", "pilot_render.py")

CELL = "bowl_C_full"
GPU = 6
DEV_SEEDS = [51, 52, 53]
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
    import pilot_import
    return pilot_import


def preflight_target(sim: Path) -> Path:
    """Where the driver's `pilot_import` resolves the charged diagnostic."""
    relative = _support().SOURCE_REL
    expected = str(base.STUDY / "coordination/dsw-preflight")
    if relative != expected:
        raise SystemExit(f"pilot_import.SOURCE_REL is {relative!r}, expected {expected!r}")
    return sim / relative


def case_config(cell, condition):
    """The base case, rewritten to this pilot's device, provider, scope and starter.

    Ordinary fields, watchdogs, service URLs, ports and the nonprivileged env
    wiring are the base's. `api_key_helper` and `disable_experimental_betas` are
    removed outright: the local-vllm branch has no credential and never reads the
    beta flag, so leaving either present would be a stale claim about this run.
    """
    case = original_case(cell, condition)
    case.update(
        gpu=GPU, cuda_visible_devices=str(GPU), egl_device_id=GPU,
        dev_seeds=list(DEV_SEEDS), condition="C", profile="judgment",
        executable_world_revision="r1", c_arm="full",
        model_provider="local-vllm", inference_endpoint=ENDPOINT,
        model=MODEL, model_tag=MODEL, expected_served_model=MODEL,
        effort="xhigh", context_tokens=1000000, max_output_tokens=64000,
        claude_bin=CLAUDE_BIN,
        pilot_interface_doc=str(base.INTERFACE_DOC),
        c_starter=str(base.STUDY / "cells" / cell / "prior_c"),
        generation_context="native Qwen C full development pilot; seeds 51-53")
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
                                   "reason": "no high-level strategy MD in this C pilot"})
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
    return case, changes


def external_inputs(case):
    """The starter and the imported diagnostic live under docs/, outside the hashed
    source directories, so they are pinned explicitly or they could drift."""
    sim = base.cell_sim(case["id"])
    extra = base.tree_files(sim / case["c_starter"]) + base.tree_files(preflight_target(sim))
    return original_external(case) + extra


def render_prompts(case):
    """Render through the frozen scope adapter, in a fresh subprocess.

    The base shells `native_world_campaign.py --render-only`, which renders the
    pristine 51-65 scope with its Stage 2 promise and never sees `pilot_scope`.
    The adapter imports the same frozen campaign module from the staged repo,
    applies the same patch the driver applies, and prints the same JSON keys.
    """
    import subprocess
    sim = base.cell_sim(case["id"])
    renderer = base.PARENT / "support/pilot_render.py"
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
        "kind": "native Qwen C full development pilot, one cell",
        "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
        "cells": [{"id": CELL, "condition": "C", "task": base.TASKS["bowl"],
                   "profile": "judgment"}],
        "pilot_scope": {"development_seeds": DEV_SEEDS,
                        "evaluation_seeds": [], "held_out_performed": False,
                        "evaluate_invoked": False,
                        "terminal_status": "pilot_complete",
                        "reason": "development pilot only; no held-out stage exists"},
        "budgets": {"max_charged_attempts_per_dev_seed": 3,
                    "imported_charged_attempts": {"51": 1},
                    "remaining_attempts": {"51": 2, "52": 3, "53": 3},
                    "campaign_timeout_seconds": 12 * 3600,
                    "max_steps": 4000, "trial_timeout_seconds": 900},
        "common_starter": {"source": str(STARTER_SOURCE), "pins": STARTER_PINS,
                           "shared_by": [CELL],
                           "note": "the whole declared bowl starter; no other results"},
        "experiment_model": {"provider": "local-vllm", "endpoint": ENDPOINT,
                             "request": MODEL, "served": MODEL, "effort": "xhigh",
                             "context_tokens": 1000000, "max_output_tokens": 64000,
                             "claude_bin": CLAUDE_BIN, "api_key_helper": None},
        "gpu": GPU, "executable_world_revision": "r1", "c_arm": "full",
        "no_heldout": True, "no_A_launch": True,
    }


def prepare() -> dict:
    for path in (base.PARENT, base.RUNTIME, base.CAMPAIGN_PLAN, base.BASELINE_CONTRACT):
        if path.exists():
            raise SystemExit(f"{path} exists; preserve it and stage under a fresh suffix")
    base.BASELINE_CONTRACT.write_bytes(CONTRACT_SOURCE.read_bytes())
    if base.digest(base.BASELINE_CONTRACT) != base.digest(CONTRACT_SOURCE):
        raise SystemExit("baseline contract byte copy failed")
    base.record(base.CAMPAIGN_PLAN, campaign_plan())
    base.record(base.EXPECTED_PROMPTS, {})  # No A cells and no A prompt oracle here.
    receipt = base.prepare([CELL])
    print(json.dumps({"prepared_at": receipt["prepared_at"],
                      "cells": list(receipt["cells"]),
                      "result_parent": receipt["result_parent"],
                      "runtime_root": receipt["runtime_root"],
                      "launched": receipt["launched"]}, indent=2))
    return receipt


# ---- verify ---------------------------------------------------------------


EXPECTED_CASE = {
    "id": CELL, "condition": "C", "profile": "judgment", "c_arm": "full",
    "executable_world_revision": "r1", "task": "put_the_bowl_on_the_plate",
    "model_provider": "local-vllm", "inference_endpoint": ENDPOINT,
    "model": MODEL, "model_tag": MODEL, "expected_served_model": MODEL,
    "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000,
    "claude_bin": CLAUDE_BIN, "gpu": GPU, "cuda_visible_devices": str(GPU),
    "egl_device_id": GPU, "dev_seeds": DEV_SEEDS,
    "pilot_interface_doc": str(base.INTERFACE_DOC),
    "c_starter": str(base.STUDY / "cells" / CELL / "prior_c"),
    "sam3_url": "http://127.0.0.1:8114", "graspnet_url": "http://127.0.0.1:8115",
    "pyroki_url": "http://127.0.0.1:8116", "service_ports": [8114, 8115, 8116],
    "max_steps": 4000, "trial_timeout": 900, "campaign_timeout": 12 * 3600,
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
    """The rendered worker prompt must state this pilot's scope and no other."""
    sys.path.insert(0, str(base.SUPPORT))
    import pilot_scope
    path = Path(case["control"]) / "worker-prompt.md"
    text = path.read_text()
    problems = []
    leftovers = [marker for marker in pilot_scope.FORBIDDEN_AFTER if marker in text]
    if leftovers:
        problems.append(f"contradictory scope text survived: {leftovers}")
    allowed = {str(s) for s in DEV_SEEDS}
    stray = sorted({m.group() for m in re.finditer(r"(?<![\w.\-])(5[1-9]|6[0-5])(?![\w.\-])", text)}
                   - allowed)
    if stray:
        problems.append(f"out-of-partition seed references survived: {stray}")
    listed = ", ".join(str(s) for s in DEV_SEEDS)
    for required in (f"Stage 1 (debug development seeds {listed}) ONLY.",
                     f"## Stage 1: Debug Seeds {listed}",
                     case["pilot_interface_doc"]):
        if required not in text:
            problems.append(f"the render does not state {required!r}")
    return {"path": str(path), "sha256": base.digest(path), "bytes": len(text.encode()),
            "stale_seed_references": stray, "forbidden_markers": leftovers,
            "problems": problems}


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
              "diagnostic_sources": check_preflight(case)}
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
