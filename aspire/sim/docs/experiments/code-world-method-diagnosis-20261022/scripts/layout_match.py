"""Observation-only layout comparison between seed ranges.

For one cell, take the step-0 agentview depth (the first observation the policy sees,
written by replay_trial before any action) from every dev / unseen / held-out trial and
compute the fraction of pixels whose depth differs by > 5 mm.  Reports, for each
non-held-out seed, the closest held-out seed and its distance, plus the expected alias
(seed-1) % 50 + 1.  No simulator state is read.

usage: layout_match.py <cell_dir> [extra_root ...]   -> JSON on stdout
"""
import glob, json, os, re, sys
import numpy as np

cell = sys.argv[1]
extra = sys.argv[2:]
TH = 0.005

def step0(seed_dirs):
    out = {}
    for sd, path in seed_dirs:
        for f in sorted(glob.glob(os.path.join(path, "**", "keyframes", "step_000_depth_agentview.npy"), recursive=True)):
            out.setdefault(sd, f)
    return out

def seeds_under(root, pat):
    res = []
    for d in glob.glob(os.path.join(root, pat)):
        m = re.search(r"seed_(\d+)", d)
        if m:
            res.append((int(m.group(1)), d))
    return res

held = step0(seeds_under(cell, "heldout/seed_*"))
dev = step0(seeds_under(cell, "outputs/libero_fix_loop/*/*/development/*/seed_*/attempt_*"))
for r in extra:
    for k, v in step0(seeds_under(r, "**/seed_*")).items():
        dev.setdefault(k, v)

H = {k: np.load(v).astype(np.float32) for k, v in held.items()}
def dist(a, b):
    return float((np.abs(a - b) > TH).mean())
rows = []
for s, f in sorted(dev.items()):
    a = np.load(f).astype(np.float32)
    ds = sorted((dist(a, b), h) for h, b in H.items())
    alias = (s - 1) % 50 + 1
    rows.append({"seed": s, "alias": alias, "best_heldout": ds[0][1] if ds else None,
                 "best_dist": ds[0][0] if ds else None,
                 "alias_dist": next((d for d, h in ds if h == alias), None),
                 "second_dist": ds[1][0] if len(ds) > 1 else None})
# within-held-out baseline: nearest other held-out seed
base = []
for h, a in H.items():
    o = sorted(dist(a, b) for k, b in H.items() if k != h)
    if o:
        base.append(o[0])
print(json.dumps({"cell": cell, "threshold_m": TH, "n_heldout": len(H), "rows": rows,
                  "heldout_nearest_other_min": min(base) if base else None,
                  "heldout_nearest_other_median": float(np.median(base)) if base else None}, indent=1))
