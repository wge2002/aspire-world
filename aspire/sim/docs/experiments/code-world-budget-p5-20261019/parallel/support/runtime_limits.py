"""Outer command envelope; the simulator retains its original 900s budget.

p6 copy (CC_TASK_7, 2026-10-20) of the p3 module; the p3 original stays frozen.
A case with `sweep_parallel` offers the batch sweep `trial --phase sweep
--seeds ...`, one Bash command that runs every missing development seed of one
candidate, up to `sweep_parallel` at once. Its envelope is the single-trial one
with the one trial replaced by ceil(seeds / sweep_parallel) trial slots, each
trial_timeout plus `BATCH_SEED_MARGIN` (adapter grace, run_replay's 15 s
TERM->KILL, per-seed admission and settlement). Without `sweep_parallel` every
value is the p3 one.
"""

OFFLINE_BINDINGS = 2
OFFLINE_SECONDS = 600
CLEANUP_AND_BOOKKEEPING = 420
#: Per batch-sweep trial slot on top of trial_timeout (CC_TASK_7).
BATCH_SEED_MARGIN = 120


def command_seconds(case):
    single = (OFFLINE_BINDINGS * int(case.get("offline_check_timeout", OFFLINE_SECONDS))
              + int(case["trial_timeout"]) + CLEANUP_AND_BOOKKEEPING)
    parallel = case.get("sweep_parallel")
    if not parallel:
        return single
    # The batch admits the first development seed's candidate on every other seed.
    slots = -(-(len(case["dev_seeds"]) - 1) // int(parallel))
    batch = (OFFLINE_BINDINGS * int(case.get("offline_check_timeout", OFFLINE_SECONDS))
             + slots * (int(case["trial_timeout"]) + BATCH_SEED_MARGIN) + CLEANUP_AND_BOOKKEEPING)
    return max(single, batch)


def install(settings, case):
    milliseconds = str(command_seconds(case) * 1000)
    settings["env"].update(BASH_DEFAULT_TIMEOUT_MS=milliseconds,
                           BASH_MAX_TIMEOUT_MS=milliseconds)
    return settings


def trial_timeout_error(payload, case):
    if payload.get("tool_name") != "Bash":
        return None
    inp = payload.get("tool_input", {})
    command = inp.get("command", "")
    if "native_world_protocol.py" not in command or "trial" not in command:
        return None
    import re
    if re.search(r"\btimeout\s", command):
        return "Do not wrap recorded trials in shell timeout. The protocol owns the 900s child watchdog."
    if inp.get("timeout") is not None and int(inp["timeout"]) < command_seconds(case) * 1000:
        return (f"Recorded trial admission can take two 600s offline bindings plus a 900s child. "
                f"Omit the Bash timeout to use its configured {command_seconds(case)}s envelope, "
                "or use that full envelope; wait for the same task to finish before another trial.")
    return None
