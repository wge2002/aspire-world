#!/usr/bin/env python3
"""Recorded development calls for the SAM2-streaming native Fix Loop profile.

Profile `sam2-streaming-native-v1` under `libero-pro-common-v1`
(docs/libero-pro-common-protocol.md). Like native_world_protocol.py this module
has no LLM client, agent loop or context management: native Claude Code (the
experiment's Qwen solver) generates and repairs programs and calls this CLI with
its ordinary Bash tool. The module only admits, executes and accounts.

What is new relative to the legacy native world profiles, and nothing else:

* The method runtime is the engineering `cap/world_model/World.py` stack with a
  SAM2_streaming tracker, run through replay_trial's explicit
  `--args.streaming-world-config` saved-policy profile. It is declared and
  hashed as an engineering input; it is never labeled solver-generated.
* The solver authors, per cell and from public observations only, the policy
  and a streaming object configuration (public SAM3 seed prompts plus optional
  cadence). No object inventory is supplied. Relation predicates and optional
  predictions live in the policy through the injected World helpers.
* A candidate bundle is {policy, world}; `world` digests the authored streaming
  configuration together with the engineering World source, the SAM2 source
  tree and checkpoint, so a change to any of them is a different candidate.
* The development phase has an absolute persisted deadline (50,400 s) that a
  resume never refreshes; the per-trial watchdog stays 900 s.
* Oracle gate, but an execution whose program raised (sandbox_rc != 0) never
  counts as a development success even when the environment reported
  completion. No observation-only fallback: an unexecuted bundle cannot be
  selected.

Accounting is the unchanged NativeWorldState ledger: three TOTAL charged
executions per development seed (smoke/initial/repair/diagnostic, timeouts and
crashes included), one uncharged public scene snapshot, no global revision
limit and no action/query/recovery caps. Seeds 1-50 are refused here; held-out
evaluation belongs to a separate outer stage.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_world_fixloop_state import (RETRY_LIMIT, NativeWorldState, ProtocolError,
                                       bundle_identity, code_hash, report_errors, valid_program)
from native_world_protocol import (authored_path, check_policy, gate_module, parse_result,
                                   read_json, runtime_env)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_freeze import RuntimeChanged, verify_runtime
from native_cc_trial_process import run_replay

if str(Path(__file__).resolve().parents[4]) not in sys.path:
    sys.path.append(str(Path(__file__).resolve().parents[4]))
from aspire.sim.cap.integrations.libero import initial_states as ist  # noqa: E402

PROFILE = "sam2-streaming-native-v1"
PROTOCOL_ID = "libero-pro-common-v1"
STREAMING_MODE = "sam2-streaming-world-v1"
SEMANTIC_PROFILE = "sam2-semantic-native-v2"
SEMANTIC_MODE = "sam2-semantic-world-v2"
ARTICULATION_PROFILE = "sam2-articulation-native-v3"
ARTICULATION_MODE = "sam2-articulation-world-v3"
PROFILES = {PROFILE: STREAMING_MODE, SEMANTIC_PROFILE: SEMANTIC_MODE,
            ARTICULATION_PROFILE: ARTICULATION_MODE}
SUITE = "libero_goal_swap"
TASKS = {"bowl": "put_the_bowl_on_the_plate",
         "bowldrawer": "open_the_top_drawer_and_put_the_bowl_inside",
         "drawer": "open_the_middle_drawer_of_the_cabinet",
         "wine": "put_the_wine_bottle_on_the_rack",
         "stove": "turn_on_the_stove"}
DEV_SEEDS = list(range(51, 66))
HELDOUT_SEEDS = range(1, 51)
# Fixed by the common protocol; a case that differs is refused, not adapted.
FIXED = {"condition": "C", "development_gate": "oracle", "suite": SUITE,
         "env_config": "env_configs/libero/franka_libero_traced.yaml",
         "max_steps": 4000, "trial_timeout": 900,
         "development_watchdog_seconds": 50400, "evaluation_watchdog_seconds": 18000,
         "model": "qwen3.8-flash-next", "effort": "xhigh",
         "context_tokens": 1000000, "max_output_tokens": 64000, "claude_version": "2.1.220"}
# Engineering method runtime: authored by engineering, frozen with every bundle.
ENGINEERED_WORLD_FILES = ("cap/world_model/World.py", "cap/world_model/sam2_streaming.py",
                          "cap/world_model/streaming_bridge.py",
                          "cap/world_model/streaming_replay.py",
                          "cap/world_model/control_progress.py",
                          "cap/world_model/semantic_world.py",
                          "cap/world_model/motion_feedback.py",
                          "cap/world_model/joint_models.py",
                          "cap/world_model/rigid_tracking.py",
                          "cap/world_model/articulated_world.py",
                          "cap/world_model/qwen_world_review.py",
                          "docs/articulation-streaming-world.md",
                          "docs/semantic-streaming-world.md",
                          "cap/integrations/franka/libero_reduced.py",
                          "cap/envs/simulators/libero.py", "scripts/libero/replay_trial.py",
                          "cap/integrations/libero/__init__.py",
                          "cap/integrations/libero/initial_states.py")
PROTOCOL_SOURCES = ("streaming_fixloop_protocol.py", "native_world_fixloop_state.py",
                    "native_world_protocol.py")
AUTHORED_CONFIG_KEYS = {"objects", "cadence", "notes"}
OBJECT_KEYS = {"identity", "prompt", "min_seed_score"}
SAM2_KEYS = ("source_root", "config_file", "checkpoint", "device")
RESULT_FILES = ("streaming_world_journal.jsonl", "streaming_world_snapshot.json")
# Same loopback rule as native_world_campaign.LOOPBACK_ROOT: the solver's model
# route is a local server root, never an off-host destination.
LOOPBACK_ROOT = re.compile(r"^http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}$")
# `frozen` is the only evidence a formal run accepts. `cpu-test-fake` exists for
# CPU fixtures: it lets dependency/perception freezes be absent, and every
# ledger, stage-1 result and held-out report then says so; it is never evidence.
DEPENDENCY_EVIDENCE = ("frozen", "cpu-test-fake")
# Raw simulator state and asset access from authored code: initial-state loaders,
# serialized state/asset files and the trusted registry wiring. Public robot API
# calls (get_observation, move_to_joints, ...) are unaffected.
RAW_ASSET_PATTERNS = (
    (r"\b(?:get_sim_state|set_init_state|init_states|initial_state_registry"
     r"|get_task_init_states|regenerate_obs_from_state)\b|\.set_state\s*\(",
     "simulator state access"),
    (r"\b(?:np|numpy|torch|pickle|joblib|scipy\.io)\.(?:load|loads|loadtxt|fromfile)\b"
     r"|\bloadmat\b", "loading serialized arrays or pickles"),
    (r"\.(?:npy|npz|pt|pth|pkl|pickle|pruned_init|init|bddl|xml|urdf|mjcf)\b[\"']",
     "simulator asset or state file"),
    (r"ASPIRE_LIBERO_INITIAL_STATE|initial_state_manifest|LIBERO_CONFIG_PATH|get_libero_path",
     "initial-state registry or LIBERO asset paths"),
)


class DeadlineExpired(ProtocolError):
    """The phase's absolute deadline passed; nothing more may be admitted."""


