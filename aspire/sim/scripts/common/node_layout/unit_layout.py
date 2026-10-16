"""Node layout of 2026-10-15: every 4 GPUs form one unit.

One unit = one Qwen TP4 server + SAM3 + GraspNet + PyRoKi (CPU) + two cell
slots, with perception and simulators on the unit's own Qwen cards. A 4-GPU
worker runs one unit, an 8-GPU worker two (GPUs 0-3 and 4-7). Contract:
`docs/engineering/node-layout-20261015/DESIGN.md`.

Pure functions only: GPU -> unit -> port/slot mapping, the model-server argv,
case binding and the atomic queue claim. Process management lives in
`unit_supervisor.py`. Study prepare scripts freeze a copy of this directory
into their launch dir instead of inheriting an older study's `support/`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import time

GPUS_PER_UNIT = 4
ALLOWED_GPU_COUNTS = (4, 8)
SLOT_NAMES = ("a", "b")
#: Unit 0's ports. Unit u adds `UNIT_PORT_STRIDE * u` to every service.
DEFAULT_PORTS = {"model": 8121, "sam3": 8114, "graspnet": 8115, "pyroki": 8116}
UNIT_PORT_STRIDE = 10
#: Relative card index inside the unit (DESIGN §2/§4): SAM3 0, GraspNet 1,
#: slot-a simulator 2, slot-b simulator 3. PyRoKi runs on CPU.
DEFAULT_PLACEMENT = {"sam3": 0, "graspnet": 1, "a": 2, "b": 3}
#: DESIGN §4 (measured 2026-10-15): 0.82. The KV pool must hold one 1M-token
#: request, which puts the floor near 0.76.
DEFAULT_UTIL = 0.82
UTIL_FLOOR = 0.76
UTIL_CEILING = 0.95
#: DESIGN §1: full CUDA graphs for decode only, no torch.compile.
COMPILATION_CONFIG = {"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY",
                      "cudagraph_capture_sizes": [1, 2, 4]}
#: Case fields a slot sets. They differ between cells of one queue by design,
#: so the runtime-family comparison ignores them.
SLOT_FIELDS = ("gpu", "cuda_visible_devices", "egl_device_id", "inference_endpoint",
               "sam3_url", "graspnet_url", "pyroki_url", "service_ports")
#: Everything else one unit's services were started for must agree across cells.
FAMILY_KEYS = ("model", "model_tag", "expected_served_model", "model_provider",
               "context_tokens", "max_output_tokens", "effort", "claude_bin", "env_config",
               "condition", "profile", "c_arm", "foundation_revision",
               "executable_world_revision", "prediction_contract", "development_gate")
#: A cell with any of these already ran or was interrupted: never claimable.
STARTED_MARKERS = ("campaign_state.json", "native_smoke_state.json", "queue-result.json")


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Slot:
    unit: int
    name: str
    gpu: int


@dataclass(frozen=True)
class Unit:
    index: int
    gpus: tuple[int, ...]
    ports: dict = field(hash=False)
    placement: dict = field(hash=False)

    def card(self, role: str) -> int:
        return self.gpus[self.placement[role]]

    @property
    def slots(self) -> tuple[Slot, ...]:
        return tuple(Slot(self.index, name, self.card(name)) for name in SLOT_NAMES)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.ports['model']}"

    def url(self, service: str) -> str:
        return f"http://127.0.0.1:{self.ports[service]}"

    def describe(self) -> dict:
        return {"unit": self.index, "gpus": list(self.gpus), "ports": dict(self.ports),
                "placement": {"model": list(self.gpus), "sam3": self.card("sam3"),
                              "graspnet": self.card("graspnet"), "pyroki": "cpu",
                              **{f"slot_{s.name}_simulator": s.gpu for s in self.slots}}}


def parse_gpus(value) -> list[int]:
    items = value.split(",") if isinstance(value, str) else list(value)
    try:
        gpus = [int(str(item).strip()) for item in items if str(item).strip() != ""]
    except ValueError as exc:
        raise LayoutError(f"GPU list must be integers: {value!r}") from exc
    if len(gpus) not in ALLOWED_GPU_COUNTS:
        raise LayoutError(f"the unit layout needs 4 or 8 GPUs, got {len(gpus)}: {gpus}")
    if len(set(gpus)) != len(gpus) or any(g < 0 for g in gpus):
        raise LayoutError(f"GPU list must be distinct non-negative indices: {gpus}")
    return gpus


def validate_placement(placement: dict) -> dict:
    if set(placement) != set(DEFAULT_PLACEMENT):
        raise LayoutError(f"placement keys must be {sorted(DEFAULT_PLACEMENT)}: {placement}")
    if any(type(v) is not int or not 0 <= v < GPUS_PER_UNIT for v in placement.values()):
        raise LayoutError(f"placement values are unit-relative card indices 0-3: {placement}")
    if placement["a"] == placement["b"]:
        raise LayoutError("the two slot simulators must sit on different cards")
    return dict(placement)


def unit_ports(index: int, base: dict = DEFAULT_PORTS, stride: int = UNIT_PORT_STRIDE) -> dict:
    if set(base) != set(DEFAULT_PORTS):
        raise LayoutError(f"port keys must be {sorted(DEFAULT_PORTS)}: {base}")
    return {name: int(port) + stride * index for name, port in base.items()}


def units(gpus, *, ports: dict = DEFAULT_PORTS, stride: int = UNIT_PORT_STRIDE,
          placement: dict = DEFAULT_PLACEMENT) -> list[Unit]:
    """Physical GPU list (4 or 8) -> units of four consecutive entries."""
    gpus = parse_gpus(gpus)
    placement = validate_placement(placement)
    result = [Unit(i, tuple(gpus[i * GPUS_PER_UNIT:(i + 1) * GPUS_PER_UNIT]),
                   unit_ports(i, ports, stride), placement)
              for i in range(len(gpus) // GPUS_PER_UNIT)]
    every = [p for unit in result for p in unit.ports.values()]
    if len(set(every)) != len(every) or any(not 1024 <= p <= 65535 for p in every):
        raise LayoutError(f"unit ports collide or are out of range: {[u.ports for u in result]}")
    return result


def parse_mapping(text: str, keys) -> dict:
    """`model=8121,sam3=8114,...` -> {name: int}; every key exactly once."""
    pairs = [item.split("=", 1) for item in text.split(",") if item.strip()]
    if any(len(pair) != 2 for pair in pairs):
        raise LayoutError(f"expected name=value pairs: {text!r}")
    mapping = {k.strip(): int(v) for k, v in pairs}
    if set(mapping) != set(keys) or len(pairs) != len(mapping):
        raise LayoutError(f"expected exactly {sorted(keys)}: {text!r}")
    return mapping


def slot_binding(unit: Unit, slot: Slot) -> dict:
    """What a cell running in this slot is bound to for its whole life."""
    return {"unit": unit.index, "slot": slot.name,
            "gpu": slot.gpu, "cuda_visible_devices": str(slot.gpu), "egl_device_id": slot.gpu,
            "inference_endpoint": unit.endpoint,
            "sam3_url": unit.url("sam3"), "graspnet_url": unit.url("graspnet"),
            "pyroki_url": unit.url("pyroki"),
            "service_ports": [unit.ports["sam3"], unit.ports["graspnet"], unit.ports["pyroki"]],
            "model_gpus": list(unit.gpus), "sam3_gpu": unit.card("sam3"),
            "graspnet_gpu": unit.card("graspnet"), "pyroki_device": "cpu"}


def bind_case(case: dict, binding: dict) -> dict:
    """The staged case with this slot's wiring; the binding itself is recorded too."""
    bound = dict(case)
    for key in SLOT_FIELDS:
        bound[key] = binding[key]
    bound["slot_binding"] = dict(binding)
    return bound


