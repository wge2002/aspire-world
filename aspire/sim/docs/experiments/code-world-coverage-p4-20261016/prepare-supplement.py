#!/usr/bin/env python3
"""Stage the CC_TASK_5B held-out supplement job (two p4n cells). Never launches a job.

Two p4n cells whose held-out sweep was cut by the old fixed 5 h watchdog, each
`blocked` at phase `heldout` with `state_preserved: true`:

  spatialbetween_p4n_r1   44 graded rows + seed 45 infrastructure_error -> runs 45-50
  twomoka_p4n_r1          40 graded rows + seed 41 infrastructure_error -> runs 41-50

The CC_TASK_5 path finishes them IN PLACE (heldout/ is 157 GB; coordinator
agreed): a fresh job root holds one queue entry per cell
(`heldout_supplement.stage_entry`: a byte copy of the source cell's staged
case.json + `heldout-supplement.json`), and the supervisor runs the driver with
`--heldout-supplement` on the source cell's own frozen runtime and bundle.

Support = the p4n freeze rule (p3 support, overlaid by `<p4 study>/support`)
with `heldout_supplement.py` added to SUPPORT_FILES; launch = the current
shared `scripts/common/node_layout/` (CC_TASK_5). Nothing else differs from p4n.

  prepare-supplement.py --backup       copy the source cells' small state files + sha256 (write-once)
  prepare-supplement.py --prepare      stage <root>: support, launch, two entries, receipt
  prepare-supplement.py --verify       re-verify the staging and the sources; byte diff against p4n
  prepare-supplement.py --check-after  read-only, after the job: kept rows unchanged, 50 rows, ...
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMPATH = HERE.parents[2]
P3 = SIMPATH / "docs/experiments/code-world-search-control-p3-20261013"
P4N = Path("/mnt/home/gewang/experiments/code-world-hardest-p4n-20261018")
NAME = "code-world-p4n-supplement-20261019"
PARENT = Path("/mnt/home/gewang/experiments") / NAME
BACKUP = Path("/mnt/home/gewang/experiments") / f"{NAME}-source-backup"
RECORDS = HERE / "cc5b-supplement"
#: Task 5's staged smoke root: its frozen copies are the bytes Task 5's suite tested.
CC5_SMOKE = Path("/mnt/home/gewang/experiments/code-world-coverage-p4-20261016-cc5-smoke")

#: cell -> the seeds the supplement must run (CC_TASK_5 plan, coordinator 10-18 23:30).
CELLS = {"spatialbetween_p4n_r1": list(range(45, 51)), "twomoka_p4n_r1": list(range(41, 51))}
HELDOUT_SEEDS = list(range(1, 51))
#: What the supplement rewrites in a source cell (everything else there it only
#: reads, creates beside, or moves: heldout/supplement/<n>/, new seed dirs, the
#: superseded seed dirs).
REWRITTEN = ("campaign_state.json", "heldout/heldout_state.json", "heldout/heldout_result.json")
SNAPSHOT = REWRITTEN + ("queue-result.json", "case.json", "slot-case.json", "heldout/heldout_manifest.json",
                        "heldout/frozen/fix_code.py", "heldout/frozen/fix_world_program.py")
#: The only frozen bytes allowed to differ from p4n.
SUPPORT_DIFF = {"full_deadlines.py", "run_two_task_cell.py", "heldout_supplement.py"}
LAUNCH_DIFF = {"unit_layout.py", "unit_supervisor.py", "study.json"}
STUDY_KEYS_DIFF = {"cells", "note", "required_support"}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = load(HERE / "prepare-coverage-study.py", "cc5b_p4_stager")
base, layout = pc.base, pc.layout
base.SUPPORT_FILES = tuple(base.SUPPORT_FILES) + ("heldout_supplement.py",)
pc.CELL_ORDER = tuple(CELLS)
OVERLAY = {p.name: p.resolve() for p in (HERE / "support").iterdir()
           if p.is_file() and p.name in base.SUPPORT_FILES}


def digest(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_hash(path) -> str:
    """`native_world_fixloop_state.code_hash` of a file, as the evaluator reads it."""
    return hashlib.sha256(Path(path).read_text().encode()).hexdigest()


def bundle_identity(bundle: dict) -> str:
    return hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def row_digest(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tree_inventory(folder: Path) -> dict:
    """Every file under `folder`: relative path -> [size, sha256]."""
    out = {}
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[str(path.relative_to(folder))] = [path.stat().st_size, digest(path)]
    return out


def top_level(folder: Path) -> dict:
    """Top-level entries of a directory: regular files by sha256, the rest by kind."""
    out = {}
    for path in sorted(Path(folder).iterdir()):
        if path.is_symlink():
            out[path.name] = "symlink:" + os.readlink(path)
        elif path.is_file():
            out[path.name] = digest(path)
        else:
            out[path.name] = "dir"
    return out


def root_inventory() -> dict:
    """The p4n root as the job must leave it, apart from the two cells' supplement writes."""
    cells = {p.name: top_level(p) for p in sorted(P4N.iterdir()) if p.is_dir() and (p / "case.json").is_file()}
    heldout = {cell: sorted(p.name for p in (P4N / cell / "heldout").iterdir()) for cell in CELLS}
    return {"root": top_level(P4N), "cells": cells, "heldout_entries": heldout}