def file_sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Everything in root/sam2 that the runtime executes or was built from: Python and
# Hydra configs, the CUDA/C++ extension sources and headers, and the compiled
# extension itself (sam2/_C*.so, built by the outer deployment).
SAM2_TREE_SUFFIXES = {".py", ".yaml", ".cu", ".cuh", ".cpp", ".cc", ".c", ".h", ".hpp", ".so"}


def sam2_tree(root: Path) -> dict:
    package = Path(root) / "sam2"
    if not package.is_dir():
        raise ProtocolError(f"SAM2_streaming source has no sam2 package: {root}")
    return {str(p.relative_to(root)): file_sha256(p) for p in sorted(package.rglob("*"))
            if p.is_file() and p.suffix in SAM2_TREE_SUFFIXES and "__pycache__" not in p.parts}


def tree_sha256(root: Path) -> str:
    """SAM2 package sources, extension sources/headers and compiled .so, by path and content."""
    return code_hash(json.dumps(sam2_tree(root), sort_keys=True))


def validate_case(case: dict) -> None:
    errors = [f"{key} must be {value!r} under {PROTOCOL_ID}"
              for key, value in FIXED.items() if case.get(key) != value]
    if case.get("task") not in TASKS.values():
        errors.append(f"task must be one of the five protocol tasks: {sorted(TASKS.values())}")
    if case.get("dev_seeds") != DEV_SEEDS:
        errors.append("dev_seeds must be exactly 51-65")
    if case.get("profile") not in PROFILES:
        errors.append(f"profile must be one of {sorted(PROFILES)}")
    if case.get("profile") == SEMANTIC_PROFILE:
        if case.get("method") != "sam2-semantic-v2":
            errors.append("semantic v2 requires explicitly declared method sam2-semantic-v2")
        if not case.get("method_differences"):
            errors.append("semantic v2 must declare its method_differences")
    if case.get("profile") == ARTICULATION_PROFILE:
        if case.get("method") != "sam2-articulation-qwen-v3":
            errors.append("articulation v3 requires explicitly declared method sam2-articulation-qwen-v3")
        if not case.get("method_differences"):
            errors.append("articulation v3 must declare online Qwen review and motion fitting as method differences")
    if case.get("repeat") not in (1, 2) or not case.get("method"):
        errors.append("method and independent repeat (1 or 2) identify the cell")
    for forbidden in ("skill_library_dir", "inventory", "objects", "prior_solution"):
        if forbidden in case or forbidden in case.get("streaming", {}):
            errors.append(f"condition C supplies no {forbidden}; the solver authors it")
    sam2 = case.get("streaming", {}).get("sam2", {})
    if set(sam2) != set(SAM2_KEYS):
        errors.append(f"streaming.sam2 must declare exactly {SAM2_KEYS}")
    for key in ("id", "sim", "control", "cuda_visible_devices", "egl_device_id",
                "sam3_url", "graspnet_url", "pyroki_url", "model_service"):
        if key not in case:
            errors.append(f"case requires {key}")
    service = case.get("model_service", {})
    for key in ("endpoint", "served_model", "server_manifest", "server_manifest_sha256"):
        if not isinstance(service.get(key), str) or not service.get(key):
            errors.append(f"model_service.{key} is required")
    if not LOOPBACK_ROOT.match(str(service.get("endpoint", ""))):
        errors.append("model_service.endpoint must be a loopback server root (http://127.0.0.1:PORT)")
    if service.get("served_model") != case.get("model"):
        errors.append("model_service.served_model must equal the fixed solver model")
    evidence = case.get("dependency_evidence", "frozen")
    if evidence not in DEPENDENCY_EVIDENCE:
        errors.append(f"dependency_evidence must be one of {DEPENDENCY_EVIDENCE}")
    elif evidence == "frozen":
        # A formal run cannot silently accept modified services or dependencies.
        if case.get("require_runtime_freeze") is not True:
            errors.append("a formal run requires require_runtime_freeze: true")
        for key in ("runtime_manifest", "runtime_manifest_sha256",
                    "perception_manifest", "perception_manifest_sha256",
                    "initial_state_manifest", "initial_state_manifest_sha256"):
            if not case.get(key):
                errors.append(f"a formal run requires {key}")
    if errors:
        raise ProtocolError("; ".join(errors))


