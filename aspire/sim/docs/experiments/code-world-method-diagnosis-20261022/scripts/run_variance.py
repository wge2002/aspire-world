"""Q3: run-to-run variance of held-out scores for independent cells of the same task under the same pipeline.
Scans every <experiments>/code-world-*/<cell>/heldout/heldout_result.json (read-only).  Replicates = cells in the
same root whose names differ only in the trailing _r<k>[b-z]? / digit run suffix and whose (suite, task) match.
Also the ASPIRE-budget cross-round series (p5, r3, p6b, ns), which differ in pipeline fixes but not method/budget."""
import glob, json, os, re, itertools, sys
from collections import defaultdict
import numpy as np
E = "/mnt/home/gewang/experiments"
res = []
for f in glob.glob(f"{E}/code-world-*/*/heldout/heldout_result.json"):
    root = f.split("/")[-4]
    if re.search(r"smoke|rehearsal|source-backup|method-diagnosis", root):
        continue
    d = json.load(open(f)); i = d.get("identity", {})
    if not d.get("all_seeds_accounted", True):
        continue
    res.append({"root": root, "cell": f.split("/")[-3], "suite": i.get("suite"), "task": i.get("task"), "rate": d["success_rate"],
                "per_seed": d.get("per_seed")})
groups = defaultdict(list)
for r in res:
    base = re.sub(r"(_r\d+[a-z]?|\d)$", "", r["cell"])
    groups[(re.sub(r"-(rerun-[a-z]|r\d)$", "", r["root"]), base, r["task"])].append(r)
pairs = []
for k, v in groups.items():
    if len(v) >= 2:
        rates = [x["rate"] for x in v]
        pairs.append({"root": k[0], "group": k[1], "task": k[2], "cells": [x["root"] + "/" + x["cell"] for x in v], "rates": rates})
diffs = []; gain = []
for p in pairs:
    for a, b in itertools.combinations(p["rates"], 2):
        diffs.append(abs(a - b)); gain.append(max(a, b) - (a + b) / 2)
aspire = {}
rounds = {"code-world-budget-p5-20261019": "p5", "code-world-repair-parallel-p6-20261020-r3": "r3",
          "code-world-worker-recovery-p6b-20261021": "p6b", "code-world-worker-recovery-p6b-20261021-ns": "ns"}
for r in res:
    if r["root"] in rounds:
        aspire.setdefault(r["suite"] + ":" + r["task"], []).append((rounds[r["root"]], r["cell"], r["rate"]))
# per-seed agreement between replicate runs of the same task (are the same seeds hard?)
agree = []
for p in pairs:
    vs = [x for x in res if x["root"] + "/" + x["cell"] in p["cells"]]
    for a, b in itertools.combinations(vs, 2):
        if a["per_seed"] and b["per_seed"] and 0.1 < a["rate"] < 0.9 and 0.1 < b["rate"] < 0.9:
            sa = np.array([a["per_seed"][str(s)] == "success" for s in range(1, 51)]); sb = np.array([b["per_seed"][str(s)] == "success" for s in range(1, 51)])
            agree.append({"a": a["cell"], "b": b["cell"], "root": a["root"], "phi": round(float(np.corrcoef(sa, sb)[0, 1]), 3)})
out = {"replicate_groups": pairs, "n_pairs": len(diffs), "mean_abs_diff": round(float(np.mean(diffs)), 3),
       "median_abs_diff": round(float(np.median(diffs)), 3), "share_pairs_diff_ge_0.3": round(float(np.mean(np.array(diffs) >= 0.3)), 3),
       "mean_best_of_2_gain_over_mean": round(float(np.mean(gain)), 3), "aspire_budget_series": aspire, "per_seed_agreement_mid_rate_pairs": agree}
json.dump(out, open(sys.argv[1], "w"), indent=1)
for p in pairs: print(p["root"], p["group"], p["rates"])
print({k: out[k] for k in ("n_pairs", "mean_abs_diff", "median_abs_diff", "share_pairs_diff_ge_0.3", "mean_best_of_2_gain_over_mean")})
for k, v in aspire.items(): print(k[:50], v)
print(agree)
