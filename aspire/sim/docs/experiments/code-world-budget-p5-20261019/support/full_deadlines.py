"""The one place this study's wall-clock deadlines are written down.

p4 copy (CC_TASK_5, 2026-10-19) of the p3 support module; the p3 original
stays frozen. Only the held-out watchdog changed: it is derived from the seeds
still to run instead of a fixed five hours (see `HELDOUT`).

p5 copy (CC_TASK_6, 2026-10-19) of that p4 module; the p4 original stays as
it was frozen into the p4 / p4n / p4fix / supplement roots. Only `DEVELOPMENT`
changed (14 h -> 24 h) because p5 runs the ASPIRE v1.0 development budget
(aspire-v1): before the first development seed passes, attempts on it are
uncapped, and after it passes up to 8 versions each run on all 15 development
seeds. The development phase therefore has no per-seed retry arithmetic any
more, and this watchdog is its only wall-clock bound.

The pilot carried a single 300-minute maximum runtime, which is not retained:
this study runs a 15-seed development phase *and* a 50-seed frozen held-out
sweep in the same job, so both phases need their own explicit wall-clock budget
and the supervisor's total must encompass both plus service startup.

These are **watchdogs, not caps on the method**. Nothing here limits revisions,
turns or actions; the solver's budget is the aspire-v1 development budget
(phase A: uncapped attempts on the first development seed until it passes;
phase B: at most 8 versions, at most 3 per failure mode, each run on all 15
development seeds), which the protocol ledger enforces. A deadline expiring
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
    24 h (p5, CC_TASK_6; 14 h in p4, 10 h in the gate study). Under aspire-v1
    phase A has no attempt cap, so there is no worst-case trial sum to size
    against: this is the wall-clock bound, and in phase A it is the ONLY way a
    cell ends without its first development pass (the protocol never reports
    phase A as terminal). Phase B is bounded by 8 versions x 15 seeds = 120
    trials (<= 30 h at the 900 s watchdog, ~5-14 h at the observed 2.5-7 min per
    trial) plus solver time. A development phase that has spent 24 hours is
    reported blocked with its ledger intact rather than silently continuing into
    the held-out budget.

`HELDOUT`
    Derived, not chosen (p4 CC_TASK_5, 2026-10-19): `heldout_deadline(seeds,
    trial_timeout)` = `HELDOUT_SWEEP_OVERHEAD` + seeds x (trial_timeout +
    `HELDOUT_SEED_MARGIN`), i.e. 51120 s (14.2 h) for 50 seeds at 900 s. The
    earlier fixed 5 h cut legal sweeps: the p4n cells spatialbetween_p4n_r1
    (~400 s/seed) and twomoka_p4n_r1 (~440 s/seed) were stopped at seeds 45 and
    41 although every seed finished inside its own trial timeout. Each seed is
    already bounded by `run_replay` (trial_timeout + adapter grace + 15 s
    TERM->KILL), so the sum over the seeds still to run is the tight bound and
    a sweep only times out when the evaluator itself stalls. The driver
    derives it from the case's trial_timeout and the seeds the ledger still
    lacks (a supplementary run gets the bound of its own seeds); `HELDOUT` is
    the 50-seed value the supervisor's per-cell allowance is sized from.

`HELDOUT_SEED_MARGIN`
    120 s per seed on top of trial_timeout: the adapter cleanup grace (60,
    `native_world_protocol.CLEANUP_MARGIN_SECONDS`), `run_replay`'s 15 s
    TERM->KILL, and 45 s for runtime verification, the frozen-digest check,
    result parsing and the ledger write (observed < 2 s per seed).

`HELDOUT_SWEEP_OVERHEAD`
    120 s once per sweep: evaluator start, frozen-bundle check, final report,
    and the driver's own group stop (45 s TERM + 10 s KILL) should it fire.

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
DEVELOPMENT = 24 * 3600
HELDOUT_SEED_MARGIN = 120
HELDOUT_SWEEP_OVERHEAD = 120
HELDOUT_SEED_COUNT = 50
FINALIZE_MARGIN = 1800


def heldout_deadline(seeds: int, trial_timeout: int = TRIAL_TIMEOUT) -> int:
    """Wall-clock bound of a held-out sweep over `seeds` not-yet-recorded seeds."""
    if seeds < 0 or trial_timeout <= 0:
        raise ValueError(f"invalid held-out sizing: seeds={seeds} trial_timeout={trial_timeout}")
    return HELDOUT_SWEEP_OVERHEAD + seeds * (trial_timeout + HELDOUT_SEED_MARGIN)


HELDOUT = heldout_deadline(HELDOUT_SEED_COUNT)

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
            "heldout_deadline_rule": ("HELDOUT_SWEEP_OVERHEAD + seeds_to_run x "
                                      "(trial_timeout + HELDOUT_SEED_MARGIN)"),
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
