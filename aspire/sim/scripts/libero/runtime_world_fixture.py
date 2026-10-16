"""Run CPU synthetic engineering mechanism acceptance; no model/GPU/simulator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Fresh-only fixture artifacts. Never reuse or overwrite old experiment data.
    args.output.mkdir(parents=True, exist_ok=False)
    sim = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(sim.parents[1]))
    sys.path.insert(0, str(sim / "tests"))
    from runtime_world_fixture_data import run
    cases = [run(args.output / mode, mode)[0] for mode in ("match", "mismatch", "unknown")]
    expected = {"match": ("continue", "goto_home_joint_position"),
                "mismatch": ("revise", "goto_pose"), "unknown": ("reobserve", "get_observation")}
    accepted = True
    for case in cases:
        audit = case["feedback"]["runtime_mechanism"]
        chains = audit["action_chains"]
        accepted &= (audit["status"] == "action_evidence" and audit["state_writes"] >= 2 and
                     audit["supported_predictions"] >= 1 and len(chains) == 1 and
                     (chains[0]["branch"], chains[0]["next_function"]) == expected[case["mode"]] and
                     chains[0]["results"][0]["status"] == case["mode"])
    report = {"scope": "CPU fake-public-API engineering fixture only", "accepted": bool(accepted),
              "controlled_fixture_branching": bool(accepted), "real_solver_compliance": "unverified",
              "real_perception": "unverified", "task_success_benefit": "unverified", "cases": cases}
    (args.output / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
