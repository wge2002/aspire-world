#!/usr/bin/env python3
"""Prepare, launch and run the four judgment-study two-task cells.

Three explicit modes, never implicitly chained:

  --prepare   stage one frozen runtime per cell, write one case config per cell,
              render the prompts, bind the runtime manifests and freeze the
              shared outer driver. Starts no model and no simulator.
  --launch    verify the already staged cases, frozen support and frozen queue
              source, then start one persistent detached serial queue.
  --queue     the queue body: bowl_A1, bowl_C1, drawer_A1, drawer_C1, serially,
              resumable, surviving shell disconnection.

Cells are staged from the byte-verified original Sep-14 A1 source reference under
`reference/native-A1-source/`, never from the current engineering scripts. The C
cells receive that same original source plus an explicit judgment overlay and
this study's judgment interface document. Each cell gets its own runtime tree,
its own outputs, its own strategy directory and its own native
CLAUDE_CONFIG_DIR, so nothing crosses between cells. Reference archives,
fixtures, tests, handoffs, histories, previous results and generated programs
are never copied into a solver runtime, and no secret is read here: the native
apiKeyHelper is referenced by path only.

The per-cell campaign itself is run by the already-written shared outer driver
`support/run_cell.py` (with `support/cell_read_guard.py` beside it). Both are
frozen at the result parent and re-verified before every cell. This file never
reimplements the native loop.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINEERING = Path("/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim")
RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-code-world-judgment-two-task-20260917")
PARENT = Path("/mnt/home/gewang/experiments/code-world-judgment-two-task-20260917")
STUDY = Path("docs/experiments/code-world-judgment-two-task-20260917")
PRISTINE = Path("docs/experiments/world-abc-opus46-bowl-20260913")
VENV = Path("/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero")
# The original A1 runtime's parent package tree; the two package entry points are
# copied from it verbatim so the staged import path matches the baseline.
PACKAGE_SOURCE = Path("/mnt/home/gewang/code/ASPIRE-world-native-fixloop-abc-opus46-bowl-20260914"
                      "/cells/A1/aspire")
BASE_COMMIT = "7ba73d3bcac8f6b6d4a7d67ed4040988f768d282"

STUDY_DIR = ENGINEERING / STUDY
REFERENCE = STUDY_DIR / "reference"
A_SOURCE = REFERENCE / "native-A1-source"
EXPECTED_PROMPTS = REFERENCE / "expected-baseline-prompts.json"
EXPECTED_PROMPT_DIR = REFERENCE / "expected-baseline-prompts"
SUPPORT = STUDY_DIR / "support"
BASELINE_CONTRACT = STUDY_DIR / "baseline-contract.json"
CAMPAIGN_PLAN = STUDY_DIR / "campaign-plan.json"
INTERFACE_DOC = STUDY / "NATIVE_WORLD_INTERFACE.md"

# The flat original source reference is curated already: it holds exactly the
# original cap/, scripts/, env_configs/, .claude/ files and the root files.
EXPECTED_A_SOURCE_FILES = 343
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", "outputs",
                                ".venv*", "settings.local.json", "*.jsonl", "*.log")
IGNORED_NAMES = ("__pycache__", ".git", ".pytest_cache")

# Serial order on the single physical GPU.
CELLS = (("bowl_A1", "A"), ("bowl_C1", "C"), ("drawer_A1", "A"), ("drawer_C1", "C"))
PROFILES = {"A": "legacy_native", "C": "judgment"}
SUITE = "libero_goal_swap"
TASKS = {"bowl": "put_the_bowl_on_the_plate",
         "drawer": "open_the_middle_drawer_of_the_cabinet"}
GPU = 7  # Physical device 7: CUDA mask 7 and EGL device index 7 for every cell.
CELL_HOURS = 12

PACKAGE_FILES = ("aspire/__init__.py", "aspire/sim/__init__.py")
STRATEGY_MD = ("localize.md", "grasp.md", "transport.md", "manipulation.md")
# Prior authored/reference worlds are not inputs to the new experimental solvers;
# removed identically in both conditions and recorded as an input difference.
REMOVED_SOURCES = ("cap/world_model/reference_relational_world.py",
                   "cap/world_model/reference_scene_broker.py")
# The complete judgment overlay for condition C, taken from engineering. No
# further source dependency is required, and no unrelated shadow mode is added.
C_OVERLAY = ("cap/world_model/judgment_world.py",
             "cap/world_model/simple_world.py",
             "scripts/libero/replay_trial.py",
             "scripts/libero/native_world_campaign.py",
             "scripts/libero/native_world_protocol.py",
             "scripts/libero/native_world_fixloop_state.py",
             "scripts/libero/native_world_heldout.py",
             "scripts/libero/simple_world_profile.py")
# Frozen shared outer inputs. Already written and verified; copied byte for byte.
SUPPORT_FILES = ("run_cell.py", "cell_read_guard.py")

TERMINAL = "complete"


# ---- small helpers --------------------------------------------------------


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cell_root(cell: str) -> Path:
    return RUNTIME / "cells" / cell


def cell_sim(cell: str) -> Path:
    return cell_root(cell) / "aspire/sim"


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path: Path, value) -> None:
    """Write once. An existing file means a stale run; never overwrite evidence."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def _write(path: Path, value) -> None:
    temporary = Path(path).with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def tree_files(root: Path) -> list[Path]:
    """Every regular file under `root`, ignoring caches, sorted by relative path."""
    found = []
    for path in sorted(root.rglob("*")):
        if any(part in IGNORED_NAMES for part in path.relative_to(root).parts):
            continue
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return found


