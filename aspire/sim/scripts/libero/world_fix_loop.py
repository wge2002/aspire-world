#!/usr/bin/env python3
"""Fresh model pair -> fifteen revisions -> immutable fifty-seed evaluation."""
from __future__ import annotations

import argparse
import ast
import base64
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

SIM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SIM))
from scripts.libero.world_fix_loop_state import (CONDITIONS, MECHANISM_ADMISSION, Ledger, MODEL,
                                                 PROTOCOL, ProtocolError, SUITE, TASK,
                                                 put, read, sha)
from scripts.libero.world_fix_loop_model import (NativeModel, ORIGIN, extract, ledger_schema,
                                                 model_endpoint)
from scripts.libero.paired_bowl_supervisor import artifacts, child_environment, process_identity, save, stop_owned_tree


def public_api_document():
    """Extract exposed function signatures/docstrings, not implementation/solutions."""
    docs = []
    for name in ["libero_reduced.py", "libero_reduced_skill_library.py"]:
        tree = ast.parse((SIM / "cap/integrations/franka" / name).read_text())
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
            if "functions" not in methods:
                continue
            exposed = set()
            for node in ast.walk(methods["functions"]):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
                    exposed.add(node.value.attr)
            for key in sorted(exposed - {"point_prompt_molmo"}):
                method = methods.get(key)
                if method is not None:
                    args = ast.unparse(method.args)
                    args = args.removeprefix("self, ").removeprefix("self")
                    docs.append(f"### {key}({args})\n\n{ast.get_docstring(method) or ''}\n")
    return "# Public policy API\n\n" + "\n".join(docs)


SKILL_SOURCE = SIM / "docs/experiments/world-abc-opus46-bowl-20260913/pristine-skills"
SKILL_EXPECTED = SKILL_SOURCE.parent / "skills-expected.json"
ORDINARY_CONTRACT = SIM / "docs/experiments/world-fix-loop-ordinary-interface.md"
# The document set is fixed here, not discovered from the runtime directory, so a
# request depends only on this tuple and the campaign's own sealed control copies.
SKILL_FILENAMES = ("localize.md", "grasp.md", "transport.md", "manipulation.md")


def shared_skill_documents():
    """Validate and return the pristine shared strategy MDs, at prepare time only.

    The bytes are checked against the recorded commit manifest before a campaign
    freezes them, so a cell can never seal an edited or partially staged document
    set, and C's absence of these documents is a real absence rather than a
    staging accident. Requests do NOT come back here: once frozen, the campaign's
    own `control/skills` copies (covered by `inputs_sha256`) are the input.
    """
    expected = read(SKILL_EXPECTED)
    if tuple(entry["filename"] for entry in expected["skills"]) != SKILL_FILENAMES:
        raise ProtocolError("recorded skill manifest does not name the four documents")
    documents, total = [], 0
    for entry in expected["skills"]:
        path = SKILL_SOURCE / entry["filename"]
        data = path.read_bytes()
        if (sha(path), len(data)) != (entry["sha256"], entry["bytes"]):
            raise ProtocolError("pristine skill document differs from the recorded "
                                "commit manifest: " + entry["filename"])
        documents.append((entry["filename"], data))
        total += len(data.decode())
    if total != expected["total_characters"]:
        raise ProtocolError("incomplete pristine skill document set")
    return documents