def load_case(path: Path) -> tuple[dict, Path, Path]:
    case = json.loads(Path(path).read_text())
    case.setdefault("condition", "C")
    case.setdefault("development_gate", "oracle")
    validate_case(case)
    repo = Path(case["sim"]).resolve()
    task_dir = Path(case["control"]).resolve() / "cell"
    if task_dir.is_relative_to(repo):
        raise ProtocolError("cell outputs belong outside the source checkout")
    return case, repo, task_dir


def engineered_world(repo: Path) -> dict:
    files = {name: file_sha256(repo / name) for name in ENGINEERED_WORLD_FILES}
    return {"author": "engineering (not solver-generated)", "files": files,
            "digest": code_hash(json.dumps(files, sort_keys=True))}


def sam2_identity(case: dict) -> dict:
    sam2 = case["streaming"]["sam2"]
    tree = sam2_tree(Path(sam2["source_root"]))
    compiled = {k: v for k, v in tree.items() if k.endswith(".so")}
    # A formal run freezes the real compiled extension; a CPU fixture has sources
    # only and is recorded as such, never as proof of the built runtime.
    if case.get("dependency_evidence", "frozen") == "frozen" and not compiled:
        raise RuntimeChanged("formal SAM2_streaming runtime has no compiled sam2/_C*.so")
    return {**{k: sam2[k] for k in SAM2_KEYS},
            "checkpoint_sha256": file_sha256(Path(sam2["checkpoint"])),
            "source_sha256": code_hash(json.dumps(tree, sort_keys=True)),
            "compiled_extensions": compiled or "absent (cpu-test-fake fixture)"}


