"""CC_TASK_14 smoke only: the p6d driver with turn bounds, two probes and one held-out injection.

Frozen into the smoke root only, never into a formal root. It runs
`run_two_task_cell.main` unchanged except at these boundaries:

- every native turn is bounded by TURN_SECONDS, and before turn `stop_before_turn`
  the transport returns a cancellation code instead of starting native CC, so a
  cell still developing then ends `cancelled` (a cell whose development already
  ended never reaches that point: the outer resweep / freeze / unseen / held-out
  steps need no native turn);
- `objtaskmilk_p6d_smoke2` gets Task 9's SMOKE BOUNDARY note (at most one recorded
  attempt per invocation) and stops before turn 2: it shows the prompt contract and
  the calibration feedback in real trials, not development to the end;
- before the driver starts, a GraspNet / solve_ik PROBE runs once in the slot's own
  runtime against the unit's real services (`<control>/smoke-checks/`, outside every
  path the worker may read): seed-51 observation, a depth-based object mask,
  plan_grasp on request A, B (object 5 mm closer), A, B, A, and solve_ik at a target
  beyond the workspace (x = 0.9). Nothing moves; nothing enters the ledger;
- `drawer_p6d_smoke1`'s held-out sweep is entered through `smoke_heldout_inject.py`:
  seed INJECT_SEED runs under a 60 s watchdog so that a policy still acting is
  killed, and the evaluator's settle rule must record it as a failed seed.

Model, prompts' method text, ledger, protocol, guards, services and cleanup are the real ones.
"""
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_two_task_cell as driver

TURN_SECONDS = 5400
PLAN = {"drawer_p6d_smoke1": {"boundary": False, "stop_before_turn": 6, "probe": False, "inject_heldout": True},
        "objtaskmilk_p6d_smoke2": {"boundary": True, "stop_before_turn": 2, "probe": True,
                                   "inject_heldout": False}}

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

PROBE = '''
import hashlib, json
import numpy as np
obs = get_observation()
cam = obs["agentview"]
depth = np.asarray(cam["images"]["depth"], dtype=np.float32)
h, w = depth.shape
table = float(np.median(depth[h // 4: 3 * h // 4, w // 4: 3 * w // 4]))
rows, cols = np.indices(depth.shape)
centre = (rows > h // 4) & (rows < 3 * h // 4) & (cols > w // 4) & (cols < 3 * w // 4)
mask = np.zeros(depth.shape, dtype=np.int32)
mask[centre & (depth < table - 0.03) & (depth > 0.1)] = 1
closer = depth.copy()
closer[mask == 1] -= 0.005
K = np.asarray(cam["intrinsics"], dtype=np.float64)
def digest(result):
    poses, scores = result
    return hashlib.sha256(np.ascontiguousarray(poses).tobytes() + np.ascontiguousarray(scores).tobytes()).hexdigest()[:16]
sequence = []
for name, d in (("A", depth), ("B", closer), ("A", depth), ("B", closer), ("A", depth)):
    try:
        sequence.append([name, digest(plan_grasp(d, K, mask))])
    except Exception as exc:
        sequence.append([name, "error: %s: %s" % (type(exc).__name__, exc)])
joints = solve_ik(np.array([0.9, 0.0, 0.3]), np.array([0.0, 1.0, 0.0, 0.0]))
print("PROBE_RESULT " + json.dumps({"mask_pixels": int(mask.sum()), "sequence": sequence,
                                    "ik_joints_shape": list(np.asarray(joints).shape)}))
'''


def record(control, name, data):
    path = Path(control) / "smoke-checks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def wrap(original, case):
    plan = PLAN[case["id"]]
    control = Path(case["control"])

    def run_native_cc(command, **kwargs):
        match = re.fullmatch(r"turn-(\d+)\.stdout\.jsonl", Path(kwargs["stdout_path"]).name)
        if not match:
            return original(command, **kwargs)  # the capacity probe
        turn = int(match.group(1))
        if turn >= plan["stop_before_turn"]:
            record(control, f"stop-before-turn-{turn:02d}.json",
                   {"turn": turn, "at": datetime.now(timezone.utc).isoformat(), "reason": "smoke bound"})
            return -signal.SIGINT
        command = list(command)
        if plan["boundary"]:
            index = command.index("-p") + 1
            note = LATER if "--resume" in command else FIRST
            command[index] = command[index] + "\n" + note + "\n"
            Path(kwargs["stdout_path"]).with_name(f"turn-{turn:02d}-smoke-boundary.txt").write_text(note + "\n")
        kwargs["timeout"] = min(kwargs["timeout"], TURN_SECONDS)
        return original(command, **kwargs)
    return run_native_cc


