#!/usr/bin/env python3
"""Read-only world-use / screening / prediction diagnosis over the code-world budget cells.

Reads (never writes) under the experiment roots: development_state.json (the protocol ledger),
attempts/offline/checks.jsonl (every offline screening), and per-trial
world_use_audit.json, lifecycle.json and judgment_world/events.jsonl.  Never opens a heldout/
directory.  Writes only into the research dir:
    data/world_use_trials.jsonl   one derived row per ledger trial (cache; --reuse to skip event parsing)
    data/world_use_stats.json     per-cell and pooled statistics

Usage:  python3 -I scripts/world_use_stats.py [--reuse] [--workers N]

Question map (see the summary dict keys):
  q1_screening    screenings run / rejected / blocked; P(real pass | screening goal verdict)
  q2_predictions  match/mismatch/unknown/superseded per level; pass-vs-fail; judgment confusion matrix
  q3_world_use    runtime_mechanism / world_use_audit status; mismatch-informed decisions and retries
  q4_failures     world_program_error / infrastructure / crash rows and the budget they consumed
Level semantics are copied from cap/world_model/search_control.py (entity measured only through
get_observation = gripper level, any other measured entity = object level).
"""
import argparse, glob, json, math, os, re, sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "data")
E = "/mnt/home/gewang/experiments"
ROOTS = {
    "p5": f"{E}/code-world-budget-p5-20261019",
    "r3": f"{E}/code-world-repair-parallel-p6-20261020-r3",
    "p6b": f"{E}/code-world-worker-recovery-p6b-20261021",
    "ns": f"{E}/code-world-worker-recovery-p6b-20261021-ns",
    "l10": f"{E}/code-world-worker-recovery-p6b-20261021-l10",
}
GRADED = ("initial", "repair", "sweep", "smoke")        # phases that grade a candidate
PROPRIOCEPTIVE_APIS = {"get_observation"}
MEASURED_LAYERS = {"observed", "rehearsal"}
STATUSES = ("match", "mismatch", "unknown", "superseded", "expired", "unresolved")
EV_RE = re.compile(r'^\{"sequence": (\d+), "elapsed_seconds": [^,]+, "event": "(\w+)"')
API_RE = re.compile(r'"api_call": (\d+), "function": "(\w+)"')
QNAME_RE = re.compile(r'"name": "(\w+)"')
RETRY_RE = re.compile(r"retry|re_?grasp|again|re_?attempt|attempt_?[2-9]|second|recover|fallback|"
                      r"alternat|re_?position|re_?align|adjust|correct|redo|re_?try|try_|different|switch|"
                      r"re_?approach|re_?open|re_?close", re.I)
REPERCEIVE_RE = re.compile(r"re_?observe|re_?localiz|re_?perceiv|refresh|re_?sense|re_?detect|re_?segment|"
                           r"verify|confirm|re_?check|re_?measure|re_?acquire", re.I)


# ----------------------------------------------------------------------------- helpers
def load(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def rate(a, b):
    return round(a / b, 4) if b else None


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - h) / d, 4), round((c + h) / d, 4)]


def auc(pos, neg):
    """P(score_pos > score_neg) + 0.5 ties (Mann-Whitney); None if a class is empty."""
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = {}, 0
    vals = [v for v, _ in allv]
    rk = [0.0] * len(vals)
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[i]:
            j += 1
        for k in range(i, j + 1):
            rk[k] = (i + j) / 2 + 1
        i = j + 1
    rsum = sum(r for r, (_, c) in zip(rk, allv) if c == 1)
    n1, n0 = len(pos), len(neg)
    return round((rsum - n1 * (n1 + 1) / 2) / (n1 * n0), 4)


def strat_auc(items):
    """items: (stratum, passed, score).  AUC(fail score higher than pass score) averaged over strata
    that contain both classes, weighted by n_pass*n_fail.  None if no stratum has both."""
    by = defaultdict(lambda: ([], []))
    for k, passed, x in items:
        if x is not None:
            by[k][0 if not passed else 1].append(x)
    num = den = 0.0
    for k, (f, p) in by.items():
        a = auc(f, p)
        if a is not None:
            w = len(f) * len(p)
            num += a * w
            den += w
    return round(num / den, 4) if den else None


