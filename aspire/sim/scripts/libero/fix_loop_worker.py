# SPDX-License-Identifier: MIT
"""Shared OpenAI-compatible model worker for LIBERO Fix Loop Stage 0/1.

The worker is deliberately task-agnostic scaffolding: it owns the tool plumbing,
transcript/provenance logging, and the protocol guardrails from
``.claude/libero/fix-loop/subagent-prompt.md``. Every task-specific decision
(scene analysis, initial program, diagnosis, repairs, synthesis, findings) is
produced by the served model, never by this file.

The structured trial tool enforces seed partitions and per-seed budgets.
File/command checks catch known protocol violations; arbitrary shell/Python
execution is not a hardened security boundary. Use an isolated checkout.
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

from fix_loop_state import Stage1State, ProtocolError, code_hash

FORBIDDEN_PATTERNS = [
    r"\.sim\b",
    r"sim\.data\.",
    r"sim\.model\.",
    r"sim\.forward\s*\(",
    r"body_xpos",
    r"get_site_xpos",
    r"set_joint_qpos",
    r"body_name2id",
    r"joint_id2name",
    r"\bqpos\b",
    r"parsed_problem",
    r"_eval_predicate",
    r"obj_body_id",
    r"_step_once",
    r"env\.handle\.env",
    r"\.bddl\b",
    r"\.urdf\b",
]

HELD_OUT_BLOCK = re.compile(r"run_fix_loop_validation\.py|libero_fix_loop_eval")
TRIAL_RE = re.compile(r"--args\.trial[= ]+(\d+)")
SKILLS_DIR = ".claude/libero/skills"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Inspect files from the aspire/sim repo root. Use run_trial for all replays; "
                "use this for listing files and parsing trace.json. Do not modify runner state or start simulators."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command."},
                    "timeout": {
                        "type": "integer",
                        "description": "Inspection command timeout in seconds (default 60, max 300).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file. Returns numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "1-based start line."},
                    "limit": {"type": "integer", "description": "Max lines (default 400)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file, creating parent directories.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_text",
            "description": (
                "Replace one exact text fragment in an existing UTF-8 file. Use this for small "
                "candidate_code.py revisions when rewriting the whole file is unnecessary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copy a tested UTF-8 program to its required final path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": (
                "Attach an image (scene snapshot or keyframe) to the conversation so you can "
                "actually look at it. Use this for visual feedback on failures."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call once Step 5 is complete, with the required return summary.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25) :]
    return f"{head}\n\n...[{len(text) - len(head) - len(tail)} chars elided]...\n\n{tail}"


class Guardrail(Exception):
    """Raised when a tool call would violate the benchmark protocol."""


def check_command(command: str, dev_seeds: range, repo: Path) -> None:
    checkout = repo.parents[1].resolve()
    if re.search(r"\bfind\s+/(?:\s|$)", command):
        raise Guardrail("BLOCKED: filesystem-wide searches are forbidden; stay inside this checkout.")
    for raw_path in re.findall(r"/(?:mnt|home)/[^\s;|&\"']+", command):
        candidate = Path(raw_path.rstrip(",:)]}"))
        try:
            candidate.resolve(strict=False).relative_to(checkout)
        except ValueError:
            raise Guardrail(
                "BLOCKED: this command references a path outside the isolated experiment "
                f"checkout ({candidate}). Use only {checkout}."
            )
    if HELD_OUT_BLOCK.search(command):
        raise Guardrail(
            "BLOCKED: held-out evaluation is the coordinator's job. You may not run "
            "run_fix_loop_validation.py or touch outputs/libero_fix_loop_eval."
        )
    for raw in TRIAL_RE.findall(command):
        trial = int(raw)
        if trial not in dev_seeds:
            raise Guardrail(
                f"BLOCKED: trial {trial} is outside development seeds "
                f"{dev_seeds.start}-{dev_seeds.stop - 1}. Held-out seeds are forbidden."
            )
    if re.search(rf"(>|>>|tee|cp|mv|rm|sed -i|touch)\s+\S*{re.escape(SKILLS_DIR)}", command):
        raise Guardrail(
            "BLOCKED: the shared skill library is read-only for you. Put reusable knowledge "
            "in findings.md; only the coordinator edits skills."
        )
    if re.search(r"\bgit\s+(push|commit|reset|checkout|clean)\b", command):
        raise Guardrail("BLOCKED: git state changes are not part of this task.")


def check_write(path: Path, content: str, repo: Path) -> None:
    try:
        rel = path.resolve().relative_to(repo.resolve())
    except ValueError:
        if not str(path.resolve()).startswith("/tmp/"):
            raise Guardrail(f"BLOCKED: writes must stay inside the repo or /tmp; got {path}")
        rel = Path("tmp") / path.name
    if SKILLS_DIR in str(rel):
        raise Guardrail(
            "BLOCKED: the shared skill library is read-only for you. Use findings.md instead."
        )
    if path.suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            raise Guardrail(f"REJECTED: Python syntax error: {exc}") from exc
        hits = sorted({p for p in FORBIDDEN_PATTERNS if re.search(p, content)})
        if hits:
            raise Guardrail(
                "REJECTED: this program uses forbidden simulator ground-truth APIs "
                f"(matched: {hits}). Results would be invalid. Rewrite it using only the "
                "allowed perception/motion APIs and write it again."
            )


TOOLS += [
    {"type": "function", "function": {
        "name": "run_trial",
        "description": "Run one recorded development trial. Each seed has its own three-repair budget. The runner preserves code, logs, traces, and results in a new directory.",
        "parameters": {"type": "object", "properties": {
            "phase": {"type": "string", "enum": ["snapshot", "smoke", "initial", "repair"]},
            "seed": {"type": "integer"}, "code_path": {"type": "string"},
        }, "required": ["phase", "seed"]},
    }},
    {"type": "function", "function": {
        "name": "get_state", "description": "Read recorded per-seed results, remaining budgets, and evidence paths. Always available.",
        "parameters": {"type": "object", "properties": {}},
    }},
]


def complete_turns(messages: list[dict]) -> list[list[dict]]:
    """An assistant reply and all of its tool results/images form one turn."""
    turns: list[list[dict]] = []
    for message in messages:
        if message["role"] == "assistant":
            turns.append([message])
        elif turns:
            turns[-1].append(message)
    return turns


class Worker:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.repo = Path(args.repo).resolve()
        self.dev_seeds = range(args.dev_seed_start, args.dev_seed_end + 1)
        self.task_dir = self.repo / "outputs/libero_fix_loop" / args.suite / args.task
        self.working_code = self.repo / "outputs/working_codes" / f"{args.suite}_{args.task}_fix.py"
        system, assignment = self.build_prompt()
        identity = {
            "suite": args.suite, "task": args.task, "dev_seeds": list(self.dev_seeds),
            "repair_limit": 3, "smoke_budget": args.smoke_budget,
            "model": args.model, "model_family": args.model_family,
            "reasoning_effort": args.reasoning_effort, "temperature": args.temperature,
            "max_tokens": args.max_tokens, "keep_turns": args.keep_turns,
            "max_steps": args.max_steps, "max_hours": args.max_hours,
            "config_sha256": code_hash((self.repo / args.config).read_text()),
            "prompt_sha256": code_hash(system + assignment),
            "framework_sha256": code_hash(Path(__file__).read_text() + Path(__file__).with_name("fix_loop_state.py").read_text()),
        }
        self.state = Stage1State(self.task_dir, identity, resume=args.resume)
        self.transcript = self.task_dir / f"model_transcript_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}.jsonl"
        self.guardrail_hits: list[str] = []
        self.history_limit = args.keep_turns

    def log(self, record: dict) -> None:
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with self.transcript.open("a") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def runtime_env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items()
               if not re.search(r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|SESSION_TOKEN|^HF_TOKEN$|HUGGING_FACE_HUB_TOKEN", key)}
        env.update(
            MUJOCO_GL="egl", CUDA_VISIBLE_DEVICES=self.args.cuda_visible_devices or str(self.args.gpu),
            TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1", PYTHONPATH=self.args.python_root,
            PYTHON_ROOT=self.args.python_root, ASPIRE_ROOT=str(self.repo),
            SAM3_SERVICE_URL=f"http://127.0.0.1:{self.args.sam3_port}",
            GRASPNET_SERVICE_URL=f"http://127.0.0.1:{self.args.graspnet_port}",
            PYROKI_SERVICE_URL=f"http://127.0.0.1:{self.args.pyroki_port}",
        )
        if self.args.egl_device_id is not None:
            env["MUJOCO_EGL_DEVICE_ID"] = str(self.args.egl_device_id)
        return env

    def path_for_read(self, raw: str) -> Path:
        path = (self.repo / raw).resolve()
        try:
            path.relative_to(self.repo.parents[1])
        except ValueError as exc:
            raise Guardrail("reads must stay inside this isolated checkout") from exc
        if HELD_OUT_BLOCK.search(str(path)) or path.suffix in {".bddl", ".xml", ".urdf"}:
            raise Guardrail("held-out results and simulator asset files are not development evidence")
        if path.is_relative_to(self.repo / "outputs") and not (
            path.is_relative_to(self.task_dir) or path == self.working_code
        ):
            raise Guardrail("other tasks/campaigns are not part of this worker's evidence")
        return path

    def write_file(self, raw: str, content: str) -> str:
        path = self.path_for_read(raw)
        check_write(path, content, self.repo)
        # The model edits its own reports/programs, never executable infrastructure or receipts.
        if not (path.is_relative_to(self.task_dir) or path == self.working_code):
            raise Guardrail("write task programs/reports in TASK_DIR, or the required working_codes copy")
        if path.is_relative_to(self.task_dir / "development") or path.name in {
            "development_state.json", "stage1_result.json", "validation_result.json"
        } or path.name.startswith("model_transcript_"):
            raise Guardrail("runner-owned evidence is read-only")
        if self.state.data["stage1_complete"]:
            raise Guardrail("Stage 1 is frozen")
        if path.name == "initial_code.py" and self.state.records("initial"):
            if code_hash(content) != self.state.records("initial")[0]["code_sha256"]:
                raise Guardrail("initial_code.py is frozen after the first initial trial")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"wrote {path.relative_to(self.repo)}"

    def read_file(self, raw: str, offset: int = 1, limit: int = 200) -> str:
        path = self.path_for_read(raw)
        lines = path.read_text(errors="replace").splitlines()
        start = max(0, offset - 1)
        output, size = [], 0
        for index in range(start, min(len(lines), start + max(1, limit))):
            row = f"{index + 1}\t{lines[index]}"
            if output and size + len(row) > self.args.max_tool_chars:
                break
            output.append(row)
            size += len(row)
        next_line = start + len(output) + 1
        suffix = f"\nContinue with offset={next_line}." if next_line <= len(lines) else "\nEnd of file."
        return "\n".join(output) + suffix

    def run_bash(self, command: str, timeout: int = 60) -> str:
        check_command(command, self.dev_seeds, self.repo)
        if re.search(r"replay_trial|scene_snapshot|run_libero_batch|cap\.envs\.launch|development_state\.json.*(?:>|write)|\brm\b", command):
            raise Guardrail("use run_trial for simulator execution; do not mutate runner evidence")
        process = subprocess.run(
            ["bash", "-c", command], cwd=self.repo, env=self.runtime_env(),
            capture_output=True, text=True, timeout=min(max(timeout, 1), 300),
        )
        output = (process.stdout or "") + (process.stderr or "")
        if len(output) > self.args.max_tool_chars:
            directory = self.task_dir / "inspection_outputs"
            directory.mkdir(exist_ok=True)
            path = directory / f"{uuid.uuid4().hex[:12]}.txt"
            path.write_text(output)
            output = truncate(output, self.args.max_tool_chars) + f"\nFull output: {path.relative_to(self.repo)} (read_file supports pagination)."
        return f"exit={process.returncode}\n{output}"

    def trial_command(self, record: dict) -> list[str]:
        directory = self.task_dir / record["directory"]
        return [
            str(self.repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
            "--args.suite", self.args.suite, "--args.task", self.args.task,
            "--args.trial", str(record["seed"]), "--args.model", self.args.model,
            "--args.replay-code", str(directory / "code.py"),
            "--args.config", self.args.config, "--args.output-dir", str(directory / "results"),
        ]

    def run_trial(self, phase: str, seed: int, code_path: str | None = None) -> str:
        if phase == "snapshot":
            source = (self.repo / "scripts/libero/scene_snapshot.py").read_text()
        else:
            if not code_path:
                raise ProtocolError("code_path is required for smoke/initial/repair trials")
            path = self.path_for_read(code_path)
            if not path.is_relative_to(self.task_dir):
                raise ProtocolError("replay programs must be generated inside this TASK_DIR")
            if phase == "initial" and path != self.task_dir / "initial_code.py":
                raise ProtocolError("initial trials must use TASK_DIR/initial_code.py")
            source = path.read_text()
            check_write(path, source, self.repo)
        record = self.state.begin_trial(phase, seed, source)
        directory = self.task_dir / record["directory"]
        log = directory / "replay.log"
        env = self.runtime_env()
        env["SNAPSHOT_DIR"] = str(self.task_dir)
        env.pop("ASPIRE_SAM3_PROMPTS", None)
        exit_code, error = None, ""
        try:
            with log.open("w") as stream:
                process = subprocess.run(
                    self.trial_command(record), cwd=self.repo, env=env, stdout=stream,
                    stderr=subprocess.STDOUT, text=True, timeout=self.args.trial_timeout,
                )
                exit_code = process.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"{type(exc).__name__}: {exc}"
        pattern = re.compile(rf"trial_{seed:02d}_sandboxrc_(\d+)_reward_([\d.]+)_taskcompleted_(\d+)")
        matches = [(p, pattern.fullmatch(p.name)) for p in (directory / "results").rglob("trial_*") if p.is_dir()]
        matches = [(p, m) for p, m in matches if m]
        result = None
        if len(matches) == 1:
            path, match = matches[0]
            result = {"sandbox_rc": int(match[1]), "reward": float(match[2]),
                      "task_completed": int(match[3]), "trial_dir": str(path.relative_to(self.task_dir))}
            # Return the actual exception alongside the outcome, not a hard-coded diagnosis.
            summary = path / "summary.txt"
            evidence = summary.read_text(errors="replace") if summary.exists() else log.read_text(errors="replace")
            failures = [line for line in evidence.splitlines() if re.search(r"\b\w*(?:Error|Exception):", line)]
            error = "\n".join(failures[-5:])
        elif not error:
            error = f"expected one trial artifact, found {len(matches)}; inspect {log}"
        if phase == "snapshot" and (not result or result["sandbox_rc"] or not all(
            (self.task_dir / name).exists() for name in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg")
        )):
            result, error = None, "scene snapshot did not produce both images; inspect its replay.log"
        self.state.finish_trial(record, result=result, exit_code=exit_code, error=error)
        return json.dumps(record, ensure_ascii=False)

    def dispatch(self, name: str, raw_args: str) -> tuple[str, dict | None]:
        self.log({"type": "tool_call", "tool": name, "arguments": raw_args})
        try:
            args = json.loads(raw_args or "{}")
            if name == "read_file":
                return self.read_file(args["path"], args.get("offset", 1), args.get("limit", 200)), None
            if name == "write_file":
                return self.write_file(args["path"], args["content"]), None
            if name == "replace_text":
                source = self.path_for_read(args["path"]).read_text()
                if source.count(args["old"]) != 1:
                    raise ProtocolError("old text must occur exactly once")
                return self.write_file(args["path"], source.replace(args["old"], args["new"], 1)), None
            if name == "copy_file":
                return self.write_file(args["destination"], self.path_for_read(args["source"]).read_text()), None
            if name == "bash":
                return self.run_bash(args["command"], args.get("timeout", 60)), None
            if name == "get_state":
                return json.dumps(self.state.progress(), ensure_ascii=False), None
            if name == "run_trial":
                return self.run_trial(args["phase"], args["seed"], args.get("code_path")), None
            if name == "view_image":
                path = self.path_for_read(args["path"])
                mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                encoded = base64.b64encode(path.read_bytes()).decode()
                return f"Image: {args['path']}", {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
            raise ProtocolError(f"unknown tool: {name}")
        except (Guardrail, ProtocolError) as exc:
            self.guardrail_hits.append(str(exc))
            return f"PROTOCOL: {exc}", None
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}", None

    def build_prompt(self) -> tuple[str, str]:
        paths = ["CLAUDE.md", ".claude/libero/fix-loop/subagent-prompt.md",
                 ".claude/libero/fix-loop/skills/task-exploration.md", ".claude/libero/api-reference.md"]
        parts = [f"SOURCE: {path}\n{(self.repo / path).read_text()}" for path in paths]
        system = (
            "You are the ASPIRE Fix Loop worker. Make all task-specific decisions yourself. "
            "Use observation-driven code and follow the attached canonical protocol.\n"
            "The runner provides structured run_trial instead of shell replay commands. "
            "Use phase=snapshot once to run the canonical snapshot script; inspect both images. "
            "Use smoke only for the configured bounded crash check before the frozen initial batch. "
            "Use initial once for each development seed, then at most three repairs PER failed seed. "
            "On three failures write that seed's BLOCKED note and MOVE TO THE NEXT SEED. "
            "All scene capture and simulator execution must use run_trial. Bash is for inspection. "
            "Do not modify the runner, its ledger, frozen initial code, or trial snapshots.\n"
            "API functions are injected as bare globals in robot programs; do not import them from libero.libero. "
            "Read the allowed API source for exact signatures. Keep notes.md with your current hypothesis, "
            "API facts and evidence paths; notes and factual per-seed state survive context trimming/restarts. "
            "All development evidence reads stay available at every phase. Never inspect another campaign, "
            "baseline solution, held-out result, simulator ground truth, or simulator asset.\n"
            "Before finishing, choose a generalizable tested code snapshot. If no repair succeeds, "
            "select most development successes, then fewer crashes, as the runbook specifies. "
            "Test any synthesis within the existing repair budget; do not add extra trials. "
            "If every candidate crashes, the runbook permits just get_observation() as a legal fallback. "
            "Write findings with Root causes observed, What fixed them, Generalizable patterns, and Blocked seeds "
            "sections; use none when appropriate. A placeholder does not complete the protocol.\n\n"
            + "\n\n".join(parts)
        )
        assignment = json.dumps({
            "suite": self.args.suite, "task": self.args.task, "gpu": self.args.gpu,
            "TASK_DIR": str(self.task_dir.relative_to(self.repo)), "dev_seeds": list(self.dev_seeds),
            "smoke_budget": self.args.smoke_budget, "held_out": "coordinator only; seeds 1–50 forbidden",
            "perception_ports": [self.args.sam3_port, self.args.graspnet_port, self.args.pyroki_port],
        }, ensure_ascii=False)
        return system, assignment

    def compact(self, messages: list[dict], keep: int | None = None) -> list[dict]:
        turns = complete_turns(messages[2:])
        # Replace the old runner-state message, never accumulate stale diagnoses.
        for turn in turns:
            turn[:] = [m for m in turn if not (m["role"] == "user" and isinstance(m.get("content"), str)
                                               and m["content"].startswith("[Runner state]"))]
        notes = self.task_dir / "notes.md"
        memory = {"recorded_progress": self.state.progress(),
                  "remaining_model_steps": self.args.max_steps - self.state.data["model_steps"],
                  "model_notes": truncate(notes.read_text(), 4000) if notes.exists() else "No notes yet; save relevant findings in notes.md."}
        marker = {"role": "user", "content": "[Runner state]\n" + json.dumps(memory, ensure_ascii=False)}
        retained = turns[-(keep or self.history_limit):]
        return messages[:2] + [marker] + [message for turn in retained for message in turn]

    def request_payload(self, messages: list[dict]) -> dict:
        payload = {"model": self.args.model, "messages": messages, "tools": TOOLS,
                   "tool_choice": "auto", "temperature": self.args.temperature, "max_tokens": self.args.max_tokens}
        if self.args.model_family == "deepseek":
            effort = self.args.reasoning_effort
            template = {"thinking": effort not in {"none", "off", "minimal"}}
            if template["thinking"]:
                template["reasoning_effort"] = effort if effort in {"low", "max"} else "high"
            payload.update(top_p=0.95, chat_template_kwargs=template)
        else:
            payload.update(reasoning_effort=self.args.reasoning_effort,
                           chat_template_kwargs={"enable_thinking": True, "preserve_thinking": True})
        return payload

    def post(self, payload: dict) -> dict:
        request = urllib.request.Request(self.args.endpoint, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.args.request_timeout) as response:
            return json.load(response)

    def call_model(self, messages: list[dict]) -> dict:
        for attempt in range(self.args.max_retries):
            try:
                data = self.post(self.request_payload(messages))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                if exc.code == 400 and re.search(r"context length|context window|maximum.*tokens", detail, re.I):
                    count = len(complete_turns(messages[2:]))
                    if count <= 1:
                        raise ProtocolError("fixed prompt/current turn exceeds model context; stop and fix configuration before running more trials") from exc
                    self.history_limit = max(1, count // 2)
                    messages[:] = self.compact(messages, self.history_limit)
                    self.log({"type": "context_retry", "keep_complete_turns": self.history_limit,
                              "max_tokens": self.args.max_tokens})
                    continue
                if exc.code < 500 or attempt + 1 == self.args.max_retries:
                    raise RuntimeError(f"model HTTP {exc.code}: {detail[:500]}") from exc
                time.sleep(min(3 * (attempt + 1), 15))
            except (urllib.error.URLError, TimeoutError):
                if attempt + 1 == self.args.max_retries:
                    raise
                time.sleep(min(3 * (attempt + 1), 15))
        else:
            raise ProtocolError("model request exhausted context/transport retries")
        served = data.get("model")
        if served != self.args.model:
            raise ProtocolError(f"served model mismatch: expected {self.args.model}, received {served}")
        models = self.state.data["model_served"]
        models[served] = models.get(served, 0) + 1
        usage = self.state.data["usage"]
        usage["requests"] = usage.get("requests", 0) + 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[key] = usage.get(key, 0) + (data.get("usage", {}).get(key) or 0)
        self.state.save()
        self.log({"type": "response", "model": served, "usage": data.get("usage"), "choices": data.get("choices")})
        return data

    def run(self) -> int:
        if self.state.data["stage1_complete"]:
            print("Stage 1 already complete; no new model requests or trials.")
            return 0
        system, assignment = self.build_prompt()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": assignment}]
        summary, blocker = None, None
        self.log({"type": "start", "identity": self.state.data["identity"]})
        try:
            while self.state.data["model_steps"] < self.args.max_steps:
                if self.state.data["model_seconds"] >= self.args.max_hours * 3600:
                    blocker = "campaign wall-clock budget exhausted"
                    break
                started = time.monotonic()
                try:
                    messages = self.compact(messages)
                    self.state.data["model_steps"] += 1
                    self.state.save()
                    response = self.call_model(messages)
                    choice = response["choices"][0]
                    message = choice["message"]
                    assistant = {key: message[key] for key in ("role", "content", "tool_calls", "reasoning_content") if key in message}
                    assistant["role"] = "assistant"
                    messages.append(assistant)
                    images = []
                    for call in message.get("tool_calls") or []:
                        name, arguments = call["function"]["name"], call["function"].get("arguments", "{}")
                        if name == "finish":
                            errors = self.state.completion_errors(working_code=self.working_code)
                            try:
                                proposed = json.loads(arguments).get("summary", "")
                            except (json.JSONDecodeError, AttributeError):
                                proposed = ""
                            if not isinstance(proposed, str) or not proposed.strip():
                                errors.append("finish requires a summary")
                            result = "INCOMPLETE: " + "; ".join(errors) if errors else "complete"
                            image = None
                            if not errors:
                                summary = proposed.strip()
                        else:
                            result, image = self.dispatch(name, arguments)
                        self.log({"type": "tool_result", "tool": name, "result": result})
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                        if image:
                            images.extend([image, {"type": "text", "text": result}])
                        if summary is not None:
                            break
                    if images:
                        messages.append({"role": "user", "content": images})
                    if self.state.progress()["infrastructure_errors"]:
                        blocker = "trial infrastructure evidence is incomplete; inspect the ledger and replay.log"
                        break
                    if summary is not None:
                        break
                finally:
                    self.state.data["model_seconds"] += time.monotonic() - started
                    self.state.save()
        except Exception as exc:
            blocker = f"{type(exc).__name__}: {exc}"
            self.log({"type": "abort", "reason": blocker})
        return self.finalize(summary, blocker)

    def finalize(self, summary: str | None, blocker: str | None = None) -> int:
        errors = self.state.completion_errors(working_code=self.working_code)
        if not summary:
            errors.append(blocker or "model stopped before a valid finish")
        self.state.data["stage1_complete"] = not errors
        self.state.save()
        fix = self.task_dir / "fix_code.py"
        source = fix.read_text() if fix.exists() else ""
        result = {
            "schema_version": 2, "suite": self.args.suite, "task": self.args.task,
            "stage1_complete": not errors, "completion_errors": errors,
            "dev_seeds": list(self.dev_seeds), "model_requested": self.args.model,
            "model_served": self.state.data["model_served"], "usage": self.state.data["usage"],
            "code_sha256": code_hash(source) if source else None,
            "progress": self.state.progress(), "final_code_development_trials": self.state.final_coverage(source),
            "final_code_tested": bool(self.state.final_coverage(source)),
            "finish_summary": summary, "development_state": str(self.state.path),
        }
        (self.task_dir / "stage1_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if not errors else 1


def parse_args(argv: list[str] | None = None, *, family: str | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-family", choices=["deepseek", "qwen"], default=family or "deepseek")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--python-root", default=os.environ.get("PYTHON_ROOT", ""))
    parser.add_argument("--suite", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--cuda-visible-devices", default="")
    parser.add_argument("--egl-device-id", type=int)
    parser.add_argument("--dev-seed-start", type=int, default=51)
    parser.add_argument("--dev-seed-end", type=int, default=65)
    parser.add_argument("--smoke-budget", type=int, default=1)
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--request-timeout", type=int, default=1800)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=400)
    parser.add_argument("--max-hours", type=float, default=12.0)
    parser.add_argument("--keep-turns", type=int, default=8, help="Complete assistant/tool turns, not individual messages")
    parser.add_argument("--max-tool-chars", type=int, default=8000)
    parser.add_argument("--trial-timeout", type=int, default=900)
    parser.add_argument("--sam3-port", type=int, default=8214)
    parser.add_argument("--graspnet-port", type=int, default=8215)
    parser.add_argument("--pyroki-port", type=int, default=8216)
    parser.add_argument("--config", default="env_configs/libero/franka_libero_traced.yaml")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if any(Path(value).name != value or value in {".", ".."} for value in (args.suite, args.task)):
        parser.error("suite and task must be single path components")
    if min(args.keep_turns, args.max_tokens, args.max_retries, args.max_steps, args.max_hours, args.trial_timeout) <= 0 or args.smoke_budget < 0:
        parser.error("budgets must be positive (smoke-budget may be zero)")
    if args.cuda_visible_devices and args.cuda_visible_devices.split(",")[0] != str(args.gpu):
        parser.error("the first CUDA device must be the assigned simulation GPU")
    deepseek = args.model_family == "deepseek"
    args.endpoint = args.endpoint or f"http://127.0.0.1:{8120 if deepseek else 8121}/v1/chat/completions"
    args.model = args.model or ("deepseek-v4-flash-vision-exp" if deepseek else "qwen3.8-flash-next")
    args.reasoning_effort = args.reasoning_effort or ("max" if deepseek else "xhigh")
    args.python_root = args.python_root or str(Path(args.repo).resolve().parents[1])
    return args


def main(argv: list[str] | None = None, *, family: str | None = None) -> int:
    return Worker(parse_args(argv, family=family)).run()


if __name__ == "__main__":
    sys.exit(main())
