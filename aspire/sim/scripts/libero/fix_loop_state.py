# SPDX-License-Identifier: MIT
"""Persist development evidence for the shared LIBERO model worker.

This is a protocol ledger, not a security sandbox. Only the runner records
trials; model-authored reports cannot turn an incomplete campaign into a result.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path


class ProtocolError(ValueError):
    pass


def code_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def valid_program(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(isinstance(node, ast.Call) for node in ast.walk(tree))


def minimal_fallback(source: str) -> bool:
    """The runbook permits a single legal observation when every program crashes."""
    try:
        return ast.dump(ast.parse(source)) == ast.dump(ast.parse("get_observation()"))
    except SyntaxError:
        return False


def report_errors(path: Path, headings: tuple[str, ...]) -> list[str]:
    if not path.is_file():
        return [f"missing {path.name}"]
    text = path.read_text()
    if re.search(r"\bplaceholder\b|\blorem ipsum\b|evidence gathering in progress", text, re.I):
        return [f"{path.name} contains placeholder text"]
    errors = []
    for heading in headings:
        match = re.search(rf"^##\s+{re.escape(heading)}\s*:?(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
        if not match or not match.group(1).strip():
            errors.append(f"{path.name} needs a nonempty '{heading}' section (use 'none' if applicable)")
    return errors


class Stage1State:
    VERSION = 1
    REPAIR_LIMIT = 3

    def __init__(self, task_dir: Path, identity: dict, *, resume: bool = False):
        self.task_dir = task_dir.resolve()
        self.path = self.task_dir / "development_state.json"
        self.seeds = identity["dev_seeds"]
        if not self.seeds or len(set(self.seeds)) != len(self.seeds) or any(s <= 50 for s in self.seeds):
            raise ProtocolError("development seeds must be distinct and outside held-out seeds 1–50")
        if self.path.exists():
            if not resume:
                raise ProtocolError(f"campaign already exists; use --resume: {self.path}")
            self.data = json.loads(self.path.read_text())
            if self.data.get("version") != self.VERSION or self.data.get("identity") != identity:
                raise ProtocolError("resume requires the same protocol, model, configuration, and prompts")
        else:
            if resume:
                raise ProtocolError("no development ledger to resume; legacy campaigns cannot be adopted")
            if self.task_dir.exists() and any(self.task_dir.iterdir()):
                raise ProtocolError("use a fresh task directory; existing experiment artifacts are preserved")
            self.task_dir.mkdir(parents=True, exist_ok=True)
            self.data = {
                "version": self.VERSION, "identity": identity, "trials": [],
                "model_steps": 0, "model_seconds": 0.0, "model_served": {},
                "usage": {}, "stage1_complete": False,
            }
            self.save()

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(self.path)

    def records(self, phase: str, seed: int | None = None) -> list[dict]:
        return [r for r in self.data["trials"] if r["phase"] == phase and (seed is None or r["seed"] == seed)]

    def begin_trial(self, phase: str, seed: int, source: str) -> dict:
        if self.data["stage1_complete"]:
            raise ProtocolError("Stage 1 is frozen")
        if seed not in self.seeds:
            raise ProtocolError(f"seed {seed} is outside the development partition")
        if any(r["status"] != "complete" for r in self.data["trials"]):
            raise ProtocolError("a trial has incomplete infrastructure evidence; resolve it before continuing")
        if phase not in {"snapshot", "smoke", "initial", "repair"}:
            raise ProtocolError(f"unknown phase: {phase}")
        if not valid_program(source):
            raise ProtocolError("program must contain executable Python, not an empty file or prose")
        if phase in {"snapshot", "smoke"}:
            limit = 1 if phase == "snapshot" else self.data["identity"]["smoke_budget"]
            if seed != self.seeds[0] or len(self.records(phase)) >= limit or self.records("initial"):
                raise ProtocolError(f"{phase} is bounded to {limit} invocation(s) on the first seed before initial evaluation")
        if phase == "initial":
            snapshot = self.records("snapshot")
            if not snapshot or snapshot[0]["status"] != "complete":
                raise ProtocolError("capture the initial scene before grading the initial program")
            if self.data["identity"]["smoke_budget"] and not self.records("smoke"):
                raise ProtocolError("perform the bounded crash smoke before the initial batch")
            if self.records("initial", seed):
                raise ProtocolError(f"initial seed {seed} was already run")
            if self.records("initial") and self.records("initial")[0]["code_sha256"] != code_hash(source):
                raise ProtocolError("the initial program is frozen for the complete initial batch")
        if phase == "repair":
            if len(self.records("initial")) != len(self.seeds):
                raise ProtocolError("complete all initial seeds before repairing")
            if self.records("initial", seed)[0]["task_completed"]:
                raise ProtocolError(f"initial seed {seed} already passed; repair only failed seeds")
            previous = self.records("repair", seed)
            if any(r["task_completed"] for r in previous):
                raise ProtocolError(f"seed {seed} already has a successful repair")
            if len(previous) >= self.REPAIR_LIMIT:
                raise ProtocolError(f"seed {seed} exhausted its three repairs; move to another seed")
        attempt = len(self.records(phase, seed)) + 1
        relative = Path("development") / phase / f"seed_{seed}" / f"attempt_{attempt}"
        directory = self.task_dir / relative
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "code.py").write_text(source)
        record = {
            "phase": phase, "seed": seed, "attempt": attempt, "status": "running",
            "directory": str(relative), "code_sha256": code_hash(source),
        }
        self.data["trials"].append(record)
        self.save()
        return record

    def finish_trial(self, record: dict, *, result: dict | None, exit_code: int | None, error: str = "") -> None:
        record.update(process_exit_code=exit_code, error=error)
        if result is not None and exit_code == 0:
            record.update(result, status="complete")
        else:
            record["status"] = "infrastructure_error"
        self.save()

    def progress(self) -> dict:
        missing_initial, repair_pending, blocked_notes = [], [], []
        for seed in self.seeds:
            initial = self.records("initial", seed)
            if not initial or initial[0]["status"] != "complete":
                missing_initial.append(seed)
                continue
            if initial[0]["task_completed"]:
                continue
            repairs = self.records("repair", seed)
            if any(r.get("task_completed") for r in repairs):
                continue
            if len(repairs) < self.REPAIR_LIMIT:
                repair_pending.append(seed)
            else:
                path = self.task_dir / "attempts" / f"seed_{seed}_BLOCKED.md"
                if report_errors(path, ("Root Cause", "Details", "What Was Tried")):
                    blocked_notes.append(seed)
        latest = self.data["trials"][-1] if self.data["trials"] else None
        return {
            "missing_initial_seeds": missing_initial,
            "pending_repair_seeds": repair_pending,
            "repairs_used_per_seed": {str(s): len(self.records("repair", s)) for s in self.seeds},
            "blocked_notes_required": blocked_notes,
            "infrastructure_errors": [r["directory"] for r in self.data["trials"] if r["status"] != "complete"],
            "latest_trial": latest,
            "evidence_ledger": str(self.path),
        }

    def candidates(self) -> dict[str, dict]:
        candidates: dict[str, dict] = {}
        for record in self.data["trials"]:
            if record["phase"] not in {"initial", "repair"} or record["status"] != "complete":
                continue
            row = candidates.setdefault(record["code_sha256"], {"passes": set(), "crashes": 0, "trials": []})
            if record["task_completed"]:
                row["passes"].add(record["seed"])
            row["crashes"] += int(record["sandbox_rc"] != 0)
            row["trials"].append(record)
        return candidates

    def completion_errors(self, *, working_code: Path) -> list[str]:
        progress = self.progress()
        errors = [f"{key}: {progress[key]}" for key in (
            "missing_initial_seeds", "pending_repair_seeds", "blocked_notes_required", "infrastructure_errors"
        ) if progress[key]]
        analysis = self.task_dir / "task_analysis.md"
        if not analysis.is_file() or not analysis.read_text().strip():
            errors.append("missing task_analysis.md")
        snapshot = self.records("snapshot")
        if not snapshot or snapshot[0]["status"] != "complete":
            errors.append("missing completed scene snapshot")
        errors += report_errors(self.task_dir / "findings.md", (
            "Root causes observed", "What fixed them", "Generalizable patterns", "Blocked seeds"
        ))
        fix = self.task_dir / "fix_code.py"
        if not fix.is_file() or not valid_program(fix.read_text()):
            return errors + ["missing executable fix_code.py"]
        source = fix.read_text()
        if not working_code.is_file() or working_code.read_text() != source:
            errors.append("working_codes copy must match fix_code.py")
        candidates = self.candidates()
        digest = code_hash(source)
        all_crashed = bool(candidates) and all(
            all(r["sandbox_rc"] != 0 for r in row["trials"]) for row in candidates.values()
        )
        if all_crashed and minimal_fallback(source):
            return errors
        if digest not in candidates:
            return errors + ["final program must match a development-tested snapshot; test synthesis within the repair budget"]
        if not any(r.get("task_completed") for r in self.records("repair")):
            best_score = max((len(row["passes"]), -row["crashes"]) for row in candidates.values())
            selected = candidates[digest]
            if (len(selected["passes"]), -selected["crashes"]) < best_score:
                errors.append("no repair succeeded: select most development successes, then fewer crashes")
        return errors

    def final_coverage(self, source: str) -> list[dict]:
        return [r for r in self.data["trials"] if r["phase"] in {"initial", "repair"}
                and r["status"] == "complete" and r["code_sha256"] == code_hash(source)]