def strat_diff(items):
    """items: (stratum, group, passed).  Weighted mean of pass_rate(group) - pass_rate(not group) over strata
    that contain both groups (weights n1*n0/(n1+n0)); also n of strata used."""
    by = defaultdict(lambda: [[0, 0], [0, 0]])       # [not group: [pass, n], group: [pass, n]]
    for k, g, passed in items:
        c = by[k][1 if g else 0]
        c[0] += 1 if passed else 0
        c[1] += 1
    num = den = 0.0
    used = 0
    for k, (a, b) in by.items():
        if a[1] and b[1]:
            w = a[1] * b[1] / (a[1] + b[1])
            num += w * (b[0] / b[1] - a[0] / a[1])
            den += w
            used += 1
    return {"adj_pass_rate_diff": round(num / den, 4) if den else None, "strata_used": used}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def sim_tag(trial_dir):
    m = re.search(r"sandboxrc_(\d+)_reward_([\d.]+)_taskcompleted_(\d)", trial_dir or "")
    return (int(m.group(1)), float(m.group(2)), int(m.group(3))) if m else (None, None, None)


# ----------------------------------------------------------------------------- per-trial event parse
def parse_events(path):
    out = {"events_ok": False}
    try:
        fh = open(path, errors="replace")
    except OSError:
        return out
    meas = defaultdict(set)         # entity -> set of measured source apis
    commits, checks = [], []        # (seq, entity), (seq, entity, status)
    decisions, motors, queries = [], [], Counter()
    self_eval = None
    n_api = Counter()
    with fh:
        for line in fh:
            m = EV_RE.match(line)
            if not m:
                continue
            seq, ev = int(m.group(1)), m.group(2)
            if ev == "state_write":
                try:
                    rec = json.loads(line).get("record") or {}
                except ValueError:
                    continue
                if rec.get("layer", "observed") in MEASURED_LAYERS and rec.get("entity"):
                    meas[rec["entity"]].add((rec.get("source") or {}).get("api"))
            elif ev == "prediction_committed":
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("entity"):
                    commits.append((seq, e["entity"], e.get("source_function")))
            elif ev == "prediction_check":
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("entity"):
                    checks.append((seq, e["entity"], e.get("status"), e.get("source_function"), e.get("residual")))
            elif ev == "public_api":
                a = API_RE.search(line[:400])
                if a:
                    fn = a.group(2)
                    n_api[fn] += 1
                    if fn in ("close_gripper", "open_gripper", "goto_pose", "goto_home_joint_position",
                              "move_to_joints"):
                        motors.append((seq, fn))
            elif ev == "world_query":
                q = QNAME_RE.search(line[:600])
                queries[q.group(1) if q else "?"] += 1
            elif ev == "policy_decision":
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                res = (e.get("feedback") or {}).get("results") or []
                decisions.append({"seq": seq, "branch": str(e.get("branch")),
                                  "fb_n": len(res), "fb_status": dict(Counter(r.get("status") for r in res))})
            elif ev == "self_evaluation":
                try:
                    self_eval = (json.loads(line).get("result") or {}).get("verdict")
                except ValueError:
                    pass
    level = {e: ("gripper" if apis and apis <= PROPRIOCEPTIVE_APIS else "object") for e, apis in meas.items()}
    lv = {k: {"committed": 0, **{s: 0 for s in STATUSES}} for k in ("gripper", "object", "unmeasured")}
    for _, ent, _ in commits:
        lv[level.get(ent, "unmeasured")]["committed"] += 1
    for _, ent, st, _, _ in checks:
        if st in STATUSES:
            lv[level.get(ent, "unmeasured")][st] += 1
    # first live mismatch (any supported prediction) and what the policy did afterwards
    mm = [(s, ent, sf) for s, ent, st, sf, _ in checks if st == "mismatch"]
    first_mm = min(s for s, _, _ in mm) if mm else None
    closes = [s for s, fn in motors if fn == "close_gripper"]
    out.update({
        "events_ok": True, "levels": lv, "n_mismatch": len(mm),
        "mismatch_levels": dict(Counter(level.get(ent, "unmeasured") for _, ent, _ in mm)),
        "n_close_gripper": len(closes), "n_motor": len(motors),
        "closes_after_first_mismatch": sum(1 for s in closes if first_mm is not None and s > first_mm),
        "motors_after_first_mismatch": sum(1 for s, _ in motors if first_mm is not None and s > first_mm),
        "queries": dict(queries), "decisions": decisions, "self_eval_verdict": self_eval,
    })
    return out