def supplement_module(support: Path):
    return load(support / "heldout_supplement.py", f"cc5b_heldout_supplement_{abs(hash(str(support)))}")


# ---- backup -------------------------------------------------------------------


def backup() -> int:
    if BACKUP.exists():
        raise SystemExit(f"{BACKUP} exists; the backup is write-once")
    supplement = supplement_module(HERE / "support")
    cells = {}
    for cell, expected in CELLS.items():
        control = P4N / cell
        planned = supplement.plan(control / "heldout")
        if planned["seeds_to_run"] != expected:
            raise SystemExit(f"{cell}: seeds_to_run {planned['seeds_to_run']} != {expected}")
        ledger = json.loads((control / "heldout/heldout_state.json").read_text())
        files = {}
        for relative in SNAPSHOT:
            source, target = control / relative, BACKUP / cell / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            files[relative] = {"source_sha256": digest(source), "backup_sha256": digest(target),
                               "bytes": source.stat().st_size, "rewritten_by_supplement": relative in REWRITTEN}
        moved = {f"seed_{s:02d}": tree_inventory(control / "heldout" / f"seed_{s:02d}")
                 for s in expected if (control / "heldout" / f"seed_{s:02d}").is_dir()}
        cells[cell] = {"source_control": str(control), "files": files,
                       "row_sha256": {k: row_digest(v) for k, v in ledger["seeds"].items()},
                       "row_status": {k: v["status"] for k, v in ledger["seeds"].items()},
                       "seeds_to_run": expected, "plan": planned,
                       "moved_by_supplement": moved,
                       "ledger_serialization_stable": (json.dumps(ledger, indent=2) + "\n").encode()
                       == (control / "heldout/heldout_state.json").read_bytes()}
    manifest = {"created_at": base.now(), "p4n_root": str(P4N), "cells": cells,
                "root_inventory": root_inventory(),
                "note": ("CC_TASK_5B pre-supplement snapshot: copies (shutil.copy2) of every small state "
                         "file the supplement rewrites or reads as identity, their sha256, per-row sha256 "
                         "of each held-out ledger, a full inventory of the seed directories the supplement "
                         "moves to heldout/supplement/<n>/superseded/, and a top-level sha256 inventory of "
                         "every p4n cell. Restore = copy the files back; nothing here is read by the job.")}
    base.record(BACKUP / "backup-manifest.json", manifest)
    print(json.dumps({"backup": str(BACKUP), "manifest_sha256": digest(BACKUP / "backup-manifest.json"),
                      "cells": {c: {"files": len(v["files"]), "rows": len(v["row_sha256"]),
                                    "moved_dirs": {k: len(t) for k, t in v["moved_by_supplement"].items()},
                                    "ledger_serialization_stable": v["ledger_serialization_stable"]}
                                for c, v in cells.items()}}, indent=2))
    return 0


