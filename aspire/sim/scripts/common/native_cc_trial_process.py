"""Supervise a single replay and preserve interruption evidence before returning."""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time


class TrialInterrupted(Exception):
    pass


#: Why a signalled replay ended; the serial and the batch path record the same text.
INTERRUPTED = "trial interrupted by signal {}; same frozen trial requires infrastructure recovery"

#: CC_TASK_12 G6: seconds a replay may keep running after its trial timeout once the
#: policy has already finished, to save the artifacts of a run that is over (trace
#: keyframes, video, the world's final record). The policy's own budget stays the
#: trial timeout: the grace starts only when the replay's trial directory with its
#: summary exists, which `replay_trial.py` writes right after the last code block
#: returned. l10 twomoka's seed-51 attempt 19 finished its policy at 891 s and was
#: killed at 900 s while writing keyframes (they take 15-19 s); the row could then
#: be neither graded nor closed and the cell blocked. Equal to the adapter
#: world's cleanup margin, which already gives its child the same allowance.
PERSISTENCE_GRACE_SECONDS = 60


def policy_finished(command: list[str]) -> bool:
    """Has this replay written its trial summary (`<output-dir>/**/trial_*/summary.txt`)?"""
    if "--args.output-dir" not in command:
        return False
    output = Path(command[command.index("--args.output-dir") + 1])
    return any(path.is_file() for path in output.glob("*/*/*/run/trial_*/summary.txt"))


