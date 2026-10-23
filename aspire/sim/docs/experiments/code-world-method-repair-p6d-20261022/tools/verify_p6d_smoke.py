#!/usr/bin/env python3
"""CC_TASK_14 verify_after_run for the p6d smoke (read-only on the smoke root).

Writes `<root>/verify-after-run.json`; the trial launch script requires `passed: true`.
One check per section-1 change, each from the real run's own records:

  prompt_contract      both cells' frozen worker prompt AND the worker's received assignment
                       carry the subgoal-verification / success-rule sections and the skill paths,
                       and the frozen skill files are present in each runtime
  calibration_feedback a graded development trial row of each cell carries judgment_calibration,
                       and the worker's transcript contains it (it reached the worker)
  sweep_only_score     drawer: the identity carries the switch; every phase-B candidate's recorded
                       passes/crashes (frozen state code, resumed read-only) equal a recount from its
                       sweep rows alone; the admitting seed (v0: 51) has its own sweep row
  prefreeze_resweep    drawer: development_end then a prefreeze_resweep plan + result, resweep rows
                       for every planned candidate on all 15 seeds, campaign_state.prefreeze_resweep
  unseen_measurement   drawer: heldout/unseen/unseen_result.json with 15 seeds 66-80 and
                       campaign_state.unseen; no unseen path in any worker transcript
  heldout_timeout_rule drawer: injected seed 7 is `failure` with watchdog kind policy_timeout (or
                       the recorded outcome of a persistence cut), the sweep was not stopped, 50 rows
  graspnet_determinism smoke2 probe: plan_grasp on request A three times, same digest; B differs
                       or not (reported); solve_ik printed its clipping report line
  no_budget_world_error no development or held-out row of either cell is a world_program_error
                       caused by "executing action in terminated episode"
  node_cleanup         every unit attempt ended with exit_cleanup.verified_empty

  verify_p6d_smoke.py [--root R]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path("/mnt/home/gewang/experiments/code-world-method-repair-p6d-20261022-smoke")
DRAWER, MILK = "drawer_p6d_smoke1", "objtaskmilk_p6d_smoke2"
MARKERS = ("### Subgoal verification contract", "cap/world_model/skills/subgoal-verification.md",
           "cap/world_model/skills/api-revision-m4.md", "### Success rule libero_latch")


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def case_of(root, cell):
    return read(root / cell / "case.json")


def ledger(case):
    return read(Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
                / "development_state.json") or {"trials": []}


def transcripts(root, cell):
    native = root / cell / "native"
    return [p for p in native.rglob("*.jsonl")] if native.is_dir() else []


def check(name, ok, **detail):
    return {"check": name, "ok": bool(ok), **detail}


def prompt_contract(root):
    rows = {}
    for cell in (DRAWER, MILK):
        case = case_of(root, cell)
        prompt = (root / cell / "worker-prompt.md").read_text()
        state = read(root / cell / "campaign_state.json") or {}
        received = Path(((state.get("actual_worker_prompt_receipt") or {}).get("actual_child_transcript")) or "/nonexistent")
        got = received.read_text(errors="replace") if received.is_file() else ""
        skills = [(Path(case["sim"]) / m).is_file() for m in MARKERS[1:3]]
        rows[cell] = {"frozen_prompt": all(prompt.count(m) == 1 for m in MARKERS),
                      "received_by_worker": all(m in got for m in MARKERS[:2]), "skill_files_frozen": all(skills),
                      "worker_transcript": str(received)}
    return check("prompt_contract", all(all(v for k, v in r.items() if k != "worker_transcript") for r in rows.values()),
                 cells=rows)


def calibration_feedback(root):
    rows = {}
    for cell in (DRAWER, MILK):
        trials = ledger(case_of(root, cell))["trials"]
        carrying = [r["directory"] for r in trials if isinstance(r.get("judgment_calibration"), dict)
                    and "this_trial" in r["judgment_calibration"]]
        seen = any("judgment_calibration" in p.read_text(errors="replace") for p in transcripts(root, cell))
        rows[cell] = {"rows_with_feedback": len(carrying), "first": carrying[:1], "in_worker_transcript": seen}
    return check("calibration_feedback", all(r["rows_with_feedback"] and r["in_worker_transcript"] for r in rows.values()),
                 cells=rows)


def frozen_state(case):
    """The cell's own frozen NativeWorldState, resumed read-only (resume never saves)."""
    import importlib.util, sys
    repo = Path(case["sim"])
    sys.path.insert(0, str(repo / "scripts/libero"))
    spec = importlib.util.spec_from_file_location(
        "p6d_verify_state", repo / "scripts/libero/native_world_fixloop_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = ledger(case)
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    return module.NativeWorldState(task_dir, data["identity"], resume=True)


def sweep_only_score(root):
    case = case_of(root, DRAWER)
    trials = ledger(case)["trials"]
    try:
        state = frozen_state(case)
        records = state.budget_candidates()
        switched = state.sweep_only()
    except Exception as error:  # reported, the check fails
        return check("sweep_only_score", False, error=repr(error))
    rows, mismatched, repair_differs = {}, [], []
    for record in records:
        digest = record["bundle_sha256"]
        sweep = [r for r in state.graded() if r["bundle_sha256"] == digest and r["phase"] == "sweep"]
        every = [r for r in state.graded() if r["bundle_sha256"] == digest]
        expect = sorted({r["seed"] for r in sweep if state.passed(r)})
        with_repair = sorted({r["seed"] for r in every if state.passed(r)})
        crashes = sum(int(r["sandbox_rc"] != 0) for r in sweep)
        if (record["passes"], record["crashes"]) != (expect, crashes):
            mismatched.append(record["label"])
        if with_repair != expect:
            repair_differs.append(record["label"])
        rows[record["label"]] = {"sweep_seeds": sorted({r["seed"] for r in sweep}), "passes": record["passes"],
                                 "passes_if_repair_rows_counted": with_repair}
    full = [l for l, r in rows.items() if r["sweep_seeds"] == list(range(51, 66))]
    v0_51 = any(r["phase"] == "sweep" and r["seed"] == 51 for r in trials)
    return check("sweep_only_score", switched and full and v0_51 and not mismatched,
                 identity_switch=switched, candidates=rows, candidates_swept_on_all_15=len(full),
                 seed51_swept=v0_51, record_not_from_sweep_rows=mismatched,
                 repair_row_would_change_score=repair_differs)


def prefreeze_resweep(root):
    data = ledger(case_of(root, DRAWER))
    plan = data.get("prefreeze_resweep") or {}
    rows = [r for r in data["trials"] if r.get("phase") == "resweep"]
    per = {d: sorted({r["seed"] for r in rows if r["bundle_sha256"] == d and r["status"] == "complete"})
           for d in plan.get("candidates", [])}
    state = read(root / DRAWER / "campaign_state.json") or {}
    step = state.get("prefreeze_resweep") or {}
    return check("prefreeze_resweep", plan.get("candidates") and all(v == list(range(51, 66)) for v in per.values())
                 and (plan.get("result") or {}).get("selected") and step.get("exit_code") == 0
                 and (data.get("development_end") or {}).get("reason"),
                 development_end=(data.get("development_end") or {}).get("reason"), planned=plan.get("labels"),
                 resweep_seeds=per, result=plan.get("result"), driver_step_exit=step.get("exit_code"))


def unseen_measurement(root):
    report = read(root / DRAWER / "heldout/unseen/unseen_result.json") or {}
    state = (read(root / DRAWER / "campaign_state.json") or {}).get("unseen") or {}
    leaked = [str(p) for cell in (DRAWER, MILK) for p in transcripts(root, cell)
              if "unseen_result" in p.read_text(errors="replace") or "heldout/unseen" in p.read_text(errors="replace")]
    seeds = sorted(int(s) for s in report.get("per_seed", {}))
    return check("unseen_measurement", seeds == list(range(66, 81)) and state.get("performed") and not leaked,
                 seeds=seeds, counts=report.get("counts"), campaign_state=bool(state), leaked_to=leaked)


def heldout_timeout_rule(root):
    data = read(root / DRAWER / "heldout/heldout_state.json") or {"seeds": {}}
    row = data["seeds"].get("7") or {}
    injected = (root / DRAWER / "heldout/seed_07/smoke-injection.json").is_file()
    kind = (row.get("watchdog") or {}).get("kind")
    ok = injected and len(data["seeds"]) == 50 and row.get("status") in ("failure", "success", "crash") and \
        kind in ("policy_timeout", "persistence_cut")
    return check("heldout_timeout_rule", ok, injected=injected, seed7_status=row.get("status"), watchdog_kind=kind,
                 rows=len(data["seeds"]),
                 statuses=sorted({r.get("status") for r in data["seeds"].values()}))


def graspnet_determinism(root):
    probe = read(root / MILK / "smoke-checks/probe.json") or {}
    sequence = (probe.get("result") or {}).get("sequence") or []
    b = {d for n, d in sequence if n == "B"}
    return check("graspnet_determinism", probe.get("graspnet_same_request_same_result")
                 and probe.get("solve_ik_report_lines") and probe.get("api_revision_env") == "m4-r1",
                 sequence=sequence, b_distinct=len(b), solve_ik_lines=probe.get("solve_ik_report_lines"),
                 error=probe.get("error"))


def no_budget_world_error(root):
    bad = []
    for cell in (DRAWER, MILK):
        for r in ledger(case_of(root, cell))["trials"]:
            if r.get("status") == "world_program_error" and "terminated episode" in json.dumps(r.get("world_error")):
                bad.append(f"{cell}:{r['directory']}")
        for seed, r in ((read(root / cell / "heldout/heldout_state.json") or {}).get("seeds") or {}).items():
            if r.get("status") == "crash" and "terminated episode" in json.dumps(r.get("world_error")):
                bad.append(f"{cell}:heldout/{seed}")
    return check("no_budget_world_error", not bad, rows=bad)


def node_cleanup(root):
    statuses = [read(p) or {} for p in (root / "queue").glob("worker-node*/attempts/*/status.json")]
    clean = [bool((s.get("exit_cleanup") or {}).get("verified_empty")) for s in statuses]
    return check("node_cleanup", clean and all(clean), attempts=len(clean))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    checks = [f(args.root) for f in (prompt_contract, calibration_feedback, sweep_only_score, prefreeze_resweep,
                                      unseen_measurement, heldout_timeout_rule, graspnet_determinism,
                                      no_budget_world_error, node_cleanup)]
    report = {"root": str(args.root), "checks": checks, "passed": all(c["ok"] for c in checks),
              "failed": [c["check"] for c in checks if not c["ok"]]}
    (args.root / "verify-after-run.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
