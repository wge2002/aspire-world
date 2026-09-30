#!/usr/bin/env python3
"""Generate and score opt-in numeric world programs; never starts a simulator.

All model settings and artifact paths are explicit. Existing replay/fix-loop
defaults are neither imported nor changed. Generation is one bounded API call;
scoring uses a credential-free numeric subprocess, not the model endpoint.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

SUITE = "libero_object_swap"
TASK = "pick_up_the_alphabet_soup_and_place_it_in_the_basket"
CONTRACT = """Write a pure numerical world program with these four functions:
initialize(context) -> JSON state
advance(state, step) -> JSON state
predict(state, step) -> prediction dict
assimilate(state, evidence) -> JSON state

Each call executes in a fresh subprocess: carry all memory in the JSON state.
Only import math; use basic Python builtins and JSON-compatible finite values.
Do not access files, environment, network, simulator internals, assets or images.
Do not execute the policy; its source below is context for state design.

step = {index, frame_id, action:{api,args}, robot_state:{position:[x,y,z],
orientation_wxyz:[w,x,y,z], gripper:normalized_opening},
budget:{limit,used,remaining}}. Budget is read-only and counts post-anchor
query attempts. A run supplies its actual cap; the same program is tested at
caps 2 and 4. Spend queries according to the remaining budget and useful evidence.
All positions are metres in the public observation world frame. Robot position
is the actual measured API end-effector reference, never a commanded position.
An action returning, especially close_gripper, is not evidence of grasp success.

initialize context = {object_id, frame:'world', step, budget:{limit,used,remaining}, measurement:{status:
'ok'|'unknown', object_id, frame:'world', axes:[0,1,2], values:[x,y,z]|null,
reason}}. The first frame is the only initial object anchor, even if unknown.
advance receives only action and measured robot state on every subsequent frame.
predict returns {position:[x,y,z]|null, request_query:bool,
query_axes:[0,1,2], hypotheses:optional_JSON}. Choose a nonempty subset of axes
for a query. Position is a common physical readout of your internal state, not
an arbitrary latent coordinate. Preserve unknowns rather than invent certainty.

The runtime freezes your prediction, optionally releases the selected axes of
the measurement, compares with an externally fixed tolerance, then calls
assimilate. evidence = {index,frame_id,world_version,measurement:{status,
object_id,frame,axes,values:partial_values|null,reason}, comparison:{status:
'SUPPORT'|'CONTRADICT'|'UNKNOWN',error_m,residual}}. Never assume unreturned axes.
Queries that fail or return unknown still spend budget. The initial anchor is
counted separately. Independent scoring measurements never enter your state.

The target measurement is the median of visible segmented depth points from a
fixed external camera. It is an observable surface statistic, not a guaranteed
rigid body center; occlusion, rotation and identity ambiguity may invalidate it.
Geometric agreement alone cannot prove target identity or task completion.
Generate a useful conditional dynamics model and query proposal using only past
evidence, action history, measured robot state and the policy's requirements.
Query count/cost and prediction quality matter. You do not control thresholds,
measurement semantics or action execution. Return only Python source, optionally
inside one python code fence. Include brief comments explaining the representation.
"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def validate_model(model: str, source: str) -> None:
    if not model.strip() or re.search(r"claude|anthropic|gpt|openai|gemini", model, re.I):
        raise ValueError("This experiment requires an explicit open-weights model ID")
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query:
        raise ValueError("Provide a public HTTPS model-weight source without credentials or query parameters")


def validate_endpoint(endpoint: str) -> None:
    parsed = urllib.parse.urlsplit(endpoint)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or (parsed.scheme == "http" and not local)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Use HTTPS or a loopback HTTP endpoint; keep credentials in an environment variable")


def extract_program(content: str) -> str:
    match = re.fullmatch(r"\s*```(?:python)?\s*\n(.*?)\n```\s*", content, re.S)
    source = (match.group(1) if match else content).strip() + "\n"
    tree = ast.parse(source)
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    required = {"initialize", "advance", "predict", "assimilate"}
    if not required <= functions:
        raise ValueError(f"World program is missing functions: {sorted(required - functions)}")
    return source