def run_replay(command: list[str], *, repo: Path, env: dict, directory: Path,
               timeout: int, stdin_path: Path | None = None,
               interrupt: "BatchInterrupt | None" = None,
               persistence_grace: int = PERSISTENCE_GRACE_SECONDS) -> tuple[int | None, str]:
    """Supervise one replay. ``stdin_path`` feeds an authored batch REPL session.

    Callers that pass no stdin keep the original DEVNULL-free behavior byte for
    byte; only the interactive diagnostic path supplies a file.

    ``interrupt`` is passed only by a `ReplayBatch` worker thread (CC_TASK_7).
    Signal handlers can be installed from the main thread only, so the batch
    owns SIGTERM/SIGINT and sets the event instead; this replay then ends
    exactly as a signalled serial one does (TERM -> 15 s -> KILL of its own
    session, error ``TrialInterrupted: ...``). Without it nothing changes.

    CC_TASK_12: at the timeout, a replay whose policy has finished
    (`policy_finished`) gets `persistence_grace` more seconds to save its
    artifacts; `lifecycle.json` then records `persistence_grace`. A replay still
    running after that is killed exactly as at the timeout.
    """
    process, exit_code, error = None, None, ""
    lifecycle = {"host": socket.gethostname(), "started_at": time.time(), "command": command,
                 "stdin": str(stdin_path) if stdin_path else None}
    def interrupted(signum, _frame):
        raise TrialInterrupted(INTERRUPTED.format(signum))
    handlers = ({sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
                if interrupt is None else {})
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
            try:
                exit_code = (process.wait(timeout=timeout) if interrupt is None
                             else _wait(process, timeout, interrupt))
            except subprocess.TimeoutExpired:
                if not (persistence_grace and policy_finished(command)):
                    raise
                lifecycle["persistence_grace"] = {"seconds": persistence_grace, "started_at": time.time()}
                try:
                    exit_code = (process.wait(timeout=persistence_grace) if interrupt is None
                                 else _wait(process, persistence_grace, interrupt))
                except subprocess.TimeoutExpired:
                    raise subprocess.TimeoutExpired(process.args, timeout + persistence_grace) from None
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


def _wait(process: subprocess.Popen, timeout: float, interrupt: "BatchInterrupt") -> int:
    """`process.wait(timeout)` that also ends when the batch is interrupted."""
    deadline = time.monotonic() + timeout
    while (code := process.poll()) is None:
        if interrupt.is_set():
            raise TrialInterrupted(interrupt.reason)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        interrupt.wait(min(0.2, remaining))
    return code


class BatchInterrupt(threading.Event):
    """Set once, with the reason every interrupted replay of the batch records."""

    reason = ""

    def trigger(self, reason: str) -> None:
        if not self.is_set():
            self.reason = reason
            self.set()


class ReplayBatch:
    """Up to `limit` replays at once, each `run_replay` in its own worker thread.

    CC_TASK_7. Only the process supervision runs in the workers: the caller's
    main thread prepares every replay, settles every result and is the only
    writer of any ledger. While the batch is open it owns SIGTERM/SIGINT: the
    first signal stops new submissions and ends every running replay the way
    a signalled serial replay ends (its own session TERM -> 15 s -> KILL, error
    `TrialInterrupted: ...`, all of them concurrently), so the caller can still
    record each row; later signals are ignored until the batch closes. Leaving
    the batch by an exception interrupts whatever still runs. Every replay
    keeps `start_new_session=True`, so the per-cell process cleanup that tracks
    the driver's descendants reaches all of them as it reached the one.
    """

    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError(f"a replay batch runs at least one replay, not {limit}")
        self.limit = limit
        self.interrupt = BatchInterrupt()
        self.futures: dict = {}

    def __enter__(self) -> "ReplayBatch":
        def interrupted(signum, _frame):
            self.interrupt.trigger(INTERRUPTED.format(signum))
        self.handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.limit,
                                                              thread_name_prefix="replay")
        return self

    @property
    def stopping(self) -> bool:
        return self.interrupt.is_set()

    def free(self) -> int:
        return 0 if self.stopping else self.limit - len(self.futures)

    def running(self) -> int:
        return len(self.futures)

    def submit(self, key, replay, command: list[str], **kwargs) -> None:
        """Start `replay(command, **kwargs, interrupt=...)`, normally `run_replay`.

        The caller passes its own module's `run_replay` so a wrapper that patched
        it (the runtime entry, `heldout_stop_on_infra`) applies to every replay.
        """
        if self.free() < 1:
            raise RuntimeError("replay batch is full or stopping")
        future = self.executor.submit(replay, command, interrupt=self.interrupt, **kwargs)
        self.futures[future] = key

    def wait(self, poll: float = 0.5) -> list[tuple]:
        """Block until at least one replay ended; return [(key, (exit_code, error))].

        Waits in short slices so the main thread runs a signal handler promptly.
        """
        done = []
        while self.futures and not done:
            finished, _ = concurrent.futures.wait(list(self.futures), timeout=poll,
                                                  return_when=concurrent.futures.FIRST_COMPLETED)
            for future in finished:
                key = self.futures.pop(future)
                try:
                    outcome = future.result()
                except Exception as exc:  # run_replay itself failed: nothing usable ran.
                    outcome = (None, f"{type(exc).__name__}: {exc}")
                done.append((key, outcome))
        return done

    def __exit__(self, kind, value, traceback) -> bool:
        if kind is not None:
            self.interrupt.trigger(f"replay batch stopped by {kind.__name__}: {value}")
        self.executor.shutdown(wait=True)
        for sig, handler in self.handlers.items():
            signal.signal(sig, handler)
        return False


def parallel_env(env: dict, threads: int) -> dict:
    """A batch replay's environment: the caller's, with CPU thread pools capped.

    Device wiring (CUDA_VISIBLE_DEVICES, MUJOCO_EGL_DEVICE_ID, the EGL vendor
    file, service URLs) is the slot's and is left exactly as given, so every
    replay of a batch runs on the slot's own card.
    """
    capped = dict(env)
    for name in THREAD_VARIABLES:
        capped[name] = str(threads)
    return capped


#: CPU thread pools a replay child may start (OpenMP/torch, MKL, OpenBLAS, numexpr, BLIS, Accelerate).
THREAD_VARIABLES = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS", "BLIS_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS")
