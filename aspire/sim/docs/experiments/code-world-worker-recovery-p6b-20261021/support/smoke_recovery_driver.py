"""CC_TASK_9 smoke only: the p6b driver with a scripted resume failure and turn bounds.

Frozen into the smoke root only, never into a formal root. It runs
`run_two_task_cell.main` unchanged except at the native transport boundary:

- every coordinator prompt gains a SMOKE BOUNDARY note so the worker returns a
  checkpoint after at most one recorded attempt per invocation (the note is
  saved beside the turn prompt as turn-NN-smoke-boundary.txt);
- for the injected cell, before the first continuation turn the primary's native
  subagent transcript is moved out of the native config (to
  <control>/smoke-injection/), exactly the state in which native Claude Code
  answers "could not be resumed: No transcript found"; the move is recorded;
- before turn `stop_before_turn` the transport returns a cancellation code
  instead of starting native CC, so the driver ends the smoke as `cancelled`
  (no closeout, no freeze, no held-out) after the turns the smoke needs;
- every native turn is bounded by TURN_SECONDS (the job's cost bound).

Model, prompts' method text, ledger, protocol, guards and cleanup are the real ones.
"""
import json
from pathlib import Path
import re
import shutil
import signal
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_two_task_cell as driver

TURN_SECONDS = 5400
PLAN = {"drawer_p6b_smoke1": {"inject": True, "stop_before_turn": 3},
        "drawer_p6b_smoke2": {"inject": False, "stop_before_turn": 2}}

FIRST = ("SMOKE BOUNDARY (smoke harness, not part of the method): right after dispatching the "
         "primary, SendMessage that SAME new worker: 'For this first invocation do only what your "
         "assignment or handoff requires before trials, then at most ONE recorded attempt, then "
         "return a checkpoint with your findings so far. This is a smoke stage boundary, not an "
         "action cap or extra budget.' After its completion notification save its findings and run check "
         "as above, report, and END this invocation: do not SendMessage the worker again in this "
         "invocation; the next invocation continues the work.")
LATER = ("SMOKE BOUNDARY (smoke harness, not part of the method): end the protocol-only message "
         "to the worker with: 'For this invocation run at most ONE more recorded attempt, then "
         "return a checkpoint with your findings so far.' After its completion notification save its "
         "findings and run check as above, report, and END this invocation without resuming the worker again.")


def record(control, name, data):
    path = Path(control) / "smoke-injection" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def wrap(original, case):
    plan = PLAN[case["id"]]
    control = Path(case["control"])
    done = {"injected": False}

    def run_native_cc(command, **kwargs):
        match = re.fullmatch(r"turn-(\d+)\.stdout\.jsonl", Path(kwargs["stdout_path"]).name)
        if not match:
            return original(command, **kwargs)  # the capacity probe
        turn = int(match.group(1))
        if turn >= plan["stop_before_turn"]:
            record(control, f"stop-before-turn-{turn:02d}.json",
                   {"turn": turn, "at": datetime.now(timezone.utc).isoformat(), "reason": "smoke bound"})
            return -signal.SIGINT
        if plan["inject"] and turn >= 1 and not done["injected"]:
            state = json.loads((control / "campaign_state.json").read_text())
            source = Path(state["actual_worker_prompt_receipt"]["actual_child_transcript"])
            target = control / "smoke-injection" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            size = source.stat().st_size
            shutil.move(str(source), str(target))
            record(control, "injection.json", {"turn": turn, "moved_from": str(source), "moved_to": str(target),
                                               "bytes": size, "at": datetime.now(timezone.utc).isoformat()})
            done["injected"] = True
        index = command.index("-p") + 1
        note = LATER if "--resume" in command else FIRST
        command = list(command)
        command[index] = command[index] + "\n" + note + "\n"
        Path(kwargs["stdout_path"]).with_name(f"turn-{turn:02d}-smoke-boundary.txt").write_text(note + "\n")
        kwargs["timeout"] = min(kwargs["timeout"], TURN_SECONDS)
        return original(command, **kwargs)
    return run_native_cc


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    case = json.loads(Path(argv[argv.index("--case") + 1]).read_text())
    driver.fixed_transport.run_native_cc = wrap(driver.fixed_transport.run_native_cc, case)
    return driver.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
