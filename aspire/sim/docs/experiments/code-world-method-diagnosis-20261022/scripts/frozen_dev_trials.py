"""List the frozen (selected) bundle's development trials per dev seed for every cell with a selection.
Read-only.  Output: data/frozen_dev_trials.json  [{cell, round, seed, phase, status, passed, trial_dir, replay_log}]"""
import json, sys
from pathlib import Path
cells = json.load(open(sys.argv[1]))
out = []
for c in cells:
    if not c.get("selected_bundle") or not c.get("task_dir"):
        continue
    td = Path(c["task_dir"])
    ds = json.loads((td / "development_state.json").read_text())
    sel = ds["selected"]["bundle_sha256"]
    rows = {}
    for t in ds["trials"]:
        if t.get("bundle_sha256") != sel or t.get("seed") not in range(51, 66) or t.get("phase") in ("snapshot", "probe", "diagnostic"):
            continue
        rows.setdefault(t["seed"], []).append(t)
    for seed in range(51, 66):
        ts = rows.get(seed, [])
        if not ts:
            out.append({"round": c["round"], "cell": c["cell"], "seed": seed, "status": "untested"}); continue
        passed = any(t.get("task_completed") == 1 for t in ts)
        t = ts[-1]
        out.append({"round": c["round"], "cell": c["cell"], "seed": seed, "phase": t["phase"], "status": t.get("status"),
                    "passed": passed, "n_trials_this_bundle": len(ts),
                    "outcomes_this_bundle": [t2.get("task_completed") for t2 in ts],
                    "trial_dir": str(td / t["directory"]), "replay_log": str(td / t["directory"] / "replay.log"),
                    "world_error": (t.get("world_error") or "")[:300] if isinstance(t.get("world_error"), str) else t.get("world_error")})
json.dump(out, open(sys.argv[2], "w"), indent=1)
from collections import Counter
print(Counter((r["round"], r["cell"], r.get("passed")) for r in out))
print("repeat trials of same bundle+seed:", [(r["cell"], r["seed"], r["outcomes_this_bundle"]) for r in out if r.get("n_trials_this_bundle", 0) > 1])