def trial_job(args):
    row, tdir = args
    d = os.path.join(tdir, row["directory"])
    res = dict(row)
    ev = os.path.join(d, "judgment_world", "events.jsonl")
    if row["phase"] in GRADED and row["status"] in ("complete", "world_program_error") and row.get("executed"):
        res.update(parse_events(ev))
        au = load(os.path.join(d, "world_use_audit.json"), {})
        rm = au.get("runtime_mechanism") or {}
        res["audit"] = {"status": au.get("status"), "counts": au.get("counts"),
                        "prediction_use": (au.get("prediction_use") or {}).get("status"),
                        "mech_status": rm.get("status"), "policy_decisions": rm.get("policy_decisions"),
                        "prediction_queries": rm.get("prediction_queries"),
                        "action_chains": len(rm.get("action_chains") or []),
                        "supported_predictions": rm.get("supported_predictions"),
                        "n_audit": bool(au)}
    lc = load(os.path.join(d, "lifecycle.json"), {})
    if lc.get("started_at") and lc.get("finished_at"):
        res["secs"] = round(lc["finished_at"] - lc["started_at"], 1)
    return res


# ----------------------------------------------------------------------------- ledger read
def discover():
    cells = []
    for label, root in ROOTS.items():
        for f in sorted(glob.glob(f"{root}/*/outputs/libero_fix_loop/*/*/development_state.json")):
            tdir = os.path.dirname(f)
            cells.append((label, os.path.basename(f"{root}/" + f[len(root) + 1:].split("/")[0]), tdir, f))
    return cells


def base_rows(cells):
    rows, cellmeta, jobs = [], {}, []
    for label, cell, tdir, f in cells:
        d = load(f)
        checks = []
        cp = os.path.join(tdir, "attempts/offline/checks.jsonl")
        if os.path.exists(cp):
            for line in open(cp):
                try:
                    checks.append(json.loads(line))
                except ValueError:
                    pass
        rej = d.get("rejected") or []
        cellmeta[cell] = {
            "round": label, "task_dir": tdir,
            "checks": [{"status": c.get("status"), "conclusions": c.get("conclusions"),
                        "modes": [(r.get("mode"), r.get("status"), (r.get("goal") or {}).get("verdict")) for r in c.get("reports", [])],
                        "policy": (c.get("identity") or {}).get("policy"), "world": (c.get("identity") or {}).get("world"),
                        "tape": bool((c.get("identity") or {}).get("tape"))} for c in checks],
            "rejected": [{"phase": r.get("phase"), "seed": r.get("seed"), "reason": (r.get("reason") or "")[:60]} for r in rej],
            "blockers": [{"phase": b.get("phase"), "seed": b.get("seed"), "resolved": b.get("resolved"),
                          "reason": (b.get("reason") or "")[:60]} for b in (d.get("blockers") or [])],
            "selected": bool((d.get("selected") or {}).get("bundle_sha256")),
        }
        for i, t in enumerate(d["trials"]):
            scr = t.get("screening") or {}
            rep = {r.get("mode"): r for r in scr.get("reports", [])}
            fc = t.get("foundation_calibration") or {}
            we = t.get("world_error") or {}
            rc, rew, tag = sim_tag(t.get("trial_dir"))
            bud = t.get("budget") or {}
            row = {
                "round": label, "cell": cell, "idx": i, "phase": t["phase"], "seed": t["seed"], "attempt": t.get("attempt"),
                "status": t["status"], "executed": t.get("executed"), "spends_retry": t.get("spends_retry"),
                "directory": t["directory"], "bundle": (t.get("bundle_sha256") or "")[:12],
                "version": bud.get("version"), "bphase": bud.get("phase"),
                "passed": t.get("task_completed") == 1, "ledger_tc": t.get("task_completed"),
                "sim_rc": rc, "sim_tag_success": (tag == 1) if tag is not None else None,
                "scr_status": scr.get("status"), "scr_cached": scr.get("cached"),
                "scr_conc": scr.get("conclusions"),
                "scr_reh_status": (rep.get("rehearsal") or {}).get("status"),
                "scr_reh_goal": ((rep.get("rehearsal") or {}).get("goal") or {}).get("verdict"),
                "scr_rep_status": (rep.get("replay") or {}).get("status"),
                "scr_rep_goal": ((rep.get("replay") or {}).get("goal") or {}).get("verdict"),
                "scr_baseline_exc": bool(t.get("screening_baseline_exception")),
                "cal_cat": fc.get("category"), "cal_status": fc.get("status"),
                "cal_verdict": (fc.get("final_self_evaluation") or {}).get("verdict"),
                "cal_eval": fc.get("evaluator_success"),
                "werr": [re.sub(r"\d+", "N", (e.get("error") or ""))[:80] for e in (we.get("errors") or [])][:3],
                "err": (t.get("error") or "")[:80],
                "spl": t.get("search_prediction_levels"),
            }
            jobs.append((row, tdir))
            rows.append(row)
    return rows, cellmeta, jobs