def prepare(campaign, credential_settings, *, endpoint=ORIGIN, trust_env=True,
            condition=None, repeat=None, gpu=None, egl_device_id=None):
    """Seal one campaign identity. A caller naming no condition keeps r4 behaviour.

    `condition` is "A", "B" or "C" of the matched study and fixes the source
    schema, the mechanism-admission rule, whether the shared strategy documents
    are frozen as control inputs, and which model-facing contract is used. It is
    sealed here together with `repeat`, the GPU and the EGL device id, so none of
    them is a run-time argument later. Omitting it retains the historical world
    identity, including physical GPU 7.
    """
    endpoint = model_endpoint(endpoint)
    if type(trust_env) is not bool:
        raise ProtocolError("trust_env must be boolean")
    if (condition is None) != (repeat is None):
        raise ProtocolError("a study condition requires its repeat id, and vice versa")
    bundle = CONDITIONS[condition] if condition in CONDITIONS else None
    if condition is not None and bundle is None:
        raise ProtocolError("unknown study condition: " + str(condition))
    # New cells run on physical GPU 0 / raw EGL 0; the legacy default stays 7.
    gpu = ("0" if condition is not None else "7") if gpu is None else str(gpu)
    if egl_device_id is None:
        egl_device_id = 0 if condition is not None else 7
    if type(egl_device_id) is not int or isinstance(egl_device_id, bool) or egl_device_id < 0:
        raise ProtocolError("egl_device_id must be a nonnegative integer")
    if not gpu.isdigit():
        raise ProtocolError("gpu must be a physical device index")
    campaign = Path(campaign).resolve()
    campaign.mkdir(parents=True, exist_ok=False)
    control = campaign / "control"
    put(control / "public-api.md", public_api_document().encode())
    # Every condition receives exactly one model-facing contract: the world one
    # for B/C and for every legacy campaign, the ordinary-policy one for A. A gets
    # no world contract at all, under any filename.
    world_model = bundle["world_model"] if bundle is not None else True
    contract = SIM / "docs/experiments/world-fix-loop-interface.md" if world_model else ORDINARY_CONTRACT
    put(control / ("world-interface.md" if world_model else "policy-interface.md"),
        contract.read_bytes())
    if bundle is not None and bundle["shared_skill_md"]:
        for filename, data in shared_skill_documents():
            put(control / "skills" / filename, data)
    # The host's default EGL discovery selects Mesa. Match the proven NVIDIA
    # vendor wiring explicitly; the device index is the sealed physical GPU.
    put(control / "nvidia-egl-vendor.json", {"file_format_version": "1.0.0",
        "ICD": {"library_path": "/usr/lib/x86_64-linux-gnu/libEGL_nvidia.so.0"}})
    yaml = SIM / "env_configs/libero/franka_libero_traced.yaml"
    paths = [*SIM.glob("cap/**/*.py"), *SIM.glob("scripts/**/*.py"), yaml, contract]
    # Resolve no external dependency symlinks into the execution-source manifest.
    paths = [p for p in paths if p.resolve().is_relative_to(SIM) and "third_party" not in p.parts]
    settings = {"runtime_root": str(SIM), "runtime_sha256": {str(p.relative_to(SIM)): sha(p) for p in sorted(set(paths))},
                "yaml": str(yaml), "yaml_sha256": sha(yaml), "model_origin": endpoint,
                "model_trust_env": trust_env,
                # Frozen into the immutable identity of THIS campaign only. Older
                # campaigns have no such key and retain their historical rule.
                "mechanism_admission": bundle["mechanism_admission"] if bundle else MECHANISM_ADMISSION,
                "credential_settings": str(Path(credential_settings).resolve()),
                "inputs_sha256": {str(p.relative_to(campaign)): sha(p)
                                  for p in sorted(control.rglob("*")) if p.is_file()},
                "gpu": gpu, "egl_device_id": egl_device_id, "generation_max_seconds": 240,
                "feedback": "all compact dev outcomes; latest executed dev trace/world events; first/middle/last video keyframes",
                "engineering": "Codex takeover after recorded incomplete native CC API responses; experimental model remains Opus4.6"}
    if bundle is not None:
        # Sealed together, so the condition, its schema and its admission rule
        # cannot be separated later. Ledger re-validates the triple on every open.
        settings.update(condition=condition, repeat=repeat, cell=condition + str(repeat),
                        source_schema=bundle["source_schema"],
                        world_model=bundle["world_model"],
                        shared_skill_md=bundle["shared_skill_md"],
                        # This round's actual engineering wiring. The old text
                        # described the r4 Codex takeover after native CC API
                        # failures, which is not what produced this cell.
                        engineering="matched A/B/C bowl study wired by Claude Code Opus5/high"
                                    " on the current scripted harness; experimental model"
                                    " remains Opus4.6")
    ledger = Ledger(campaign, settings)
    put(campaign / "prepared.json", {"identity_sha256": ledger.identity_sha, "prepared_unix": time.time(),
                                    "protocol": PROTOCOL, "condition": condition, "repeat": repeat,
                                    "status": "prepared_no_live_execution"})
    return ledger


