"""Read-only per-cell extraction for the ASPIRE-budget rounds (p5, r3, p6b, ns, l10).

Reads campaign_state.json, development_state.json (the protocol ledger), heldout_result.json,
turn-*.stdout.jsonl result lines (token usage) and attempts/mode_*_BLOCKED.md.  Writes
data/cells.json.  Never writes under a result root.

Ledger semantics reproduced from the trial records: a version = one bundle_sha256 that was
swept in phase B (or the phase-A bundle that first passed seed 51); its dev count is the set
of dev seeds with a task_completed trial of that bundle in any phase (a repair trial that
passes on its target seed counts, as in development_state.selected.selection_evidence).
"""
import glob, json, os, re, sys
from collections import Counter, defaultdict

ROOTS = {
    "p5": "/mnt/home/gewang/experiments/code-world-budget-p5-20261019",
    "r3": "/mnt/home/gewang/experiments/code-world-repair-parallel-p6-20261020-r3",
    "p6b": "/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021",
    "ns": "/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-ns",
    "l10": "/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-l10",
}
DEV = list(range(51, 66))


def load(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def tokens(cell_dir):
    tin = tout = 0
    cost = 0.0
    n = 0
    for f in sorted(glob.glob(os.path.join(cell_dir, "campaign-*", "turn-*.stdout.jsonl"))):
        last = None
        with open(f, errors="replace") as fh:
            for line in fh:
                if line.startswith('{"is_error"') or '"type":"result"' in line[-400:]:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") == "result":
                        last = d
        if last:
            n += 1
            for m in (last.get("modelUsage") or {}).values():
                tin += m.get("inputTokens", 0) + m.get("cacheReadInputTokens", 0)
                tout += m.get("outputTokens", 0)
                cost += m.get("costUSD", 0.0)
    return {"turn_results": n, "input_tokens": tin, "output_tokens": tout}


def analyse(root_label, cell_dir):
    case = load(os.path.join(cell_dir, "case.json"), {})
    cs = load(os.path.join(cell_dir, "campaign_state.json"), {})
    task_dirs = glob.glob(os.path.join(cell_dir, "outputs", "libero_fix_loop", "*", "*"))
    tdir = next((t for t in task_dirs if os.path.exists(os.path.join(t, "development_state.json"))), None)
    ds = load(os.path.join(tdir, "development_state.json"), {}) if tdir else {}
    ho = load(os.path.join(cell_dir, "heldout", "heldout_result.json"))
    trials = ds.get("trials", [])
    out = {
        "round": root_label, "cell": os.path.basename(cell_dir), "suite": case.get("suite"),
        "task": case.get("task"), "status": cs.get("status"), "blocker": cs.get("blocker"),
        "dev_hours": round((cs.get("development_seconds") or max([t.get("elapsed_seconds", 0) for t in cs.get("turns", [])] or [0])) / 3600, 2),
        "n_turns": len(cs.get("turns", [])),
        "worker_generations": len(cs.get("worker_generations", []) or []) or None,
        "n_trials": len(trials),
        "trial_status": dict(Counter(t.get("status") for t in trials)),
        "trial_phase": dict(Counter(t.get("phase") for t in trials)),
    }
    out.update(tokens(cell_dir))
    # phase A: trials on seed 51 that spend budget, until first pass
    a_trials = [t for t in trials if (t.get("budget") or {}).get("phase") == "A" and t.get("phase") not in ("snapshot",)]
    first = None
    for i, t in enumerate(a_trials):
        if t.get("seed") == 51 and t.get("task_completed") == 1:
            first = i + 1
            break
    def started(t):
        lc = load(os.path.join(tdir, t["directory"], "lifecycle.json"), {}) if tdir else {}
        return lc.get("started_at"), lc.get("finished_at")
    starts = [started(t) for t in trials]
    t0 = min([s for s, _ in starts if s] or [None]) if starts else None
    tend = max([f for _, f in starts if f] or [None]) if starts else None
    out["trial_span_hours"] = round((tend - t0) / 3600, 2) if t0 and tend else None
    if first:
        s1 = started(a_trials[first - 1])[1]
        out["hours_to_first_51_pass"] = round((s1 - t0) / 3600, 2) if s1 and t0 else None
    else:
        out["hours_to_first_51_pass"] = None
    out["phaseA_trials"] = len(a_trials)
    out["phaseA_first_pass_at"] = first
    out["phaseA_executed_programs"] = len({t.get("bundle_sha256") for t in a_trials if t.get("phase") in ("smoke", "initial", "repair")})
    # versions
    by_bundle = defaultdict(lambda: {"passed": set(), "tested": set(), "phases": Counter(), "order": None,
                                      "version": None, "mode": None, "wpe": 0})
    for i, t in enumerate(trials):
        b = t.get("bundle_sha256")
        if not b or t.get("phase") in ("snapshot", "probe", "diagnostic"):
            continue
        e = by_bundle[b]
        if e["order"] is None:
            e["order"] = i
        e["phases"][t.get("phase")] += 1
        if t.get("seed") in DEV and t.get("status") in ("complete", "world_program_error", "crash"):
            e["tested"].add(t["seed"])
            if t.get("task_completed") == 1:
                e["passed"].add(t["seed"])
        if t.get("status") == "world_program_error":
            e["wpe"] += 1
        bud = t.get("budget") or {}
        if bud.get("version") is not None and e["version"] is None:
            e["version"] = bud.get("version")
            e["mode"] = bud.get("mode")
    swept = [(b, e) for b, e in by_bundle.items() if e["phases"].get("sweep")]
    swept.sort(key=lambda x: x[1]["order"])
    versions = []
    for b, e in swept:
        versions.append({"bundle": b[:8], "version": e["version"] if e["version"] is not None else 0,
                         "mode": e["mode"], "passed": len(e["passed"]), "tested": len(e["tested"]),
                         "passed_seeds": sorted(e["passed"]), "world_program_errors": e["wpe"]})
    out["versions"] = versions
    out["n_versions_swept"] = len(versions)
    modes = Counter()
    for t in trials:
        bud = t.get("budget") or {}
        if t.get("phase") == "repair" and bud.get("phase") == "B" and bud.get("mode"):
            modes[bud["mode"]] += 1
    out["repair_modes_B"] = dict(modes)
    out["blocked_modes"] = sorted(os.path.basename(p)[5:-11] for p in glob.glob(os.path.join(tdir or "/nonexistent", "attempts", "mode_*_BLOCKED.md")))
    sel = ds.get("selected") or {}
    out["selected_bundle"] = (sel.get("bundle_sha256") or "")[:8] or None
    ev = sel.get("selection_evidence") or {}
    out["selected_ledger_passed"] = ev.get("ledger_passed")
    out["selected_ledger_tested"] = ev.get("ledger_tested")
    out["selected_passing_seeds"] = ev.get("passing_seeds")
    out["selected_reason"] = (sel.get("reason") or "")[:600]
    if ho:
        out["heldout_rate"] = ho.get("success_rate")
        out["heldout_counts"] = ho.get("counts")
        out["heldout_per_seed"] = ho.get("per_seed")
        out["heldout_bundle"] = ((ho.get("identity") or {}).get("bundle_sha256") or "")[:8]
    else:
        out["heldout_rate"] = None
    out["usage_ledger"] = ds.get("usage")
    out["task_dir"] = tdir
    return out


def main():
    rows = []
    for lab, root in ROOTS.items():
        for cd in sorted(glob.glob(os.path.join(root, "*"))):
            if os.path.exists(os.path.join(cd, "case.json")):
                rows.append(analyse(lab, cd))
    json.dump(rows, open(sys.argv[1], "w"), indent=1)
    for r in rows:
        print(r["round"], r["cell"], r["status"], r["n_turns"], r["dev_hours"], r["trial_span_hours"], r["hours_to_first_51_pass"], "A:", r["phaseA_trials"], r["phaseA_first_pass_at"],
              "V:", r["n_versions_swept"], [v["passed"] for v in r["versions"]], "sel:", r["selected_bundle"], r["selected_ledger_passed"],
              "HO:", r["heldout_rate"], "tok:", round(r["input_tokens"] / 1e6, 1), "M", r["blocked_modes"])


if __name__ == "__main__":
    main()
