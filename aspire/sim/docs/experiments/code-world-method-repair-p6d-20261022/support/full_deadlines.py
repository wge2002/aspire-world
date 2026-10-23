"""P6 mixed-N overlay: cases N=1/2/4; per-case held-out and node-worst-case sizing.
Original parallel bound, development budgets, watchdogs and supplements retained.

p6d copy (CC_TASK_14): a case with `method_switches` also reserves the outer
pre-freeze re-sweep (at most max_candidates x the development seeds) and the
unseen measurement, both sized by the held-out rule at the case's N and both
outside the 24 h development deadline. Without the key, `cell_timeout` is the p6 value.
"""
from __future__ import annotations

TRIAL_TIMEOUT = 900
SERVICE_STARTUP = 2400
NATIVE_COMPAT = 1920
DEVELOPMENT = 24 * 3600
HELDOUT_SEED_MARGIN = 120
HELDOUT_SWEEP_OVERHEAD = 120
HELDOUT_SEED_COUNT = 50
FINALIZE_MARGIN = 1800
#: p6 (CC_TASK_7): held-out seeds one cell runs at once; see `HELDOUT_PARALLEL`.
HELDOUT_PARALLEL = 4


def heldout_deadline(seeds: int, trial_timeout: int = TRIAL_TIMEOUT, parallel: int = 1) -> int:
    """Wall-clock bound of a held-out sweep over `seeds` not-yet-recorded seeds,
    at most `parallel` of them running at once."""
    if seeds < 0 or trial_timeout <= 0 or parallel < 1:
        raise ValueError(f"invalid held-out sizing: seeds={seeds} trial_timeout={trial_timeout} "
                         f"parallel={parallel}")
    return HELDOUT_SWEEP_OVERHEAD + -(-seeds // parallel) * (trial_timeout + HELDOUT_SEED_MARGIN)


def check_case(case: dict) -> list[str]:
    """Validate mixed per-cell N; sizing uses this exact case, never the maximum N."""
    n = case.get("heldout_parallel", 1)
    sweep = case.get("sweep_parallel", 1)
    problems = []
    if type(n) is not int or n not in (1, 2, 4):
        problems.append(f"invalid P6 heldout_parallel {n!r}; expected 1, 2 or 4")
    if type(sweep) is not int or sweep != n:
        problems.append(f"P6 sweep_parallel {sweep!r} != heldout_parallel {n!r}")
    if case.get("trial_timeout") != TRIAL_TIMEOUT:
        problems.append("P6 trial_timeout must remain 900 seconds")
    return problems


def method_extra(case: dict) -> int:
    """Outer steps a p6d case adds after development: re-sweep and unseen measurement."""
    switches = case.get("method_switches") or {}
    parallel, extra = case.get("heldout_parallel", 1), 0
    resweep = switches.get("prefreeze_resweep")
    if resweep:
        extra += heldout_deadline(resweep["max_candidates"] * len(case["dev_seeds"]),
                                  case["trial_timeout"], parallel)
    if switches.get("unseen_seeds"):
        extra += heldout_deadline(len(switches["unseen_seeds"]), case["trial_timeout"], parallel)
    return extra


def cell_timeout(case: dict) -> int:
    problems = check_case(case)
    if problems:
        raise ValueError("; ".join(problems))
    return (DEVELOPMENT + heldout_deadline(HELDOUT_SEED_COUNT, case["trial_timeout"],
                                           case.get("heldout_parallel", 1)) + FINALIZE_MARGIN
            + method_extra(case))


# Global report is conservative; the supervisor uses cell_timeout per assigned node.
HELDOUT = heldout_deadline(HELDOUT_SEED_COUNT, TRIAL_TIMEOUT, 1)

TOTAL = SERVICE_STARTUP + NATIVE_COMPAT + DEVELOPMENT + HELDOUT + FINALIZE_MARGIN

#: What the DLC request asks for, rounded up to a whole minute above TOTAL.
DLC_MAX_RUNNING_MINUTES = -(-TOTAL // 60)

#: The user-facing estimate. Not a permission or correctness bound: the phases
#: above are what actually stops the job.
ESTIMATE_HOURS = (12, 40)


def summary() -> dict:
    return {"trial_timeout_seconds": TRIAL_TIMEOUT,
            "service_startup_seconds": SERVICE_STARTUP,
            "native_compat_seconds": NATIVE_COMPAT,
            "development_deadline_seconds": DEVELOPMENT,
            "heldout_deadline_seconds": HELDOUT,
            "heldout_deadline_rule": ("HELDOUT_SWEEP_OVERHEAD + ceil(seeds_to_run / "
                                      "heldout_parallel) x (trial_timeout + HELDOUT_SEED_MARGIN)"),
            "heldout_parallel": "per-case",
            "allowed_parallel": [1, 2, 4],
            "global_heldout_sizing_parallel": 1,
            "heldout_seed_margin_seconds": HELDOUT_SEED_MARGIN,
            "heldout_sweep_overhead_seconds": HELDOUT_SWEEP_OVERHEAD,
            "finalize_margin_seconds": FINALIZE_MARGIN,
            "total_seconds": TOTAL,
            "dlc_max_running_minutes": DLC_MAX_RUNNING_MINUTES,
            "user_facing_estimate_hours": list(ESTIMATE_HOURS),
            "note": ("wall-clock watchdogs only; no global revision, turn or action "
                     "cap is added here; the development budget is aspire-v1 "
                     "(phase A uncapped on the first development seed, phase B "
                     "8 versions / 3 per mode, every version on all 15 seeds), "
                     "enforced by the protocol ledger")}


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
