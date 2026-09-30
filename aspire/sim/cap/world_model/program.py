"""Execute a frozen, pure numerical world program with a per-call timeout.

This is reliability isolation, NOT a security sandbox. Generated Python must
still run on an appropriate isolated host. The worker receives source and JSON
arguments, never a tape path, environment handle, camera input, or credentials.
Only ``math`` and ordinary numerical/container builtins are provided. Each call
uses a fresh process: persistent state must be returned explicitly as JSON.

Required program functions::

    initialize(context) -> state
    advance(state, step) -> state
    predict(state, step) -> {position, request_query, query_axes?, hypotheses?}
    assimilate(state, evidence) -> state

``state`` may be any JSON value. ``position`` is a finite world-frame XYZ list
or None. Query axes are a nonempty subset of [0, 1, 2]. See runtime.py for the
exact context, step and evidence records. The runtime owns state versions.

``predict`` is read-only. Every call receives a detached JSON copy of the state
in a fresh process, and callers keep only its returned prediction, so mutating
the ``state`` argument inside ``predict`` has no effect. Only the values returned
by ``advance`` and ``assimilate`` persist.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any


class ProgramExecutionError(RuntimeError):
    """A world program failed or did not return JSON."""


class ProgramTimeoutError(ProgramExecutionError):
    """A world-program invocation exceeded its wall-clock limit."""


_WORKER = r'''
import builtins
import json
import math
import sys

def numeric_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name != "math" or level:
        raise ImportError("world programs may import only math")
    return math

names = (
    "abs all any bool dict enumerate filter float int isinstance len list map "
    "max min next pow range reversed round set slice sorted str sum tuple zip "
    "Exception ValueError TypeError KeyError IndexError RuntimeError ZeroDivisionError"
).split()
allowed = {name: getattr(builtins, name) for name in names}
allowed["__import__"] = numeric_import
namespace = {"__builtins__": allowed, "__name__": "world_program", "math": math}
try:
    request = json.load(sys.stdin)
    exec(compile(request["source"], "<world_program>", "exec"), namespace)
    function = namespace.get(request["operation"])
    if not callable(function):
        raise ValueError("missing function: " + request["operation"])
    value = function(*request["args"])
    response = json.dumps({"ok": True, "value": value}, allow_nan=False)
except BaseException as exc:
    response = json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)})
sys.stdout.write(response)
'''


@dataclass(frozen=True)
class FrozenPythonProgram:
    """User-specified source. ``timeout_s`` includes import and function time."""

    source: str
    timeout_s: float = 2.0

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("program source must be a nonempty string")
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")

    @classmethod
    def from_file(cls, path: str | Path, timeout_s: float = 2.0) -> FrozenPythonProgram:
        """Read source in the coordinator; its path is not sent to the worker."""
        return cls(Path(path).read_text(encoding="utf-8"), timeout_s=timeout_s)

    def call(self, operation: str, *args: Any) -> Any:
        if operation not in {"initialize", "advance", "predict", "assimilate"}:
            raise ValueError("unknown world-program operation")
        request = json.dumps(
            {"source": self.source, "operation": operation, "args": args},
            allow_nan=False,
        )
        # No inherited secrets or project path. -I/-S also avoid user site code.
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-u", "-c", _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={},
            cwd=os.path.abspath(os.sep),
            start_new_session=(os.name == "posix"),
        )
        try:
            stdout, _stderr = process.communicate(request, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate()
            raise ProgramTimeoutError(
                f"{operation} exceeded {self.timeout_s:g} seconds"
            ) from None
        if process.returncode:
            raise ProgramExecutionError(
                f"{operation} worker exited with code {process.returncode}"
            )
        try:
            response = json.loads(stdout)
        except (ValueError, TypeError):
            raise ProgramExecutionError(f"{operation} returned invalid worker JSON") from None
        if not isinstance(response, dict) or not response.get("ok"):
            message = response.get("error", "invalid response") if isinstance(response, dict) else "invalid response"
            raise ProgramExecutionError(f"{operation}: {message}")
        return response["value"]