# ----------------------------------------------------------------------------- aggregation
def confusion(rs):
    c = Counter()
    for r in rs:
        c[r.get("cal_cat") or ("na_" + str(r.get("cal_status")))] += 1
    tp, fp, tn, fn, unk = (c.get(k, 0) for k in ("true_positive", "false_positive", "true_negative", "false_negative", "unknown"))
    dec = tp + fp + tn + fn
    return {"counts": dict(c), "decided": dec, "unknown": unk,
            "coverage": rate(dec, dec + unk), "accuracy_decided": rate(tp + tn, dec),
            "precision_true": rate(tp, tp + fp), "recall_pass": rate(tp, tp + fn),
            "npv_false": rate(tn, tn + fn), "specificity_fail": rate(tn, tn + fp),
            "pass_rate_when_world_true": rate(tp, tp + fp), "pass_rate_when_world_false": rate(fn, fn + tn),
            "pass_rate_when_world_unknown": None, "base_pass_rate_audited": rate(tp + fn, dec)}


def screening_block(rs, meta_cells):
    graded = [r for r in rs if r["phase"] in GRADED and r["executed"] and r["status"] in ("complete", "world_program_error")]
    has = [r for r in graded if r["scr_status"]]
    nc = [r for r in graded if r["status"] == "complete" and r["sim_rc"] in (0, None)]

    def by_goal(sub, key):
        out = {}
        for v in ("true", "false", "unknown", None):
            g = [r for r in sub if r[key] == v]
            k = sum(r["passed"] for r in g)
            out[str(v)] = {"n": len(g), "passed": k, "pass_rate": rate(k, len(g)), "wilson95": wilson(k, len(g))}
        return out

    checks = [c for cn in meta_cells for c in cn["checks"]]
    rejected = [x for cn in meta_cells for x in cn["rejected"]]
    screen_rej = [x for x in rejected if x["reason"].startswith("offline candidate error")]
    other_rej = [x for x in rejected if not x["reason"].startswith("offline candidate error")]
    fresh = [r for r in has if r["scr_cached"] is False]
    # bundle level: first graded-and-screened trial of every distinct bundle
    first = {}
    for r in sorted(has, key=lambda r: (r["cell"], r["idx"])):
        first.setdefault((r["cell"], r["bundle"]), r)
    bundles = list(first.values())
    anypass = defaultdict(bool)
    for r in has:
        anypass[(r["cell"], r["bundle"])] |= r["passed"]

    def pt(sub, key):
        return by_goal(sub, key)

    reh_true = [r for r in has if r["scr_reh_goal"] == "true"]
    reh_nottrue = [r for r in has if r["scr_reh_goal"] != "true"]
    allpass = sum(r["passed"] for r in has)
    return {
        "screenings_logged": len(checks),
        "screening_status": dict(Counter(c["status"] for c in checks)),
        "screening_conclusions": dict(Counter(tuple(c["conclusions"] or []) and ",".join(c["conclusions"]) for c in checks)),
        "rejected_by_screening_authored_error": len(screen_rej),
        "rejected_other_prechecks": len(other_rej),
        "rejected_other_reasons": dict(Counter(x["reason"][:40] for x in other_rej)),
        "blocked_infrastructure": sum(1 for c in checks if c["status"] == "blocked"),
        "blockers_in_ledger": sum(len(cn.get("blockers", [])) for cn in meta_cells),
        "rejected_share_of_screenings": rate(len(screen_rej), len(checks)),
        "trials_with_screening": len(has),
        "trials_screening_cached": sum(1 for r in has if r["scr_cached"]),
        "screening_ever_predicts_fail_conclusion": any("supported_fail" in (c["conclusions"] or []) for c in checks),
        "rehearsal_status_counts": dict(Counter(r["scr_reh_status"] for r in has)),
        "all_trials_with_screening": {"n": len(has), "passed": allpass, "pass_rate": rate(allpass, len(has))},
        "pass_rate_by_rehearsal_goal__all_trials": pt(has, "scr_reh_goal"),
        "pass_rate_by_rehearsal_goal__fresh_screening_only": pt(fresh, "scr_reh_goal"),
        "pass_rate_by_rehearsal_goal__non_sweep": pt([r for r in has if r["phase"] != "sweep"], "scr_reh_goal"),
        "pass_rate_by_rehearsal_goal__sweep_only": pt([r for r in has if r["phase"] == "sweep"], "scr_reh_goal"),
        "pass_rate_by_replay_goal__all_trials": pt(has, "scr_rep_goal"),
        "pass_rate_by_rehearsal_goal__bundle_first_trial": pt(bundles, "scr_reh_goal"),
        "bundle_ever_pass_by_rehearsal_goal": {
            v: {"bundles": sum(1 for b in bundles if str(b["scr_reh_goal"]) == v),
                "ever_passed": sum(1 for b in bundles if str(b["scr_reh_goal"]) == v and anypass[(b["cell"], b["bundle"])])}
            for v in ("true", "false", "unknown", "None")},
        "gate_if_it_blocked_unless_goal_true": {
            "would_block_trials": len(reh_nottrue), "of_which_actually_passed": sum(r["passed"] for r in reh_nottrue),
            "would_pass_through": len(reh_true), "of_which_passed": sum(r["passed"] for r in reh_true),
            "false_block_share_of_all_passes": rate(sum(r["passed"] for r in reh_nottrue), allpass)},
        "n_distinct_bundles_screened": len(bundles),
        "replay_reports": {"n": sum(1 for r in has if r["scr_rep_status"]),
                           "unsupported_share": rate(sum(1 for r in has if r["scr_rep_status"] == "unsupported"),
                                                     sum(1 for r in has if r["scr_rep_status"])),
                           "goal_verdicts": dict(Counter(r["scr_rep_goal"] for r in has if r["scr_rep_status"] == "complete"))},
        "screening_checks_replay_modes_logged": sum(1 for c in checks for m in c["modes"] if m[0] == "replay"),
    }


