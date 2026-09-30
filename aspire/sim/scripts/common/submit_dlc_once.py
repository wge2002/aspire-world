#!/usr/bin/env python3
"""Journal one approved DLC submission; an uncertain outcome never permits retry.

This is an operations helper, not an experiment worker. The caller must finish
preflight, check existing jobs, and obtain the applicable user authorization.
Keep the same receipt directory for the lifetime of the experiment group.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time


def submit_once(directory: Path, command: list[str], *, timeout: int = 180,
                runner=subprocess.run) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    claim = directory / "submission-attempt.json"
    attempt = {"state": "claimed", "started_at": time.time(), "argv": command}
    # O_EXCL claims the shared CPFS path before any paid API call. Retain this
    # file even on failure or interruption: CLI failure is not proof that the
    # server rejected CreateJob, so retry must never be automatic.
    with claim.open("x") as stream:
        json.dump(attempt, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    receipt = {"state": "uncertain", "started_at": attempt["started_at"]}
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout)
        (directory / "submission.stdout.log").write_text(result.stdout or "")
        (directory / "submission.stderr.log").write_text(result.stderr or "")
        ids = sorted(set(re.findall(r"\bdlc[a-z0-9]{10,}\b", result.stdout or "")))
        receipt.update(exit_code=result.returncode, job_ids=ids)
        if result.returncode == 0 and len(ids) == 1:
            receipt.update(state="submitted", job_id=ids[0])
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        receipt["finished_at"] = time.time()
        with (directory / "submission-receipt.json").open("x") as stream:
            json.dump(receipt, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a previously approved submission command is required")
    try:
        receipt = submit_once(args.receipt_dir, command)
    except FileExistsError:
        print("Submission already claimed. Inspect its receipt and platform state; do not resubmit.")
        return 2
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["state"] == "submitted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