def probe(case):
    """The GraspNet / solve_ik probe in this slot's runtime, against the unit's services."""
    repo = Path(case["sim"])
    sys.path[:0] = [str(repo / "scripts/libero"), str(repo / "scripts/common")]
    import native_world_protocol as protocol
    out = Path(case["control"]) / "smoke-checks/probe"
    out.mkdir(parents=True, exist_ok=False)
    (out / "code.py").write_text(PROBE)
    env = protocol.runtime_env(case, repo)
    env.update(SNAPSHOT_DIR=str(out), ASPIRE_RUNTIME_EVIDENCE_DIR=str(out), ASPIRE_MAX_STEPS=str(case["max_steps"]))
    command = [str(repo / ".venv-libero/bin/python3"), "scripts/libero/runtime/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"], "--args.trial", "51",
               "--args.model", case["model"], "--args.config", case["env_config"],
               "--args.output-dir", str(out / "results"), "--args.replay-code", str(out / "code.py")]
    started = time.time()
    with open(out / "replay.log", "w") as log:
        proc = subprocess.run(command, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, timeout=case["trial_timeout"])
    text = (out / "replay.log").read_text(errors="replace")
    lines = [line for line in text.splitlines() if line.startswith("PROBE_RESULT ")]
    result = json.loads(lines[-1][len("PROBE_RESULT "):]) if lines else None
    digests_a = [d for name, d in (result or {}).get("sequence", []) if name == "A"]
    record(case["control"], "probe.json", {
        "exit_code": proc.returncode, "wall_seconds": round(time.time() - started, 1), "result": result,
        "graspnet_same_request_same_result": bool(digests_a) and len(set(digests_a)) == 1
        and not any(str(d).startswith("error") for d in digests_a),
        "solve_ik_report_lines": [line for line in text.splitlines() if line.startswith("[solve_ik]")],
        "api_revision_env": env.get("ASPIRE_API_REVISION"), "success_rule_env": env.get("ASPIRE_SUCCESS_RULE"),
        "graspnet_url": case["graspnet_url"], "log": str(out / "replay.log")})


INJECT_ENTRY = "smoke_heldout_inject.py"


def run_heldout(campaign, repo, env, folder, argv, timeout):
    """`driver.run_heldout` with the injection entry in place of heldout_stop_on_infra.py."""
    command = [str(repo / ".venv-libero/bin/python3"), str(Path(__file__).with_name(INJECT_ENTRY)), *argv]
    stdout_path, stderr_path = folder / "heldout.stdout.json", folder / "heldout.stderr.log"
    started = time.monotonic()
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            code, timed_out = driver.stop_group(process), True
    try:
        payload = json.loads(stdout_path.read_text())
    except (json.JSONDecodeError, OSError):
        payload = None
    step = {"step": "heldout", "command": command, "exit_code": code, "result": payload,
            "stderr_tail": stderr_path.read_text()[-4000:], "timed_out": timed_out,
            "timeout_seconds": timeout, "elapsed_seconds": round(time.monotonic() - started, 1),
            "stdout_path": str(stdout_path), "smoke_injection": INJECT_ENTRY}
    campaign.atomic_json(folder / "outer_heldout.json", step)
    return step


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    case = json.loads(Path(argv[argv.index("--case") + 1]).read_text())
    plan = PLAN[case["id"]]
    if plan["probe"] and not (Path(case["control"]) / "smoke-checks/probe.json").exists():
        try:
            probe(case)
        except Exception as exc:  # A failed probe is evidence, not a reason to skip the smoke.
            record(case["control"], "probe.json", {"error": f"{type(exc).__name__}: {exc}"})
    driver.fixed_transport.run_native_cc = wrap(driver.fixed_transport.run_native_cc, case)
    if plan["inject_heldout"]:
        driver.run_heldout = run_heldout
    return driver.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
