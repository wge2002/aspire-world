"""The one place this study's wall-clock deadlines are written down.

The pilot carried a single 300-minute maximum runtime, which is not retained:
this study runs a 15-seed development phase *and* a 50-seed frozen held-out
sweep in the same job, so both phases need their own explicit wall-clock budget
and the supervisor's total must encompass both plus service startup.

These are **watchdogs, not caps on the method**. Nothing here limits revisions,
turns or actions; the per-seed charged-attempt cap is the only budget the solver
has, and it is unchanged (3 total per development seed). A deadline expiring
produces a structured `blocked` status with the phase named and state preserved — it
never rewrites a ledger or refunds an attempt.

Sizing, all values seconds:

`TRIAL_TIMEOUT`
    900, unchanged from the pilot and from the case file. Per trial.

`SERVICE_STARTUP`
    Model (TP4 cold start from local weights) plus SAM3, GraspNet and PyRoKi.
    v4 waited 1800 for readiness; 2400 leaves room for a cold page cache.

`NATIVE_COMPAT`
    The image/Agent/compaction fixture. v4's own 1920, unchanged.

`DEVELOPMENT`
    10 h. Worst case by arithmetic is 15 seeds x 3 charged trials x 900 s =
    11.25 h of trials alone, so this is deliberately a wall-clock watchdog and
    not a worst-case sum: a development phase that has spent ten hours is
    reported blocked with its ledger intact rather than silently continuing into
    the held-out budget.

`HELDOUT`
    5 h. 50 seeds x (900 + 60 s adapter cleanup margin) = 13.3 h worst case;
    observed held-out trials run far below their timeout, and a sweep that
    exceeds five hours is a stall to report, not to wait out. `--resume`
    continues it from its per-seed ledger without re-running a recorded seed.

`FINALIZE_MARGIN`
    Development finalization, freeze, manifest write and the final report.

`TOTAL`
    The supervisor's own budget and the DLC `job_max_running_time_minutes`
    basis: startup + compat + development + heldout + margin.
"""
from __future__ import annotations

TRIAL_TIMEOUT = 900
SERVICE_STARTUP = 2400
NATIVE_COMPAT = 1920
DEVELOPMENT = 10 * 3600
HELDOUT = 5 * 3600
FINALIZE_MARGIN = 1800

TOTAL = SERVICE_STARTUP + NATIVE_COMPAT + DEVELOPMENT + HELDOUT + FINALIZE_MARGIN

#: What the DLC request asks for, rounded up to a whole minute above TOTAL.
DLC_MAX_RUNNING_MINUTES = -(-TOTAL // 60)

#: The user-facing estimate. Not a permission or correctness bound: the phases
#: above are what actually stops the job.
ESTIMATE_HOURS = (8, 16)


def summary() -> dict:
    return {"trial_timeout_seconds": TRIAL_TIMEOUT,
            "service_startup_seconds": SERVICE_STARTUP,
            "native_compat_seconds": NATIVE_COMPAT,
            "development_deadline_seconds": DEVELOPMENT,
            "heldout_deadline_seconds": HELDOUT,
            "finalize_margin_seconds": FINALIZE_MARGIN,
            "total_seconds": TOTAL,
            "dlc_max_running_minutes": DLC_MAX_RUNNING_MINUTES,
            "user_facing_estimate_hours": list(ESTIMATE_HOURS),
            "note": ("wall-clock watchdogs only; no global revision, turn or action "
                     "cap is added, and the per-seed charged-attempt budget is "
                     "unchanged at 3 total per development seed")}


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