def backup_current(manifest: dict) -> list[str]:
    """Problems if any snapshotted source file changed since the backup."""
    problems = []
    for cell, entry in manifest["cells"].items():
        for relative, info in entry["files"].items():
            if digest(P4N / cell / relative) != info["source_sha256"]:
                problems.append(f"{cell}/{relative} changed since the backup")
            if digest(BACKUP / cell / relative) != info["backup_sha256"]:
                problems.append(f"backup copy {cell}/{relative} changed")
    return problems


# ---- prepare --------------------------------------------------------------------


def study() -> dict:
    result = pc.study()
    result["note"] = ("CC_TASK_5B held-out supplement: spatialbetween_p4n_r1 seeds 45-50 and twomoka_p4n_r1 "
                      "seeds 41-50 on their frozen p4n bundles, in place in "
                      f"{P4N}; one 4-GPU unit, one entry per slot")
    return result


def prepare() -> int:
    if PARENT.exists():
        raise SystemExit(f"{PARENT} exists; stage a fresh root")
    manifest = json.loads((BACKUP / "backup-manifest.json").read_text())
    problems = backup_current(manifest)
    supplement = supplement_module(HERE / "support")
    for cell, expected in CELLS.items():
        state = json.loads((P4N / cell / "campaign_state.json").read_text())
        planned = supplement.plan(P4N / cell / "heldout")
        problems += [f"{cell}: {p}" for p in supplement.eligibility(state) + planned["problems"]]
        if planned["seeds_to_run"] != expected:
            problems.append(f"{cell}: seeds_to_run {planned['seeds_to_run']} != {expected}")
    if problems:
        raise SystemExit(f"refusing to stage: {problems}")
    RECORDS.mkdir(parents=True, exist_ok=True)
    PARENT.mkdir(parents=True)
    launch_manifest = layout.freeze_launch(PARENT / "launch", study(),
                                           compat=P3 / "support/qwen-native-compat-r2.py")
    frozen = {name: base.freeze_bytes(OVERLAY.get(name, base.SUPPORT / name), PARENT / "support" / name)
              for name in base.SUPPORT_FILES}
    base.record(PARENT / "support-freeze-manifest.json", {
        "frozen_at": base.now(), "support_dir": str(base.SUPPORT),
        "overlay": {n: str(p) for n, p in OVERLAY.items()}, "files": frozen})
    cells, runtime = {}, {}
    for cell in CELLS:
        marker = supplement.stage_entry(P4N / cell, PARENT / cell)
        case_path = PARENT / cell / "case.json"
        case = json.loads(case_path.read_text())
        runtime[cell] = {n: base.digest(Path(case["sim"]) / n) for n in base.C_OVERLAY}
        cells[cell] = {"case": str(case_path), "case_sha256": base.digest(case_path),
                       "source_control": str(P4N / cell), "suite": case["suite"], "task": case["task"],
                       "sim": case["sim"], "marker_sha256": base.digest(PARENT / cell / layout.SUPPLEMENT_MARKER),
                       "seeds_to_run": marker["seeds_to_run_at_staging"]}
    receipt = {"prepared_at": base.now(), "study": str(HERE.relative_to(SIMPATH)),
               "layout": "node-layout-20261015 (4 GPUs per unit, 2 slots per unit)",
               "kind": "CC_TASK_5B held-out supplement (in place in the p4n root)",
               "runtime_root": None, "result_parent": str(PARENT), "source_root": str(P4N),
               "driver": str(PARENT / "support/run_two_task_cell.py"), "frozen_support": frozen,
               "support_freeze_manifest": str(PARENT / "support-freeze-manifest.json"),
               "runtime_sha256": runtime,
               "prepare_source": str(Path(__file__)),
               "prepare_source_sha256": base.freeze_bytes(Path(__file__), PARENT / "frozen-prepare-supplement.py"),
               "backup": str(BACKUP), "backup_manifest_sha256": digest(BACKUP / "backup-manifest.json"),
               "launch_dir": str(PARENT / "launch"),
               "launch_manifest_sha256": base.digest(PARENT / "launch/launch-manifest.json"),
               "launch_files": sorted(launch_manifest["files"]), "serial_order": list(CELLS),
               "cells": cells, "launched": False,
               "note": "staged only; verify + supervisor/dlc preflight, then the coordinator submits once."}
    base.record(PARENT / "prepare-receipt.json", receipt)
    print(json.dumps({"prepared_at": receipt["prepared_at"], "root": str(PARENT),
                      "cells": {c: v["seeds_to_run"] for c, v in cells.items()}, "launched": False}, indent=2))
    return 0


