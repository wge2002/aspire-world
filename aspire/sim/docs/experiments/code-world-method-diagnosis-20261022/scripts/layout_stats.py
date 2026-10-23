"""Q1(c): are dev 51-65 layouts drawn from the same distribution as held-out 1-50?  Observation only.

Per seed: the step-0 agentview depth the policy sees before any action (dev: any development trial of
that seed; held-out: the held-out trial).  Layout vector = depth downsampled 8x (64x100), table region.
Reports (1) energy-distance permutation test dev vs held-out, (2) dev->nearest held-out vs held-out
leave-one-out nearest distances, (3) held-out success of the frozen policy stratified by whether its
nearest dev layout passed the frozen version in development (an over-fitting signature: held-out seeds
resembling passing dev layouts should pass more).  Uses only held-out step-0 depth and per-seed outcome.
usage: layout_stats.py <frozen_dev_trials.json> <cell_dir> ...  -> JSON
"""
import glob, json, os, re, sys
import numpy as np

frozen = json.load(open(sys.argv[1]))
rng = np.random.default_rng(0)


def vec(f):
    d = np.load(f).astype(np.float32)
    d = d[: d.shape[0] // 8 * 8, : d.shape[1] // 8 * 8]
    d = d.reshape(d.shape[0] // 8, 8, d.shape[1] // 8, 8).mean(axis=(1, 3))
    return np.clip(d, 0, 3).ravel()


def first_step0(paths):
    for p in paths:
        fs = sorted(glob.glob(os.path.join(p, "**", "keyframes", "step_000_depth_agentview.npy"), recursive=True))
        if fs:
            return fs[0]


def energy(X, Y):
    def md(A, B):
        return np.mean(np.linalg.norm(A[:, None] - B[None], axis=2))
    return 2 * md(X, Y) - md(X, X) - md(Y, Y)


out = []
for cell in sys.argv[2:]:
    name = os.path.basename(cell.rstrip("/"))
    held = {}
    for d in glob.glob(os.path.join(cell, "heldout", "seed_*")):
        s = int(re.search(r"seed_(\d+)", d).group(1))
        f = first_step0([d])
        if f:
            held[s] = vec(f)
    dev = {}
    for s in range(51, 66):
        ds = sorted(glob.glob(os.path.join(cell, "outputs/libero_fix_loop/*/*/development/*", f"seed_{s}", "attempt_*")))
        f = first_step0(ds)
        if f:
            dev[s] = vec(f)
    ho = json.load(open(os.path.join(cell, "heldout", "heldout_result.json")))["per_seed"]
    fz = {r["seed"]: r.get("passed") for r in frozen if r["cell"] == name}
    if len(held) < 10 or len(dev) < 10:
        out.append({"cell": name, "skipped": f"step-0 depth found for {len(held)} held-out / {len(dev)} dev seeds"})
        continue
    H = np.stack([held[k] for k in sorted(held)]); hk = sorted(held)
    D = np.stack([dev[k] for k in sorted(dev)]); dk = sorted(dev)
    e = energy(D, H)
    allv = np.concatenate([D, H]); n = len(D)
    perm = []
    for _ in range(500):
        idx = rng.permutation(len(allv))
        perm.append(energy(allv[idx[:n]], allv[idx[n:]]))
    p = float((np.sum(np.array(perm) >= e) + 1) / (len(perm) + 1))
    dist = lambda a, b: float(np.mean(np.abs(a - b) > 0.005))
    dev_nn = [min(dist(dev[s], H[i]) for i in range(len(H))) for s in dk]
    ho_nn = [min(dist(H[i], H[j]) for j in range(len(H)) if j != i) for i in range(len(H))]
    strat = {"near_dev_pass": [0, 0], "near_dev_fail": [0, 0]}
    for i, s in enumerate(hk):
        nd = min(dk, key=lambda t: dist(H[i], dev[t]))
        key = "near_dev_pass" if fz.get(nd) else "near_dev_fail"
        strat[key][0] += ho[str(s)] == "success"; strat[key][1] += 1
    out.append({"cell": name, "n_dev": len(dk), "n_heldout": len(hk), "energy_distance": round(float(e), 4),
                "perm_p_value": round(p, 3), "dev_to_nearest_heldout_median": round(float(np.median(dev_nn)), 4),
                "heldout_loo_nearest_median": round(float(np.median(ho_nn)), 4),
                "heldout_success_by_nearest_dev_frozen_outcome": {k: f"{v[0]}/{v[1]}" for k, v in strat.items()},
                "frozen_dev_pass": f"{sum(1 for v in fz.values() if v)}/15", "heldout_rate": sum(v == 'success' for v in ho.values()) / 50})
print(json.dumps(out, indent=1))
