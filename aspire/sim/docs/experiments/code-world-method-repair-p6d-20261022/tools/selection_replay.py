#!/usr/bin/env python3
"""CC_TASK_14 replay: what the p6d selection rules pick on real development ledgers (read-only).

For each cell: the real `development_state.json` is copied into a temporary task
directory, its identity gains the p6d method switches, and the ledger's own code
computes (a) the legacy candidate scores, (b) the sweep-only scores and (c) the
pre-freeze re-sweep plan. In the real cells a version was never swept on the seed
it was admitted on, so its sweep-only record covers 14 seeds; the plan is applied
to those 14-seed counts (every candidate has the same coverage). Where the
diagnosis replay job (dlcy9kjkbyp99mhb) re-ran a candidate on 51-65, the merged
two-run count is shown; the same job's 66-80 results are shown for comparison only.

  selection_replay.py <out.json>
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

SIM = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(SIM / "scripts/libero"), str(SIM / "scripts/common"), str(SIM.parents[1])]
import method_switches as ms  # noqa: E402
import native_world_fixloop_state as ledger  # noqa: E402

NS = Path("/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-ns")
CELLS = ("objtaskmilk_p6b_r1", "spatialtaskcookie_p6b_r1")
REPLAY = SIM / "docs/experiments/code-world-method-diagnosis-20261022/data/replay_analysis.json"


def task_dir(cell):
    case = json.loads((NS / cell / "case.json").read_text())
    return Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]


def load_state(cell, tmp, switches):
    source = task_dir(cell) / "development_state.json"
    data = json.loads(source.read_text())
    folder = Path(tmp) / cell
    folder.mkdir()
    if switches is not None:
        data["identity"][ms.KEY] = switches
    (folder / "development_state.json").write_text(json.dumps(data))
    return ledger.NativeWorldState(folder, data["identity"], resume=True), source


def replay_counts(cell):
    data = json.loads(REPLAY.read_text())
    dev = [r for r in data["frozen_dev"] if r["job"].startswith(cell + "__")]
    unseen = {r["version"]: r["unseen"] for r in data["unseen"].get(cell, []) if r["job"].endswith("__r1")}
    return dev, unseen


def cell_report(cell, tmp):
    legacy, source = load_state(cell, tmp, None)
    (Path(tmp) / "p6d").mkdir(exist_ok=True)
    p6d, _ = load_state(cell, Path(tmp) / "p6d", dict(ms.P6D))
    old = {c["label"]: c for c in legacy.budget_candidates()}
    new = {c["label"]: c for c in p6d.budget_candidates()}
    rows, versions = [], {v["bundle_sha256"]: v for v in p6d.versions()}
    for label, c in new.items():
        digest = c["bundle_sha256"]
        rows.append({"label": label, "legacy_passes": len(old[label]["passes"]),
                     "legacy_complete": old[label]["complete"],
                     "sweep_only_passes": len(c["passes"]), "sweep_seeds": len(c["covered_seeds"]),
                     "missing_sweep_seeds": c["missing"],
                     "admitted_on": (versions.get(digest) or {}).get("first_seed", 51 if label == "v0" else None)})
    best = max(r["sweep_only_passes"] for r in rows)
    order = {r["label"]: i for i, r in enumerate(rows)}
    margin, cap = ms.P6D["prefreeze_resweep"]["margin"], ms.P6D["prefreeze_resweep"]["max_candidates"]
    near = sorted((r for r in rows if r["sweep_only_passes"] >= best - margin),
                  key=lambda r: (-r["sweep_only_passes"], order[r["label"]]))[:cap]
    frozen = (legacy.data.get("selected") or {}).get("bundle_sha256")
    frozen_label = next((l for l, c in new.items() if c["bundle_sha256"] == frozen), None)
    dev, unseen = replay_counts(cell)
    reruns = [r["replay_dev"] for r in dev]
    return {
        "cell": cell, "ledger": str(source), "versions": rows, "frozen_label": frozen_label,
        "legacy_frozen_passes": len(old[frozen_label]["passes"]) if frozen_label else None,
        "sweep_only_best": best,
        "resweep_plan_on_sweep_only_counts": [r["label"] for r in near],
        "frozen_in_plan": frozen_label in [r["label"] for r in near],
        "frozen_replay_dev_reruns": reruns,
        "frozen_merged_with_replay_runs": ([next(r for r in rows if r["label"] == frozen_label)["sweep_only_passes"]
                                            + x for x in reruns] if frozen_label else None),
        "unseen_66_80_by_version_from_replay": {f"v{k}": v for k, v in sorted(unseen.items())},
        "note": ("sweep-only counts cover the 14 seeds each version was swept on (the real protocol refused a "
                 "sweep on the admitting seed); under p6d that seed is swept too"),
    }


def main():
    out = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        report = {"tool": "docs/experiments/code-world-method-repair-p6d-20261022/tools/selection_replay.py",
                  "switches": ms.P6D, "cells": [cell_report(c, tmp) for c in CELLS]}
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
