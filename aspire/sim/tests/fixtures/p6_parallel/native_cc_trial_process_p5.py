"""Supervise a single replay and preserve interruption evidence before returning."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


class TrialInterrupted(Exception):
    pass


def run_replay(command: list[str], *, repo: Path, env: dict, directory: Path,
               timeout: int, stdin_path: Path | None = None) -> tuple[int | None, str]:
    """Supervise one replay. ``stdin_path`` feeds an authored batch REPL session.

    Callers that pass no stdin keep the original DEVNULL-free behavior byte for
    byte; only the interactive diagnostic path supplies a file.
    """
    process, exit_code, error = None, None, ""
    lifecycle = {"host": socket.gethostname(), "started_at": time.time(), "command": command,
                 "stdin": str(stdin_path) if stdin_path else None}
    def interrupted(signum, _frame):
        raise TrialInterrupted(f"trial interrupted by signal {signum}; same frozen trial requires infrastructure recovery")
    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with (directory / "replay.log").open("x") as log:
            source = open(stdin_path, "rb") if stdin_path is not None else None
            try:
                process = subprocess.Popen(command, cwd=repo, env=env, stdout=log,
                                           stdin=source, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            finally:
                if source is not None:
                    source.close()
            lifecycle["pid"] = process.pid
            stat = Path(f"/proc/{process.pid}/stat")
            lifecycle["proc_start_ticks"] = stat.read_text().rsplit(")", 1)[1].split()[19] if stat.exists() else None
            (directory / "lifecycle.json").write_text(json.dumps(lifecycle, indent=2))
            exit_code = process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, TrialInterrupted) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        # Prevent a second TERM from interrupting cleanup and ledger persistence.
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass
        if process is not None:
            exit_code = process.returncode
        lifecycle.update(finished_at=time.time(), process_exit_code=exit_code, error=error)
        (directory / "lifecycle.json").write_text(json.dumps(lifecycle, indent=2))
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return exit_code, error