def tree_digest(root: Path) -> tuple[str, int]:
    """A single digest over (relative path, content) pairs, plus the file count."""
    files = tree_files(root)
    accumulator = hashlib.sha256()
    for path in files:
        accumulator.update(str(path.relative_to(root)).encode())
        accumulator.update(b"\0")
        accumulator.update(digest(path).encode())
        accumulator.update(b"\n")
    return accumulator.hexdigest(), len(files)


# ---- case configuration ---------------------------------------------------


def case_config(cell: str, condition: str) -> dict:
    """The runtime case. Keys are exactly the original native case dictionary's."""
    control = PARENT / cell
    sim = cell_sim(cell)
    task = TASKS[cell.split("_")[0]]
    # The strategy directory lives under docs/, outside the hashed source
    # directories, so within-cell promotion stays possible without breaking the
    # freeze. A keeps a private copy of the four original MD; C has none.
    skills = f"{STUDY}/cells/{cell}/" + ("skills" if condition == "A" else "notes")
    return {
        "id": cell, "condition": condition, "profile": PROFILES[condition],
        "sim": str(sim), "python_root": str(cell_root(cell)), "control": str(control),
        "claude_config_dir": str(control / "native/config"),
        "suite": SUITE, "task": task, "dev_seeds": list(range(51, 66)),
        "max_steps": 4000, "trial_timeout": 900,
        "env_config": "env_configs/libero/franka_libero_traced.yaml",
        # Device and service wiring consumed by runtime_env. One trial at a time.
        "gpu": GPU, "cuda_visible_devices": str(GPU), "egl_device_id": GPU,
        "egl_vendor_config": str(control / "nvidia-egl-vendor.json"),
        "service_ports": [8114, 8115, 8116],
        "sam3_url": "http://127.0.0.1:8114",
        "graspnet_url": "http://127.0.0.1:8115",
        "pyroki_url": "http://127.0.0.1:8116",
        # The verified native recipe: tagged model, high effort, 1M context, 64K out.
        "model": "claude-opus-4-6", "model_tag": "claude-opus-4-6[1m]",
        "expected_served_model": "claude-opus-4-6",
        "effort": "high", "context_tokens": 1000000, "max_output_tokens": 64000,
        # Reused from the existing service's own configured value, not a new knob.
        "disable_experimental_betas": "1",
        "claude_bin": "/mnt/home/gewang/.local/bin/claude",
        "api_key_helper": "/mnt/home/gewang/.local/bin/claude-roboscience-api-key",
        "campaign_timeout": CELL_HOURS * 3600,
        "generation_context": (f"native-cc {cell} {PROFILES[condition]} profile fix loop, "
                               "dev seeds 51-65"),
        "base_commit": BASE_COMMIT, "skill_library_dir": skills,
        "runtime_manifest": str(control / "runtime-manifest.json"),
        "require_runtime_freeze": True,
    }


