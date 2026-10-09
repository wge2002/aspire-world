"""Regressions for the 2026-10-05 pipeline repair (CPU only, no simulator, no model).

The diagnostic REPL received the authored file on stdin and pushed it line by
line, so a valid script with compound statements produced SyntaxError /
IndentationError records (bowldrawer_C seed 54: a file that compiles as 18
top-level statements was charged as a diagnostic_program_error with 14 errors).
"""
from __future__ import annotations

import code
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SIM = Path(__file__).resolve().parents[1]
for path in (SIM / "tests", SIM / "scripts/libero", SIM / "scripts/common", SIM.parents[1]):
    sys.path.insert(0, str(path))
import native_world_protocol as protocol
import replay_trial
import test_native_world_fixloop as fixtures

# The shapes that broke seed 54: a blank line inside a function body, a loop
# followed directly by a dedented statement, a lambda sort inside a block.
COMPOUND = '''\
import math

def summarize(bars):
    total = 0

    for width, name in bars:
        total += width
    bars.sort(key=lambda b: -b[0])
    return total, [n for _, n in bars]

bars = [(1, "a"), (3, "b"), (2, "c")]
for width, _ in bars:
    width += 1
result = summarize(bars)
if result[0] > 5:
    label = "wide"
else:
    label = "narrow"
hyp = round(math.hypot(3, 4))
'''


def console(namespace=None):
    return replay_trial._diagnostic_console(code)(namespace if namespace is not None else {})


def args(tmp_path):
    return SimpleNamespace(output_dir=str(tmp_path), suite="synthetic", task="synthetic", trial=54)


def test_line_by_line_push_reproduces_the_seed54_defect():
    legacy = console()
    for line in COMPOUND.splitlines():
        legacy.push(line)
    assert {e["type"] for e in legacy.errors} & {"SyntaxError", "IndentationError"}


def test_compound_script_runs_as_written(tmp_path):
    namespace = {}
    session = console(namespace)
    session.run_script(COMPOUND)
    assert session.errors == []
    assert session.statements == 7  # top-level statements, not lines
    assert namespace["result"] == (6, ["b", "c", "a"])
    assert namespace["label"] == "wide" and namespace["hyp"] == 5
    replay_trial._write_diagnostic_session(args(tmp_path), session)
    report = json.loads((tmp_path / "diagnostic_session.json").read_text())
    assert report["error_count"] == 0 and report["statements"] == 7
    assert report["execution"] == "script"


def test_runtime_errors_are_recorded_per_statement_and_execution_continues(capsys):
    namespace = {"env": object()}
    session = console(namespace)
    session.run_script("x = 1\nenv.handle\n\ndef f():\n    return plate_c\n\nf()\nx + 41\ny = 2\n")
    assert [e["type"] for e in session.errors] == ["AttributeError", "NameError"]
    assert [e["statement_index"] for e in session.errors] == [2, 4]
    assert [e["line"] for e in session.errors] == [2, 7]
    assert "return plate_c" in session.errors[1]["traceback"]  # authored source is quoted
    assert namespace["y"] == 2 and session.statements == 6
    assert "42" in capsys.readouterr().out  # bare expressions still echo, as in the REPL


def test_genuine_syntax_error_is_one_record_and_nothing_runs():
    namespace = {}
    session = console(namespace)
    session.run_script("ran = True\ndef broken(:\n    pass\n")
    assert len(session.errors) == 1
    error = session.errors[0]
    assert error["kind"] == "syntax_error" and error["type"] == "SyntaxError"
    assert error["line"] == 2 and error["statement_index"] == 1
    assert "ran" not in namespace and session.statements == 0


def test_future_directives_carry_across_statements():
    # Codex review 2026-10-05: per-statement compilation must keep the file's
    # `from __future__` flags, or postponed annotations are evaluated.
    namespace = {}
    session = console(namespace)
    session.run_script("from __future__ import annotations\n"
                       "def f(x: UndefinedType) -> AlsoUndefined:\n    return x\n"
                       "value = f(3)\n")
    assert session.errors == []
    assert namespace["value"] == 3
    assert namespace["f"].__annotations__ == {"x": "UndefinedType", "return": "AlsoUndefined"}


def test_replay_module_future_flags_do_not_leak_into_authored_code():
    # replay_trial.py itself uses `from __future__ import annotations`.
    namespace = {}
    session = console(namespace)
    session.run_script("def f(x: int): return x\n")
    assert namespace["f"].__annotations__ == {"x": int}


def test_system_exit_ends_the_session_after_recorded_statements():
    namespace = {}
    session = console(namespace)
    with pytest.raises(SystemExit):
        session.run_script("a = 1\nraise SystemExit(0)\nb = 2\n")
    assert namespace["a"] == 1 and "b" not in namespace and session.statements == 1


class _Api:
    def __init__(self, calls):
        self.calls = calls

    def functions(self):
        return {"get_observation": lambda: self.calls.append("obs") or {"rgb": None}}


def test_batch_stdin_session_runs_the_whole_script(tmp_path, monkeypatch, capsys):
    calls = []
    env = SimpleNamespace(_apis={"synthetic": _Api(calls)})
    monkeypatch.delenv(replay_trial.SEALED_OUTCOME_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        "def probe():\n    o = get_observation()\n\n    return sorted(o)\nkeys = probe()\nprint('KEYS', keys)\n"))
    replay_trial._run_interactive_repl(env, {}, args(tmp_path))
    report = json.loads((tmp_path / "diagnostic_session.json").read_text())
    assert report["errors"] == [] and report["statements"] == 3
    assert calls == ["obs"]
    out = capsys.readouterr().out
    assert "KEYS ['rgb']" in out and "REPL exited." in out


def test_uncompilable_diagnostic_is_rejected_before_admission():
    with ExitStack() as stack:
        h = fixtures.Harness(stack)
        h.initial_batch(failures=fixtures.FAILED)
        h.replay.reset_mock()
        before = h.state.retries_used(54)
        # Unparseable files were already refused by the bundle check; a file
        # that parses but cannot compile used to be admitted and charged.
        h.write("diagnostic_session.py", "for bar in bars:\nprint(bar)\n")
        with pytest.raises(protocol.ProtocolError, match="executable Python"):
            h.trial("diagnostic", 54)
        h.write("diagnostic_session.py", "print(get_observation())\nreturn 2\n")
        with pytest.raises(protocol.ProtocolError, match="does not compile"):
            h.trial("diagnostic", 54)
        h.replay.assert_not_called()
        assert h.state.retries_used(54) == before
        assert "'return' outside function" in h.state.data["rejected"][-1]["reason"]


def test_valid_compound_diagnostic_is_admitted_and_charged():
    with ExitStack() as stack:
        h = fixtures.Harness(stack)
        h.initial_batch(failures=fixtures.FAILED)
        h.write("diagnostic_session.py", COMPOUND)
        before = h.state.retries_used(54)
        h.trial("diagnostic", 54)
        h.replay.assert_called()
        assert h.state.retries_used(54) == before + 1
