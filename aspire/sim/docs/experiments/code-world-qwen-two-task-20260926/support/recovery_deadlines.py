"""Wall-clock watchdogs for the append-only bowldrawer C continuation.

The original eight-hour development window ended while its solver was still
working. This fresh job allows ten additional hours for only the remaining
development attempts; it does not alter the three-attempt-per-seed ledger.
"""

SERVICE_STARTUP = 2400
NATIVE_COMPAT = 1920
DEVELOPMENT = 10 * 3600
HELDOUT = 5 * 3600
FINALIZE_MARGIN = 1800
TOTAL = SERVICE_STARTUP + NATIVE_COMPAT + DEVELOPMENT + HELDOUT + FINALIZE_MARGIN
DLC_MAX_RUNNING_MINUTES = -(-TOTAL // 60)


def summary() -> dict:
    return {
        "service_startup_seconds": SERVICE_STARTUP,
        "native_compat_seconds": NATIVE_COMPAT,
        "development_deadline_seconds": DEVELOPMENT,
        "heldout_deadline_seconds": HELDOUT,
        "finalize_margin_seconds": FINALIZE_MARGIN,
        "total_seconds": TOTAL,
        "dlc_max_running_minutes": DLC_MAX_RUNNING_MINUTES,
        "note": "Manual continuation of the same C r2 ledger and Qwen session; no attempt refund",
    }