# ---- staging --------------------------------------------------------------


def stage_cell(cell: str, condition: str, contract: dict) -> tuple[dict, dict]:
    """One isolated runtime tree for one cell. Stages only; runs nothing.

    The task directory is deliberately NOT created here: the original
    initialization owns it and refuses a directory that already has artifacts.
    """
    sim = cell_sim(cell)
    control = PARENT / cell
    (control / "native/config").mkdir(parents=True)
    sim.mkdir(parents=True)

    # 1. The original Sep-14 A1 source, verbatim, for both conditions.
    source_sha, source_count = tree_digest(A_SOURCE)
    if source_count != EXPECTED_A_SOURCE_FILES:
        raise SystemExit(f"{cell}: original A source has {source_count} files, "
                         f"expected {EXPECTED_A_SOURCE_FILES}; do not stage from engineering")
    shutil.copytree(A_SOURCE, sim, symlinks=False, ignore=IGNORE, dirs_exist_ok=True)
    staged_sha, staged_count = tree_digest(sim)
    if (staged_sha, staged_count) != (source_sha, source_count):
        raise SystemExit(f"{cell}: staged tree differs from the original A source reference")
    differences: dict = {"a_source": {"path": str(A_SOURCE), "tree_sha256": source_sha,
                                      "file_count": source_count},
                         "removed": [], "overlay": {}, "added": {}}

    # 2. Identical removal of the two old reference authored-world files.
    for relative in REMOVED_SOURCES:
        target = sim / relative
        if target.is_file():
            differences["removed"].append({"path": relative, "source_sha256": digest(target)})
            target.unlink()
        else:
            differences["removed"].append({"path": relative, "source_sha256": None,
                                           "note": "absent in the original A source"})

    # 3. Condition C only: the explicit judgment overlay plus its interface doc.
    if condition == "C":
        for relative in C_OVERLAY:
            source = ENGINEERING / relative
            if not source.is_file():
                raise SystemExit(f"{cell}: missing judgment overlay source {source}")
            target = sim / relative
            replaced = digest(target) if target.is_file() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            differences["overlay"][relative] = {"source": str(source),
                                                "sha256": digest(target),
                                                "replaced_sha256": replaced}
        interface = sim / INTERFACE_DOC
        interface.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ENGINEERING / INTERFACE_DOC, interface)
        differences["added"][str(INTERFACE_DOC)] = digest(interface)

    # 4. Baseline pins. Every contract file must still hash exactly, except the
    #    files C deliberately overlays; those are recorded as differences above.
    overlaid = set(C_OVERLAY) if condition == "C" else set()
    for relative, expected in contract["baseline_files"].items():
        target = sim / relative
        if relative in overlaid:
            continue
        if not target.is_file():
            raise SystemExit(f"{cell}: baseline file missing from the staged runtime: {relative}")
        if digest(target) != expected:
            raise SystemExit(f"{cell}: baseline pin mismatch for {relative}")

    # 5. Package entry points, from the original A1 runtime's parent package tree.
    for relative in PACKAGE_FILES:
        source = PACKAGE_SOURCE / Path(relative).relative_to("aspire")
        target = cell_root(cell) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        differences["added"][relative] = digest(target)

    # 6. Vetted dependencies are symlinked, never copied. The interpreter is the
    #    only runtime dependency link; there are no source links.
    (sim / ".venv-libero").symlink_to(VENV, target_is_directory=True)
    differences["dependency_links"] = {".venv-libero": str(VENV)}
    differences["source_links"] = {}

    # 7. Generated programs and evidence live in the result parent; the runtime
    #    tree itself stays frozen. Guard comparisons resolve both sides.
    (control / "outputs/working_codes").mkdir(parents=True)
    (sim / "outputs").symlink_to(control / "outputs", target_is_directory=True)

    case = case_config(cell, condition)

    # 8. The private strategy directory. A gets exactly the four byte-verified
    #    original MD; C has no high-level strategy library at all.
    strategy = sim / case["skill_library_dir"]
    strategy.mkdir(parents=True)
    if condition == "A":
        for filename in STRATEGY_MD:
            expected = contract["initial_md"][filename]
            target = strategy / filename
            shutil.copy2(ENGINEERING / PRISTINE / "pristine-skills" / filename, target)
            if digest(target) != expected:
                raise SystemExit(f"{cell}: staged initial MD differs: {filename}")
            differences["added"][f"{case['skill_library_dir']}/{filename}"] = expected
    else:
        (strategy / "README.md").write_text(
            "# This cell has no high-level strategy library\n\n"
            "This cell reads the skill API reference, the API source and the judgment\n"
            "world interface only. There is no strategy document to read and no\n"
            "promotion step.\n")

    record(Path(case["egl_vendor_config"]), {"file_format_version": "1.0.0",
           "ICD": {"library_path": "libEGL_nvidia.so.0"}})
    return case, differences