def prediction_block(rs):
    base = [r for r in rs if r["phase"] in GRADED and r["status"] == "complete" and r.get("events_ok") and r["sim_rc"] in (0, None)]
    out = {"n_trials": len(base), "n_pass": sum(r["passed"] for r in base)}
    pooled = {lv: Counter() for lv in ("gripper", "object", "unmeasured")}
    for r in base:
        for lv, c in r["levels"].items():
            pooled[lv].update(c)
    lvl = {}
    for lv, c in pooled.items():
        res = c["match"] + c["mismatch"]
        tot = sum(c[s] for s in STATUSES)
        lvl[lv] = {**{k: c[k] for k in ("committed",) + STATUSES}, "resolved": res,
                   "mismatch_rate_of_resolved": rate(c["mismatch"], res),
                   "share_of_checks": {s: rate(c[s], tot) for s in STATUSES}}
    out["pooled_by_level"] = lvl

    def per_trial(lv):
        v = []
        for r in base:
            c = r["levels"][lv]
            res = c["match"] + c["mismatch"]
            v.append((r["passed"], rate(c["mismatch"], res) if res else None, c["mismatch"], res, c))
        return v

    for lv in ("gripper", "object"):
        v = per_trial(lv)
        P = [x for x in v if x[0]]
        F = [x for x in v if not x[0]]
        out[f"{lv}_pass_vs_fail"] = {
            "pass": {"n": len(P), "mean_trial_mismatch_rate": mean([x[1] for x in P]),
                     "pooled_mismatch_rate": rate(sum(x[2] for x in P), sum(x[3] for x in P)),
                     "frac_trials_with_any_mismatch": rate(sum(1 for x in P if x[2] > 0), len(P)),
                     "mean_resolved": mean([x[3] for x in P])},
            "fail": {"n": len(F), "mean_trial_mismatch_rate": mean([x[1] for x in F]),
                     "pooled_mismatch_rate": rate(sum(x[2] for x in F), sum(x[3] for x in F)),
                     "frac_trials_with_any_mismatch": rate(sum(1 for x in F if x[2] > 0), len(F)),
                     "mean_resolved": mean([x[3] for x in F])},
            "auc_mismatch_rate_fail_higher": auc([x[1] for x in F if x[1] is not None], [x[1] for x in P if x[1] is not None]),
            "auc_within_cell": strat_auc([(r["cell"], r["passed"], x[1]) for r, x in zip(base, v)]),
            "auc_within_cell_and_phase": strat_auc([((r["cell"], r["phase"]), r["passed"], x[1]) for r, x in zip(base, v)]),
            "mean_unknown_superseded_expired_per_trial": {
                s: mean([x[4][s] for x in v]) for s in ("unknown", "superseded", "expired", "unresolved")}}
    # per-trial stats on search_prediction_levels (ledger) agree with events; kept as sanity check
    chk = sum(1 for r in base if r.get("spl") and r["spl"]["object"]["mismatch"] != r["levels"]["object"]["mismatch"])
    out["ledger_vs_events_object_mismatch_disagreements"] = chk
    # world judgment vs simulator success
    aud = [r for r in rs if r["phase"] in GRADED and r["status"] == "complete" and r["cal_status"] == "audited"]
    out["judgment_confusion_all_graded"] = confusion(aud)
    unk = [r for r in aud if r["cal_cat"] == "unknown"]
    out["judgment_confusion_all_graded"]["pass_rate_when_world_unknown"] = rate(sum(r["passed"] for r in unk), len(unk))
    for ph in ("initial", "repair", "sweep"):
        sub = [r for r in aud if r["phase"] == ph]
        c = confusion(sub)
        u = [r for r in sub if r["cal_cat"] == "unknown"]
        c["pass_rate_when_world_unknown"] = rate(sum(r["passed"] for r in u), len(u))
        out[f"judgment_confusion_{ph}"] = c
    out["judgment_unavailable_or_missing"] = dict(Counter(
        str(r["cal_status"]) for r in rs if r["phase"] in GRADED and r["status"] == "complete" and r["cal_status"] != "audited"))
    return out


