"""Outer command envelope; the simulator retains its original 900s budget."""

OFFLINE_BINDINGS = 2
OFFLINE_SECONDS = 600
CLEANUP_AND_BOOKKEEPING = 420


def command_seconds(case):
    return (OFFLINE_BINDINGS * int(case.get("offline_check_timeout", OFFLINE_SECONDS))
            + int(case["trial_timeout"]) + CLEANUP_AND_BOOKKEEPING)


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
