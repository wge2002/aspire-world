#!/usr/bin/env python3
"""Keep native CC's stdin open and deliver its background completion events.

Claude Code owns all inference, tools, subagents, and compaction. This module
only transports native task notifications back to the same CLI session.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time


class NotificationFlow:
    """Track whether the native session may still need transported input.

    A native coordinator emits one `result` per assistant turn, not one per
    session, and it may start new background work in a later turn of the same
    session. So an end-turn with an empty task set is only a *candidate*
    terminal state (`idle`) that any later event revokes; `finished` is reserved
    for a hard terminal signal. Treating end-turn as terminal is what closed
    stdin under a live session and turned a later notification into a fatal
    write on a closed stream.
    """

    def __init__(self):
        self.active: set[str] = set()
        self.pending: list[dict] = []
        self.delivered: set[str] = set()
        self.busy = True
        self.finished = False
        self.idle = False

    def accept(self, event: dict) -> list[dict]:
        """Return native notifications to deliver at a parent turn boundary."""
        if event.get("parent_tool_use_id"):
            return []
        subtype = event.get("subtype")
        if (event.get("type") == "system" and subtype == "init") or event.get("type") in {"assistant", "stream_event"}:
            # A new parent turn may think silently longer than idle_grace.
            # Its next result, not the previous turn's result, releases input.
            self.busy = True
        elif event.get("type") == "system" and subtype == "task_started":
            self.active.add(event["task_id"])
        elif event.get("type") == "system" and subtype == "task_notification":
            self.active.discard(event["task_id"])
            key = event.get("uuid") or json.dumps(event, sort_keys=True)
            if key not in self.delivered:
                self.delivered.add(key)
                self.pending.append(event)
        elif event.get("type") == "result":
            self.busy = False
            if event.get("is_error"):
                self.finished = True
                self.idle = False
                return []
        if not self.busy and self.pending:
            notifications, self.pending = self.pending, []
            self.busy = True
            self.idle = False
            return notifications
        self.idle = not self.busy and not self.active and not self.pending
        return []


def stream_command(command: list[str]) -> tuple[list[str], str]:
    command = list(command)
    index = command.index("-p")
    prompt = command.pop(index + 1)
    if "--input-format" in command:
        raise ValueError("input format is configured by native_cc_stream")
    command.extend(["--input-format", "stream-json"])
    return command, prompt


def run_native_cc(command: list[str], *, cwd: Path, env: dict[str, str],
                  stdout_path: Path, stderr_path: Path, timeout: int,
                  first_wait: int = 300, on_start=None,
                  idle_grace: float = 90.0) -> int:
    """Run one native CC session, transporting its background notifications.

    `idle_grace` is how long an apparently-finished session is left with stdin
    open before it is closed. It exists because an end-turn is a turn boundary,
    not a session end: a coordinator that is about to report a background task
    needs stdin to still be there.
    """
    command, prompt = stream_command(command)
    events: queue.Queue[str | None] = queue.Queue()
    flow = NotificationFlow()
    started = time.monotonic()
    with stdout_path.open("x") as out, stderr_path.open("x") as err:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=err, text=True,
                                   bufsize=1, start_new_session=True)

        def read_output():
            try:
                for line in process.stdout:
                    out.write(line)
                    out.flush()
                    events.put(line)
            finally:
                events.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        dispatcher = None

        undeliverable: list[dict] = []

        def send(text: str) -> bool:
            """Write one stream-json user message. False if input is gone.

            A closed or broken stdin means the native session can no longer
            accept input. That is not by itself a transport defect, so it is
            recorded rather than raised; a genuinely failed write of the initial
            prompt is still fatal (checked by the caller below).
            """
            if process.stdin.closed:
                return False
            try:
                process.stdin.write(json.dumps({"type": "user", "message": {
                    "role": "user", "content": text}}, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (ValueError, BrokenPipeError, OSError):
                return False
            return True

        try:
            if on_start is not None:
                on_start(process)
            if not send(prompt):
                raise RuntimeError(
                    "native CC input transport failed: initial prompt was not accepted")
            print(json.dumps({"cc_pid": process.pid, "transport": "stream-json",
                              "stdout": str(stdout_path), "first_check_after_seconds": first_wait}), flush=True)
            # Transport consumes events immediately; the caller does not inspect
            # progress before the repository's uninterrupted first wait expires.
            def receive():
                while True:
                    # While the session looks idle, wait only for the grace
                    # period: a native coordinator that has truly finished emits
                    # nothing further, and closing stdin is what lets it exit.
                    try:
                        line = events.get(timeout=idle_grace) if flow.idle else events.get()
                    except queue.Empty:
                        if flow.idle and not process.stdin.closed:
                            process.stdin.close()
                        continue
                    if line is None:
                        return
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    notifications = flow.accept(event)
                    if notifications:
                        # Use the emitted summaries, never load the full worker
                        # transcript or synthesize a solution on its behalf.
                        payload = [{k: n[k] for k in ("task_id", "status", "summary")}
                                   for n in notifications]
                        if not send("Native Claude Code background task notifications:\n" +
                                    json.dumps(payload, ensure_ascii=False) +
                                    "\nContinue the original task using these native "
                                    "notifications. Keep the original instructions and "
                                    "budgets."):
                            # Input is gone, so these completions never reached the
                            # session. Record them for the caller instead of
                            # discarding them or pretending the turn continued.
                            undeliverable.extend(payload)
                    if flow.finished and not process.stdin.closed:
                        process.stdin.close()

            failures = []

            def dispatch():
                try:
                    receive()
                except Exception as exc:
                    failures.append(exc)
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

            dispatcher = threading.Thread(target=dispatch, daemon=True)
            dispatcher.start()
            time.sleep(first_wait)
            remaining = max(1, timeout - (time.monotonic() - started))
            code = process.wait(timeout=remaining)
            reader.join(timeout=10)
            dispatcher.join(timeout=10)
            if failures:
                raise RuntimeError(f"native CC input transport failed: {failures[0]}")
            if undeliverable and code == 0:
                # The native session exited cleanly, but background completions
                # it never saw are still missing work. Reporting exit 0 here
                # would label an incomplete worker as a success.
                raise RuntimeError(
                    "native CC input transport failed: background notifications were "
                    "not delivered to the native session: " +
                    json.dumps(undeliverable, ensure_ascii=False))
            return code
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            reader.join(timeout=10)
            if dispatcher is not None:
                dispatcher.join(timeout=10)
            if not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            process.stdout.close()