def use_block(rs):
    base = [r for r in rs if r["phase"] in GRADED and r["status"] == "complete" and r.get("events_ok") and r["sim_rc"] in (0, None)]
    withaudit = [r for r in base if (r.get("audit") or {}).get("n_audit")]
    out = {"n_trials": len(base), "n_with_audit": len(withaudit)}
    out["audit_status"] = dict(Counter(r["audit"]["status"] for r in withaudit))
    out["mechanism_status"] = dict(Counter(r["audit"]["mech_status"] for r in withaudit))
    out["prediction_use_status"] = dict(Counter(r["audit"]["prediction_use"] for r in withaudit))

    def subset(f):
        s = [r for r in withaudit if f(r)]
        return {"n": len(s), "frac_of_trials": rate(len(s), len(withaudit)),
                "pass_rate": rate(sum(r["passed"] for r in s), len(s))}
    out["action_evidence_trials"] = subset(lambda r: r["audit"]["mech_status"] == "action_evidence")
    out["no_action_evidence_trials"] = subset(lambda r: r["audit"]["mech_status"] != "action_evidence")
    out["any_policy_decision"] = subset(lambda r: (r["audit"]["policy_decisions"] or 0) > 0)
    out["no_policy_decision"] = subset(lambda r: not (r["audit"]["policy_decisions"] or 0))
    out["queried_prediction_feedback"] = subset(lambda r: r["queries"].get("prediction_feedback", 0) > 0)
    out["static_supported_use_candidate_site"] = subset(lambda r: ((r["audit"]["counts"] or {}).get("supported_use_candidate", 0)) > 0)
    # decisions
    dec = [(r, d) for r in base for d in r["decisions"]]
    mm_dec = [(r, d) for r, d in dec if d["fb_status"].get("mismatch")]

    def kind(b):
        return "retry_like" if RETRY_RE.search(b) else ("reperceive_like" if REPERCEIVE_RE.search(b) else "proceed_like")
    out["decisions_total"] = len(dec)
    out["decisions_with_mismatch_in_feedback"] = len(mm_dec)
    out["decisions_with_nonempty_feedback"] = sum(1 for _, d in dec if d["fb_n"])
    out["decision_branch_kind_all"] = dict(Counter(kind(d["branch"]) for _, d in dec))
    out["decision_branch_kind_after_mismatch"] = dict(Counter(kind(d["branch"]) for _, d in mm_dec))
    out["top_branches_after_mismatch"] = dict(Counter(d["branch"] for _, d in mm_dec).most_common(12))
    # trials with a mismatch-informed decision
    tm = {}
    for r, d in mm_dec:
        tm.setdefault((r["cell"], r["idx"]), r)
    ts = list(tm.values())
    out["trials_with_mismatch_decision"] = {"n": len(ts), "pass_rate": rate(sum(r["passed"] for r in ts), len(ts))}
    retry_ts = {(r["cell"], r["idx"]): r for r, d in mm_dec if kind(d["branch"]) == "retry_like"}
    out["trials_with_retry_like_branch_after_mismatch"] = {"n": len(retry_ts), "pass_rate": rate(
        sum(r["passed"] for r in retry_ts.values()), len(retry_ts))}
    # behavioural retry: another close_gripper after the first live mismatch
    mmt = [r for r in base if r["n_mismatch"] > 0]
    reg = [r for r in mmt if r["closes_after_first_mismatch"] > 0]
    noreg = [r for r in mmt if r["closes_after_first_mismatch"] == 0]
    out["trials_with_any_live_mismatch"] = {"n": len(mmt), "frac_of_trials": rate(len(mmt), len(base)),
                                             "pass_rate": rate(sum(r["passed"] for r in mmt), len(mmt))}
    nomm = [r for r in base if r["n_mismatch"] == 0]
    out["trials_with_no_live_mismatch"] = {"n": len(nomm), "pass_rate": rate(sum(r["passed"] for r in nomm), len(nomm))}
    out["mismatch_then_regrasp(close_gripper_after)"] = {"n": len(reg), "passed": sum(r["passed"] for r in reg),
                                                          "pass_rate": rate(sum(r["passed"] for r in reg), len(reg))}
    out["mismatch_no_further_grasp"] = {"n": len(noreg), "passed": sum(r["passed"] for r in noreg),
                                        "pass_rate": rate(sum(r["passed"] for r in noreg), len(noreg))}
    out["weak_proxy_multi_grasp_trials(>=2 close_gripper; close_gripper also used to hold)"] = {
        "n": sum(1 for r in base if r["n_close_gripper"] >= 2),
        "pass_rate": rate(sum(r["passed"] for r in base if r["n_close_gripper"] >= 2), sum(1 for r in base if r["n_close_gripper"] >= 2)),
        "single_grasp_pass_rate": rate(sum(r["passed"] for r in base if r["n_close_gripper"] < 2), sum(1 for r in base if r["n_close_gripper"] < 2))}
    anyretry = {(r["cell"], r["idx"]): r for r, d in dec if kind(d["branch"]) == "retry_like"}
    sd = lambda f: strat_diff([((r["cell"], r["phase"]), f(r), r["passed"]) for r in withaudit])
    out["adjusted_within_cell_phase"] = {
        "action_evidence_vs_not": sd(lambda r: r["audit"]["mech_status"] == "action_evidence"),
        "any_live_mismatch_vs_none": sd(lambda r: r["n_mismatch"] > 0),
        "retry_like_branch_after_mismatch_vs_not": sd(lambda r: (r["cell"], r["idx"]) in retry_ts),
        "retry_like_branch_any_vs_not": sd(lambda r: (r["cell"], r["idx"]) in anyretry),
        "mismatch_informed_decision_vs_not": sd(lambda r: (r["cell"], r["idx"]) in tm)}
    # policy-authored branches that were retry-like at all (regardless of mismatch)
    out["trials_with_retry_like_branch_any"] = {"n": len(anyretry), "pass_rate": rate(sum(r["passed"] for r in anyretry.values()), len(anyretry))}
    return out


