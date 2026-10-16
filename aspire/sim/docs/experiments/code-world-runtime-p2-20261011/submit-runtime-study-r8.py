"""One journaled DLC submission after final environment and actual-work gates."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import fcntl
from startup_gate_successor_r1 import require_fresh_review

ROOT = Path("/mnt/home/gewang/experiments/code-world-runtime-p2-20261011")
INPUT = Path("/mnt/home/gewang/code/ASPIRE-code-world-runtime-p2-input-20261011/aspire/sim")
DOC = INPUT / "docs/experiments/code-world-runtime-p2-20261011"
PY = "/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3"
NAME = "aspire-runtime-p2-1011-queue01"


def command(mode):
    return ["/mnt/home/gewang/.local/bin/dlc-run", mode, "--name", NAME, "--gpus", "40",
            "--workdir", str(ROOT), "--max-minutes", "2700", "--priority", "9",
            "--skip-preflight", "--", "bash", str(ROOT / "launch-20261011-r8/dlc-queue-entry.sh")]


def require_submit_release(release,current,deployment):
    if not (release.get('released') is True and release.get('reviewer')
            and release.get('source_manifest_sha256')==deployment['launch_manifest_sha256']
            and current.get('identity_change_disclosed') is True
            and release.get('gate_binding_sha256')==current['binding_sha256']):
        raise RuntimeError('No explicit main release bound to this r8 deployment and actual successor mechanism')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    if not args.submit:
        result = subprocess.run(command("plan"), capture_output=True, text=True, check=True)
        (ROOT / "coordination/concrete-dlc-plan-r8.log").write_text(result.stdout)
        print(result.stdout)
        return 0
    lock=(ROOT / "bowl_p2_r1/recovery.lock").open("a")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    platform = (ROOT / "coordination/platform-preflight-r8.log").read_text()
    assert "preflight 通过(rc=0)" in platform and '"environment_preflight": "passed"' in platform
    deployment = json.loads((ROOT / "coordination/deployment-r8-receipt.json").read_text())
    assert deployment["deployment_revision"] == 8 and deployment["job_max_minutes"] == 2700
    import hashlib
    manifest = ROOT / "launch-20261011-r8/launch-manifest.json"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == deployment["launch_manifest_sha256"]
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==deployment['files']['submit-runtime-study-r8.py']
    for name, expected in deployment["files"].items():
        assert hashlib.sha256((manifest.parent / name).read_bytes()).hexdigest() == expected
    gate = json.loads((ROOT / "coordination/native-work-gate-verification.json").read_text())
    current=require_fresh_review(ROOT / "bowl_p2_r1", gate)
    release=json.loads((ROOT / 'coordination/dlc-submit-release-r8.json').read_text())
    require_submit_release(release,current,deployment)
    (ROOT / "coordination/pre-submit-live-gate.json").write_text(json.dumps(current,indent=2)+"\n")
    check = subprocess.run([PY, str(DOC / "verify-staging-successor-r8.py")],
                           capture_output=True, text=True)
    (ROOT / "coordination/pre-submit-verification.json").write_text(check.stdout)
    assert check.returncode == 0, check.stderr[-2000:]
    # Use the same already-authorized wrapper identity. List access is normal;
    # GetJob denial is not bypassed or treated as success.
    jobs = subprocess.run(["/mnt/home/gewang/.local/bin/dlc", "get", "job", "--workspace_id=1000309",
                           "--display_name", NAME, "--page_size=20"], capture_output=True, text=True, check=True)
    (ROOT / "coordination/pre-submit-job-list.log").write_text(jobs.stdout)
    if NAME in jobs.stdout:
        raise RuntimeError("a matching job already exists; inspect it, never duplicate submission")
    # Recheck after staging and cloud lookup, immediately before the submit journal.
    require_fresh_review(ROOT / "bowl_p2_r1", gate)
    spec = importlib.util.spec_from_file_location("submit_once", INPUT / "scripts/common/submit_dlc_once.py")
    journal = importlib.util.module_from_spec(spec); spec.loader.exec_module(journal)
    receipt = journal.submit_once(ROOT / "coordination/submissions/queue01", command("submit"))
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["state"] == "submitted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