# ---- manifest, prompts ----------------------------------------------------

# Run in a fresh subprocess against the staged tree so that A and C runner
# revisions never share this process's module cache.
MANIFEST_PROGRAM = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import native_cc_freeze
manifest = native_cc_freeze.build_manifest(
    Path(sys.argv[2]),
    external_files=[Path(p) for p in json.loads(sys.argv[3])],
    executables=[Path(p) for p in json.loads(sys.argv[4])])
sys.stdout.write(json.dumps(manifest))
"""


def external_inputs(case: dict) -> list[Path]:
    """Files outside the hashed source dirs that still must not drift."""
    external = [cell_root(case["id"]) / relative for relative in PACKAGE_FILES]
    external.append(Path(case["egl_vendor_config"]))
    # The shared outer driver and read guard are frozen runtime inputs of every
    # cell, in both conditions, byte-identical across them.
    external.extend(PARENT / "support" / name for name in SUPPORT_FILES)
    if case["condition"] == "C":
        external.append(cell_sim(case["id"]) / INTERFACE_DOC)
    return external


def bind_manifest(case: dict) -> dict:
    """Freeze this cell's runtime, pin the manifest, then write the case once."""
    sim = cell_sim(case["id"])
    external = [str(path) for path in external_inputs(case)]
    proc = subprocess.run(
        [str(sim / ".venv-libero/bin/python3"), "-c", MANIFEST_PROGRAM,
         str(sim / "scripts/common"), str(sim), json.dumps(external),
         json.dumps([case["claude_bin"]])],
        cwd=sim, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: manifest build failed:\n{proc.stderr[-4000:]}")
    manifest = json.loads(proc.stdout)
    path = Path(case["runtime_manifest"])
    record(path, manifest)
    case["runtime_manifest_sha256"] = digest(path)
    record(Path(case["control"]) / "case.json", case)
    return case


def render_prompts(case: dict) -> dict:
    """Render this cell's worker/coordinator prompts. Runs no model, no simulator."""
    sim = cell_sim(case["id"])
    proc = subprocess.run(
        [str(sim / ".venv-libero/bin/python3"), "scripts/libero/native_world_campaign.py",
         "--case", str(Path(case["control"]) / "case.json"), "--render-only"],
        cwd=sim, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: prompt rendering failed:\n{proc.stderr[-4000:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"stdout": proc.stdout[-4000:]}


def verify_prompts(case: dict, oracles: dict) -> dict:
    """A prompts must equal the independent oracle byte for byte; C is recorded."""
    control = Path(case["control"])
    saved = {}
    for name in ("worker-prompt.md", "coordinator-prompt.md"):
        path = control / name
        if not path.is_file():
            raise SystemExit(f"{case['id']}: {name} was not rendered")
        saved[name] = digest(path)
    if case["condition"] != "A":
        return saved
    oracle = oracles.get(case["id"])
    if oracle is None:
        raise SystemExit(f"{case['id']}: no baseline prompt oracle to compare against")
    for key, value in oracle["render_fields"].items():
        if str(case[key]) != str(value):
            raise SystemExit(f"{case['id']}: render field {key} is {case[key]!r}, "
                             f"oracle expects {value!r}")
    for name, expected in oracle["sha256"].items():
        if saved[name] != expected:
            raise SystemExit(f"{case['id']}: {name} differs from the baseline oracle")
    for name in saved:
        reference = EXPECTED_PROMPT_DIR / case["id"] / name
        if not reference.is_file():
            raise SystemExit(f"{case['id']}: missing baseline prompt oracle {reference}")
        if reference.read_bytes() != (control / name).read_bytes():
            raise SystemExit(f"{case['id']}: {name} differs from {reference} byte for byte")
    return saved


# ---- frozen shared inputs -------------------------------------------------


def freeze_bytes(source: Path, target: Path) -> str:
    """Freeze one external input at the result parent. Never overwrite evidence."""
    payload = Path(source).read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise SystemExit(f"{target} already exists with different bytes; inspect it")
    else:
        with target.open("xb") as stream:
            stream.write(payload)
    return hashlib.sha256(payload).hexdigest()


def freeze_support() -> dict:
    """The shared outer driver and its read guard, byte-identical for every cell."""
    frozen = {}
    for name in SUPPORT_FILES:
        source = SUPPORT / name
        if not source.is_file():
            raise SystemExit(f"missing shared support file {source}")
        frozen[name] = freeze_bytes(source, PARENT / "support" / name)
    return frozen


def verify_support(frozen: dict) -> None:
    for name, expected in frozen.items():
        path = PARENT / "support" / name
        if not path.is_file():
            raise SystemExit(f"frozen support file missing: {path}")
        if digest(path) != expected:
            raise SystemExit(f"frozen support file changed since --prepare: {path}")


# ---- prepare --------------------------------------------------------------


def prepare(cells: list[str]) -> dict:
    """Stage runtime, configs, manifests and prompts only. Starts nothing."""
    for path in (BASELINE_CONTRACT, CAMPAIGN_PLAN, EXPECTED_PROMPTS):
        if not path.is_file():
            raise SystemExit(f"missing required pin: {path}")
    if not A_SOURCE.is_dir():
        raise SystemExit(f"missing original A source reference: {A_SOURCE}")
    contract = json.loads(BASELINE_CONTRACT.read_text())
    plan = json.loads(CAMPAIGN_PLAN.read_text())
    oracles = json.loads(EXPECTED_PROMPTS.read_text())
    planned = {entry["id"]: entry for entry in plan["cells"]}
    for cell, condition in CELLS:
        entry = planned.get(cell)
        if entry is None or entry["condition"] != condition:
            raise SystemExit(f"{cell}: campaign-plan.json does not declare this cell")
        if entry["task"] != TASKS[cell.split("_")[0]] or entry["profile"] != PROFILES[condition]:
            raise SystemExit(f"{cell}: task/profile disagrees with campaign-plan.json")
    if digest(BASELINE_CONTRACT) != plan["baseline_contract_sha256"]:
        raise SystemExit("baseline-contract.json does not match the SHA pinned in campaign-plan.json")

    # --prepare refuses existing cells outright; it never reuses or resets one.
    for cell in cells:
        for path in (cell_root(cell), PARENT / cell):
            if path.exists():
                raise SystemExit(f"{cell}: {path} exists; use fresh runtime and result paths")

    frozen_support = freeze_support()
    staged = {}
    for cell, condition in CELLS:
        if cell not in cells:
            continue
        case, differences = stage_cell(cell, condition, contract)
        case = bind_manifest(case)
        render = render_prompts(case)
        prompts = verify_prompts(case, oracles)
        case_path = Path(case["control"]) / "case.json"
        record(Path(case["control"]) / "staged-inputs.json",
               {"cell": cell, "condition": condition, "profile": PROFILES[condition],
                "staged_at": now(), "base_commit": BASE_COMMIT,
                "source_differences": differences,
                "frozen_support": frozen_support,
                "external_inputs": [str(p) for p in external_inputs(case)]})
        staged[cell] = {"case": str(case_path), "case_sha256": digest(case_path),
                        "condition": condition, "profile": PROFILES[condition],
                        "task": case["task"], "gpu": case["gpu"],
                        "runtime_manifest": case["runtime_manifest"],
                        "runtime_manifest_sha256": case["runtime_manifest_sha256"],
                        "prompt_sha256": prompts,
                        "prompt_oracle": "reference/expected-baseline-prompts.json"
                                         if condition == "A" else None,
                        "source_differences": differences,
                        "render": render}

    queue_script = PARENT / "frozen-prepare-and-launch.py"
    queue_sha = freeze_bytes(Path(__file__), queue_script)
    receipts = sorted(str(p) for p in REFERENCE.glob("native-A1-source*")
                      if p.is_file())
    receipt = {"prepared_at": now(), "study": str(STUDY),
               "runtime_root": str(RUNTIME), "result_parent": str(PARENT),
               "queue_script": str(queue_script), "queue_script_sha256": queue_sha,
               "driver": str(PARENT / "support/run_cell.py"),
               "frozen_support": frozen_support,
               "base_commit": BASE_COMMIT,
               "baseline_contract_sha256": digest(BASELINE_CONTRACT),
               "campaign_plan_sha256": digest(CAMPAIGN_PLAN),
               "expected_prompts_sha256": digest(EXPECTED_PROMPTS),
               "a_source_receipts": receipts,
               "serial_order": [cell for cell, _ in CELLS if cell in cells],
               "cells": staged, "launched": False,
               "note": "staged only; no model, simulator or queue has run. Verify, then --launch."}
    record(PARENT / "prepare-receipt.json", receipt)
    return receipt


# ---- queue ----------------------------------------------------------------


def verify_cell(entry: dict, frozen_support: dict) -> dict:
    """Re-verify one staged cell's pinned inputs immediately before its driver."""
    case_path = Path(entry["case"])
    if not case_path.is_file():
        raise SystemExit(f"missing staged case: {case_path}")
    if digest(case_path) != entry["case_sha256"]:
        raise SystemExit(f"{case_path} changed since --prepare")
    case = json.loads(case_path.read_text())
    if digest(Path(case["runtime_manifest"])) != case["runtime_manifest_sha256"]:
        raise SystemExit(f"{case['id']}: staged runtime manifest changed since --prepare")
    for name, expected in entry["prompt_sha256"].items():
        path = Path(case["control"]) / name
        if not path.is_file():
            raise SystemExit(f"{case['id']}: missing rendered {name}; re-run --prepare")
        if digest(path) != expected:
            raise SystemExit(f"{case['id']}: rendered {name} changed since --prepare")
    verify_support(frozen_support)
    # Verify the actual source/external inputs, not just their manifest file.
    program = (
        'import json,sys; from pathlib import Path; '
        'sys.path.insert(0,"scripts/common"); '
        'from native_cc_freeze import verify_runtime; '
        'case=json.loads(Path(sys.argv[1]).read_text()); '
        'verify_runtime(case,Path(case["sim"]))'
    )
    proc = subprocess.run(
        [str(Path(case["sim"]) / ".venv-libero/bin/python3"), "-c", program, str(case_path)],
        cwd=case["sim"], capture_output=True, text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
    if proc.returncode:
        raise SystemExit(f"{case['id']}: frozen runtime verification failed: {proc.stderr[-4000:]}")
    return case


def cell_started(case: dict) -> list[str]:
    """Artifacts proving something already ran for this cell, by absolute path."""
    control, sim = Path(case["control"]), Path(case["sim"])
    candidates = [control / "campaign_state.json", control / "probe.json",
                  control / "probe.stdout.jsonl", control / "probe.stderr.log",
                  sim / "outputs/libero_fix_loop" / case["suite"] / case["task"]]
    candidates.extend(sorted(control.glob("recovery-*")))
    return [str(path) for path in candidates if path.exists()]


def run_cell(case: dict, case_path: Path, log: Path) -> dict:
    """Run one fresh cell through the frozen shared driver. Never reruns evidence."""
    control = Path(case["control"])
    state_path = control / "campaign_state.json"
    if state_path.is_file():
        try:
            recorded = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return {"cell": case["id"], "action": "skipped", "status": "needs_inspection",
                    "blocker": f"unreadable campaign record: {exc}",
                    "reason": "existing evidence is preserved and not replaced"}
        return {"cell": case["id"], "action": "skipped",
                "status": recorded.get("status"), "blocker": recorded.get("blocker"),
                "recovery_dir": recorded.get("recovery_dir"),
                "reason": "this cell already has a campaign record; preserved, not rerun"}
    artifacts = cell_started(case)
    if artifacts:
        # Artifacts with no campaign record: an interrupted start. Replaying over
        # them would destroy the only evidence of what ran. This queue does not
        # recover an interrupted cell; a human inspects it.
        return {"cell": case["id"], "action": "skipped", "status": "needs_inspection",
                "blocker": "prior partial artifacts exist without a campaign record",
                "artifacts": artifacts,
                "reason": "inspect and move the artifacts before this cell is run again"}
    driver = PARENT / "support/run_cell.py"
    command = [str(Path(case["sim"]) / ".venv-libero/bin/python3"), str(driver),
               "--case", str(case_path)]
    with log.open("a") as stream:
        stream.write(f"\n=== {case['id']} start {now()} ===\n")
        stream.write(f"command: {command}\n")
        stream.flush()
        code = subprocess.run(command, cwd=Path(case["sim"]), stdin=subprocess.DEVNULL,
                              stdout=stream, stderr=subprocess.STDOUT).returncode
    final = {}
    if state_path.is_file():
        try:
            final = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError):
            final = {}
    return {"cell": case["id"], "action": "ran", "exit_code": code,
            "status": final.get("status", "unknown"), "blocker": final.get("blocker"),
            "recovery_dir": final.get("recovery_dir")}


def queue(cells: list[str]) -> int:
    """Serial bowl_A1 -> bowl_C1 -> drawer_A1 -> drawer_C1 on the one GPU.

    Resumable: a recorded outcome is skipped, a blocked cell never stops the
    remaining unstarted cells, and nothing is reset or resampled.
    """
    receipt_path = PARENT / "prepare-receipt.json"
    if not receipt_path.is_file():
        raise SystemExit("nothing staged; run --prepare and verify the cases first")
    receipt = json.loads(receipt_path.read_text())
    if digest(Path(__file__)) != receipt["queue_script_sha256"]:
        raise SystemExit("queue source changed since --prepare")
    frozen_support = receipt["frozen_support"]
    state_path = PARENT / "queue-state.json"
    log = PARENT / "queue.log"
    PARENT.mkdir(parents=True, exist_ok=True)
    lock = (PARENT / "queue.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another queue already holds this experiment's lock")
    try:
        state = json.loads(state_path.read_text()) if state_path.is_file() else {"cells": {}}
        state["pid"] = os.getpid()
        state["gpu"] = GPU
        state["started_at"] = now()
        state.setdefault("runs", []).append({"pid": os.getpid(), "started_at": now(),
                                             "cells": cells})
        _write(state_path, state)
        for cell, _ in CELLS:
            if cell not in cells or cell not in receipt["cells"]:
                continue
            previous = state["cells"].get(cell, {})
            if previous.get("status") == TERMINAL:
                continue  # Finished in an earlier queue run.
            if previous.get("action") == "ran" and previous.get("status") not in (None, "running"):
                continue  # A recorded non-terminal outcome; preserved, not rerun.
            entry = receipt["cells"][cell]
            started = now()
            try:
                case = verify_cell(entry, frozen_support)
            except SystemExit as exc:
                state["cells"][cell] = {"cell": cell, "action": "skipped",
                                        "status": "needs_inspection", "blocker": str(exc),
                                        "started_at": started, "finished_at": now(),
                                        "reason": "pinned input verification failed"}
                _write(state_path, state)
                continue  # Other unstarted cells still run.
            state["cells"][cell] = {"cell": cell, "status": "running", "started_at": started}
            _write(state_path, state)
            try:
                outcome = run_cell(case, Path(entry["case"]), log)
            except Exception as exc:  # The queue itself never dies with a cell.
                outcome = {"cell": cell, "action": "error", "status": "queue_error",
                           "blocker": f"{type(exc).__name__}: {exc}"}
            outcome["started_at"] = started
            outcome["finished_at"] = now()
            state["cells"][cell] = outcome
            _write(state_path, state)
        state["finished_at"] = now()
        state["runs"][-1]["finished_at"] = state["finished_at"]
        _write(state_path, state)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    print(json.dumps(state, indent=2))
    selected = [cell for cell, _ in CELLS if cell in cells and cell in receipt["cells"]]
    complete = [c for c in selected if state["cells"].get(c, {}).get("status") == TERMINAL]
    return 0 if len(complete) == len(selected) else 1


# ---- launch ---------------------------------------------------------------


def launch(cells: list[str]) -> dict:
    """Verify every pinned input, then detach one serial queue with a receipt."""
    receipt_path = PARENT / "prepare-receipt.json"
    if not receipt_path.is_file():
        raise SystemExit("nothing staged; run --prepare and verify the cases first")
    receipt = json.loads(receipt_path.read_text())
    queue_script = Path(receipt["queue_script"])
    if digest(queue_script) != receipt["queue_script_sha256"]:
        raise SystemExit("frozen queue source changed since --prepare")
    verify_support(receipt["frozen_support"])
    for cell in cells:
        if cell not in receipt["cells"]:
            raise SystemExit(f"{cell} was not prepared")
        verify_cell(receipt["cells"][cell], receipt["frozen_support"])
    ordered = [cell for cell, _ in CELLS if cell in cells]
    log = PARENT / "queue.log"
    command = [str(VENV / "bin/python3"), str(queue_script), "--queue",
               "--cells", ",".join(ordered)]
    with log.open("a") as stream:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    started = now()
    launch_receipt = {"launched_at": started, "pid": process.pid, "command": command,
                      "log": str(log), "queue_state": str(PARENT / "queue-state.json"),
                      "gpu": GPU, "serial_order": ordered,
                      "queue_script_sha256": receipt["queue_script_sha256"],
                      "frozen_support": receipt["frozen_support"],
                      "note": "one detached serial queue; cells run one at a time on GPU 7."}
    record(PARENT / f"launch-receipt-{started.replace(':', '')}.json", launch_receipt)
    return launch_receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true",
                      help="stage runtime, configs, manifests and prompts only; start nothing")
    mode.add_argument("--launch", action="store_true",
                      help="verify staged cases and frozen inputs, then detach the serial queue")
    mode.add_argument("--queue", action="store_true", help="the serial queue body itself")
    parser.add_argument("--cells", default=",".join(c for c, _ in CELLS))
    args = parser.parse_args()
    cells = [c.strip() for c in args.cells.split(",") if c.strip()]
    unknown = [c for c in cells if c not in {name for name, _ in CELLS}]
    if unknown:
        raise SystemExit(f"unknown cells: {unknown}")
    if args.prepare:
        print(json.dumps(prepare(cells), indent=2))
        return 0
    if args.launch:
        print(json.dumps(launch(cells), indent=2))
        return 0
    return queue(cells)


if __name__ == "__main__":
    raise SystemExit(main())