def failure_block(rs, cellmeta_cells):
    allr = rs
    n = len(allr)
    graded = [r for r in allr if r["phase"] in GRADED and r["executed"]]
    ng = len(graded)
    st = Counter(r["status"] for r in graded)
    res = {"rows_total": n, "graded_rows": ng, "graded_status": dict(st), "all_status": dict(Counter(r["status"] for r in allr))}
    wpe = [r for r in graded if r["status"] == "world_program_error"]
    infra = [r for r in allr if r["status"] == "infrastructure_error"]
    diag = [r for r in allr if r["status"] == "diagnostic_program_error"]
    crash = [r for r in graded if r["status"] == "complete" and r["sim_rc"] not in (0, None)]
    res["world_program_error"] = {
        "rows": len(wpe), "share_of_graded": rate(len(wpe), ng),
        "charged_retry_rows": sum(1 for r in wpe if r["spends_retry"]),
        "by_phase": dict(Counter(r["phase"] for r in wpe)),
        "error_signatures": dict(Counter((r["werr"][0] if r["werr"] else "(none)") for r in wpe).most_common(6)),
        "sim_reported_success_in_dirname_but_ledger_failed": sum(1 for r in wpe if r["sim_tag_success"]),
        "sim_success_by_phase": dict(Counter(r["phase"] for r in wpe if r["sim_tag_success"])),
        "sim_success_by_round": dict(Counter(r["round"] for r in wpe if r["sim_tag_success"])),
        "sim_success_share_of_wpe": rate(sum(1 for r in wpe if r["sim_tag_success"]), len(wpe)),
        "wall_secs": round(sum(r.get("secs") or 0 for r in wpe)),
    }
    res["infrastructure_error"] = {"rows": len(infra), "share_of_all_rows": rate(len(infra), n),
                                    "by_phase": dict(Counter(r["phase"] for r in infra)),
                                    "charged_retry_rows": sum(1 for r in infra if r["spends_retry"])}
    res["diagnostic_program_error"] = {"rows": len(diag), "share_of_all_rows": rate(len(diag), n)}
    res["policy_crash_status_complete_sandbox_rc_nonzero"] = {
        "rows": len(crash), "share_of_graded": rate(len(crash), ng),
        "sim_tag_success": sum(1 for r in crash if r["sim_tag_success"]),
        "ledger_passed": sum(1 for r in crash if r["passed"])}
    bad = wpe + [r for r in graded if r in infra] + crash
    res["failed_or_crashed_total"] = {"rows": len(bad), "share_of_graded": rate(len(bad), ng)}
    res["charged_retries"] = {"total": sum(1 for r in graded if r["spends_retry"]),
                              "wpe": sum(1 for r in wpe if r["spends_retry"]),
                              "share_wpe": rate(sum(1 for r in wpe if r["spends_retry"]), sum(1 for r in graded if r["spends_retry"])),
                              "crash_status_complete": sum(1 for r in crash if r["spends_retry"])}
    # versions (bundles) consumed
    byb = defaultdict(list)
    for r in graded:
        if r["bundle"]:
            byb[(r["cell"], r["bundle"])].append(r)
    nb = len(byb)
    with_wpe = [k for k, v in byb.items() if any(x["status"] == "world_program_error" for x in v)]
    only_wpe_first = [k for k, v in byb.items() if v and v[0]["status"] == "world_program_error"]
    swept = {k: v for k, v in byb.items() if any(x["phase"] == "sweep" for x in v)}
    swept_wpe = [k for k, v in swept.items() if any(x["status"] == "world_program_error" for x in v)]
    swept_cov_loss = sum(sum(1 for x in v if x["status"] == "world_program_error") for v in swept.values())
    swept_rows = sum(len(v) for v in swept.values())
    res["versions"] = {"distinct_bundles_executed": nb, "bundles_with_a_wpe_trial": len(with_wpe),
                       "share": rate(len(with_wpe), nb), "bundles_whose_first_trial_was_wpe": len(only_wpe_first),
                       "swept_bundles": len(swept), "swept_bundles_with_a_wpe_trial": len(swept_wpe),
                       "swept_bundle_rows": swept_rows, "swept_rows_wpe": swept_cov_loss,
                       "swept_wpe_row_share": rate(swept_cov_loss, swept_rows)}
    res["wall_secs_share_wpe"] = rate(sum(r.get("secs") or 0 for r in wpe), sum(r.get("secs") or 0 for r in graded))
    return res


