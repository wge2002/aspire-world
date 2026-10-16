#!/usr/bin/env python3
"""Retrospective p3 search-control signals on frozen p2 development ledgers.

Read-only. For each cell it orders the charged attempts by lifecycle completion
time and reports the informative-attempt fraction, the initial early-stop
trigger, the saturation trigger and the first development success.

The p2 worlds have no progress() and the p2 policies declare no
PRIMITIVE_CLASS, so two substitutions are made and labelled in the output:
  progress_source = "retrospective_proxy"
      stove:  |net change of knob lever/tab angle| / 0.8 rad, the angle read
              modulo pi because the recorded lever estimate flips end-for-end;
      drawer: net handle displacement along +y (the pull direction) / 0.15 m;
      both from the world's own recorded world_update inputs, clipped to [0, 1].
  class_source = "single_class_assumption"
      every bundle of a cell is one primitive class; bundles are still
      aggregated separately inside it.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

SIM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SIM.parents[1]))
from aspire.sim.cap.world_model import search_control as sc  # noqa: E402

CELLS = ("stove_p2_r1", "stove_p2_r2", "drawer_p2_r1", "drawer_p2_r2")
#: Task-level proxy: which recorded world_update keys carry the goal quantity,
#: how the change is read, and the change that counts as the whole goal.
PROXIES = {
    "turn_on_the_stove": {"keys": ("lever_angle", "tab_angle"), "kind": "angle", "goal": 0.8},
    "open_the_middle_drawer_of_the_cabinet": {"keys": ("handle_center", "handle"), "kind": "y", "goal": 0.15},
}
#: The validation gates in PLAN.md: saturation must fire before `fire_before`
#: and must not fire before `quiet_before`; early stop at attempt <= 5.
GATES = {"stove_p2_r2": {"fire_before": 30}, "drawer_p2_r2": {"fire_before": 30},
         "stove_p2_r1": {"quiet_before": 21}, "drawer_p2_r1": {"quiet_before": 34}}
EARLY_STOP_MAX = 5
SWEEP = {"K": (3, 5, 8), "N": (5, 8, 12), "frac": (0.3, 0.5, 0.7)}


def _values(node, keys):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and value is not None:
                found.append(value)
            found += _values(value, keys)
    return found


def recorded_goal_values(trial_dir, keys):
    """The goal quantity as the world itself recorded it, in update order."""
    values = []
    for event in sc.read_events(trial_dir):
        if event.get("event") == "world_update":
            values += _values((event.get("inputs") or {}).get("values"), set(keys))
    return values


def proxy_progress(values, proxy):
    if proxy["kind"] == "angle":
        angles = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not angles:
            return None
        change = (angles[-1] - angles[0] + math.pi / 2) % math.pi - math.pi / 2
        return min(1.0, abs(change) / proxy["goal"])
    points = [v for v in values if isinstance(v, list) and len(v) >= 2]
    if not points:
        return None
    return min(1.0, max(0.0, (points[-1][1] - points[0][1]) / proxy["goal"]))


def lifecycle_order(task_dir, ledger):
    index = {id(r): i for i, r in enumerate(ledger["trials"])}

    def key(row):
        try:
            finished = json.loads((task_dir / row["directory"] / "lifecycle.json").read_text())["finished_at"]
        except (OSError, json.JSONDecodeError, KeyError):
            finished = math.inf
        return (finished, index[id(row)])
    return key


def first_trigger(entries):
    triggers = [e["trigger_attempt"] for e in entries if e["trigger_attempt"] is not None]
    return min(triggers) if triggers else None


def analyse(cell_root, *, k=3, thresholds=None):
    thresholds = thresholds or dict(sc.SATURATION_DEFAULTS)
    case = json.loads((cell_root / "case.json").read_text())
    task_dir = Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    ledger = json.loads((task_dir / "development_state.json").read_text())
    proxy = PROXIES[case["task"]]
    attempts = sc.charged_attempts(ledger, task_dir, order=lifecycle_order(task_dir, ledger))
    progress = {}
    for a in attempts:
        if a["graded"]:
            progress[a["n"]] = proxy_progress(recorded_goal_values(task_dir / a["directory"], proxy["keys"]), proxy)
    info = sc.informative_attempts(attempts)
    stop = sc.initial_early_stop(attempts, k, seeds=case["dev_seeds"])
    report = sc.primitive_report(attempts, lambda a: progress.get(a["n"]),
                                 class_fn=lambda a: "single_class")
    saturated = sc.saturation(report, **thresholds)
    return {"cell": cell_root.name, "task": case["task"], "ledger": str(task_dir / "development_state.json"),
            "progress_source": "retrospective_proxy", "proxy": proxy,
            "class_source": "single_class_assumption",
            "declared_classes_seen": sorted({a["primitive_class"] for a in attempts}),
            "charged_attempts": len(attempts), "graded_attempts": info["graded"],
            "informative": {k_: info[k_] for k_ in ("informative", "graded", "fraction",
                                                    "distinct_signatures", "until_first_success")},
            "early_stop": stop, "early_stop_attempt": stop["trigger_attempt"],
            "saturation": saturated, "saturation_attempt": first_trigger(saturated),
            "first_success": info["first_success"],
            "observed_attempts": sum(c["observed_attempts"] for c in report["classes"]),
            "unobserved_attempts": sum(c["unobserved_attempts"] for c in report["classes"]),
            "per_attempt": [{"n": a["n"], "phase": a["phase"], "seed": a["seed"],
                             "bundle": (a["bundle_sha256"] or "")[:12], "status": a["status"],
                             "passed": a["passed"], "progress": progress.get(a["n"]),
                             "observed": (sc.is_observed(progress.get(a["n"]), a["prediction_levels"])
                                          if a["graded"] else None),
                             "gripper_checks": a["gripper_checks"],
                             "informative": row["informative"],
                             "signature": a["signature"]["digest"][:12] if a["signature"] else None}
                            for a, row in zip(attempts, info["attempts"])],
            "_report": report, "_attempts": attempts, "_seeds": case["dev_seeds"], "_progress": progress}


def gate_check(result):
    gate = GATES.get(result["cell"], {})
    sat = result["saturation_attempt"]
    checks = {"early_stop<=5": result["early_stop_attempt"] is not None
              and result["early_stop_attempt"] <= EARLY_STOP_MAX}
    if "fire_before" in gate:
        checks[f"saturation<{gate['fire_before']}"] = sat is not None and sat < gate["fire_before"]
    if "quiet_before" in gate:
        checks[f"no_saturation<{gate['quiet_before']}"] = sat is None or sat >= gate["quiet_before"]
    return checks


def sweep(results):
    """Saturation trigger per cell over K x N x frac (gripper_match fixed)."""
    rows = []
    for K, N, frac in itertools.product(SWEEP["K"], SWEEP["N"], SWEEP["frac"]):
        thresholds = {"K": K, "N": N, "frac": frac, "gripper_match": sc.SATURATION_DEFAULTS["gripper_match"]}
        row = {"K": K, "N": N, "frac": frac, "triggers": {}, "gates_met": {}}
        for r in results:
            trigger = first_trigger(sc.saturation(r["_report"], **thresholds))
            row["triggers"][r["cell"]] = trigger
            gate = GATES.get(r["cell"], {})
            if "fire_before" in gate:
                row["gates_met"][r["cell"]] = trigger is not None and trigger < gate["fire_before"]
            elif "quiet_before" in gate:
                row["gates_met"][r["cell"]] = trigger is None or trigger >= gate["quiet_before"]
        row["all_saturation_gates_met"] = all(row["gates_met"].values())
        rows.append(row)
    return rows


def early_stop_sweep(results, ks=(2, 3, 4)):
    return {str(k): {r["cell"]: sc.initial_early_stop(r["_attempts"], k, seeds=r["_seeds"])["trigger_attempt"]
                     for r in results} for k in ks}


def _part(*names):
    def key(attempt):
        signature = attempt["signature"]
        return json.dumps({n: signature[n] for n in names}, sort_keys=True)
    return key


#: Analysis-only signature variants. The default (`default`, status + object
#: residuals + outcome) is the definition used everywhere else since CC Task 2;
#: `task1_with_object_change` is the Task 1 definition that also hashed the
#: rounded object-fact change. The rest show which component drives a trigger.
SIGNATURE_VARIANTS = {
    "default": None,
    "status_only": _part("prediction_status", "outcome"),
    "task1_with_object_change": _part("prediction_status", "object_residuals", "object_change", "outcome"),
    "status_change": _part("prediction_status", "object_change", "outcome"),
}


def signature_sweep(results, k=3):
    return {name: {r["cell"]: sc.initial_early_stop(r["_attempts"], k, seeds=r["_seeds"], key=key)["trigger_attempt"]
                   for r in results} for name, key in SIGNATURE_VARIANTS.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--cells", default=",".join(CELLS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--k", type=int, default=3)
    for name, default in sc.SATURATION_DEFAULTS.items():
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, type=type(default), default=default)
    args = parser.parse_args()
    thresholds = {name: getattr(args, name) for name in sc.SATURATION_DEFAULTS}
    results = [analyse(args.study_root / cell, k=args.k, thresholds=thresholds)
               for cell in args.cells.split(",") if cell]
    for r in results:
        r["gates"] = gate_check(r)
    output = {"schema": "p3-search-retro/1", "study_root": str(args.study_root),
              "read_only": True, "order": "lifecycle finished_at of each charged attempt",
              "thresholds_used": {"early_stop_k": args.k, **thresholds},
              "signature_step": sc.SIGNATURE_STEP,
              "observed_definition": "graded attempt with a progress value (here the proxy) and >= 1 "
                                     "decided (match/mismatch) object-level prediction; N and best "
                                     "progress count observed attempts only",
              "signature_definition": "prediction-status counts + rounded object residuals + outcome "
                                      "(object-fact change recorded, not hashed)",
              "summary": [{k: r[k] for k in ("cell", "charged_attempts", "graded_attempts",
                                             "observed_attempts", "unobserved_attempts",
                                             "early_stop_attempt", "saturation_attempt",
                                             "first_success", "gates")}
                          | {"informative_fraction": r["informative"]["fraction"],
                             "progress_source": r["progress_source"], "class_source": r["class_source"]}
                          for r in results],
              "all_gates_met": all(all(r["gates"].values()) for r in results),
              "cells": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results],
              "sensitivity": {"saturation": sweep(results), "early_stop_k": early_stop_sweep(results),
                              "early_stop_signature_variant_k3": signature_sweep(results, args.k)}}
    args.out.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({k: output[k] for k in ("thresholds_used", "summary", "all_gates_met")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