def same_runtime_family(reference: dict, case: dict) -> list[str]:
    """Keys that differ although one unit's services serve both cells.

    Slot wiring is deliberately excluded: two cells of one queue run in
    different slots, and the wiring each one gets comes from its slot.
    """
    return [key for key in FAMILY_KEYS if reference.get(key) != case.get(key)]


# ---- model server ---------------------------------------------------------


def _option(argv: list, name: str):
    return argv[argv.index(name) + 1] if name in argv else None


def _set_option(argv: list, name: str, value: str) -> list:
    argv = list(argv)
    if name in argv:
        argv[argv.index(name) + 1] = value
    else:
        argv += [name, value]
    return argv


def expandable_segments(env: dict) -> bool:
    return "expandable_segments" in env.get("PYTORCH_CUDA_ALLOC_CONF", "")


def check_reference(reference: dict) -> None:
    """DESIGN §1 invariants of the reference model-server config."""
    argv, env = reference["argv"], reference["env"]
    problems = []
    if "--enforce-eager" in argv:
        problems.append("--enforce-eager is present")
    raw = _option(argv, "--compilation-config")
    if raw is None or json.loads(raw) != COMPILATION_CONFIG:
        problems.append(f"--compilation-config is {raw!r}, expected {COMPILATION_CONFIG}")
    if expandable_segments(env):
        problems.append("PYTORCH_CUDA_ALLOC_CONF sets expandable_segments")
    if "--disable-custom-all-reduce" in argv:
        problems.append("custom all-reduce must stay on")
    if _option(argv, "--tensor-parallel-size") != str(GPUS_PER_UNIT):
        problems.append("one unit serves one TP4 model")
    if problems:
        raise LayoutError("model-server reference violates DESIGN §1: " + "; ".join(problems))


