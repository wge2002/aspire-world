"""Q1(b)/Q3: dev-selected score vs held-out, and the best-of-K winner's curse under a null of equal versions.
Input data/cells.json; output data/selection_bias.json"""
import json, sys
import numpy as np
cells = json.load(open(sys.argv[1]))
rng = np.random.default_rng(0)
rows = []
for c in cells:
    if c.get("heldout_rate") is None or not c.get("versions"):
        continue
    v = [x["passed"] / 15 for x in c["versions"] if x["tested"] == 15]
    sel = (c["selected_ledger_passed"] or 0) / 15
    mean_v = float(np.mean(v)); k = len(v)
    # null: every version has the same true rate = mean of versions; how high does the best of k single 15-seed draws look?
    sims = rng.binomial(15, mean_v, size=(20000, k)).max(axis=1) / 15 if k else np.array([sel])
    rows.append({"round": c["round"], "cell": c["cell"], "suite": c["suite"], "k_versions": k, "dev_versions": [x["passed"] for x in c["versions"]],
                 "dev_selected": round(sel, 3), "dev_mean_versions": round(mean_v, 3), "heldout": c["heldout_rate"],
                 "gap_selected_minus_heldout": round(sel - c["heldout_rate"], 3),
                 "null_best_of_k_mean": round(float(sims.mean()), 3), "null_p_best_ge_selected": round(float((sims >= sel - 1e-9).mean()), 3)})
sel = np.array([r["dev_selected"] for r in rows]); mv = np.array([r["dev_mean_versions"] for r in rows]); ho = np.array([r["heldout"] for r in rows])
multi = [r for r in rows if r["k_versions"] >= 3]
summary = {"n_cells": len(rows),
           "mae_selected_vs_heldout": round(float(np.mean(np.abs(sel - ho))), 3),
           "mae_mean_versions_vs_heldout": round(float(np.mean(np.abs(mv - ho))), 3),
           "mean_gap_selected_minus_heldout": round(float(np.mean(sel - ho)), 3),
           "multi_version_cells(k>=3)": {"n": len(multi),
               "mean_gap_selected_minus_heldout": round(float(np.mean([r["gap_selected_minus_heldout"] for r in multi])), 3),
               "mean_gap_meanversions_minus_heldout": round(float(np.mean([r["dev_mean_versions"] - r["heldout"] for r in multi])), 3)},
           "single_version_cells": {"n": len(rows) - len(multi),
               "mean_gap_selected_minus_heldout": round(float(np.mean([r["gap_selected_minus_heldout"] for r in rows if r["k_versions"] < 3])), 3)}}
json.dump({"rows": rows, "summary": summary}, open(sys.argv[2], "w"), indent=1)
for r in rows:
    print(r["round"], r["cell"], r["k_versions"], r["dev_versions"], "sel", r["dev_selected"], "mean", r["dev_mean_versions"], "HO", r["heldout"], "null_best", r["null_best_of_k_mean"], "p", r["null_p_best_ge_selected"])
print(json.dumps(summary, indent=1))