# ---- verify ---------------------------------------------------------------------

#: Run with the cell's own venv from its runtime: the frozen supervisor's checks
#: (the ones `RealHooks.verify_cell` makes at claim time) plus the runtime freeze
#: and output ownership the driver re-checks at start.
CLAIM_CHECK = r'''
import json, sys
from pathlib import Path
root, cell = Path(sys.argv[1]), sys.argv[2]
sys.path[:0] = [str(root / "launch")]
import unit_supervisor as supervisor, unit_layout as layout
study = json.loads((root / "launch/study.json").read_text())
receipt = json.loads((root / "prepare-receipt.json").read_text())
case = json.loads((root / cell / "case.json").read_text())
reference = json.loads((root / study["cells"][0] / "case.json").read_text())
supervisor.check_case(case, study)
marker = supervisor.check_supplement_entry(root / cell, case)
measured = supervisor.verify_staged_runtime(Path(case["sim"]), receipt, case["id"], study["required_runtime"])
leftover = [n for n in supervisor.RECOVERY_CHECKPOINTS if (Path(case["control"]) / n).exists()]
sys.path.insert(0, str(Path(case["sim"]) / "scripts/common"))
from native_cc_freeze import verify_runtime
verify_runtime(case, Path(case["sim"]))
sys.path.insert(0, str(root / "support"))
import output_ownership
output_ownership.verify(case, Path(case["sim"]))
print(json.dumps({"family_diff": layout.same_runtime_family(reference, case), "supplement_marker": bool(marker),
                  "staged_runtime_files": len(measured), "recovery_checkpoints": leftover,
                  "runtime_freeze_verified": True, "output_ownership_verified": True}))
'''


def claim_check(cell: str) -> dict:
    case = json.loads((PARENT / cell / "case.json").read_text())
    proc = subprocess.run([str(Path(case["sim"]) / ".venv-libero/bin/python3"), "-c", CLAIM_CHECK,
                           str(PARENT), cell], cwd=case["sim"], capture_output=True, text=True,
                          env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"},
                          timeout=600)
    if proc.returncode:
        return {"problems": [f"claim-time checks failed: {proc.stderr[-3000:]}"]}
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    result["problems"] = ([f"runtime family differs: {result['family_diff']}"] * bool(result["family_diff"])
                          + [f"recovery checkpoints {result['recovery_checkpoints']}"] * bool(result["recovery_checkpoints"]))
    return result


