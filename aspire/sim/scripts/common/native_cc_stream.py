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
    def __init__(self):
        self.active: set[str] = set()
        self.pending: list[dict] = []
        self.delivered: set[str] = set()
        self.busy = True
        self.finished = False

    def accept(self, event: dict) -> list[dict]:
        """Return native notifications to deliver at a parent turn boundary."""
        if event.get("parent_tool_use_id"):
            return []
        subtype = event.get("subtype")
        if event.get("type") == "system" and subtype == "task_started":
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
                return []
        if not self.busy and self.pending:
            notifications, self.pending = self.pending, []
            self.busy = True
            return notifications
        if not self.busy and not self.active:
            self.finished = True
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
                  first_wait: int = 300, on_start=None) -> int:
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

        def send(text: str):
            process.stdin.write(json.dumps({"type": "user", "message": {
                "role": "user", "content": text}}, ensure_ascii=False) + "\n")
            process.stdin.flush()

        try:
            if on_start is not None:
                on_start(process)
            send(prompt)
            print(json.dumps({"cc_pid": process.pid, "transport": "stream-json",
                              "stdout": str(stdout_path), "first_check_after_seconds": first_wait}), flush=True)
            # Transport consumes events immediately; the caller does not inspect
            # progress before the repository's uninterrupted first wait expires.
            def receive():
                while True:
                    line = events.get()
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
                        send("Native Claude Code background task notifications:\n" +
                             json.dumps(payload, ensure_ascii=False) +
                             "\nContinue the original task using these native notifications. "
                             "Keep the original instructions and budgets.")
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
