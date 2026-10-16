"""Replace an unstarted gang job with three independently scheduled 8-GPU jobs.

Default is plan only. Platform mutations require primary exact-manifest release,
fresh mechanism review, zero-trial startup preflight and a confirmed stopped old
job with no queue/cell startup. Partial/uncertain submissions are never retried.
"""
import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import time
from schedule_r9 import OLD_JOB, command, job_state, no_started_work, sha, write_once
from startup_gate_successor_r1 import require_fresh_review

ROOT = Path("/mnt/home/gewang/experiments/code-world-runtime-p2-20261011")
INPUT_DOC = Path("/mnt/home/gewang/code/ASPIRE-code-world-runtime-p2-input-20261011/aspire/sim/docs/experiments/code-world-runtime-p2-20261011")
PY = "/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3"
NODES = 3


def require_release(release, current, manifest_hash):
    if not (release.get("released") is True and release.get("reviewer")
            and release.get("nodes") == NODES and release.get("old_job_id") == OLD_JOB
            and release.get("source_manifest_sha256") == manifest_hash
            and release.get("gate_binding_sha256") == current["binding_sha256"]):
        raise RuntimeError("no primary release for exact r9/three nodes/old job/current gate")


def old_listing(root, label, runner=subprocess.run):
    result = runner(["/mnt/home/gewang/.local/bin/dlc", "get", "job", "--workspace_id=1000309",
                     f"--job_ids={OLD_JOB}", "--page_size=20"], capture_output=True, text=True, check=True)
    text = result.stdout
    (Path(root) / "coordination" / label).write_text(text)
    state = job_state(text)
    # A late-running gang must not be replaced as an unstarted campaign.
    row = next([x.strip() for x in line.split("|")] for line in text.splitlines()
               if len(line.split("|")) > 7 and line.split("|")[2].strip() == OLD_JOB)
    if row[7]:
        raise RuntimeError("old job has RunningTime; unstarted replacement is refused")
    return state


def stop_unstarted_old(root, nodes, manifest_hash, runner=subprocess.run, sleep=time.sleep):
    root = Path(root); coordination = root / "coordination"
    no_started_work(root)
    state = old_listing(root, "r9-old-job-before-stop.log", runner)
    if state not in {"Queuing", "Stopped"}:
        raise RuntimeError(f"old job is {state}, not a confirmed unstarted queue")
    if state != "Stopped":
        journal = coordination / "replacement-r9-stop"
        journal.mkdir(exist_ok=True)
        argv = ["/mnt/home/gewang/.local/bin/dlc", "stop", "job", OLD_JOB]
        write_once(journal / "stop-attempt.json", {"argv": argv, "started_at": time.time()})
        # Retained attempt prohibits reissuing a stop after an ambiguous result.
        try:
            result = runner(argv, capture_output=True, text=True, timeout=120)
            (journal / "stdout.log").write_text(result.stdout or "")
            (journal / "stderr.log").write_text(result.stderr or "")
        except Exception as exc:
            write_once(journal / "uncertain.json", {"error": str(exc)})
        deadline = time.monotonic() + 180
        while True:
            state = old_listing(root, "r9-old-job-confirm-stop.log", runner)
            if state == "Stopped":
                break
            if state not in {"Queuing", "Stopping"} or time.monotonic() >= deadline:
                raise RuntimeError(f"old job stop unconfirmed: {state}; do not submit replacement")
            sleep(5)
    else:
        old_listing(root, "r9-old-job-confirm-stop.log", runner)
    no_started_work(root)
    proof = {"old_job_id": OLD_JOB, "old_status": "Stopped", "confirmed_at": time.time(),
             "no_old_queue_or_cell_started": True, "nodes": nodes,
             "launch_manifest_sha256": manifest_hash,
             "ListJobs_confirmation": str(coordination / "r9-old-job-confirm-stop.log"),
             "ListJobs_confirmation_sha256": sha(coordination / "r9-old-job-confirm-stop.log")}
    write_once(coordination / "replacement-r9-old-job-stopped.json", proof)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        for node in range(NODES):
            result = subprocess.run(command("plan", node, NODES, ROOT), capture_output=True, text=True, check=True)
            (ROOT / "coordination" / f"concrete-dlc-plan-r9-node{node}.log").write_text(result.stdout)
            print(result.stdout)
        return 0
    lock = (ROOT / "bowl_p2_r1/recovery.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    launch = ROOT / "launch-20261011-r9"
    manifest = json.loads((launch / "launch-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if sha(launch / name) != expected:
            raise RuntimeError(f"r9 launch changed: {name}")
    if sha(__file__) != manifest["files"]["submit-runtime-study-r9.py"]:
        raise RuntimeError("unreviewed submit implementation")
    platform = (ROOT / "coordination/platform-preflight-r9.log").read_text()
    if "preflight 通过(rc=0)" not in platform or '"environment_preflight": "passed"' not in platform:
        raise RuntimeError("fresh r9 zero-trial preflight not passed")
    saved = json.loads((ROOT / "coordination/native-work-gate-verification.json").read_text())
    current = require_fresh_review(ROOT / "bowl_p2_r1", saved)
    release = json.loads((ROOT / "coordination/dlc-submit-release-r9.json").read_text())
    manifest_hash = sha(launch / "launch-manifest.json")
    require_release(release, current, manifest_hash)
    check = subprocess.run([PY, str(INPUT_DOC / "verify-staging-successor-r8.py")], capture_output=True, text=True)
    (ROOT / "coordination/pre-replacement-r9-staging.json").write_text(check.stdout)
    if check.returncode:
        raise RuntimeError(check.stderr[-2000:])
    # Check every replacement name and journal before changing the old job.
    for node in range(NODES):
        name = f"aspire-runtime-p2-1011-r9-node{node}"
        journal = ROOT / "coordination/submissions" / f"r9-node{node}"
        if journal.exists():
            raise RuntimeError("replacement journal already exists; inspect partial result, never retry")
        result = subprocess.run(["/mnt/home/gewang/.local/bin/dlc", "get", "job", "--workspace_id=1000309",
                                 "--display_name", name, "--page_size=20"], capture_output=True, text=True, check=True)
        (ROOT / "coordination" / f"pre-submit-r9-node{node}-list.log").write_text(result.stdout)
        if name in result.stdout:
            raise RuntimeError(f"job name exists: {name}; do not replace or duplicate")
    require_fresh_review(ROOT / "bowl_p2_r1", saved)
    stop_unstarted_old(ROOT, NODES, manifest_hash)
    spec = importlib.util.spec_from_file_location("r9_submit_once", INPUT_DOC.parents[2] / "scripts/common/submit_dlc_once.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    receipts = []
    for node in range(NODES):
        if old_listing(ROOT, f"r9-old-job-before-node{node}.log") != "Stopped":
            raise RuntimeError("old job no longer confirmed Stopped")
        receipt = module.submit_once(ROOT / "coordination/submissions" / f"r9-node{node}",
                                     command("submit", node, NODES, ROOT))
        receipts.append({"node_id": node, **receipt})
        print(json.dumps(receipts[-1]), flush=True)
        if receipt["state"] != "submitted":
            write_once(ROOT / "coordination/r9-partial-submission.json", {"receipts": receipts})
            return 1  # No retry and no further paid job after ambiguous submission.
    write_once(ROOT / "coordination/r9-submission-receipts.json", {"receipts": receipts})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