def identity_check(cell: str, expected: list[int], supplement) -> dict:
    """Same frozen bundle and same suite/task/env_config/trial_timeout/max_steps as the original sweep."""
    control, entry = P4N / cell, PARENT / cell
    case = json.loads((entry / "case.json").read_text())
    repo = Path(case["sim"])
    ledger = json.loads((control / "heldout/heldout_state.json").read_text())
    identity = ledger["identity"]
    manifest_path = control / "heldout/heldout_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    state = json.loads((control / "campaign_state.json").read_text())
    marker = json.loads((entry / layout.SUPPLEMENT_MARKER).read_text())
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    stage1 = json.loads((task_dir / "stage1_result.json").read_text())
    frozen = control / "heldout/frozen"
    bundle = {"policy": code_hash(frozen / "fix_code.py"), "world": code_hash(frozen / "fix_world_program.py")}
    planned = supplement.plan(control / "heldout")
    checks = {
        "frozen_files_eq_identity_bundle": bundle == identity["bundle"],
        "bundle_sha256_eq_identity": bundle_identity(bundle) == identity["bundle_sha256"],
        "identity_eq_manifest_bundle": identity["bundle"] == manifest["selected_bundle"]
        and identity["bundle_sha256"] == manifest["selected_bundle_sha256"],
        "stage1_selection_eq_bundle": stage1.get("selected_bundle") == bundle
        and stage1.get("selection", {}).get("bundle_sha256") == identity["bundle_sha256"],
        "selected_bundle_was_tested": identity["bundle_sha256"] in (stage1.get("tested_bundles") or {}),
        "env_config_sha256_eq_identity": code_hash(repo / case["env_config"]) == identity["config_sha256"],
        "suite_task_eq_identity": (case["suite"], case["task"]) == (identity["suite"], identity["task"])
        == (manifest["suite"], manifest["task"]),
        "trial_timeout_max_steps_eq_identity": (case["trial_timeout"], case["max_steps"])
        == (identity["trial_timeout"], identity["max_steps"]) == (manifest["trial_timeout"], manifest["max_steps"]),
        "manifest_sha256_eq_state_and_marker": digest(manifest_path) == state["heldout_manifest"]["sha256"]
        == marker["heldout_manifest_sha256"],
        "entry_case_bytes_eq_source": (entry / "case.json").read_bytes() == (control / "case.json").read_bytes(),
        "campaign_state_sha256_eq_marker": digest(control / "campaign_state.json")
        == marker["source_campaign_state_sha256"],
        "eligible": not supplement.eligibility(state) and not planned["problems"],
        "seeds_to_run_as_planned": planned["seeds_to_run"] == expected == marker["seeds_to_run_at_staging"],
        "no_supplement_attempt_yet": not (control / "heldout/supplement").exists(),
    }
    kept = {k: v["status"] for k, v in ledger["seeds"].items() if int(k) not in expected}
    return {"bundle_sha256": identity["bundle_sha256"], "bundle": bundle,
            "bundle_sha256_matches": all(checks[k] for k in ("frozen_files_eq_identity_bundle",
                                                             "bundle_sha256_eq_identity",
                                                             "identity_eq_manifest_bundle",
                                                             "stage1_selection_eq_bundle")),
            "suite": case["suite"], "task": case["task"], "env_config": case["env_config"],
            "trial_timeout": case["trial_timeout"], "max_steps": case["max_steps"],
            "existing_rows": {"rows": len(ledger["seeds"]), "kept": len(kept),
                              "kept_counts": {s: list(kept.values()).count(s) for s in ("success", "failure", "crash")},
                              "superseded_infrastructure_error": planned["rerun_infrastructure_error"],
                              "missing": planned["missing"]},
            "seeds_to_run": planned["seeds_to_run"],
            "derived_deadline_seconds": None, "checks": checks,
            "problems": [f"{cell}: {k} is false" for k, ok in checks.items() if not ok]}