def reference_from_eager(eager: dict, util: float = DEFAULT_UTIL) -> dict:
    """Apply DESIGN §1 to the frozen eager config; nothing else changes."""
    reference = json.loads(json.dumps(eager))
    argv = [a for a in reference["argv"] if a != "--enforce-eager"]
    argv = _set_option(argv, "--gpu-memory-utilization", f"{util:.2f}")
    argv = _set_option(argv, "--compilation-config",
                       json.dumps(COMPILATION_CONFIG))
    reference["argv"] = argv
    reference["env"] = {k: v for k, v in reference["env"].items()
                        if not (k == "PYTORCH_CUDA_ALLOC_CONF" and "expandable_segments" in v)}
    check_reference(reference)
    return reference


def model_server(reference: dict, unit: Unit, util: float) -> dict:
    """The one model-server command for this unit: its port, cards and util."""
    check_reference(reference)
    if not UTIL_FLOOR <= float(util) <= UTIL_CEILING:
        raise LayoutError(f"gpu-memory-utilization {util} outside [{UTIL_FLOOR}, {UTIL_CEILING}]")
    argv = _set_option(reference["argv"], "--port", str(unit.ports["model"]))
    argv = _set_option(argv, "--gpu-memory-utilization", f"{float(util):.2f}")
    env = dict(reference["env"], CUDA_VISIBLE_DEVICES=",".join(map(str, unit.gpus)))
    return {"argv": argv, "env": env, "cwd": reference.get("cwd"), "endpoint": unit.endpoint,
            "model": reference["model"], "context_tokens": reference["context_tokens"],
            "util": float(util), "unit": unit.index}


# ---- queue ------------------------------------------------------------------


def write_once(path: Path, value) -> None:
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def started(cell: Path) -> bool:
    return any((Path(cell) / marker).exists() for marker in STARTED_MARKERS)


def claim_next(root: Path, cells: list[Path], worker: str, binding: dict) -> Path | None:
    """Atomically claim the next unclaimed cell for one slot.

    Same semantics as p3: `mkdir` either creates the claim directory or fails
    because another slot (of this or another node) already did. A cell with any
    run record is never claimable; an interrupted cell keeps its claim and
    state for manual review.
    """
    claims = Path(root) / "queue/claims"
    claims.mkdir(parents=True, exist_ok=True)
    for cell in cells:
        if started(cell):
            continue
        claim = claims / Path(cell).name
        try:
            claim.mkdir()
        except FileExistsError:
            continue
        write_once(claim / "claim.json", {"cell": Path(cell).name, "worker": worker,
                                          "slot_binding": binding, "host": os.uname().nodename,
                                          "claimed_at": time.time()})
        return Path(cell)
    return None