def verified_manifest(path: str, bound: str, expected: dict, what: str) -> dict:
    """Hash the declared manifest file itself and check the identity it records.

    The manifest is a small fixed record written when the service was launched
    (endpoint, served model, capacity, weight digests). Binding it avoids
    rehashing model weights per trial; a dict in the case is not evidence.
    """
    manifest = Path(path)
    if not manifest.is_file():
        raise RuntimeChanged(f"{what} manifest is missing: {manifest}")
    actual = file_sha256(manifest)
    if actual != bound:
        raise RuntimeChanged(f"{what} manifest hash does not match the bound sha256")
    try:
        content = json.loads(manifest.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeChanged(f"{what} manifest is not JSON: {exc}") from exc
    mismatched = {key: {"declared": value, "manifest": content.get(key)}
                  for key, value in expected.items()
                  if not isinstance(content, dict) or content.get(key) != value}
    if mismatched:
        raise RuntimeChanged(f"{what} manifest disagrees with the case: {mismatched}")
    return {"path": str(manifest), "sha256": actual, "verified": expected}


def model_service_identity(case: dict) -> dict:
    service = case["model_service"]
    return {"endpoint": service["endpoint"], "served_model": service["served_model"],
            "manifest": verified_manifest(
                service["server_manifest"], service["server_manifest_sha256"],
                {"endpoint": service["endpoint"], "served_model": service["served_model"],
                 "max_model_len": case["context_tokens"]}, "model service")}


def perception_identity(case: dict) -> dict:
    urls = {k: case[k] for k in ("sam3_url", "graspnet_url", "pyroki_url")}
    if not case.get("perception_manifest"):
        # Only reachable under cpu-test-fake (validate_case); recorded as such.
        return {"services": urls, "manifest": None, "evidence": "cpu-test-fake"}
    return {"services": urls, "evidence": "frozen", "manifest": verified_manifest(
        case["perception_manifest"], case.get("perception_manifest_sha256", ""), urls,
        "perception")}


def initial_state_identity(case: dict) -> dict:
    """The common initial-state registry, verified against its bound sha256.

    Formal runs require it (validate_case): test seeds 1-50 read the original
    slots, development seeds 51-65 the 15 common development states, and the
    50-state modulo reuse is refused. Only a cpu-test-fake fixture may omit it,
    and then every identity records that the legacy loader was used.
    """
    if not case.get("initial_state_manifest"):
        return {"evidence": "cpu-test-fake", "manifest": None,
                "seed_mapping": "legacy loader (CPU fixture only; never a formal run)"}
    try:
        verified = ist.InitialStateRegistry(case["initial_state_manifest"],
                                            case.get("initial_state_manifest_sha256", ""),
                                            case["suite"], case["task"]).verify()
    except ist.InitialStateError as exc:
        raise RuntimeChanged(f"initial-state registry: {exc}") from exc
    return {"evidence": "frozen", **verified}


def identity(case: dict, repo: Path) -> dict:
    """Everything a resume must reproduce exactly. Budgets and clocks are data."""
    here = Path(__file__).resolve().parent
    world = engineered_world(repo)
    return {
        "harness": "claude-code", "harness_version": case["claude_version"],
        "profile": case["profile"], "protocol_id": PROTOCOL_ID,
        "method_differences": case.get("method_differences", []),
        "cell": case["id"], "method": case["method"], "repeat": case["repeat"],
        "suite": case["suite"], "task": case["task"], "condition": "C",
        "development_gate": "oracle", "strategy_md": False, "prearranged_inventory": False,
        "model": case["model"], "model_service": model_service_identity(case),
        "dependency_evidence": case.get("dependency_evidence", "frozen"),
        "effort": case["effort"], "context_tokens": case["context_tokens"],
        "max_output_tokens": case["max_output_tokens"],
        "online_world_review": review_config(case) if case["profile"] == ARTICULATION_PROFILE else None,
        "dev_seeds": case["dev_seeds"], "retry_limit": RETRY_LIMIT,
        "max_steps": case["max_steps"], "trial_timeout": case["trial_timeout"],
        "development_watchdog_seconds": case["development_watchdog_seconds"],
        "evaluation_watchdog_seconds": case["evaluation_watchdog_seconds"],
        "devices": {"cuda_visible_devices": case["cuda_visible_devices"],
                    "egl_device_id": case["egl_device_id"],
                    "egl_vendor_config": case.get("egl_vendor_config")},
        "perception_services": perception_identity(case),
        "config_sha256": file_sha256(repo / case["env_config"]),
        "protocol_sha256": code_hash("".join((here / n).read_text() for n in PROTOCOL_SOURCES)),
        "engineered_world": world, "sam2": sam2_identity(case),
        "environment_version": ist.ENVIRONMENT_VERSION,
        "initial_states": initial_state_identity(case),
        "camera": case["streaming"].get("camera", "agentview"),
        **({"runtime_manifest_sha256": verify_runtime(case, repo)}
           if case.get("runtime_manifest") or case.get("require_runtime_freeze") else {}),
    }


class StreamingFixLoopState(NativeWorldState):
    """The legacy ledger plus a persisted absolute phase deadline.

    Retry accounting, admission order, aliasing and evidence are inherited
    unchanged. Two declared differences: a program error is never a pass, and
    no unexecuted observation fallback can be selected.
    """

    def __init__(self, task_dir: Path, identity: dict, *, resume: bool = False, clock=time.time):
        self.clock = clock
        super().__init__(task_dir, identity, resume=resume)
        if not resume:
            self.data["phase_clock"] = {}
            self.start_phase("development")
        elif "development" not in self.data.get("phase_clock", {}):
            raise ProtocolError("ledger has no recorded development deadline; it cannot be resumed")

    def start_phase(self, name: str) -> dict:
        """Record a phase's first start once; later calls return the same window."""
        clock = self.data.setdefault("phase_clock", {})
        if name not in clock:
            seconds = self.data["identity"][f"{name}_watchdog_seconds"]
            now = self.clock()
            clock[name] = {"started_at": now, "watchdog_seconds": seconds,
                           "deadline": now + seconds}
            self.save()
        return clock[name]

    def remaining(self, name: str = "development") -> float:
        return self.data["phase_clock"][name]["deadline"] - self.clock()

    def passed(self, record: dict) -> bool:
        return (record.get("status") == "complete" and record.get("sandbox_rc") == 0
                and bool(record.get("task_completed")))

    def observation_fallback(self, bundle: dict, source: str) -> bool:
        return False

    def validate_admission(self, phase: str, seed: int, digest: str) -> None:
        if seed in HELDOUT_SEEDS:
            raise ProtocolError("seeds 1-50 are held out; only the outer evaluation runs them")
        if self.remaining() <= 0:
            raise DeadlineExpired("the development deadline has passed; nothing more is admitted "
                                  "and the remaining budget stays unspent")
        super().validate_admission(phase, seed, digest)

    def budget(self) -> dict:
        used = sum(self.retries_used(s) for s in self.seeds)
        return {"charged_attempts_used": used,
                "charged_attempts_limit": RETRY_LIMIT * len(self.seeds),
                "phase_clock": self.data["phase_clock"],
                "development_seconds_remaining": max(0.0, self.remaining())}


def streaming_config_errors(text: str, profile=PROFILE) -> list[str]:
    from aspire.sim.cap.world_model.World import Cadence
    try:
        config = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"streaming config is not valid JSON: {exc}"]
    if not isinstance(config, dict):
        return ["streaming config must be a JSON object"]
    errors = []
    semantic = profile in (SEMANTIC_PROFILE, ARTICULATION_PROFILE)
    allowed = AUTHORED_CONFIG_KEYS | ({"parts"} if semantic else set()) | ({"joints"} if profile == ARTICULATION_PROFILE else set())
    object_keys = OBJECT_KEYS | ({"selector"} if semantic else set())
    frozen = set(config) - allowed
    if frozen:
        errors.append(f"streaming config may set only {sorted(allowed)}; "
                      f"{sorted(frozen)} are frozen engineering runtime fields")
    objects = config.get("objects")
    if not isinstance(objects, list) or not objects:
        return errors + ["streaming config needs a nonempty `objects` list of public prompts"]
    identities = []
    for item in objects:
        if (not isinstance(item, dict) or set(item) - object_keys
                or not all(isinstance(item.get(k), str) and item[k].strip()
                           for k in ("identity", "prompt"))):
            return errors + ["each object needs nonempty string identity and prompt, "
                             "optionally min_seed_score"]
        score = item.get("min_seed_score", 0.5)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            errors.append("min_seed_score must lie between zero and one")
        identities.append(item["identity"])
        selector = item.get("selector")
        if selector is not None and (not isinstance(selector, dict) or set(selector) != {"axis", "rank"}
                or selector["axis"] not in ("image_x", "image_y") or type(selector["rank"]) is not int or selector["rank"] < 0):
            errors.append("selector requires image_x/image_y axis and a nonnegative integer rank")
    if len(set(identities)) != len(identities) or "robot" in identities:
        errors.append("object identities must be unique and may not be 'robot'")
    if semantic:
        from aspire.sim.cap.world_model.semantic_world import validate_parts
        try:
            validate_parts(config.get("parts"), set(identities))
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid scene-derived parts: {exc}")
    if profile == ARTICULATION_PROFILE and "joints" in config:
        from aspire.sim.cap.world_model.articulated_world import validate_joints
        try:
            nodes = validate_parts(config.get("parts"), set(identities))
            validate_joints(config["joints"], nodes)
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid public joint hypotheses: {exc}")
    try:
        Cadence(**config.get("cadence", {}))
    except (TypeError, ValueError) as exc:
        errors.append(f"invalid cadence: {exc}")
    return errors