def generate(args) -> dict:
    validate_model(args.model, args.open_weights_source)
    validate_endpoint(args.endpoint)
    policy = Path(args.policy).read_bytes()
    representation = {
        "flat": "Use explicit world-coordinate numerical state with action-conditioned dynamics. "
                "This is a strong baseline: propagate using actual robot translation AND rotation, "
                "retain uncertainty and conditional modes; do not merely cache the last position. "
                "You may use numerical transforms, history and hypotheses as needed.",
        "relation": "Choose a compact task-relevant relational representation and its reference frames. "
                    "Generate the state structure, conditional relation hypotheses, dynamics, common "
                    "physical readout and evidence-driven query proposal yourself.",
    }[args.representation]
    prompt = CONTRACT + "\nRepresentation assignment:\n" + representation + "\nFrozen policy source:\n" + policy.decode()
    payload = {"model": args.model, "messages": [{"role": "user", "content": prompt}],
               "temperature": args.temperature, "max_tokens": args.max_tokens}
    if args.model_family == "qwen":
        payload.update(reasoning_effort=args.reasoning_effort,
                       chat_template_kwargs={"enable_thinking": True, "preserve_thinking": True})
    elif args.model_family == "deepseek":
        if args.reasoning_effort not in {"low", "high", "max"}:
            raise ValueError("DeepSeek generation requires an explicit low/high/max effort")
        payload["chat_template_kwargs"] = {"thinking": True, "reasoning_effort": args.reasoning_effort}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "request.json", payload)
    (output / "policy.py").write_bytes(policy)
    manifest = {"schema_version": 1, "status": "prepared" if args.prepare_only else "running",
                "model_requested": args.model, "model_served": None,
                "open_weights_source": args.open_weights_source,
                "open_weights_status": "declared_source; endpoint_identity_checked_on_response",
                "representation": args.representation, "policy_sha256": digest(policy),
                "request_sha256": digest((output / "request.json").read_bytes()),
                "endpoint": args.endpoint, "usage": None, "program_sha256": None}
    write_json(output / "generation.json", manifest)
    if args.prepare_only:
        return manifest
    headers = {"Content-Type": "application/json"}
    if args.api_key_env:
        key = os.environ.get(args.api_key_env)
        if not key:
            manifest.update(status="failed", error="configured_api_key_environment_variable_missing")
            write_json(output / "generation.json", manifest)
            raise ValueError("Configured API key environment variable is missing")
        headers["Authorization"] = f"Bearer {key}"
    try:
        request = urllib.request.Request(args.endpoint, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = json.load(response)
        # Provider response is saved without HTTP headers or request credentials.
        write_json(output / "response.json", body)
        manifest.update(model_served=body.get("model"), usage=body.get("usage"))
        if body.get("model") != args.model:
            raise ValueError("Endpoint returned a different model ID")
        choice = body["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Generation did not stop normally; truncated programs are not admitted")
        source = extract_program(choice["message"]["content"])
        (output / "world.py").write_text(source)
        manifest.update(status="generated_unvalidated", program_sha256=digest(source.encode()))
    except Exception as exc:
        manifest.update(status="failed", error=type(exc).__name__)
        write_json(output / "generation.json", manifest)
        # Do not echo provider errors or URLs that may contain sensitive data.
        raise RuntimeError(f"Generation failed ({type(exc).__name__}); inspect the local manifest") from None
    write_json(output / "generation.json", manifest)
    return manifest


def load_tape(path: Path) -> tuple[list[dict], dict]:
    manifest = read_json(path.parent / "capture_manifest.json")
    if manifest.get("status") != "complete":
        raise ValueError("Only a complete capture is admitted")
    if manifest.get("suite") != SUITE or manifest.get("task") != TASK or manifest.get("seed") not in range(51, 66):
        raise ValueError("v0 scoring only admits the selected Alphabet soup development seeds 51–65")
    data = path.read_bytes()
    if manifest.get("tape_sha256") != digest(data):
        raise ValueError("Capture tape hash mismatch")
    records = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    if manifest.get("frame_count") != len(records):
        raise ValueError("Capture frame count mismatch")
    return records, manifest


def admit_program(path: Path, policy_sha256: str, hand_control: bool) -> dict:
    source_hash = digest(path.read_bytes())
    if hand_control:
        return {"origin": "hand_control", "program_sha256": source_hash}
    manifest = read_json(path.parent / "generation.json")
    if manifest.get("status") != "generated_unvalidated":
        raise ValueError("A real completed generation is required; prepared requests are not generated programs")
    validate_model(manifest.get("model_requested", ""), manifest.get("open_weights_source", ""))
    if (manifest.get("model_served") != manifest["model_requested"]
            or manifest.get("program_sha256") != source_hash
            or manifest.get("policy_sha256") != policy_sha256):
        raise ValueError("Program, model or common frozen-policy provenance mismatch")
    return manifest | {"origin": "api_generated"}


def score(args) -> dict:
    # Kept separate from generation to keep credentials outside program workers.
    from aspire.sim.cap.world_model.program import FrozenPythonProgram
    from aspire.sim.cap.world_model.runtime import ReplayConfig, replay_tape

    records, capture = load_tape(Path(args.tape))
    identity = admit_program(Path(args.program), capture["policy_sha256"], args.hand_control)
    indices = tuple(int(i) for i in args.fixed_indices.split(",") if i.strip())
    axes = tuple(int(i) for i in args.query_axes.split(",") if i.strip())
    config = ReplayConfig(query_budget=args.query_budget, schedule=args.schedule,
                          fixed_indices=indices, tolerance=args.tolerance,
                          query_axes=axes)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "status": "running", "seed": capture["seed"],
                "tape_sha256": capture["tape_sha256"], "policy_sha256": capture["policy_sha256"],
                "program": identity, "schedule": args.schedule, "query_budget": args.query_budget,
                "fixed_indices": indices, "fixed_query_axes": axes, "tolerance_m": args.tolerance,
                "claim_scope": "passive_numeric_observer; no closed-loop recovery or camera savings"}
    write_json(output / "score_manifest.json", manifest)
    try:
        program = FrozenPythonProgram.from_file(args.program, timeout_s=args.timeout)
        with (output / "events.jsonl").open("x") as events:
            def emit(event):
                events.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
                events.flush()
            result = replay_tape(records, program, config, event_sink=emit)
        write_json(output / "summary.json", result["summary"])
        if result["summary"].get("status") != "complete":
            raise RuntimeError("World-program replay was incomplete; inspect summary.json and events.jsonl")
        manifest["status"] = "complete"
    except Exception as exc:
        manifest.update(status="failed", error=type(exc).__name__)
        write_json(output / "score_manifest.json", manifest)
        raise
    write_json(output / "score_manifest.json", manifest)
    return result["summary"]


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="one explicit open-model request, or prepare-only without network")
    gen.add_argument("--policy", required=True)
    gen.add_argument("--representation", required=True, choices=["flat", "relation"])
    gen.add_argument("--endpoint", required=True, help="full chat/completions endpoint")
    gen.add_argument("--model", required=True)
    gen.add_argument("--model-family", required=True, choices=["qwen", "deepseek", "generic"])
    gen.add_argument("--open-weights-source", required=True)
    gen.add_argument("--api-key-env", help="name of a protected environment variable, never the key itself")
    gen.add_argument("--reasoning-effort", required=True)
    gen.add_argument("--max-tokens", type=int, default=8192)
    gen.add_argument("--temperature", type=float, default=0.6)
    gen.add_argument("--timeout", type=float, default=600)
    gen.add_argument("--output", required=True)
    gen.add_argument("--prepare-only", action="store_true", help="save a request; do not call any model")
    scoring = sub.add_parser("score", help="blind numeric replay of one generated program on one development tape")
    scoring.add_argument("--tape", required=True)
    scoring.add_argument("--program", required=True)
    scoring.add_argument("--hand-control", action="store_true", help="label explicitly as a human-written control")
    scoring.add_argument("--schedule", required=True, choices=["fixed", "adaptive"])
    scoring.add_argument("--query-budget", required=True, type=int)
    scoring.add_argument("--fixed-indices", default="", help="tape row indices frozen on calibration data; row 0 is the anchor, first eligible row is 1")
    scoring.add_argument("--query-axes", default="0,1,2", help="fixed-schedule readout axes")
    scoring.add_argument("--tolerance", required=True, type=float, help="calibrated metres; not tuned during scoring")
    scoring.add_argument("--timeout", type=float, default=2)
    scoring.add_argument("--output", required=True)
    return parser


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    result = generate(args) if args.command == "generate" else score(args)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
