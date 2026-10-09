"""Bounded wall-clock watchdogs for the fresh r3 full Fix Loop.

These bounds do not change seeds, charged attempts, actions, or per-trial
limits. Early completion exits immediately. DEVELOPMENT is the work window;
CLOSEOUT is a separate report-only recovery window, never subtracted from it.

The r2 development lifecycle measurements showed 6,716–9,835 seconds of
trial execution and 21,023–27,253 seconds of non-trial gaps per cell.
Non-trial gaps include native reasoning, offline checks and tool overhead.
The allowance below rounds the largest observed gap up to eight hours, then
adds two hours for finishing the original budget. It is a watchdog, not a
requirement to spend that time.

HELDOUT covers all 50 trials at their existing timeout plus cleanup, with a
small sweep-bookkeeping allowance. A legitimate sequence of slow trials is
not classified as stalled merely because its total exceeds five hours.
"""

from __future__ import annotations

TRIAL_TIMEOUT = 900
TRIAL_CLEANUP = 60
SERVICE_STARTUP = 2400
NATIVE_COMPAT = 1920

DEVELOPMENT_TRIAL_BOUND = 15 * 3 * (TRIAL_TIMEOUT + TRIAL_CLEANUP)
NATIVE_REASONING_ALLOWANCE = 10 * 3600
DEVELOPMENT = DEVELOPMENT_TRIAL_BOUND + NATIVE_REASONING_ALLOWANCE
CLOSEOUT = 3600

HELDOUT = 50 * (TRIAL_TIMEOUT + TRIAL_CLEANUP) + 600
FINALIZE_MARGIN = 1800

TOTAL = (SERVICE_STARTUP + NATIVE_COMPAT + DEVELOPMENT + CLOSEOUT
         + HELDOUT + FINALIZE_MARGIN)
DLC_MAX_RUNNING_MINUTES = -(-TOTAL // 60)

# An estimate based on the preceding round, distinct from the 38.2h hard max.
ESTIMATE_HOURS = (12, 24)


def summary() -> dict:
    return {
        "trial_timeout_seconds": TRIAL_TIMEOUT,
        "trial_cleanup_seconds": TRIAL_CLEANUP,
        "service_startup_seconds": SERVICE_STARTUP,
        "native_compat_seconds": NATIVE_COMPAT,
        "development_deadline_seconds": DEVELOPMENT,
        "development_trial_bound_seconds": DEVELOPMENT_TRIAL_BOUND,
        "native_reasoning_allowance_seconds": NATIVE_REASONING_ALLOWANCE,
        "closeout_deadline_seconds": CLOSEOUT,
        "heldout_deadline_seconds": HELDOUT,
        "finalize_margin_seconds": FINALIZE_MARGIN,
        "total_seconds": TOTAL,
        "dlc_max_running_minutes": DLC_MAX_RUNNING_MINUTES,
        "user_facing_estimate_hours": list(ESTIMATE_HOURS),
        "derivation": {
            "development": "15*3*(900+60) + 10*3600",
            "nontrial_allowance": "r2 max 27253s: ceil to8h +2h margin",
            "closeout": "separate bounded 1h; no additional simulator attempts",
            "heldout": "50*(900+60) +600",
        },
        "note": (
            "Wall-clock watchdogs only. No revision, native-turn, action or "
            "recovery cap is added. Each development seed retains3 total "
            "charged executions and each trial retains its900s timeout. "
            "38.2h is the whole-job hard maximum, not expected spending; "
            "normal early completion exits immediately."
        ),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