def command(seed, policy, output, config):
    return [str(SIM / ".venv-libero/bin/python3"), str(SIM / "scripts/libero/replay_trial.py"),
            "--args.suite", SUITE, "--args.task", TASK, "--args.trial", str(seed),
            "--args.model", MODEL, "--args.replay-code", str(policy),
            "--args.config", str(config), "--args.output-dir", str(output), "--args.record-video"]


def execution_environment(ledger):
    vendor = ledger.root / "control/nvidia-egl-vendor.json"
    settings = ledger.identity["settings"]
    ledger.verify_refs(settings["inputs_sha256"])
    env = child_environment()
    # child_environment() hardcodes the historical GPU 7. The device actually used
    # is whatever this campaign sealed at prepare time, so the sealed value
    # overrides it unconditionally rather than only when it happens to differ.
    # An identity predating these keys keeps the historical device.
    env.update(CUDA_VISIBLE_DEVICES=str(settings.get("gpu", "7")),
               MUJOCO_EGL_DEVICE_ID=str(settings.get("egl_device_id", 7)))
    env["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(vendor)
    return env


def execute(command_args, directory, env, timeout):
    """One owned process tree; a resumed reservation is never silently replayed."""
    put(directory / "command.json", command_args)
    started = time.monotonic()
    receipt = {"status": "interrupted", "exit_code": None}
    process = None
    try:
        with (directory / "replay.log").open("x") as stream:
            process = subprocess.Popen(command_args, cwd=SIM, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            put(directory / "process.json", {"pid": process.pid, "starttime": process_identity(process.pid),
                                             "started_unix": time.time()})
            try:
                receipt["exit_code"] = process.wait(timeout=timeout)
                receipt["status"] = "completed" if process.returncode == 0 else "nonzero_exit"
            except subprocess.TimeoutExpired:
                receipt["status"] = "timeout"
                stop_owned_tree(process)
    except BaseException:
        if process is not None and process.poll() is None:
            stop_owned_tree(process)
        raise
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        put(directory / "execution.json", receipt)
    return receipt


def evidence_files(directory):
    # Full videos and transient logs are retained but not repeatedly hashed.
    # Public trace, keyframes, source copies and all authoritative receipts are.
    return sorted(p for p in Path(directory).rglob("*") if p.is_file()
                  and p.suffix in {".json", ".jsonl", ".yaml", ".py", ".txt", ".jpg", ".png"}
                  and p.name != "result.json")


def snapshot(ledger):
    directory = ledger.root / "snapshot"
    if (directory / "result.json").exists():
        result = read(directory / "result.json")
        ledger.verify_refs(result["evidence"])
        if result["status"] != "complete":
            raise ProtocolError("snapshot incomplete")
        return result
    if directory.exists():
        raise ProtocolError("snapshot already reserved without terminal; inspect, do not silently recapture")
    directory.mkdir()
    source = SIM / "scripts/libero/scene_snapshot.py"
    policy = directory / "frozen/shared_policy.py"
    put(policy, source.read_bytes())
    put(directory / "reserved.json", {"seed": 51, "source_sha256": sha(source), "identity_sha256": ledger.identity_sha})
    env = execution_environment(ledger)
    env["SNAPSHOT_DIR"] = str(directory)
    env.pop("ASPIRE_SAM3_PROMPTS", None)
    receipt = execute(command(51, policy, directory / "results", ledger.identity["settings"]["yaml"]),
                      directory, env, PROTOCOL["trial_timeout_seconds"])
    result = artifacts(directory / "results", directory, 51, "ordinary")
    images = [directory / name for name in ["scene_snapshot.jpg", "scene_snapshot_wrist.jpg"]]
    language = re.findall(r"TASK_LANGUAGE:\s*([^\n]+)", (directory / "replay.log").read_text(errors="replace"))
    # replay prints the captured stdout; use the actual emitted task language.
    if not language and result.get("trial_dir"):
        language = re.findall(r"TASK_LANGUAGE:\s*([^\n]+)", (Path(result["trial_dir"]) / "summary.txt").read_text())
    ok = receipt["status"] == "completed" and result["valid"] and result["sandbox_rc"] == 0 and all(p.is_file() for p in images) and bool(language)
    result.update(status="complete" if ok else "infrastructure_error", task_language=language[-1] if language else None,
                  evidence=ledger.refs(evidence_files(directory)))
    put(directory / "result.json", result)
    if not ok:
        raise ProtocolError("initial scene snapshot missing verified public evidence")
    return result


def image_block(path):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(Path(path).read_bytes()).decode()}}