# ---- DLC plan --------------------------------------------------------------


def dlc_plan_argv(name: str, node: int, nodes: int, root, max_minutes: int, gpus: int,
                  *, priority: int = 9, margin_minutes: int = 10) -> list[str]:
    """`dlc-run plan` for one single-node job; submission is a separate approved act."""
    if gpus not in ALLOWED_GPU_COUNTS:
        raise LayoutError(f"a unit-layout job requests 4 or 8 GPUs, not {gpus}")
    if type(node) is not int or type(nodes) is not int or not 0 <= node < nodes:
        raise LayoutError("node IDs are distinct integers 0..nodes-1")
    if type(max_minutes) is not int or max_minutes <= margin_minutes + 60:
        raise LayoutError("max_minutes must leave a node window of at least 60 minutes")
    return ["/mnt/home/gewang/.local/bin/dlc-run", "plan", "--name", f"{name}-node{node}",
            "--gpus", str(gpus), "--workdir", str(root), "--max-minutes", str(max_minutes),
            "--priority", str(priority), "--skip-preflight", "--",
            "bash", str(Path(root) / "launch/unit-entry.sh"),
            "--node-id", str(node), "--node-count", str(nodes),
            "--node-window-minutes", str(max_minutes - margin_minutes)]


#: Frozen into `<result root>/launch/` by a study's prepare script.
LAUNCH_FILES = ("unit_layout.py", "unit_supervisor.py", "unit-entry.sh", "sam3_service_health.py",
                "reference/model-server.json", "reference/sam3-public-dev51-agentview.jpg",
                "reference/sam3-public-image-provenance.json")


def freeze_launch(launch: Path, study: dict, *, compat: Path, extra: dict | None = None) -> dict:
    """Copy this layer, `native_cc_runtime.py` and the study's compat fixture into
    `launch`, write `study.json` and a manifest of every file. Never overwrites."""
    import hashlib
    import shutil
    here = Path(__file__).resolve().parent
    sources = {name: here / name for name in LAUNCH_FILES}
    sources["native_cc_runtime.py"] = here.parent / "native_cc_runtime.py"
    sources["qwen-native-compat.py"] = Path(compat)
    sources.update({k: Path(v) for k, v in (extra or {}).items()})
    launch = Path(launch)
    if launch.exists():
        raise LayoutError(f"{launch} exists; stage a fresh launch directory")
    for name, source in sources.items():
        (launch / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, launch / name)
    (launch / "unit-entry.sh").chmod(0o755)
    check_reference(json.loads((launch / "reference/model-server.json").read_text()))
    write_once(launch / "study.json", study)
    files = {name: hashlib.sha256((launch / name).read_bytes()).hexdigest()
             for name in [*sources, "study.json"]}
    manifest = {"files": files, "layout": "node-layout-20261015 (4 GPUs per unit, 2 slots per unit)",
                "sources": {name: str(path) for name, path in sources.items()},
                "frozen_at": time.time()}
    write_once(launch / "launch-manifest.json", manifest)
    return manifest


def register_worker(root: Path, launch: Path, node: int, nodes: int, gpus: list[int]) -> str:
    """Write-once node identity before any service; a rank collision is refused."""
    import hashlib
    if type(node) is not int or type(nodes) is not int or not 0 <= node < nodes:
        raise LayoutError("node IDs are distinct integers 0..nodes-1")
    worker = f"worker-node{node}"
    registry = Path(root) / "queue/worker-registry"
    registry.mkdir(parents=True, exist_ok=True)
    manifest = Path(launch) / "launch-manifest.json"
    write_once(registry / f"{worker}.json", {
        "worker": worker, "global_node_id": node, "node_count": nodes, "gpus": list(gpus),
        "units": len(gpus) // GPUS_PER_UNIT, "host": os.uname().nodename, "pid": os.getpid(),
        "started_at": time.time(),
        "launch_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()})
    return worker