def summarize(rs, cellmeta):
    names = sorted({r["cell"] for r in rs})
    return {
        "n_cells": len(names),
        "q1_screening": screening_block(rs, [cellmeta[c] for c in names]),
        "q2_predictions": prediction_block(rs),
        "q3_world_use": use_block(rs),
        "q4_failures": failure_block(rs, [cellmeta[c] for c in names]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true", help="reuse data/world_use_trials.jsonl instead of re-parsing events")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    cache = os.path.join(OUT, "world_use_trials.jsonl")
    cells = discover()
    rows, cellmeta, jobs = base_rows(cells)
    if a.reuse and os.path.exists(cache):
        rows = [json.loads(l) for l in open(cache)]
    else:
        with ProcessPoolExecutor(a.workers) as ex:
            rows = list(ex.map(trial_job, jobs, chunksize=8))
        with open(cache, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    result = {"meta": {"roots": ROOTS, "cells": len(cells), "trial_rows": len(rows),
                       "graded_phases": GRADED,
                       "notes": [
                           "passed = ledger task_completed==1 (simulator verdict).",
                           "levels: gripper = entity measured only via get_observation; object = other measured entity.",
                           "screening: status 'rejected' only for authored program_error/unsupported_contract; goal verdict "
                           "(true/false/unknown) is informational and never gates execution.",
                           "events are not read for probe/snapshot/diagnostic rows."]},
              "pooled": summarize(rows, cellmeta), "per_cell": {}, "per_round": {}}
    for c in sorted(cellmeta):
        sub = [r for r in rows if r["cell"] == c]
        result["per_cell"][c] = {"round": cellmeta[c]["round"], **summarize(sub, cellmeta)}
    for rd in ROOTS:
        sub = [r for r in rows if r["round"] == rd]
        if sub:
            result["per_round"][rd] = summarize(sub, cellmeta)
    with open(os.path.join(OUT, "world_use_stats.json"), "w") as f:
        json.dump(result, f, indent=1)
    print("wrote", os.path.join(OUT, "world_use_stats.json"), "rows", len(rows))


if __name__ == "__main__":
    main()