# Development-summary fields, split by whether they describe a world mechanism.
# The world keys are meaningful only where a mechanism ran; a world-free cell must
# not receive them as nulls, which would imply a mechanism it never had.
COMMON_SUMMARY_FIELDS = ["status", "task_completed", "reward", "sandbox_rc"]
WORLD_SUMMARY_FIELDS = [
    "world_verify_calls", "query_used", "world_program_errors",
    # Real-mechanism evidence: a world_verify call count alone does not show that
    # any evidence was measured. These distinguish an attempted relation query
    # returning UNKNOWN from no query at all.
    "world_reference_established", "world_reference_purpose_queries",
    "world_relation_measurement_attempts", "world_relation_verdicts",
    "world_relation_checks_not_requested", "world_off_schedule_reference_requests",
    "world_mechanism_evidence_observed"]
ORDINARY_SUMMARY_FIELDS = [
    "ordinary_action_count", "ordinary_attempted_actions",
    "ordinary_action_limit_denials", "ordinary_recovery_used"]


def build_request(ledger, index):
    settings = ledger.identity["settings"]
    ledger.verify_refs(settings["inputs_sha256"])
    # Absence of a condition is the legacy world identity, unchanged.
    world = ledger.condition["world_model"] if ledger.condition else True
    snap = read(ledger.root / "snapshot/result.json")
    ledger.verify_refs(snap["evidence"])
    contract = "control/world-interface.md" if world else "control/policy-interface.md"
    chunks = [(ledger.root / contract).read_text(),
              (ledger.root / "control/public-api.md").read_text()]
    if ledger.condition and ledger.condition["shared_skill_md"]:
        # The full, identical documents on every request of this cell, read from
        # this campaign's sealed control copies only. Their hashes are in the
        # inputs_sha256 verified just above, so a tampered document cannot reach
        # the model; the runtime source directory and its manifest are not
        # consulted here, so a request never depends on unsealed files.
        for filename in SKILL_FILENAMES:
            chunks.append(f"# Shared strategy document: {filename}\n\n"
                          + (ledger.root / "control/skills" / filename).read_text())
    chunks += ["Actual public task language: " + snap["task_language"],
               f"This is {'initial generation' if index == 0 else 'revision '+str(index)+' of 15'}. "
               + ("Produce a complete world/policy/inventory JSON response."
                  if world else "Produce a complete policy JSON response.")
               + " No extra tools or hidden trials."]
    images = [ledger.root / "snapshot/scene_snapshot.jpg", ledger.root / "snapshot/scene_snapshot_wrist.jpg"]
    fields = COMMON_SUMMARY_FIELDS + (WORLD_SUMMARY_FIELDS if world else ORDINARY_SUMMARY_FIELDS)
    summaries, current, latest_trial = [], None, None
    for previous in range(index):
        generation = ledger.generation(previous)
        item = {"version": previous, "generation_status": generation["status"], "error": generation["error"]}
        if generation["status"] == "valid":
            current = previous
            result = ledger.trial("initial" if previous == 0 else "repair", previous)
            item.update({k: result.get(k) for k in fields})
            item["source_hashes"] = {key: sha(path) for key, path in ledger.sources(previous).items()}
            latest_trial = result
        summaries.append(item)
    if summaries:
        chunks.append("Accumulated development outcomes (all versions retained):\n" + json.dumps(summaries, ensure_ascii=False))
    if current is not None:
        sources = ledger.sources(current)
        label = "program pair" if world else "policy"
        chunks.append(f"Current complete {label} is development version {current}:\n" + json.dumps(
            {k: read(p) if k == "inventory" else p.read_text() for k, p in sources.items()}, ensure_ascii=False))
    if latest_trial and latest_trial.get("trial_dir"):
        trial = Path(latest_trial["trial_dir"])
        chunks.append("Latest DEVELOPMENT public stdout/stderr (tail, up to16000 characters):\n" +
                      (trial / "summary.txt").read_text(errors="replace").split("Environment response:", 1)[-1][-16000:])
        trace = read(trial / "trace.json")
        chunks.append("Latest DEVELOPMENT public API trace (up to24000 characters):\n" + json.dumps(trace, ensure_ascii=False)[:24000])
        # World evidence exists only where a world mechanism ran. A world-free
        # condition's feedback is its real stdout and API trace; it must not be
        # given an empty "world evidence" section implying a mechanism was there.
        if latest_trial.get("live_manifest"):
            live = Path(latest_trial["live_manifest"]).parent
            events = [json.loads(line) for line in (live / "live_tape.jsonl").read_text().splitlines()]
            keep = {"scene_anchor_committed", "prediction_committed", "query_comparison", "world_verify",
                    "program_error", "recovery_invoked", "recovery_denied", "reference_invalidated",
                    "reference_request_off_schedule", "relation_check_not_requested"}
            chunks.append("Latest DEVELOPMENT public world evidence (up to24000 characters):\n" + json.dumps(
                [e for e in events if e["event"] in keep], ensure_ascii=False)[:24000])
        frames = sorted((trial / "keyframes").glob("video_frame_*.jpg"))
        if frames:
            images += [frames[i] for i in sorted({0, len(frames)//2, len(frames)-1})]
    chunks.append("Images are initial agentview/wrist, followed (when available) by first/middle/last frames of the latest DEVELOPMENT rollout. Final evaluation data is never provided.")
    body = {"model": MODEL, "max_tokens": PROTOCOL["max_tokens"], "stream": False,
            "messages": [{"role": "user", "content": [*map(image_block, images),
                            {"type": "text", "text": "\n\n".join(chunks)}]}]}
    return body


def generation(ledger, index, model):
    slot = ledger.slot(index)
    if (slot / "result.json").exists():
        return ledger.generation(index)
    if not (slot / "reserved.json").exists():
        ledger.begin_generation(index, build_request(ledger, index))
    try:
        response_path = model.generate(index)
        response = read(response_path)
    except Exception as exc:
        ledger.finish_generation(index, "infrastructure_error", error=type(exc).__name__ + ": " + str(exc))
        raise
    if response.get("model") != MODEL:
        ledger.finish_generation(index, "infrastructure_error", error="served model mismatch")
        raise ProtocolError("served model differs; no model fallback")
    try:
        # The response shape is the one sealed in this campaign's identity, so a
        # policy-only cell cannot admit a world program and a world cell cannot
        # admit a response that omits one.
        sources = extract(response, ledger_schema(ledger))
    except (ValueError, SyntaxError, TypeError, KeyError) as exc:
        ledger.finish_generation(index, "content_error", error=type(exc).__name__ + ": " + str(exc),
            response_path=response_path if response.get("stop_reason") == "end_turn" else None)
    else:
        ledger.finish_generation(index, "valid", response_path=response_path, sources=sources)
    return ledger.generation(index)


def _finish_replay(ledger, phase, index, record, directory, child_dir, outcome_fn):
    """Shared tail: never silently rerun a live reservation, then classify once.

    The liveness check is unconditional. `_launch_child` writes child_exit.json
    with status="running" *before* waiting, so a resumed reservation whose parent
    replay is still alive has both a process record and a child receipt. Checking
    the receipt first would let a running trial be recorded permanently as an
    infrastructure error while it is still doing real work.
    """
    if (directory / "process.json").exists():
        process = read(directory / "process.json")
        if process_identity(process["pid"]) == process["starttime"]:
            raise ProtocolError("reserved replay still alive; do not launch another")
    if (child_dir / "child_exit.json").exists():
        outcome = outcome_fn(child_dir, read(child_dir / "child_exit.json"))
    else:
        outcome = {"status": "infrastructure_error", "valid": False,
                   "validation_error": "reserved replay has no child terminal; no silent rerun"}
    ledger.verify_runtime()
    outcome.update(phase=phase, index=index, seed=record["seed"], version=record["version"])
    ledger.finish_trial(phase, index, outcome, evidence_files(directory))
    return ledger.trial(phase, index)


def replay(ledger, phase, index):
    from cap.world_model.fix_loop_scene_broker import make_config, terminal_outcome
    directory = ledger.trial_dir(phase, index)
    if (directory / "result.json").exists():
        return ledger.trial(phase, index)
    already_reserved = (directory / "reserved.json").exists()
    record = ledger.admission(phase, index) if already_reserved else ledger.begin_trial(phase, index)
    sources = ledger.sources(record["version"])
    live = directory / "evidence/world" / f"seed_{record['seed']}"
    if not already_reserved:
        for key, name in [("policy", "shared_policy.py"), ("world", "world_program.py")]:
            put(directory / "frozen" / name, sources[key].read_bytes())
        config = directory / "config.json"
        put(config, make_config(ledger, phase, index))
        cmd = command(record["seed"], sources["policy"], directory / "ordinary", ledger.identity["settings"]["yaml"])
        cmd += ["--args.world-model-config", str(config)]
        execute(cmd, directory, execution_environment(ledger), PROTOCOL["trial_timeout_seconds"] + 60)
    return _finish_replay(ledger, phase, index, record, directory, live,
                          lambda child, receipt: terminal_outcome(child, ledger, phase, index, receipt))


def replay_ordinary(ledger, phase, index):
    """Execute one world-free trial (condition A) for a policy-only campaign.

    Same reservation, watchdog, environment and evidence accounting as `replay`;
    the launched child carries an ordinary-budget config and no world config, so
    no world program, inventory or verification callable exists for this trial.
    """
    from cap.world_model.ordinary_fix_loop_guard import (RUN_NAME, make_config,
                                                         terminal_outcome)
    directory = ledger.trial_dir(phase, index)
    if (directory / "result.json").exists():
        return ledger.trial(phase, index)
    already_reserved = (directory / "reserved.json").exists()
    record = ledger.admission(phase, index) if already_reserved else ledger.begin_trial(phase, index)
    sources = ledger.sources(record["version"])
    if set(sources) != {"policy"}:
        raise ProtocolError("ordinary replay requires a policy-only campaign schema")
    child_dir = directory / "evidence" / RUN_NAME / f"seed_{record['seed']}"
    if not already_reserved:
        # artifacts() compares the executed code against this frozen copy.
        put(directory / "frozen/shared_policy.py", sources["policy"].read_bytes())
        config = directory / "config.json"
        put(config, make_config(ledger, phase, index))
        cmd = command(record["seed"], sources["policy"], directory / "ordinary",
                      ledger.identity["settings"]["yaml"])
        cmd += ["--args.ordinary-budget-config", str(config)]
        execute(cmd, directory, execution_environment(ledger), PROTOCOL["trial_timeout_seconds"] + 60)
    return _finish_replay(ledger, phase, index, record, directory, child_dir,
                          lambda child, receipt: terminal_outcome(child, ledger, phase, index, receipt))


def progress(ledger, phase, index=None):
    result = ledger.summary()
    result.update(phase=phase, index=index, updated_unix=time.time(), pid=os.getpid(),
                  pid_starttime=process_identity(os.getpid()), campaign=str(ledger.root))
    save(ledger.root / "status.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def condition_replay(ledger):
    """The replay function this campaign's sealed condition requires.

    Resolved from the frozen identity, never from a caller's preference: a
    policy-only cell cannot be executed through the world path, and a world cell
    cannot be executed through the ordinary one.
    """
    world = ledger.condition["world_model"] if ledger.condition else True
    if world != (set(ledger.source_files) != {"policy"}):
        raise ProtocolError("sealed condition and source schema disagree about the world")
    return replay if world else replay_ordinary


def campaign(ledger, model_factory=NativeModel, replay_fn=None, snapshot_fn=snapshot):
    # An explicitly injected replay function is honoured (the offline tests supply
    # one); otherwise the sealed condition chooses, so no caller has to.
    replay_fn = condition_replay(ledger) if replay_fn is None else replay_fn
    with ledger.lock():
        ledger.verify_runtime()
        progress(ledger, "snapshot")
        snapshot_fn(ledger)
        # No credential helper or model client is created during held-out resume.
        model = None
        if not (ledger.root / "selection.json").exists():
            for index in range(16):
                ledger.verify_runtime()
                progress(ledger, "initial_generation" if index == 0 else "revision", index)
                if not (ledger.slot(index) / "result.json").exists() and model is None:
                    model = model_factory(ledger)
                result = generation(ledger, index, model)
                if result["status"] == "infrastructure_error":
                    raise ProtocolError("model infrastructure failure remains unresolved")
                if result["status"] == "valid":
                    phase = "initial" if index == 0 else "repair"
                    progress(ledger, phase, index)
                    if replay_fn(ledger, phase, index)["status"] == "infrastructure_error":
                        raise ProtocolError("development replay infrastructure failure")
            ledger.freeze()
        model = None
        ledger.selection()
        for seed in range(1, 51):
            ledger.verify_runtime()
            progress(ledger, "heldout", seed)
            if replay_fn(ledger, "heldout", seed)["status"] == "infrastructure_error":
                raise ProtocolError("held-out replay infrastructure failure")
        result = progress(ledger, "complete")
        if result["status"] != "complete":
            raise ProtocolError("required terminal counts missing")
        if not (ledger.root / "completed.json").exists():
            put(ledger.root / "completed.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "audit"])
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--credential-settings", default="/mnt/home/gewang/.claude/settings.json")
    parser.add_argument("--model-endpoint", default=ORIGIN)
    parser.add_argument("--direct-model-connection", action="store_true")
    # Sealed into the identity at prepare time. Omitting them keeps the legacy
    # world identity on the historical GPU 7; a new study cell names all of them.
    parser.add_argument("--condition", choices=sorted(CONDITIONS))
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--gpu")
    parser.add_argument("--egl-device-id", type=int)
    args = parser.parse_args()
    if args.action == "prepare":
        ledger = prepare(args.campaign, args.credential_settings, endpoint=args.model_endpoint,
                         trust_env=not args.direct_model_connection, condition=args.condition,
                         repeat=args.repeat, gpu=args.gpu, egl_device_id=args.egl_device_id)
        print(json.dumps({"status": "prepared", "identity_sha256": ledger.identity_sha,
                          "condition": args.condition, "repeat": args.repeat,
                          "cell": ledger.condition["cell"] if ledger.condition else None,
                          "gpu": ledger.identity["settings"]["gpu"],
                          "egl_device_id": ledger.identity["settings"]["egl_device_id"]}))
        return
    ledger = Ledger(args.campaign)
    if args.action == "audit":
        ledger.verify_runtime()
        print(json.dumps(ledger.summary(), indent=2))
        return
    def interrupted(signum, _frame):
        raise KeyboardInterrupt("signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    try:
        campaign(ledger)
    except BaseException as exc:
        save(ledger.root / "blocked.json", {"status": "incomplete", "error_type": type(exc).__name__,
             "error": str(exc), "time_unix": time.time(), "pid": os.getpid()})
        print(json.dumps({"status": "incomplete", "error_type": type(exc).__name__, "error": str(exc)}), flush=True)
        raise


if __name__ == "__main__":
    main()
