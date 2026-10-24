#!/usr/bin/env python3
"""Outer coordinator CLI for a supervised repair study (never a solver tool).

Subcommands, all read-only toward the runtime, ledger and cell files; none
starts a simulator, model or service:

* `queue --case C [--case C ...]` — per cell: campaign status, round, state, the
  evidence digest awaiting review and the exact review path expected.
* `export-review-request --case C --out FILE` — recompute the closed round's
  evidence digest from the ledger, check it equals the recorded one, and write
  the request (per-seed public development summary + references) to FILE.
* `approve-review --case C --round R --decision continue|freeze|stop
  --analysis-file A --guidance-file G --reviewer TEXT` — as root only: write the
  immutable `round_R.review.json` into the case's root-owned review directory,
  bound to cell, study, round and evidence digest. Analysis and guidance are
  read from files, never assembled in a shell. An existing review is never
  overwritten.

The campaign consumes the review; this CLI never edits the ledger. See
docs/streaming-supervised-repair.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import streaming_fixloop_protocol as sp  # noqa: E402
import streaming_fixloop_rounds as rounds  # noqa: E402


class ReviewRefused(sp.ProtocolError):
    pass


def open_cell(case_path: Path) -> tuple[dict, Path, dict, dict]:
    case, _, task_dir = sp.load_case(case_path)
    if "supervised_repair" not in case:
        raise ReviewRefused("this case declares no supervised_repair study")
    ledger_path = task_dir / "development_state.json"
    if not ledger_path.is_file():
        raise ReviewRefused(f"no development ledger yet: {ledger_path}")
    data = json.loads(ledger_path.read_text())
    if data["identity"].get("cell") != case["id"] or \
            (data.get("supervised") or {}).get("study_id") != case["supervised_repair"]["study_id"]:
        raise ReviewRefused("the ledger belongs to another cell or study")
    campaign = Path(case["control"]).resolve() / "campaign_state.json"
    state = json.loads(campaign.read_text()) if campaign.is_file() else {}
    return case, task_dir, data, state


def queue_entry(case_path: Path) -> dict:
    try:
        case, task_dir, data, state = open_cell(case_path)
    except (sp.ProtocolError, OSError, ValueError) as exc:
        return {"case": str(case_path), "error": str(exc)}
    sup = data["supervised"]
    r = sup["current_round"]
    record = sup["rounds"][r - 1]
    awaiting = sup["state"] == "awaiting_review"
    return {"case": str(case_path), "cell": case["id"], "study_id": sup["study_id"],
            "campaign_status": state.get("status"), "blocker": state.get("blocker"),
            "state": sup["state"], "round": r, "closure": sup.get("closure"),
            "awaiting_review": awaiting,
            "evidence_sha256": record.get("evidence_sha256") if awaiting else None,
            "review_path": str(rounds.review_path(case, r)) if awaiting else None,
            "review_present": rounds.review_path(case, r).exists() if awaiting else None,
            "rejected_reviews": (state.get("supervised") or {}).get("rejected_reviews", []),
            "charged_attempts_used": sum(1 for t in data["trials"] if t.get("spends_retry")),
            "study_successes": len(rounds.study_successes(data)),
            "deadline": data["phase_clock"]["development"]["deadline"],
            "seconds_remaining": data["phase_clock"]["development"]["deadline"] - time.time()}


def awaiting(case_path: Path, round_: int | None = None) -> tuple[dict, Path, dict, dict]:
    case, task_dir, data, _ = open_cell(case_path)
    sup = data["supervised"]
    r = sup["current_round"]
    if sup["state"] != "awaiting_review" or (round_ is not None and round_ != r):
        raise ReviewRefused(f"cell {case['id']} is {sup['state']} in round {r}; "
                            f"round {round_ if round_ is not None else r} is not awaiting review")
    evidence = rounds.round_evidence(data, task_dir, r)
    if evidence["evidence_sha256"] != sup["rounds"][r - 1]["evidence_sha256"]:
        raise ReviewRefused(f"round {r} evidence changed after it was closed; do not approve")
    return case, task_dir, data, evidence


def export_request(case_path: Path, out: Path) -> dict:
    case, _, data, evidence = awaiting(case_path)
    out = out.resolve()
    review_dir = Path(case["supervised_repair"]["review_dir"]).resolve()
    if out.is_relative_to(Path(case["control"]).resolve()) or out.is_relative_to(review_dir):
        raise ReviewRefused("write the request outside the cell control and review directories")
    request = {"schema": "aspire-supervised-repair-review-request-v1", "cell": case["id"],
               "study_id": evidence["study_id"], "round": evidence["round"],
               "evidence_sha256": evidence["evidence_sha256"],
               "review_path": str(rounds.review_path(case, evidence["round"])),
               "decisions": list(rounds.DECISIONS) if evidence["round"] < rounds.ROUNDS
               else ["freeze", "stop"], "evidence": evidence,
               "budget": {"charged_attempts_used": evidence["charged_attempts_used"],
                          "charged_attempts_limit": 45}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(request, indent=2) + "\n")
    return {k: request[k] for k in ("cell", "study_id", "round", "evidence_sha256", "review_path")}


def approve(case_path: Path, round_: int, decision: str, analysis: Path, guidance: Path,
            reviewer: str) -> dict:
    if os.geteuid() != rounds.REVIEW_OWNER_UID:
        raise ReviewRefused("approve-review runs as the outer root user only")
    case, _, data, evidence = awaiting(case_path, round_)
    texts = {}
    for name, path in (("analysis", analysis), ("next_round_guidance", guidance)):
        text = Path(path).read_text()
        if not text.strip():
            raise ReviewRefused(f"{name} file {path} is empty; write the real analysis first")
        texts[name] = text
    if decision not in rounds.DECISIONS or (decision == "continue" and round_ >= rounds.ROUNDS):
        raise ReviewRefused(f"decision {decision!r} is not available in round {round_}")
    if decision == "freeze":
        selected = (data.get("selected") or {}).get("bundle_sha256")
        if selected not in {s["bundle_sha256"] for s in rounds.study_successes(data)}:
            raise ReviewRefused("freeze needs a recorded selection whose bundle has its own oracle "
                                "success in this study")
    target = rounds.review_path(case, round_)
    problems = (rounds._owner_errors(target.parent, "review directory", directory=True)
                + rounds._owner_errors(target.parent.parent, "review directory parent", directory=True))
    if problems:
        raise ReviewRefused("; ".join(problems))
    if target.exists():
        raise ReviewRefused(f"{target} already exists; a round has exactly one immutable review")
    review = {"schema": rounds.REVIEW_SCHEMA, "study_id": evidence["study_id"], "cell": case["id"],
              "round": round_, "decision": decision, **texts,
              "evidence_sha256": evidence["evidence_sha256"], "reviewer": reviewer,
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    temporary = target.with_name(f".{target.name}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(review, indent=2, ensure_ascii=False) + "\n")
    os.chown(temporary, rounds.REVIEW_OWNER_UID, target.parent.stat().st_gid)
    os.chmod(temporary, 0o640)
    os.link(temporary, target)  # fails if a review appeared meanwhile; never overwrites
    os.unlink(temporary)
    _, sha, errors = rounds.review_errors(case, data, round_, target)
    if errors:
        target.unlink()
        raise ReviewRefused("; ".join(errors))
    return {"review": str(target), "sha256": sha, "cell": case["id"], "round": round_,
            "decision": decision, "evidence_sha256": evidence["evidence_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("queue").add_argument("--case", type=Path, action="append", required=True)
    export = sub.add_parser("export-review-request")
    export.add_argument("--case", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    approval = sub.add_parser("approve-review")
    approval.add_argument("--case", type=Path, required=True)
    approval.add_argument("--round", type=int, required=True)
    approval.add_argument("--decision", choices=rounds.DECISIONS, required=True)
    approval.add_argument("--analysis-file", type=Path, required=True)
    approval.add_argument("--guidance-file", type=Path, required=True)
    approval.add_argument("--reviewer", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "queue":
            result = {"cells": [queue_entry(c) for c in args.case]}
        elif args.action == "export-review-request":
            result = export_request(args.case, args.out)
        else:
            result = approve(args.case, args.round, args.decision, args.analysis_file,
                             args.guidance_file, args.reviewer)
    except (sp.ProtocolError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
