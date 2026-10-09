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

    `idle` alone is a weak, timer-based guess, and waiting out `idle_grace` for
    it costs wall clock the session's own deadline is also paying for. A caller
    that can *recognise* its own completed work supplies `terminal_evidence`, a
    predicate over raw events. Once it has fired and no native background work
    is outstanding (`drained`), an end-turn makes the session `complete`: stdin
    closes at once instead of after the grace period, and a deadline that
    arrives during shutdown is not a timeout. With no predicate, `evidence`
    stays False, `complete` is never True, and behaviour is unchanged.

    A completion that arrives while the parent is busy waits for that turn's
    `result`. But the parent may compact its own context in the same turn, and
    a long turn that compacts can run past its deadline without ever ending:
    the completion then sits here, never seen, while the parent waits for (or
    re-requests) work that already finished. So a *parent* `compact_boundary`
    releases pending completions at once, as queued native input the CLI
    drains inside the running turn. A child's compaction is not the parent's
    and releases nothing. Once released, a completion is not sent again at the
    next `result`. A pending completion whose task is resumed before release
    is superseded by the resumed run's own completion rather than delivered
    stale; it is kept in `superseded` for the record.
    """

    def __init__(self, terminal_evidence=None):
        self.active: set[str] = set()
        self.pending: list[dict] = []
        self.delivered: set[str] = set()
        self.undelivered: list[dict] = []
        self.superseded: list[dict] = []
        self.session_id: str | None = None
        self.busy = True
        self.finished = False
        self.idle = False
        self.evidence = False
        self._terminal_evidence = terminal_evidence

    @property
    def drained(self) -> bool:
        """True when no native background work is outstanding or unreported."""
        return not self.active and not self.pending and not self.undelivered

    @property
    def complete(self) -> bool:
        """Accepted terminal evidence, drained native work, no turn in flight."""
        return self.evidence and self.drained and not self.busy

    def accept(self, event: dict) -> list[dict]:
        """Return native notifications to deliver at a parent turn boundary."""
        # Evidence is judged before the subagent filter so a caller may accept
        # (or refuse) evidence produced inside a delegated turn. It latches:
        # completed work is not un-completed by later chatter.
        if self._terminal_evidence is not None and not self.evidence:
            self.evidence = bool(self._terminal_evidence(event))
        if event.get("parent_tool_use_id"):
            return []
        subtype = event.get("subtype")
        if event.get("type") == "system" and subtype == "init" and self.session_id is None:
            self.session_id = event.get("session_id")
        if (event.get("type") == "system" and subtype == "init") or event.get("type") in {"assistant", "stream_event"}:
            # A new parent turn may think silently longer than idle_grace.
            # Its next result, not the previous turn's result, releases input.
            self.busy = True
        elif event.get("type") == "system" and subtype == "compact_boundary":
            own = self.session_id is None or event.get("session_id") in (None, self.session_id)
            if own and self.pending and not self.finished:
                # The turn is still running; the CLI queues this input and
                # drains it into the same turn, so the parent stays busy.
                notifications, self.pending = self.pending, []
                self.idle = False
                return notifications
        elif event.get("type") == "system" and subtype == "task_started":
            task_id = event["task_id"]
            stale = [n for n in self.pending if n.get("task_id") == task_id]
            if stale:
                self.superseded.extend(stale)
                self.pending = [n for n in self.pending if n.get("task_id") != task_id]
            self.active.add(task_id)
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
                  idle_grace: float = 90.0, terminal_evidence=None,
                  shutdown_grace: float = 90.0, outcome: dict | None = None) -> int:
    """Run one native CC session, transporting its background notifications.

    `idle_grace` is how long an apparently-finished session is left with stdin
    open before it is closed. It exists because an end-turn is a turn boundary,
    not a session end: a coordinator that is about to report a background task
    needs stdin to still be there.

    `terminal_evidence` is an optional predicate over raw events naming what the
    caller accepts as proof that the requested work is done — for a fixture,
    its own verifier reporting success. When it has fired and native work is
    drained, an end-turn closes stdin immediately rather than paying
    `idle_grace` out of the same budget as the work, and `timeout` arriving
    during the session's own shutdown is handled by `shutdown_grace` instead of
    a kill: the deadline then bounds *work*, not teardown. Without evidence a
    deadline is still a hard timeout, so a stalled or incomplete session is
    killed and reported exactly as before.

    `outcome`, if given, is updated in place with `terminal_evidence`,
    `completed_at_deadline` and `undelivered` so the caller can record how the
    session ended instead of inferring it from the exit code.
    """
    command, prompt = stream_command(command)
    events: queue.Queue[str | None] = queue.Queue()
    flow = NotificationFlow(terminal_evidence)
    completed_at_deadline = False
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
                            flow.undelivered.extend(payload)
                    # `complete` is the caller's own completion contract; `idle`
                    # is only a timer. Closing here is what keeps a finished
                    # session from spending its remaining budget on silence.
                    if (flow.finished or flow.complete) and not process.stdin.closed:
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
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                # A deadline means "work may still be missing" only while the
                # work is unaccounted for. With the caller's accepted terminal
                # evidence in the stream and nothing outstanding, the session
                # has already done what it was asked to do and all that remains
                # is its own exit, so give teardown a bounded window of its own
                # rather than killing a completed run and labelling it 124.
                # This never lengthens a session that lacks evidence.
                if not (flow.evidence and flow.drained):
                    raise
                completed_at_deadline = True
                code = process.wait(timeout=shutdown_grace)
            reader.join(timeout=10)
            dispatcher.join(timeout=10)
            if failures:
                raise RuntimeError(f"native CC input transport failed: {failures[0]}")
            if flow.undelivered and code == 0:
                # The native session exited cleanly, but background completions
                # it never saw are still missing work. Reporting exit 0 here
                # would label an incomplete worker as a success.
                raise RuntimeError(
                    "native CC input transport failed: background notifications were "
                    "not delivered to the native session: " +
                    json.dumps(flow.undelivered, ensure_ascii=False))
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
            if outcome is not None:
                # Written on every path, including the raise paths, so a caller
                # can tell a genuine timeout from a completed shutdown.
                outcome.update(terminal_evidence=flow.evidence,
                               completed_at_deadline=completed_at_deadline,
                               undelivered=list(flow.undelivered))