def world_digest(identity: dict, authored_sha256: str) -> tuple[str, dict]:
    components = {"authored_streaming_config_sha256": authored_sha256,
                  "authored_by": "cell solver (policy, prompts, predicates)",
                  "engineered_world_digest": identity["engineered_world"]["digest"],
                  "sam2_checkpoint_sha256": identity["sam2"]["checkpoint_sha256"],
                  "sam2_source_sha256": identity["sam2"]["source_sha256"],
                  "streaming_mode": PROFILES.get(identity.get("profile"), STREAMING_MODE)}
    return code_hash(json.dumps(components, sort_keys=True)), components


def check_authored(source: str) -> None:
    """Ground-truth refusal plus the oracle refusal, for every authored file.

    The development gate is the outer oracle, but the policy and its streaming
    configuration may never query it while executing: env.task_completed(),
    compute_reward(), check_success, the APIS mapping, bound-method owners and
    any handle attribute except task_language are refused here, with the same
    patterns a sealed legacy cell uses. Legacy oracle cells keep check_policy's
    default and are unaffected.
    """
    check_policy(source)
    raw = [what for pattern, what in RAW_ASSET_PATTERNS if re.search(pattern, source)]
    if raw:
        raise ProtocolError("authored policy/world may not read simulator state or assets: "
                            + "; ".join(raw))
    problems = gate_module().source_errors(source)
    if problems:
        raise ProtocolError("authored policy/world may not reach the oracle or env internals: "
                            + "; ".join(p.replace("sealed cell refuses ", "") for p in problems))


def collect_bundle(case: dict, repo: Path, state: StreamingFixLoopState, phase: str,
                   code: Path | None, streaming: Path | None) -> tuple[dict, dict, dict]:
    if phase == "snapshot":
        if code is not None or streaming is not None:
            raise ProtocolError("the public scene snapshot runs the framework script only")
        path = repo / "scripts/libero/scene_snapshot.py"
        return {"policy": code_hash(path.read_text())}, {"policy": str(path)}, {}
    if code is None or streaming is None:
        raise ProtocolError("--code and --streaming-config are required")
    policy = authored_path(state.task_dir, code, "--code")
    source = policy.read_text()
    if not valid_program(source):
        raise ProtocolError("program must contain executable Python, not an empty file or prose")
    check_authored(source)
    config = authored_path(state.task_dir, streaming, "--streaming-config")
    text = config.read_text()
    problems = streaming_config_errors(text, state.data["identity"]["profile"])
    if problems:
        raise ProtocolError("; ".join(problems))
    check_authored(text)
    digest, components = world_digest(state.data["identity"], code_hash(text))
    return ({"policy": code_hash(source), "world": digest},
            {"policy": str(policy), "streaming_config": str(config)}, components)


def review_config(case: dict) -> dict:
    return {"endpoint": case["model_service"]["endpoint"], "model": case["model"],
            "effort": case["effort"], "context_tokens": case["context_tokens"],
            "max_output_tokens": case["max_output_tokens"], "timeout_seconds": case["trial_timeout"]}


def materialize_config(case: dict, directory: Path, authored: Path) -> Path:
    config = json.loads(authored.read_text())
    runtime = {"mode": PROFILES[case["profile"]], "camera": case["streaming"].get("camera", "agentview"),
               "sam2": {k: case["streaming"]["sam2"][k] for k in SAM2_KEYS},
               "objects": config["objects"], "cadence": config.get("cadence", {})}
    if case["profile"] in (SEMANTIC_PROFILE, ARTICULATION_PROFILE):
        runtime["parts"] = config["parts"]
    if case["profile"] == ARTICULATION_PROFILE:
        runtime.update(review=review_config(case))
        if "joints" in config:
            runtime["joints"] = config["joints"]
    (directory / "authored_streaming_config.json").write_bytes(authored.read_bytes())
    path = directory / "streaming_world_config.json"
    path.write_text(json.dumps(runtime, indent=2) + "\n")
    return path


