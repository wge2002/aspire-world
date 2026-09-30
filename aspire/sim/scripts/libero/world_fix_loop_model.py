"""Fixed native model client and source admission for the world fix loop."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import shlex
import subprocess
import time
from urllib.parse import urlsplit

from scripts.libero.world_fix_loop_state import MODEL, PROTOCOL, ProtocolError, put, read

ORIGIN = "https://llmapi.roboscience.xyz"


def model_endpoint(value):
    """Validate transport syntax, without a destination/port authorization list."""
    if not isinstance(value, str):
        raise ProtocolError("model endpoint must be a configured HTTPS URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ProtocolError("model endpoint requires HTTPS and no embedded credentials/query/fragment")
    return value.rstrip("/")
POLICY_IMPORTS = {"math", "numpy", "scipy.spatial.transform"}
FORBIDDEN_NAMES = {"env", "sim", "os", "sys", "open", "exec", "eval", "compile", "input",
                   "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr", "hasattr",
                   "breakpoint", "__import__", "exit", "quit", "point_prompt_molmo"}
FORBIDDEN_ATTRIBUTES = {"load", "save", "savez", "savez_compressed", "loadtxt", "savetxt", "fromfile",
                        "tofile", "memmap", "ctypeslib", "f2py", "genfromtxt", "DataSource", "ctypes",
                        "read", "write", "dump", "dumps", "to_pickle", "read_pickle", "open_memmap"}
# Names a policy may only use where a world mechanism actually exists. The
# ordinary budget helpers (recovery_available/use_recovery) are deliberately NOT
# here: they are execution-budget accounting present in every condition.
WORLD_NAMES = {"world_verify"}

# Frozen per-condition response schemas, keyed by the source-schema name sealed
# in a campaign's identity. The world triple is the historical default and is
# unchanged; the policy-only entry is what makes the plain condition's response
# carry no world program and no inventory rather than an empty, fabricated one.
RESPONSE_SCHEMAS = {
    "world_policy_inventory_v1": {"required": ("world", "policy", "inventory"),
                                  "optional": ("diagnosis",), "world_available": True},
    "policy_only_v1": {"required": ("policy",), "optional": ("diagnosis",),
                       "world_available": False},
}
DEFAULT_RESPONSE_SCHEMA = "world_policy_inventory_v1"


def validate_source(source, world, *, world_available=True):
    """Check one model source against the frozen numeric/API restrictions.

    `world=True` validates a world program. `world=False` validates a policy:
    with `world_available` it must really call `world_verify` (the world
    conditions' requirement, unchanged); without it, `world_verify` must not
    appear at all, so the plain condition cannot reach a world mechanism that
    does not exist for it. The ordinary budget helpers stay legal either way.
    """
    if not isinstance(source, str) or not 1 <= len(source) <= 200000:
        raise ValueError("source must be nonempty bounded text")
    tree = ast.parse(source)
    allowed = {"math"} if world else POLICY_IMPORTS
    forbidden_names = FORBIDDEN_NAMES if world or world_available else FORBIDDEN_NAMES | WORLD_NAMES
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module]
            if getattr(node, "level", 0) or any(n not in allowed for n in names):
                raise ValueError("source imports outside public numeric API")
            if isinstance(node, ast.ImportFrom) and any(n.name.startswith("_") or n.name == "*" or n.name in FORBIDDEN_ATTRIBUTES for n in node.names):
                raise ValueError("private/wildcard imports forbidden")
        if isinstance(node, ast.Name) and (node.id in forbidden_names or node.id.startswith("__")):
            raise ValueError("forbidden name: " + node.id)
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRIBUTES):
            raise ValueError("forbidden attribute: " + node.attr)
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)):
            raise ValueError("use ordinary functions, not classes or asynchronous code")
    if world:
        signatures = {n.name: len(n.args.args) for n in tree.body if isinstance(n, ast.FunctionDef)}
        if any(signatures.get(name) != count for name, count in
               {"initialize": 1, "advance": 2, "predict": 2, "assimilate": 2}.items()):
            raise ValueError("missing world function/signature")
    elif world_available and not any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "world_verify"
            for n in ast.walk(tree)):
        raise ValueError("policy must call world_verify and use its result")
    return tree


def response_schema(name=DEFAULT_RESPONSE_SCHEMA):
    """Resolve a frozen response schema by name; unknown names are never a default."""
    if name not in RESPONSE_SCHEMAS:
        raise ProtocolError("unknown response schema: " + str(name))
    return RESPONSE_SCHEMAS[name]


def ledger_schema(ledger):
    """The response schema named by this campaign's sealed identity settings."""
    return response_schema(ledger.identity["settings"].get("source_schema", DEFAULT_RESPONSE_SCHEMA))


def extract(response, schema=DEFAULT_RESPONSE_SCHEMA):
    if response.get("model") != MODEL or response.get("stop_reason") != "end_turn":
        raise ValueError("incomplete or wrong-model response")
    shape = schema if isinstance(schema, dict) else response_schema(schema)
    required, optional = set(shape["required"]), set(shape["optional"])
    text = "\n".join(c["text"] for c in response.get("content", []) if c.get("type") == "text").strip()
    # Presentation prose/fences are not source. Decode whole JSON values and
    # skip their interiors so braces inside Python strings cannot become code
    # candidates. Never repair JSON or choose between multiple program pairs.
    decoder, candidates, offset = json.JSONDecoder(), [], 0
    while offset < len(text):
        start = text.find("{", offset)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            offset = start + 1
            continue
        offset = end
        if isinstance(value, dict) and required <= value.keys():
            candidates.append(value)
    if len(candidates) != 1:
        raise ValueError("expected exactly one complete "
                         + "/".join(shape["required"]) + " JSON object")
    data = candidates[0]
    if set(data) - (required | optional):
        # A plain-condition response carrying a world program or inventory is
        # rejected outright rather than silently dropped, so the condition
        # cannot be weakened by an extra key nobody executes.
        raise ValueError("return exactly " + ", ".join(shape["required"])
                         + " and optional " + ", ".join(shape["optional"]))
    if "world" in required:
        validate_source(data["world"], True)
    validate_source(data["policy"], False, world_available=shape["world_available"])
    if "inventory" in required:
        from cap.world_model.reference_scene_broker import _check_inventory
        _check_inventory(data["inventory"])
    return {key: data[key] for key in shape["required"]}


class NativeModel:
    def __init__(self, ledger):
        self.ledger = ledger
        settings = ledger.identity["settings"]
        self.endpoint = model_endpoint(settings["model_origin"])
        self.trust_env = settings.get("model_trust_env", True)
        if type(self.trust_env) is not bool:
            raise ProtocolError("model_trust_env must be a frozen boolean")
        # Kept only in this coordinator's memory and HTTP headers, never in
        # prompts, replay subprocesses, environment variables or receipt files.
        helper = shlex.split(read(settings["credential_settings"])["apiKeyHelper"])
        try:
            result = subprocess.run(helper, capture_output=True, text=True, timeout=20, check=True)
        except Exception as exc:
            raise RuntimeError("protected credential helper failed: " + type(exc).__name__) from None
        self._key = result.stdout.strip()
        if not self._key or "\n" in self._key:
            raise RuntimeError("invalid credential helper output")

    def generate(self, index):
        """Retry identical payload only for transport failure, reserving each POST."""
        import httpx
        ledger, slot = self.ledger, self.ledger.slot(index)
        # A response persisted before interruption is used once, never reissued.
        for path in sorted(slot.glob("request-*.response.json")):
            response = read(path)
            if response.get("model") == MODEL and response.get("stop_reason") == "end_turn":
                return path
            if response.get("model") != MODEL or response.get("stop_reason") in {"max_tokens", "refusal", "tool_use"}:
                return path
        while len(list(slot.glob("request-*.reserved.json"))) < PROTOCOL["transport_attempts_per_slot"]:
            ledger.verify_runtime()
            attempt = ledger.begin_request(index)
            started = time.monotonic()
            receipt = {"origin": self.endpoint, "model": MODEL, "attempt": attempt}
            path = slot / f"request-{attempt}.response.json"
            try:
                with httpx.Client(timeout=httpx.Timeout(240.0, connect=30), follow_redirects=False,
                                  trust_env=self.trust_env) as client:
                    response = client.post(self.endpoint + "/v1/messages", content=(slot / "request.json").read_bytes(),
                        headers={"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
                receipt["http_status"] = response.status_code
                if response.status_code != 200:
                    receipt["status"] = "http_error"
                else:
                    answer = response.json()
                    put(path, answer)
                    receipt.update(status="response", response_model=answer.get("model"),
                                   stop_reason=answer.get("stop_reason"), usage=answer.get("usage"))
            except Exception as exc:
                # No request headers or credentials in exception logs.
                receipt.update(status="transport_error", error_type=type(exc).__name__)
            receipt["elapsed_seconds"] = time.monotonic() - started
            put(slot / f"request-{attempt}.receipt.json", receipt)
            if path.exists():
                answer = read(path)
                if answer.get("model") != MODEL or answer.get("stop_reason") is not None:
                    return path
            if receipt.get("http_status") in {301, 302, 303, 307, 308, 400, 401, 403, 404}:
                break
        raise RuntimeError("native model transport failed; attempts retained without extra content budget")