def unified(old: Path, new: Path) -> dict:
    a, b = old.read_text().splitlines(), new.read_text().splitlines()
    lines = [l for l in difflib.unified_diff(a, b, n=0, lineterm="") if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    return {"added": sum(l.startswith("+") for l in lines), "removed": sum(l.startswith("-") for l in lines)}


def byte_diff_vs_p4n() -> dict:
    mine = json.loads((PARENT / "prepare-receipt.json").read_text())
    theirs = json.loads((P4N / "prepare-receipt.json").read_text())
    support = sorted(n for n in set(mine["frozen_support"]) | set(theirs["frozen_support"])
                     if mine["frozen_support"].get(n) != theirs["frozen_support"].get(n))
    new_launch = json.loads((PARENT / "launch/launch-manifest.json").read_text())["files"]
    old_launch = json.loads((P4N / "launch/launch-manifest.json").read_text())["files"]
    launch = sorted(n for n in set(new_launch) | set(old_launch) if new_launch.get(n) != old_launch.get(n))
    study_new = json.loads((PARENT / "launch/study.json").read_text())
    study_old = json.loads((P4N / "launch/study.json").read_text())
    study_keys = sorted(k for k in set(study_new) | set(study_old) if study_new.get(k) != study_old.get(k))
    smoke = json.loads((CC5_SMOKE / "prepare-receipt.json").read_text())["frozen_support"]
    smoke_launch = json.loads((CC5_SMOKE / "launch/launch-manifest.json").read_text())["files"]
    details = {}
    for name in support:
        old = P4N / "support" / name
        details[f"support/{name}"] = {
            "p4n_sha256": theirs["frozen_support"].get(name), "new_sha256": mine["frozen_support"].get(name),
            "eq_task5_smoke_frozen": smoke.get(name) == mine["frozen_support"].get(name),
            "eq_worktree_p4_support": digest(HERE / "support" / name) == mine["frozen_support"].get(name),
            **(unified(old, PARENT / "support" / name) if old.is_file() else {"new_file": True})}
    for name in launch:
        details[f"launch/{name}"] = {
            "p4n_sha256": old_launch.get(name), "new_sha256": new_launch.get(name),
            "eq_task5_smoke_frozen": smoke_launch.get(name) == new_launch.get(name) if name != "study.json" else None,
            **unified(P4N / "launch" / name, PARENT / "launch" / name)}
    required = {"added": sorted(set(study_new["required_support"]) - set(study_old["required_support"])),
                "removed": sorted(set(study_old["required_support"]) - set(study_new["required_support"]))}
    cells = {}
    for cell in CELLS:
        cells[cell] = {
            "entry_case_sha256": mine["cells"][cell]["case_sha256"],
            "eq_p4n_receipt_case_sha256": mine["cells"][cell]["case_sha256"] == theirs["cells"][cell]["case_sha256"],
            "runtime_pins_eq_p4n_receipt": mine["runtime_sha256"][cell] == theirs["runtime_sha256"][cell],
            "added_files": ["heldout-supplement.json (the CC_TASK_5 queue marker)"]}
    problems = ([f"support {n} differs from p4n" for n in support if n not in SUPPORT_DIFF]
                + [f"launch {n} differs from p4n" for n in launch if n not in LAUNCH_DIFF]
                + [f"study.json key {k} differs from p4n" for k in study_keys if k not in STUDY_KEYS_DIFF]
                + [f"{k} is not the bytes Task 5 tested" for k, v in details.items()
                   if v.get("eq_task5_smoke_frozen") is False and k != "support/heldout_supplement.py"]
                + [f"{k} differs from the worktree p4 support" for k, v in details.items()
                   if v.get("eq_worktree_p4_support") is False]
                + ([f"required_support changes {required}"] if required != {"added": ["heldout_supplement.py"],
                                                                         "removed": []} else [])
                + [f"{c}: {k} is false" for c, row in cells.items() for k, v in row.items() if v is False])
    return {"reference": str(P4N), "support_differs": support, "launch_differs": launch,
            "study_json_keys_differ": study_keys, "required_support_change": required,
            "details": details, "cells": cells,
            "allowed": {"support": sorted(SUPPORT_DIFF), "launch": sorted(LAUNCH_DIFF),
                        "study_keys": sorted(STUDY_KEYS_DIFF)},
            "runtime": "none staged: each entry runs on its source cell's frozen p4n runtime (case.sim, unchanged)",
            "problems": problems}


def verify() -> int:
    receipt = json.loads((PARENT / "prepare-receipt.json").read_text())
    problems, checks = [], {}
    launch = PARENT / "launch"
    manifest = json.loads((launch / "launch-manifest.json").read_text())
    problems += [f"launch file changed: {n}" for n, h in manifest["files"].items() if digest(launch / n) != h]
    if digest(launch / "launch-manifest.json") != receipt["launch_manifest_sha256"]:
        problems.append("launch manifest changed since --prepare")
    problems += [f"frozen support changed: {n}" for n, h in receipt["frozen_support"].items()
                 if digest(PARENT / "support" / n) != h]
    study_json = json.loads((launch / "study.json").read_text())
    if study_json["cells"] != list(CELLS) or study_json["mode"] != "full":
        problems.append(f"study.json cells/mode {study_json['cells']} / {study_json['mode']}")
    problems += [f"support file not frozen: {n}" for n in study_json["required_support"]
                 if n not in receipt["frozen_support"]]
    if "heldout_supplement.py" not in study_json["required_support"]:
        problems.append("heldout_supplement.py is not in required_support")
    backup_manifest = json.loads((BACKUP / "backup-manifest.json").read_text())
    if digest(BACKUP / "backup-manifest.json") != receipt["backup_manifest_sha256"]:
        problems.append("backup manifest changed since --prepare")
    checks["backup_current"] = backup_current(backup_manifest)
    problems += checks["backup_current"]
    supplement = supplement_module(PARENT / "support")
    deadlines = load(PARENT / "support/full_deadlines.py", "cc5b_full_deadlines")
    checks["cells"] = {}
    for cell, expected in CELLS.items():
        row = identity_check(cell, expected, supplement)
        row["derived_deadline_seconds"] = deadlines.heldout_deadline(len(row["seeds_to_run"]), row["trial_timeout"])
        if digest(PARENT / cell / "case.json") != receipt["cells"][cell]["case_sha256"]:
            row["problems"].append(f"{cell}: entry case.json changed since --prepare")
        row["claim_time"] = claim_check(cell)
        row["problems"] += [f"{cell}: {p}" for p in row["claim_time"]["problems"]]
        checks["cells"][cell] = row
        problems += row["problems"]
    checks["byte_diff_vs_p4n"] = byte_diff_vs_p4n()
    problems += checks["byte_diff_vs_p4n"]["problems"]
    result = {"verified_at": base.now(), "root": str(PARENT), "backup": str(BACKUP), "checks": checks,
              "cell_timeout_seconds": deadlines.DEVELOPMENT + deadlines.HELDOUT + deadlines.FINALIZE_MARGIN,
              "problems": problems, "status": "verified" if not problems else "problems_found"}
    print(json.dumps(result, indent=2))
    return 0 if not problems else 1


# ---- after the job (read-only) ------------------------------------------------------


def check_after() -> int:
    """What the coordinator checks once the job is terminal. Reads only."""
    manifest = json.loads((BACKUP / "backup-manifest.json").read_text())
    rows, problems = {}, []
    for cell, expected in CELLS.items():
        control, saved = P4N / cell, manifest["cells"][cell]
        old = json.loads((BACKUP / cell / "heldout/heldout_state.json").read_text())
        new_path = control / "heldout/heldout_state.json"
        new = json.loads(new_path.read_text())
        kept = [s for s in HELDOUT_SEEDS if s not in expected]
        mine = []
        if new["identity"] != old["identity"]:
            mine.append("ledger identity changed")
        mine += [f"kept row {s} changed" for s in kept
                 if new["seeds"].get(str(s)) != old["seeds"][str(s)]
                 or row_digest(new["seeds"][str(s)]) != saved["row_sha256"][str(s)]]
        if sorted(map(int, new["seeds"])) != HELDOUT_SEEDS:
            mine.append(f"ledger seeds {sorted(map(int, new['seeds']))} are not 1-50")
        # Both writers serialize with json.dump(indent=2) + "\n": equal row objects
        # in an identically serialized file are byte-identical row text.
        if (json.dumps(new, indent=2) + "\n").encode() != new_path.read_bytes():
            mine.append("ledger is not in the canonical serialization; compare rows by hand")
        report = json.loads((control / "heldout/heldout_result.json").read_text())
        if not report.get("all_seeds_accounted") or report.get("unusable_seeds"):
            mine.append(f"report all_seeds_accounted {report.get('all_seeds_accounted')} "
                        f"unusable {report.get('unusable_seeds')}")
        if report.get("supplementary_seeds") != expected:
            mine.append(f"supplementary_seeds {report.get('supplementary_seeds')} != {expected}")
        mine += [f"report per_seed {s} changed" for s in kept
                 if (report.get("per_seed") or {}).get(str(s)) != old["seeds"][str(s)]["status"]]
        state = json.loads((control / "campaign_state.json").read_text())
        last = (state.get("heldout_supplements") or [{}])[-1]
        if state.get("status") != "full_complete" or last.get("status") != "complete":
            mine.append(f"campaign status {state.get('status')} / last supplement {last.get('status')}")
        for relative in ("heldout/heldout_manifest.json", "heldout/frozen/fix_code.py",
                         "heldout/frozen/fix_world_program.py", "case.json", "queue-result.json", "slot-case.json"):
            if digest(control / relative) != saved["files"][relative]["source_sha256"]:
                mine.append(f"{relative} changed")
        record = json.loads((control / "heldout/supplement" / str(last.get("attempt", "1")) / "supplement.json").read_text())
        mine += [f"superseded row {k} != the backed-up row" for k, v in record["superseded_rows"].items()
                 if v != old["seeds"][k]]
        for name, inventory in saved["moved_by_supplement"].items():
            moved = Path(record["moved_dirs"].get(str(int(name[5:])), "/nonexistent"))
            if not moved.is_dir() or tree_inventory(moved) != inventory:
                mine.append(f"moved {name} at {moved} differs from its pre-supplement inventory")
        rows[cell] = {"rows": len(new["seeds"]), "kept_unchanged": len(kept),
                      "counts": report.get("counts"), "supplementary_seeds": report.get("supplementary_seeds"),
                      "problems": mine}
        problems += [f"{cell}: {p}" for p in mine]
    before, after = manifest["root_inventory"], root_inventory()
    if after["root"] != before["root"]:
        problems.append("p4n root top-level files changed")
    for cell, entries in before["cells"].items():
        changed = sorted(k for k in set(entries) | set(after["cells"].get(cell, {}))
                         if entries.get(k) != after["cells"].get(cell, {}).get(k))
        allowed = {"campaign_state.json"} if cell in CELLS else set()
        problems += [f"{cell}/{k} changed" for k in changed if k not in allowed]
    for cell, expected in CELLS.items():
        gone = set(before["heldout_entries"][cell]) - set(after["heldout_entries"][cell])
        added = set(after["heldout_entries"][cell]) - set(before["heldout_entries"][cell])
        allowed_gone = set(manifest["cells"][cell]["moved_by_supplement"])
        allowed_added = {f"seed_{s:02d}" for s in expected} | {"supplement"}
        problems += [f"{cell}/heldout/{n} disappeared" for n in gone - allowed_gone]
        problems += [f"{cell}/heldout/{n} appeared" for n in added - allowed_added]
    print(json.dumps({"checked_at": base.now(), "cells": rows, "problems": problems,
                      "status": "ok" if not problems else "problems_found"}, indent=2))
    return 0 if not problems else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--backup", "--prepare", "--verify", "--check-after"):
        mode.add_argument(flag, action="store_true")
    args = parser.parse_args(argv)
    if args.backup:
        return backup()
    if args.prepare:
        return prepare()
    if args.verify:
        return verify()
    return check_after()


if __name__ == "__main__":
    raise SystemExit(main())