def trial_command(case: dict, repo: Path, directory: Path, phase: str, seed: int,
                  config: Path | None) -> list[str]:
    command = [str(repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"],
               "--args.trial", str(seed), "--args.model", case["model"],
               "--args.config", case["env_config"],
               "--args.output-dir", str(directory / "results"),
               "--args.replay-code", str(directory / "code.py")]
    if phase != "snapshot":
        # The explicit streaming profile, never combined with frozen study configs.
        command += ["--args.streaming-world-config", str(config)]
    return command


def trial_env(case: dict, repo: Path, task_dir: Path) -> dict:
    env = runtime_env(case, repo)
    root = str(Path(case["streaming"]["sam2"]["source_root"]).resolve())
    env["PYTHONPATH"] = os.pathsep.join([root, env["PYTHONPATH"]])
    env["SNAPSHOT_DIR"] = str(task_dir)
    env["ASPIRE_MAX_STEPS"] = str(case["max_steps"])
    # Trusted boundary: only the trial process's loader sees the registry path;
    # it is never placed in the policy/world namespace.
    for key in (ist.MANIFEST_ENV, ist.MANIFEST_SHA_ENV, ist.REQUIRED_ENV):
        env.pop(key, None)
    if case.get("initial_state_manifest"):
        env[ist.MANIFEST_ENV] = str(Path(case["initial_state_manifest"]).resolve())
        env[ist.MANIFEST_SHA_ENV] = case["initial_state_manifest_sha256"]
        env[ist.REQUIRED_ENV] = "1"
    return env


def startup_error(results: Path) -> dict | None:
    found = sorted(results.rglob("streaming_world_startup_error.json")) if results.exists() else []
    return read_json(found[0]) if found else None


def run_trial(case, repo, state, phase, seed, code, streaming) -> dict:
    with (state.task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("another trial is running; await its result") from exc
        state.data = json.loads(state.path.read_text())  # No stale ledger under the lock.
        return _run_trial(case, repo, state, phase, seed, code, streaming)


def _run_trial(case, repo, state, phase, seed, code, streaming) -> dict:
    verify_runtime(case, repo)
    if seed in HELDOUT_SEEDS:
        raise ProtocolError("seeds 1-50 are held out; only the outer evaluation runs them")
    try:
        bundle, sources, components = collect_bundle(case, repo, state, phase, code, streaming)
    except ProtocolError as exc:
        # Refused before any process started: preserved, and spends no retry.
        state.reject(phase, seed, str(exc), {"code": str(code) if code else None,
                                             "streaming_config": str(streaming) if streaming else None})
        raise
    state.validate_admission(phase, seed, bundle_identity(bundle))
    record = state.begin_trial(phase, seed, bundle, sources)
    started = state.clock()
    timeout = min(case["trial_timeout"], max(1, int(state.remaining())))
    record.update(bundle_components=components, started_at=started,
                  development_deadline=state.data["phase_clock"]["development"]["deadline"],
                  watchdog_seconds=timeout, deadline_clipped=timeout < case["trial_timeout"])
    state.save()
    directory = state.task_dir / record["directory"]
    (directory / "code.py").write_bytes(Path(sources["policy"]).read_bytes())
    config = (materialize_config(case, directory, Path(sources["streaming_config"]))
              if phase != "snapshot" else None)
    command = trial_command(case, repo, directory, phase, seed, config)
    results = directory / "results"
    (directory / "command.json").write_text(json.dumps(
        {"command": command, "result_root": str(results), "profile": case["profile"]}, indent=2))
    exit_code, error = run_replay(command, repo=repo, env=trial_env(case, repo, state.task_dir),
                                  directory=directory, timeout=timeout)
    result = parse_result(results, seed)
    if phase == "snapshot" and (not result or result["sandbox_rc"] or not all(
            (state.task_dir / n).is_file() for n in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"))):
        result, error = None, error or "snapshot did not produce both camera images; inspect replay.log"
    diagnostic_error = None
    if phase == "diagnostic" and result is not None:
        # Same streaming runtime, charged, and never graded evidence.
        if result["sandbox_rc"]:
            diagnostic_error = {"sandbox_rc": result["sandbox_rc"], "session_dir": result["trial_dir"]}
            result = None
        else:
            result = {"sandbox_rc": 0, "trial_dir": result["trial_dir"], "session": "diagnostic",
                      **state.failure_outcome("diagnostic session grades nothing")}
    try:
        verify_runtime(case, repo)
        if identity(case, repo) != state.data["identity"]:
            raise RuntimeChanged("cell identity (engineering World, SAM2, config or protocol) "
                                 "changed during the trial")
    except RuntimeChanged as exc:
        error = str(exc)
    if error:
        result = None
    world_error = None
    receipt = startup_error(results)
    if not error and exit_code not in (None, 0) and receipt and receipt.get("authored"):
        # The authored public prompts could not seed: a charged program fault.
        world_error = {"stage": "streaming_startup", **receipt}
    if error and "TimeoutExpired" in error:
        record["timed_out"] = True
    state.finish_trial(record, result=result, exit_code=exit_code, error=error,
                       world_error=world_error, diagnostic_error=diagnostic_error)
    finished = state.clock()
    record.update(finished_at=finished, duration_seconds=finished - started,
                  streaming_artifacts={name: str(p) for name in RESULT_FILES
                                       for p in results.rglob(name)} if results.exists() else {},
                  **({"streaming_startup": receipt} if receipt else {}))
    state.save()
    return record


def resolve_interrupted(case, repo, state, directory: str) -> dict:
    """Outer coordinator only: settle a `running` row from its recorded evidence.

    The charged attempt stays charged; no simulator process is started here.
    """
    rows = [r for r in state.unresolved() if r["directory"] == directory]
    if len(rows) != 1:
        raise ProtocolError("name exactly one unresolved attempt directory")
    record = rows[0]
    root = state.task_dir / directory
    lifecycle = read_json(root / "lifecycle.json") or {}
    exit_code = lifecycle.get("process_exit_code")
    result = parse_result(root / "results", record["seed"]) if record["phase"] != "diagnostic" else None
    state.resolve_interrupted(record, result=result, exit_code=exit_code,
                              error=lifecycle.get("error") or "",
                              recovery={"by": "outer coordinator", "at": state.clock(),
                                        "bundle_sha256": record["bundle_sha256"]})
    return record


def selected_bundle(case, state) -> tuple[dict, dict]:
    config = state.task_dir / "fix_streaming_config.json"
    text = config.read_text()
    problems = streaming_config_errors(text, state.data["identity"]["profile"])
    if problems:
        raise ProtocolError("; ".join(problems))
    digest, components = world_digest(state.data["identity"], code_hash(text))
    return {"policy": code_hash((state.task_dir / "fix_code.py").read_text()),
            "world": digest}, components


def check(case, repo, state) -> dict:
    progress = state.progress()
    expired = state.remaining() <= 0
    errors = [f"{key}: {progress[key]}" for key in (
        "seeds_needing_initial", "seeds_exhausted_without_graded_evidence",
        "seeds_pending_repair", "blocked_notes_required") if progress[key]]
    if state.unresolved():
        errors.append("an attempt is still running; the outer coordinator must resolve it")
    if not progress["graded_executions"]:
        errors.append("no graded development execution exists")
    if not progress["snapshot_complete"]:
        errors.append("missing completed scene snapshot")
    analysis = state.task_dir / "task_analysis.md"
    if not analysis.is_file() or not analysis.read_text().strip():
        errors.append("missing task_analysis.md")
    errors += report_errors(state.task_dir / "findings.md", (
        "Root causes observed", "What fixed them", "Generalizable patterns", "Blocked seeds"))
    bundle = None
    fix = state.task_dir / "fix_code.py"
    if not fix.is_file() or not valid_program(fix.read_text()):
        errors.append("missing executable fix_code.py")
    else:
        try:
            bundle, _ = selected_bundle(case, state)
        except (OSError, ProtocolError) as exc:
            errors.append(f"fix_streaming_config.json: {exc}")
    if bundle is not None:
        errors += state.selection_errors(bundle, fix.read_text())
        selection = state.data.get("selected")
        if not selection or not selection.get("reason", "").strip():
            errors.append("record the selection reason with `select`")
        elif selection["bundle_sha256"] != bundle_identity(bundle):
            errors.append("the recorded selection does not match the files on disk")
    decision = "ready" if not errors else ("terminal" if expired or progress[
        "seeds_exhausted_without_graded_evidence"] else "incomplete")
    return {"ready": not errors, "decision_type": decision, "errors": errors,
            "deadline_expired": expired, "budget": state.budget(), "progress": progress,
            "selected": state.data.get("selected"),
            "coverage": state.final_coverage(bundle) if bundle else [],
            # Reported, never blocking: timeouts and runner failures stay charged.
            "infrastructure_errors": progress["infrastructure_errors"]}


def select(case, repo, state, reason: str) -> dict:
    if state.remaining() <= 0:
        raise DeadlineExpired("the development deadline has passed; selection is closed")
    if state.unresolved():
        raise ProtocolError("an attempt is still running; resolve it before selecting")
    bundle, components = selected_bundle(case, state)
    errors = state.selection_errors(bundle, (state.task_dir / "fix_code.py").read_text())
    if errors:
        raise ProtocolError("; ".join(errors))
    selected = state.select(bundle, reason)
    selected.update(selected_at=state.clock(), bundle_components=components,
                    attempt_references=[r["directory"] for r in state.final_coverage(bundle)])
    state.save()
    return selected


def native_provenance(case: dict, observed: dict, worker_settings: Path | None,
                      probe: Path | None) -> tuple[dict, list[str]]:
    """Configured values, the settings the worker actually ran with, and what was served.

    The three are kept apart. The case only declares; `worker_settings` is the
    settings file the native launcher passed to the worker (local_vllm_settings
    shape) and `probe` is its recorded native probe (native_world_campaign.probe
    shape). Claude Code reports a custom alias's built-in default in
    modelUsage.maxOutputTokens, so the 64K output limit is proven by the effective
    request setting, exactly as the legacy probe checks it; the context window
    must be the one actually served.
    """
    configured = {k: case[k] for k in ("model", "effort", "context_tokens", "max_output_tokens")}
    configured["endpoint"] = case["model_service"]["endpoint"]
    errors = []
    if set(observed["model_served"]) != {case["model"]}:
        errors.append(f"native model provenance mismatch: {observed['model_served']}")
    if observed["context_windows"] != [case["context_tokens"]]:
        errors.append(f"actual context window mismatch or missing: {observed['context_windows']}")
    record = {"configured": configured, "observed": observed,
              "worker_settings": None, "probe": None}
    settings = read_json(worker_settings) if worker_settings and Path(worker_settings).is_file() else None
    if not isinstance(settings, dict):
        errors.append("missing recorded worker settings (effective effort, output limit and route)")
    else:
        env = settings.get("env") or {}
        effective = {"model": settings.get("model"), "effortLevel": settings.get("effortLevel"),
                     "api_key_helper": "apiKeyHelper" in settings,
                     **{k: env.get(k) for k in (
                         "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
                         "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
                         "CLAUDE_CODE_MAX_OUTPUT_TOKENS")}}
        wanted = {"model": case["model"], "effortLevel": case["effort"], "api_key_helper": False,
                  "ANTHROPIC_BASE_URL": case["model_service"]["endpoint"],
                  "ANTHROPIC_MODEL": case["model"], "CLAUDE_CODE_EFFORT_LEVEL": case["effort"],
                  "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1",
                  "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(case["context_tokens"]),
                  "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(case["max_output_tokens"])}
        bad = {k: effective[k] for k in wanted if effective[k] != wanted[k]}
        if bad:
            errors.append(f"effective worker settings mismatch: {bad}")
        record["worker_settings"] = {"path": str(worker_settings),
                                     "sha256": file_sha256(worker_settings), "effective": effective}
    proof = read_json(probe) if probe and Path(probe).is_file() else None
    if not isinstance(proof, dict):
        errors.append("missing local route proof (native probe record)")
    else:
        wanted = {"exit_code": 0, "model_served": [case["model"]],
                  "context_windows": [case["context_tokens"]]}
        bad = {k: proof.get(k) for k, v in wanted.items() if proof.get(k) != v}
        declared = proof.get("configured") or {}
        bad.update({f"configured.{k}": declared.get(k) for k in (
            "effort", "context_tokens", "max_output_tokens") if declared.get(k) != case[k]})
        if bad:
            errors.append(f"local route proof mismatch: {bad}")
        record["probe"] = {"path": str(probe), "sha256": file_sha256(probe),
                           **{k: proof.get(k) for k in wanted}}
    return record, errors


def finalize(case, repo, state, transcripts: list[Path], worker_settings: Path | None = None,
             probe: Path | None = None) -> dict:
    verify_runtime(case, repo)
    status = check(case, repo, state)
    errors = list(status["errors"])
    selection = state.data.get("selected") or {}
    if selection.get("selected_at", float("inf")) > state.data["phase_clock"]["development"]["deadline"]:
        errors.append("no selection was recorded before the development deadline")
    served, usage, contexts, outputs = {}, {"input_tokens": 0, "output_tokens": 0}, set(), set()
    for path in transcripts:
        for line in path.read_text().splitlines():
            record = read_json_line(line)
            if record.get("type") == "assistant":
                model = record.get("message", {}).get("model")
                if model and not model.startswith("<"):
                    served[model] = served.get(model, 0) + 1
            if record.get("type") == "result":
                for key in usage:
                    usage[key] += record.get("usage", {}).get(key, 0)
                for entry in (record.get("modelUsage") or {}).values():
                    if isinstance(entry, dict) and entry.get("contextWindow"):
                        contexts.add(entry["contextWindow"])
                    if isinstance(entry, dict) and entry.get("maxOutputTokens"):
                        outputs.add(entry["maxOutputTokens"])
    provenance, problems = native_provenance(
        case, {"model_served": served, "context_windows": sorted(contexts),
               # Reported alias default, kept visible but not the 64K proof.
               "reported_max_output_tokens": sorted(outputs)}, worker_settings, probe)
    errors += problems
    if errors:
        raise ProtocolError("; ".join(errors))
    bundle, components = selected_bundle(case, state)
    state.data.update(stage1_complete=True, model_served=served, usage=usage,
                      model_context_windows=sorted(contexts))
    state.save()
    identity_ = state.data["identity"]
    result = {
        "schema_version": 1, "profile": case["profile"], "protocol_id": PROTOCOL_ID,
        "harness": "claude-code", "cell": case["id"], "method": case["method"],
        "repeat": case["repeat"], "condition": "C", "development_gate": "oracle",
        "stage1_complete": True, "suite": case["suite"], "task": case["task"],
        "selected_bundle": bundle, "selected_bundle_sha256": bundle_identity(bundle),
        "bundle_components": components, "selection": state.data["selected"],
        "observation_fallback": False,
        "freeze_inputs": {"policy": str(state.task_dir / "fix_code.py"),
                          "authored_streaming_config": str(state.task_dir / "fix_streaming_config.json"),
                          "engineered_world": identity_["engineered_world"],
                          "sam2": identity_["sam2"], "config_sha256": identity_["config_sha256"],
                          "protocol_sha256": identity_["protocol_sha256"]},
        "model_served": served, "usage": usage, "model_context_windows": sorted(contexts),
        "native_provenance": provenance, "model_service": identity_["model_service"],
        "dependency_evidence": identity_["dependency_evidence"],
        "perception": identity_["perception_services"],
        "runtime_manifest_sha256": identity_.get("runtime_manifest_sha256"),
        "environment_version": identity_.get("environment_version"),
        "initial_states": identity_.get("initial_states"),
        "budget": state.budget(), "retry_limit": RETRY_LIMIT,
        "retries_used_per_seed": status["progress"]["retries_used_per_seed"],
        "seed_outcomes": status["progress"]["seeds"],
        "tested_bundles": status["progress"]["tested_bundles"],
        "rejected_revisions": state.data["rejected"], "aliases": state.data["aliases"],
        "world_program_errors": status["progress"]["world_program_errors"],
        "infrastructure_errors": status["progress"]["infrastructure_errors"],
        "diagnostic_program_errors": status["progress"]["diagnostic_program_errors"],
        "graded_executions": status["progress"]["graded_executions"],
        "final_development_coverage": state.final_coverage(bundle),
        "replay_invocations": status["progress"]["replay_invocations"],
        "transcripts": [str(p) for p in transcripts],
    }
    (state.task_dir / "stage1_result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def read_json_line(line: str) -> dict:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def open_state(case_path: Path, action: str, clock=time.time):
    case, repo, task_dir = load_case(case_path)
    state = StreamingFixLoopState(task_dir, identity(case, repo), resume=action != "init",
                                  clock=clock)
    return case, repo, state


def main(argv: list[str] | None = None, clock=time.time) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"))
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("init", "status", "check", "snapshot"):
        sub.add_parser(name)
    trial = sub.add_parser("trial")
    trial.add_argument("--phase", choices=["smoke", "initial", "repair", "diagnostic"], required=True)
    trial.add_argument("--seed", type=int, required=True)
    trial.add_argument("--code", type=Path, required=True)
    trial.add_argument("--streaming-config", type=Path, required=True)
    sub.add_parser("alias-smoke").add_argument("--seed", type=int, required=True)
    sub.add_parser("select").add_argument("--reason", required=True)
    sub.add_parser("resolve-interrupted").add_argument("--directory", required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--transcript", type=Path, action="append", required=True)
    final.add_argument("--worker-settings", type=Path, required=True,
                       help="settings file the native launcher actually passed to the worker")
    final.add_argument("--probe", type=Path, required=True,
                       help="recorded native probe of the local route (probe.json)")
    args = parser.parse_args(argv)
    if not args.case:
        parser.error("--case or ASPIRE_NATIVE_CASE is required")
    try:
        case, repo, state = open_state(args.case, args.action, clock)
        if args.action == "snapshot":
            result = run_trial(case, repo, state, "snapshot", DEV_SEEDS[0], None, None)
        elif args.action == "trial":
            result = run_trial(case, repo, state, args.phase, args.seed, args.code,
                               args.streaming_config)
        elif args.action == "alias-smoke":
            result = state.alias_smoke_as_initial(args.seed)
        elif args.action == "select":
            result = select(case, repo, state, args.reason)
        elif args.action == "resolve-interrupted":
            result = resolve_interrupted(case, repo, state, args.directory)
        elif args.action == "finalize":
            result = finalize(case, repo, state, args.transcript, args.worker_settings, args.probe)
        elif args.action == "check":
            result = check(case, repo, state)
        else:
            result = {**state.progress(), "budget": state.budget()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int((args.action == "check" and not result["ready"])
                   or (args.action in {"trial", "snapshot"} and result["status"] != "complete"))
    except (ProtocolError, RuntimeChanged, OSError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
