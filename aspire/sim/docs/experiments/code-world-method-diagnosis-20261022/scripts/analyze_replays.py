"""After the DLC replay job: compare replays with the development ledger and held-out (Q1 a/b/d).
usage: analyze_replays.py <replay root> <data/cells.json> <out.json>
 - frozen on 51-65: per-seed agreement with the ledger (eval-path / cross-run reproducibility), rep1 vs rep2
 - every version on 66-80: dev count vs unseen count (does dev rank predict unseen? optimism of the selected one)
 - frozen on 66-80 vs held-out rate (is unseen a better predictor than dev?)"""
import json, sys, glob, os
from pathlib import Path
root, cells, outp = Path(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]
plan = json.loads((root / "plan.json").read_text())
cell_by = {c["cell"]: c for c in cells}
res = {}
for j in plan:
    f = root / "jobs" / j["id"] / "replay" / "replay_result.json"
    if f.exists():
        res[j["id"]] = (j, json.loads(f.read_text()))
out = {"jobs_done": len(res), "jobs_planned": len(plan), "frozen_dev": [], "unseen": {}}
for jid, (j, r) in res.items():
    c = cell_by[j["cell"]]
    v = next(x for x in c["versions"] if x["bundle"] == j["bundle_sha256"][:8])
    if j["seeds"] == "51-65":
        ledger = set(v["passed_seeds"])
        ps = r["per_seed"]
        agree = sum((ps[str(s)] == "success") == (s in ledger) for s in range(51, 66))
        out["frozen_dev"].append({"job": jid, "ledger_dev": len(ledger), "replay_dev": r["counts"]["success"],
                                  "per_seed_agreement": f"{agree}/15", "crashes": r["counts"]["crash"],
                                  "infra": r["counts"]["infrastructure_error"]})
    else:
        out["unseen"].setdefault(j["cell"], []).append({"job": jid, "version": j["version"], "frozen": j["frozen"],
                                                        "dev": v["passed"], "unseen": r["counts"]["success"],
                                                        "heldout": c["heldout_rate"] if j["frozen"] else None})
for cell, rows in out["unseen"].items():
    xs = [(x["dev"], x["unseen"]) for x in rows if x["job"].endswith("__r1")]
    if len(xs) >= 3:
        import numpy as np
        a = np.array(xs, float)
        out.setdefault("dev_unseen_corr", {})[cell] = round(float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]), 3)
json.dump(out, open(outp, "w"), indent=1)
print(json.dumps(out, indent=1))
