#!/usr/bin/env python3
"""Single-node 8-GPU DLC entry for ONE new SAM2-streaming Fix Loop cell.

Operational orchestration only: no LLM client and no agent loop. Generation,
repair, selection, freeze and the held-out run all belong to the existing
`scripts/libero/streaming_fixloop_campaign.py` from the frozen runtime. This
entry runs as root inside one DLC worker and does the following, in order:

1. Loads the outer deployment JSON and the outer-prepared case. Both are bound
   by sha256. Checks the protocol constants: condition C, oracle gate, seeds
   51-65 with 3 charged attempts each, 900 s / 14 h / 5 h, the fixed Qwen
   solver and the fixed initial-state manifest. The case is validated with the
   runtime's own campaign `load`.
2. Static identity: the declared UID/GID must be free or must match exactly.
   Mode bits must keep the run user out of every other cell's directories, and
   the user must be able to read the runtime and evidence it needs.
3. Real checks: Qwen weight sha256 (all files), SAM3/SAM2/GraspNet/robot model
   digests, installed versions, the CUDA 13 toolchain, the runtime freeze, the
   task manifest and the initial-state registry.
4. The DLC topology is one worker with 8 idle NVIDIA GPUs, and no service port
   is already listening.
5. Minimal root setup: the cell's group and user, membership in the existing
   read-only runtime group, its HOME and its control directory. Then real
   probes as that user: identity and access, CUDA and an NVIDIA EGL render on
   the simulator GPU.
6. Starts Qwen (GPUs 2-5), SAM3 and GraspNet (GPU 6) and Pyroki (CPU) from the
   recorded manifests. Only the declared path and cache rebases are applied.
   Waits until each serves a synthetic request. Every PID, PGID, argv,
   adaptation and log is recorded.
7. Only then runs the campaign as the unprivileged cell user. Its `init` starts
   the 14 h development clock. The entry waits on that process; it does not
   poll a model.
8. Tears down exactly the processes it started (as a child subreaper), and
   writes the attempt result.

A failure is recorded and the entry exits. Nothing is retried or resubmitted,
and the entry never refunds budget or forges a pass. `--validate-only` runs
steps 1-3 with structure, file and hash checks only. It starts no service,
creates no user and takes no task action, so it is a DSW pre-submit check and
not a paid 8-GPU job. This is not a security sandbox; see AGENTS.md.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SCHEMA = "aspire-streaming-dlc-entry-v1"
CAMPAIGN = "scripts/libero/streaming_fixloop_campaign.py"
# Fixed common conditions this entry re-checks on top of the protocol's own
# validate_case. The entry refuses a case that differs; it never adapts one.
INITIAL_STATE_MANIFEST_SHA256 = "1668dd6b6bebeb0261ed507ab15f6326ac73d7a586180ca2b30a45c8b5a106e4"
CASE_FIXED = {"condition": "C", "development_gate": "oracle", "dev_seeds": list(range(51, 66)),
              "max_steps": 4000, "trial_timeout": 900, "development_watchdog_seconds": 50400,
              "evaluation_watchdog_seconds": 18000, "model": "qwen3.8-flash-next",
              "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000,
              "claude_version": "2.1.220", "dependency_evidence": "frozen",
              "require_runtime_freeze": True,
              "initial_state_manifest_sha256": INITIAL_STATE_MANIFEST_SHA256}
RETRY_LIMIT = 3
# The validated RS layout, kept as is: Qwen TP4 on 2-5, SAM3 + GraspNet on 6,
# simulator / SAM2 / EGL on 7, Pyroki on CPU. GPUs 0-1 stay idle.
GPU_COUNT = 8
LAYOUT = {"qwen": "2,3,4,5", "sam3": "6", "graspnet": "6", "pyroki": "", "simulator": "7",
          "egl_device_id": 7}
PERCEPTION = {"sam3-service": ("sam3", "sam3_url"), "graspnet-service": ("graspnet", "graspnet_url"),
              "pyroki-service": ("pyroki", "pyroki_url")}
SERVICE_PORTS = ("qwen", "sam3", "graspnet", "pyroki")
IDLE_GPU_MIB = 1024
CREDENTIAL = re.compile(r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|PASSWORD|"
                        r"HF_TOKEN|HUGGING_FACE_HUB_TOKEN|ANTHROPIC|OSS_|ALIBABA_CLOUD|ALIYUN", re.I)
# The RS-local proxy (127.0.0.1:10809) does not exist on DLC, and nothing here
# may fetch weights, Git refs or packages, so every proxy variable is dropped.
PROXY = re.compile(r"^(?:https?|all|ftp|no|socks)_proxy$", re.I)
# DLC launcher variables that would mean more than one worker.
TOPOLOGY = {"WORLD_SIZE": "1", "RANK": "0", "LOCAL_RANK": "0", "NODE_RANK": "0",
            "PET_NNODES": "1", "NNODES": "1"}
PROTECTED_PREFIXES = ("/root",)


class EntryRefused(RuntimeError):
    """A precondition failed. Nothing ran and no budget was touched."""


class StartupFailed(RuntimeError):
    """A service or probe failed before the campaign started (before init)."""


class Terminated(RuntimeError):
    """The DLC worker (or operator) sent a termination signal."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as out:
        json.dump(value, out, indent=2, default=str)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    Path(out.name).replace(path)


def require(problems: list[str], kind=EntryRefused) -> None:
    if problems:
        raise kind("; ".join(problems))


# --- host boundary (every side effect goes through here; tests replace it) -------


class Host:
    environ = os.environ

    def euid(self) -> int:
        return os.geteuid()

    def run(self, argv, **kw) -> subprocess.CompletedProcess:
        kw.setdefault("capture_output", True)
        kw.setdefault("text", True)
        kw.setdefault("timeout", 600)
        return subprocess.run(argv, **kw)

    def popen(self, argv, *, cwd, env, stdout, stderr) -> subprocess.Popen:
        # Own session and process group per launched tree; see Lifecycle.
        return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                                stderr=stderr, start_new_session=True)

    def user(self, name=None, uid=None):
        try:
            return pwd.getpwnam(name) if name is not None else pwd.getpwuid(uid)
        except KeyError:
            return None

    def group(self, name=None, gid=None):
        try:
            return grp.getgrnam(name) if name is not None else grp.getgrgid(gid)
        except KeyError:
            return None

    def listening(self, port: int) -> bool:
        with socket.socket() as sock:
            sock.settimeout(1)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def http(self, url: str, payload: dict | None = None, timeout: float = 120):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()[:2000].decode(errors="replace")
        except OSError as exc:
            return None, str(exc)
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, body[:2000].decode(errors="replace")

    def modules(self, sim: Path, libero_config: str) -> SimpleNamespace:
        """The frozen runtime's own campaign/protocol modules, not a copy of them.

        Imported as root, so no __pycache__ may be written into the frozen tree.
        LIBERO reads its config path at import and otherwise prompts on stdin and
        writes ~/.libero; the case's public config is set and stdin is closed."""
        sys.dont_write_bytecode = True
        # Any bytecode a library still writes goes to a private temp prefix.
        sys.pycache_prefix = tempfile.mkdtemp(prefix="aspire-dlc-pyc-")
        os.environ["LIBERO_CONFIG_PATH"] = str(libero_config)
        for extra in (sim / "scripts/common", sim / "scripts/libero", sim.parents[1]):
            if str(extra) not in sys.path:
                sys.path.insert(0, str(extra))
        stdin, sys.stdin = sys.stdin, open(os.devnull)
        stdout, sys.stdout = sys.stdout, sys.stderr  # import-time prints stay off the report
        try:
            import native_cc_freeze
            import native_cc_runtime
            import streaming_fixloop_campaign as camp
            import streaming_fixloop_protocol as sp
        finally:
            sys.stdin.close()
            sys.stdin, sys.stdout = stdin, stdout
        return SimpleNamespace(camp=camp, sp=sp, freeze=native_cc_freeze, runtime=native_cc_runtime)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def clock(self) -> float:
        return time.monotonic()


# --- deployment and case ----------------------------------------------------------


DEPLOYMENT_KEYS = ("schema", "cell", "workers", "gpus_per_worker", "case", "case_sha256",
                   "runtime_root", "source_checkout_prefix", "envs", "identity", "entry_dir",
                   "forbidden_read_paths", "flashinfer_seed_cache", "service_ready_timeout_seconds")
ENV_KEYS = ("qwen", "libero", "cuda_compat", "cuda_toolkit")
IDENTITY_KEYS = ("user", "uid", "group", "gid", "home", "runtime_group")


def load_deployment(path: Path) -> tuple[dict, str]:
    path = Path(path)
    dep = json.loads(path.read_text())
    problems = [f"deployment requires {k}" for k in DEPLOYMENT_KEYS if k not in dep]
    require(problems)
    if dep["schema"] != SCHEMA:
        problems.append(f"deployment schema must be {SCHEMA}")
    if dep["workers"] != 1 or dep["gpus_per_worker"] != GPU_COUNT:
        problems.append("this entry is exactly one worker with 8 GPUs; multi-worker or other "
                        "GPU counts are refused")
    for key in ("cells", "tasks", "cases", "array", "replicas"):
        if key in dep:
            problems.append(f"one deployment is one new cell; `{key}` is not accepted")
    identity = dep["identity"]
    problems += [f"identity requires {k}" for k in IDENTITY_KEYS if k not in identity]
    problems += [f"envs requires {k}" for k in ENV_KEYS if k not in dep.get("envs", {})]
    require(problems)
    group = identity["runtime_group"]
    if not isinstance(group, dict) or set(group) != {"name", "gid"}:
        problems.append("identity.runtime_group must be {name, gid} of the existing read group")
    elif group["gid"] == identity["gid"] or group["gid"] in (0, identity["uid"]):
        problems.append("the runtime read group must differ from the cell's own and root's group")
    if identity["uid"] == 0 or identity["gid"] == 0:
        problems.append("the cell user may not be root")
    if not dep["forbidden_read_paths"]:
        problems.append("declare every other cell's private paths in forbidden_read_paths")
    for key in ("case", "runtime_root", "entry_dir", "flashinfer_seed_cache"):
        if not Path(dep[key]).is_absolute():
            problems.append(f"{key} must be absolute")
    require(problems)
    return dep, sha256_file(path)


def libero_config(*cases: str) -> str:
    """The one public LIBERO config every case of this job declares."""
    paths = {json.loads(Path(c).read_text()).get("libero_config_path") for c in cases}
    if len(paths) != 1 or None in paths or not (Path(next(iter(paths))) / "config.yaml").is_file():
        raise EntryRefused(f"cases must share one existing public LIBERO config: {paths}")
    return next(iter(paths))


def check_case(dep: dict, mods: SimpleNamespace,
               layout: dict = LAYOUT) -> tuple[dict, Path, Path, str]:
    case_path = Path(dep["case"])
    if not case_path.is_file():
        raise EntryRefused(f"outer case is missing: {case_path}")
    case_sha = sha256_file(case_path)
    if case_sha != dep["case_sha256"]:
        raise EntryRefused("case sha256 differs from the deployment binding")
    try:
        case, repo, task_dir = mods.camp.load(case_path)
    except Exception as exc:  # the runtime's own refusal, kept verbatim
        raise EntryRefused(f"campaign refuses the case: {type(exc).__name__}: {exc}") from exc
    problems = [f"case {k} must be {v!r} (common protocol), not {case.get(k)!r}"
                for k, v in CASE_FIXED.items() if case.get(k) != v]
    if case["id"] != dep["cell"]:
        problems.append("case id differs from the deployment cell")
    if not re.fullmatch(r"[a-z]+_[a-z0-9]+_r[12]", case["id"]) or \
            not case["id"].endswith(f"_r{case['repeat']}"):
        problems.append("cell id must be <task>_<method>_r<repeat> and match case.repeat")
    runtime = Path(dep["runtime_root"]).resolve()
    if repo != (runtime / "aspire/sim").resolve():
        problems.append("case.sim must be the declared frozen runtime's aspire/sim")
    if str(case["cuda_visible_devices"]) != layout["simulator"] or \
            case["egl_device_id"] != layout["egl_device_id"]:
        problems.append(f"simulator/SAM2/EGL must stay on GPU {layout['simulator']}")
    if case["streaming"]["sam2"]["device"] != "cuda":
        problems.append("SAM2 must run on CUDA; no CPU fallback")
    if not case.get("egl_vendor_config"):
        problems.append("case must pin the NVIDIA EGL vendor config (no Mesa fallback)")
    control = Path(case["control"]).resolve()
    entry = Path(dep["entry_dir"]).resolve()
    home = Path(dep["identity"]["home"]).resolve()
    for name, path in (("control", control), ("entry_dir", entry), ("home", home)):
        if path.is_relative_to(runtime) or any(path.is_relative_to(p) for p in PROTECTED_PREFIXES):
            problems.append(f"{name} must be outside the runtime and /root")
        for other in dep["forbidden_read_paths"]:
            other = Path(other).resolve()
            if path == other or path.is_relative_to(other) or other.is_relative_to(path):
                problems.append(f"{name} overlaps another cell's path {other}")
    if entry.is_relative_to(control) or control.is_relative_to(entry):
        problems.append("entry_dir (root-owned service logs/caches) must be outside control")
    if home.is_relative_to(control) or control.is_relative_to(home):
        problems.append("HOME and control are separate directories")
    require(problems)
    return case, repo, task_dir, case_sha


# --- identity: static (mode bits) and conflicts -----------------------------------


def permitted(path: Path, uid: int, gids: set[int], bit: int) -> bool:
    """POSIX mode check of `path` for (uid, gids): every ancestor needs x."""
    if uid == 0:
        return True
    path = Path(path).resolve()
    try:
        for directory in [*reversed(path.parents)]:
            if not _bits(os.stat(directory), uid, gids) & 1:
                return False
        return bool(_bits(os.stat(path), uid, gids) & bit)
    except FileNotFoundError:
        return False


def _bits(st, uid, gids) -> int:
    if st.st_uid == uid:
        return (st.st_mode >> 6) & 7
    if st.st_gid in gids:
        return (st.st_mode >> 3) & 7
    return st.st_mode & 7


def forbidden_paths(dep: dict, case: dict) -> list[str]:
    """Declared private paths plus every sibling cell/user directory, auto-found."""
    own = {Path(case["control"]).resolve(), Path(dep["entry_dir"]).resolve(),
           Path(dep["identity"]["home"]).resolve()}
    paths = {str(Path(p).resolve()) for p in dep["forbidden_read_paths"]}
    for mine in (Path(case["control"]).resolve(), Path(dep["identity"]["home"]).resolve()):
        if mine.parent.is_dir():
            paths.update(str(p.resolve()) for p in mine.parent.iterdir()
                         if p.is_dir() and p.resolve() not in own)
    return sorted(paths)


def must_read(dep: dict, case: dict, repo: Path) -> list[str]:
    """What the cell user's campaign, protocol and trials open or hash."""
    paths = [dep["case"], case["task_manifest"], case["runtime_manifest"],
             case["perception_manifest"], case["model_service"]["server_manifest"],
             case["initial_state_manifest"], case["egl_vendor_config"], case["claude_bin"],
             case["streaming"]["sam2"]["checkpoint"], case["streaming"]["sam2"]["source_root"],
             str(Path(case["libero_config_path"]) / "config.yaml"), str(repo),
             str(repo / ".venv-libero/bin/python3"), *dep.get("must_read_paths", [])]
    freeze = json.loads(Path(case["runtime_manifest"]).read_text())
    paths += [str(repo / name) for name in freeze.get("files", {})]
    paths += list(freeze.get("external_files", {})) + list(freeze.get("executables", {}))
    states = json.loads(Path(case["initial_state_manifest"]).read_text())
    root = Path(case["initial_state_manifest"]).parent
    entry = states["tasks"][case["task"]]
    paths += [str(root / s["path"]) for s in entry["development"]["states"].values()]
    supervised = case.get("supervised_repair") or {}
    continuation = supervised.get("continuation")
    if continuation:
        # These are separate, own-cell read-only inputs; never put private
        # development evidence in the common runtime freeze.
        parent = Path(continuation["parent"]["ledger"]).parent
        paths += [str(p) for p in parent.rglob("*") if p.is_file()]
        paths += [continuation["parent"]["campaign_state"]]
        paths += [item["path"] for item in continuation["seed_input"].values()]
    return sorted(set(paths))


def must_not_write(dep: dict, case: dict, repo: Path) -> list[str]:
    paths = [dep["runtime_root"], str(repo), *dep["envs"].values(), str(Path(dep["case"]).parent),
             str(Path(case["initial_state_manifest"]).parent), str(Path(case["claude_bin"]).parent),
             case["streaming"]["sam2"]["source_root"],
             str(Path(case["streaming"]["sam2"]["checkpoint"]).parent)]
    continuation = (case.get("supervised_repair") or {}).get("continuation")
    if continuation:
        paths += [continuation["parent"]["ledger"], continuation["parent"]["campaign_state"]]
        paths += [item["path"] for item in continuation["seed_input"].values()]
    return sorted(set(paths))


def static_identity(dep: dict, case: dict, repo: Path, host: Host) -> dict:
    ident = dep["identity"]
    uid, gid, rgid = ident["uid"], ident["gid"], ident["runtime_group"]["gid"]
    problems = []
    # Name <-> number conflicts. Exact pre-existing matches are accepted (resume).
    for kind, name, number in (("user", ident["user"], uid), ("group", ident["group"], gid),
                               ("group", ident["runtime_group"]["name"], rgid)):
        look = host.user if kind == "user" else host.group
        by_name = look(name=name)
        by_number = look(uid=number) if kind == "user" else look(gid=number)
        if by_number is not None and by_number[0] != name:
            problems.append(f"{kind} id {number} already belongs to {by_number[0]!r}")
        if by_name is not None and by_name[2] != number:
            problems.append(f"{kind} {name!r} already exists with id {by_name[2]}")
    entry = host.user(name=ident["user"])
    if entry is not None and (entry.pw_gid != gid or entry.pw_dir != ident["home"]):
        problems.append(f"user {ident['user']!r} exists with another primary group or HOME")
    gids = {gid, rgid}
    readable = [p for p in forbidden_paths(dep, case)
                if permitted(Path(p), uid, gids, 4) or permitted(Path(p), uid, gids, 1)]
    if readable:
        problems.append("the cell user (uid %d, groups %s) could read or traverse another cell's "
                        "private paths: %s" % (uid, sorted(gids), readable))
    needed = must_read(dep, case, repo)
    unreadable = [p for p in needed if not permitted(Path(p), uid, gids, 4)]
    if unreadable:
        problems.append(f"{len(unreadable)} required runtime/evidence paths are not readable by "
                        f"the cell user, e.g. {unreadable[:5]}")
    writable = [p for p in must_not_write(dep, case, repo) if permitted(Path(p), uid, gids, 2)]
    if writable:
        problems.append(f"shared inputs are writable by the cell user: {writable}")
    return {"uid": uid, "gid": gid, "groups": sorted(gids), "problems": problems,
            "forbidden_checked": forbidden_paths(dep, case), "must_read_count": len(needed),
            "must_not_write": must_not_write(dep, case, repo)}


# --- real asset / version checks --------------------------------------------------


def dist_version(env: Path, package: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(package).replace('_', '[-_]')}-(.+)\.dist-info$", re.I)
    for site in sorted(env.glob("lib/python3*/site-packages")):
        # torch.__version__ (what the manifests recorded) lives in torch/version.py.
        version_py = site / package / "version.py"
        if package == "torch" and version_py.is_file():
            match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", version_py.read_text(), re.M)
            if match:
                return match.group(1)
        for item in site.iterdir():
            match = pattern.match(item.name)
            if match:
                # METADATA keeps the local version (+cu130); the directory name may not.
                for line in (item / "METADATA").read_text(errors="replace").splitlines():
                    if line.startswith("Version:"):
                        return line.split(":", 1)[1].strip()
                return match.group(1)
    return None


def hash_files(files: list[tuple[Path, str]], workers: int) -> list[str]:
    def one(item):
        path, expected = item
        if not path.is_file():
            return f"missing {path}"
        return None if sha256_file(path) == expected else f"sha256 mismatch {path}"
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return [r for r in pool.map(one, files) if r]


def verify_model(dep: dict, case: dict, mods: SimpleNamespace, *, rehash_weights: bool = True,
                 workers: int = 8, qwen_gpus: str = LAYOUT["qwen"]) -> dict:
    """The fixed Qwen service descriptor, its weights, env versions and CUDA 13 toolchain.

    Shared by every cell of a node, so it is checked once per node. `qwen_gpus`
    is the binding of the layout being verified; the default is this entry's own."""
    record, problems = {}, []
    record["model_service"] = mods.sp.model_service_identity(case)  # bound hash, endpoint, 1M
    qwen_manifest = json.loads(Path(case["model_service"]["server_manifest"]).read_text())
    argv_sha = hashlib.sha256(json.dumps(qwen_manifest["argv"], sort_keys=True).encode()).hexdigest()
    if argv_sha != qwen_manifest.get("argv_sha256"):
        problems.append("Qwen manifest argv differs from its recorded argv_sha256")
    for key, value in (("context_tokens", 1000000), ("effort", "xhigh"), ("max_output_config", 64000),
                       ("claude_version", "2.1.220"), ("engine", "vllm")):
        if qwen_manifest.get(key) != value:
            problems.append(f"Qwen manifest {key} must be {value!r}")
    if qwen_manifest["env"].get("VLLM_PLE_CPU_OFFLOAD") != "1":
        problems.append("Qwen manifest lost VLLM_PLE_CPU_OFFLOAD=1")
    if qwen_manifest["env"].get("CUDA_VISIBLE_DEVICES") != qwen_gpus:
        problems.append(f"Qwen must stay on GPUs {qwen_gpus}")
    # Weights: every file of the recorded revision, by content.
    weights = qwen_manifest["weights_manifest"]
    if sha256_file(Path(weights["path"])) != weights["sha256"]:
        problems.append("Qwen weights manifest hash changed")
    listing = json.loads(Path(weights["path"]).read_text())
    if listing.get("revision") != qwen_manifest["model_revision"]:
        problems.append("Qwen weights revision differs from the service manifest")
    root = Path(qwen_manifest["download_receipt"]["path"])
    model_arg = qwen_manifest["argv"][qwen_manifest["argv"].index("serve") + 1]
    if Path(model_arg).resolve() != root.resolve():
        problems.append("served model path differs from the verified download")
    files = [(root / f["file"], f["sha256"]) for f in listing["files"]]
    if rehash_weights:
        problems += hash_files(files, workers)
        record["qwen_weights"] = {"files": len(files), "rehashed": True}
    else:
        problems += [f"size mismatch {root / f['file']}" for f in listing["files"]
                     if not (root / f["file"]).is_file()
                     or (root / f["file"]).stat().st_size != f["size_bytes"]]
        record["qwen_weights"] = {"files": len(files), "rehashed": False,
                                  "checked": "existence and size only (validate-only option)"}
    qwen_env = Path(dep["envs"]["qwen"])
    record["versions"] = {"vllm": dist_version(qwen_env, "vllm"),
                          "torch": dist_version(qwen_env, "torch")}
    if record["versions"] != {"vllm": qwen_manifest["engine_version"],
                              "torch": qwen_manifest["gpu_validation"]["torch"]}:
        problems.append(f"Qwen env versions changed: {record['versions']}")
    toolchain = qwen_manifest["cuda_toolchain"]
    problems += hash_files([(Path(toolchain["nvcc"]), toolchain["nvcc_sha256"])], 1)
    for key in ("LD_LIBRARY_PATH", "CUDA_HOME"):
        if not Path(qwen_manifest["env"][key]).is_dir():
            problems.append(f"CUDA 13 path for {key} is missing")
    record["problems"] = problems
    return record


def verify_cell_assets(dep: dict, case: dict, repo: Path, mods: SimpleNamespace) -> dict:
    """Perception weights/versions, EGL vendor, runtime freeze, task and initial states."""
    record, problems = {}, []
    perception = json.loads(Path(case["perception_manifest"]).read_text())
    try:
        record["perception"] = mods.sp.perception_identity(case)  # bound hash + URLs
    except Exception as exc:
        problems.append(f"{type(exc).__name__}: {exc}")
    for weight in perception["sam_weights"]:
        problems += hash_files([(Path(weight["destination"]), weight["sha256"])], 1)
    robot = perception["robot_model"]
    cache = Path(robot["cache_path"])
    head = (cache / ".git/HEAD").read_text().strip() if (cache / ".git/HEAD").is_file() else None
    if head != robot["commit"]:
        problems.append("robot description cache is not at the recorded commit")
    urdf = cache / "robots/panda_description/urdf/panda.urdf"
    problems += hash_files([(urdf, robot["urdf_sha256"])], 1)
    compiled = perception["sam2_compiled_binary"]
    problems += hash_files([(Path(compiled["path"]), compiled["sha256"])], 1)
    libero_env = Path(dep["envs"]["libero"])
    versions = {k: dist_version(libero_env, k) for k in
                ("torch", "numpy", "mujoco", "robosuite", "sam3", "jax", "pyroki")}
    expected = {k: perception["versions"][k] for k in versions if k in perception["versions"]}
    if {k: versions[k] for k in expected} != expected:
        problems.append(f"libero env versions changed: {versions} vs {expected}")
    record["versions"] = versions
    vendor = json.loads(Path(case["egl_vendor_config"]).read_text())
    library = vendor.get("ICD", {}).get("library_path", "")
    if "nvidia" not in library.lower() or not Path(library).is_file():
        problems.append(f"EGL vendor config does not point to an existing NVIDIA ICD: {library}")
    try:
        record["runtime_manifest_sha256"] = mods.freeze.verify_runtime(case, repo)
        record["task"] = mods.camp.task_metadata(case)  # task manifest + initial-state registry
        record["engineered_world"] = mods.sp.engineered_world(repo)["digest"]
        record["sam2"] = mods.sp.sam2_identity(case)["source_sha256"]
    except Exception as exc:
        problems.append(f"{type(exc).__name__}: {exc}")
    else:
        task = json.loads(Path(case["task_manifest"]).read_text())
        if record["task"]["initial_states"].get("test_sha256") != \
                task["original_test_states"]["sha256"]:
            problems.append("original 50 test states differ between task manifest and registry")
    record["problems"] = problems
    return record


def verify_assets(dep: dict, case: dict, repo: Path, mods: SimpleNamespace, *,
                  rehash_weights: bool = True, workers: int = 8) -> dict:
    model = verify_model(dep, case, mods, rehash_weights=rehash_weights, workers=workers)
    cell = verify_cell_assets(dep, case, repo, mods)
    return {"model": model, "cell": cell, "problems": model["problems"] + cell["problems"]}


# --- services ---------------------------------------------------------------------


def rebase(value: str, prefix: str, root: str) -> str:
    return os.pathsep.join(root + part[len(prefix):] if part == prefix or
                           part.startswith(prefix + "/") else part
                           for part in value.split(os.pathsep))


def port_of(argv: list[str]) -> int:
    return int(argv[argv.index("--port") + 1])


def cache_env(cache: Path, name: str) -> dict:
    base = cache / name
    return {"XDG_CACHE_HOME": str(base / "xdg"), "HF_HOME": str(base / "hf"),
            "TORCH_HOME": str(base / "torch"), "TRITON_CACHE_DIR": str(base / "triton"),
            "TORCHINDUCTOR_CACHE_DIR": str(base / "inductor"), "CUDA_CACHE_PATH": str(base / "nv"),
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "GIT_TERMINAL_PROMPT": "0"}


def qwen_spec(dep: dict, case: dict, cache: Path) -> dict:
    """The recorded Qwen argv/env, unchanged apart from declared cwd and cache rebases."""
    prefix, root = dep["source_checkout_prefix"].rstrip("/"), str(Path(dep["runtime_root"]))
    qm = json.loads(Path(case["model_service"]["server_manifest"]).read_text())
    env = dict(qm["env"])
    adaptations = {"cwd": {"manifest": qm["cwd"], "actual": rebase(qm["cwd"], prefix, root),
                           "reason": "frozen runtime replaces the RS /root checkout"},
                   "FLASHINFER_WORKSPACE_BASE": {
                       "manifest": env["FLASHINFER_WORKSPACE_BASE"],
                       "actual": str(cache / "qwen/flashinfer"),
                       "reason": "node-local copy of the prebuilt FlashInfer workspace"}}
    env["FLASHINFER_WORKSPACE_BASE"] = str(cache / "qwen/flashinfer")
    added = cache_env(cache, "qwen")
    adaptations.update({k: {"manifest": None, "actual": v, "reason": "own cache / offline"}
                        for k, v in added.items() if k not in env})
    return {"name": "qwen", "argv": list(qm["argv"]), "env": {**added, **env},
            "cwd": adaptations["cwd"]["actual"], "port": port_of(qm["argv"]),
            "gpu": env["CUDA_VISIBLE_DEVICES"], "adaptations": adaptations,
            "url": case["model_service"]["endpoint"],
            "descriptor": {"path": case["model_service"]["server_manifest"],
                           "sha256": case["model_service"]["server_manifest_sha256"],
                           "argv_sha256": qm["argv_sha256"]},
            "seed_cache": {"from": dep["flashinfer_seed_cache"],
                           "to": env["FLASHINFER_WORKSPACE_BASE"]}}


def perception_specs(dep: dict, case: dict, repo: Path, cache: Path, layout: dict = LAYOUT,
                     descriptor: str | None = None, prefix_name: str = "") -> list[dict]:
    """SAM3/GraspNet/Pyroki from the recorded start manifest.

    The listen port follows the cell's case URL and the GPU follows the slot
    layout. Any difference from the recorded start is a declared adaptation."""
    prefix, root = dep["source_checkout_prefix"].rstrip("/"), str(Path(dep["runtime_root"]))
    source = descriptor or case["perception_manifest"]
    pm = json.loads(Path(source).read_text())
    tasks = {t["name"]: t for t in pm["startup_manifest"]["tasks"]}
    specs, problems = [], []
    for task_name, (name, url_key) in PERCEPTION.items():
        task = tasks.get(task_name)
        if task is None:
            problems.append(f"perception manifest has no {task_name}")
            continue
        env, adaptations = {}, {}
        for key, value in task["env"].items():
            actual = rebase(value, prefix, root)
            if key == "LIBERO_CONFIG_PATH":
                actual = str(case["libero_config_path"])
            if key == "CUDA_VISIBLE_DEVICES":
                actual = layout[name]
            env[key] = actual
            if actual != value:
                adaptations[key] = {"manifest": value, "actual": actual,
                                    "reason": "slot GPU layout" if key == "CUDA_VISIBLE_DEVICES"
                                    else "frozen runtime / public LIBERO config path"}
        argv = list(task["argv"])
        port = int(case[url_key].rsplit(":", 1)[1])
        if port_of(argv) != port:
            adaptations["--port"] = {"manifest": port_of(argv), "actual": port,
                                     "reason": "this cell's own loopback port (case URL)"}
            argv[argv.index("--port") + 1] = str(port)
        if name == "pyroki":
            env["ROBOT_DESCRIPTIONS_CACHE"] = str(Path(pm["robot_model"]["cache_path"]).parent)
            adaptations["ROBOT_DESCRIPTIONS_CACHE"] = {
                "manifest": None, "actual": env["ROBOT_DESCRIPTIONS_CACHE"],
                "reason": "verified robot model cache on CPFS (no Git fetch, no proxy)"}
        added = cache_env(cache, name)
        adaptations.update({k: {"manifest": None, "actual": v, "reason": "own cache / offline"}
                            for k, v in added.items() if k not in env})
        adaptations["cwd"] = {"manifest": prefix + "/aspire/sim", "actual": str(repo),
                              "reason": "frozen runtime replaces the RS /root checkout"}
        specs.append({"name": prefix_name + name, "role": name, "argv": argv,
                      "env": {**added, **env}, "cwd": str(repo), "port": port,
                      "gpu": env["CUDA_VISIBLE_DEVICES"], "url": case[url_key],
                      "adaptations": adaptations,
                      "descriptor": {"path": str(source), "sha256": sha256_file(Path(source)),
                                     "task": task_name}})
    require(problems)
    return specs


def check_specs(specs: list[dict], dep: dict, layout: dict = LAYOUT) -> None:
    prefix = dep["source_checkout_prefix"].rstrip("/")
    problems = []
    for spec in specs:
        role = spec.get("role", spec["name"])
        if spec["gpu"] != layout[role]:
            problems.append(f"{spec['name']} must stay on GPU {layout[role] or 'cpu'}")
        leaked = [v for v in [*spec["argv"], *spec["env"].values(), spec["cwd"]]
                  if any(p == prefix or p.startswith(prefix + "/") for p in str(v).split(":"))]
        if leaked:
            problems.append(f"{spec['name']} still references the /root checkout: {leaked[:3]}")
        if any(PROXY.match(k) or CREDENTIAL.search(k) for k in spec["env"]):
            problems.append(f"{spec['name']} manifest env carries proxy/credential keys")
        if not spec["url"].endswith(f":{spec['port']}") or "127.0.0.1" not in spec["url"]:
            problems.append(f"{spec['name']} port {spec['port']} differs from its URL {spec['url']}")
        if spec["argv"][spec["argv"].index("--host") + 1] != "127.0.0.1":
            problems.append(f"{spec['name']} must bind loopback only")
    require(problems)


def missing_spec_paths(specs: list[dict]) -> list[str]:
    """Interpreter, cwd and CUDA paths the recorded start relies on, on this node."""
    missing = []
    for spec in specs:
        paths = [spec["argv"][0], spec["cwd"]]
        paths += [spec["env"][k] for k in ("CUDA_HOME", "CUDA_PATH") if spec["env"].get(k)]
        paths += [p for p in spec["env"].get("LD_LIBRARY_PATH", "").split(os.pathsep) if p]
        missing += [f"{spec['name']}: {p}" for p in paths if not Path(p).exists()]
    return missing


def service_specs(dep: dict, case: dict, repo: Path, entry: Path,
                  layout: dict = LAYOUT) -> list[dict]:
    """Recorded argv/env, kept as is apart from declared path and cache rebases."""
    cache = entry / "cache"
    specs = [qwen_spec(dep, case, cache), *perception_specs(dep, case, repo, cache, layout)]
    check_specs(specs, dep, layout)
    return specs


# Only these inherited variables reach a service or a cell. Everything else from
# the DLC worker (credentials, proxies, RANK/WORLD_SIZE/MASTER_*, NCCL/PET/PAI
# settings, Python and device overrides) is dropped; each consumer gets its env
# from its recorded manifest or from cell_env.
INHERIT_KEEP = re.compile(r"^(?:LANG|LANGUAGE|LC_\w+|TZ|TERM|HOME|USER|LOGNAME|SHELL)$")


def base_env(environ) -> dict:
    return {k: v for k, v in environ.items()
            if INHERIT_KEEP.match(k) and not (CREDENTIAL.search(k) or PROXY.match(k))}


def png(width=96, height=96) -> bytes:
    """A synthetic RGB image (red square on gray); no task content."""
    rows = b"".join(b"\x00" + b"".join(b"\xc8\x20\x20" if 32 <= x < 64 and 32 <= y < 64
                                       else b"\x80\x80\x80" for x in range(width))
                    for y in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def npy(descr: str, shape: tuple, data: bytes) -> bytes:
    header = repr({"descr": descr, "fortran_order": False, "shape": shape}).encode("latin1")
    header += b" " * (63 - (10 + len(header)) % 64) + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + data


def readiness_requests(spec: dict) -> list[tuple[str, dict | None]]:
    root, role = spec["url"], spec.get("role", spec["name"])
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    if role == "qwen":
        return [(f"{root}/v1/models", None)]
    if role == "sam3":
        return [(f"{root}/segment", {"image_base64": b64(png()), "text_prompt": "red square"})]
    if role == "graspnet":
        h, w = 120, 160
        depth = struct.pack(f"<{h * w}f", *([0.6] * (h * w)))
        seg = struct.pack(f"<{h * w}i", *[1 if 40 <= y < 80 and 60 <= x < 100 else 0
                                           for y in range(h) for x in range(w)])
        cam = struct.pack("<9f", 200.0, 0.0, w / 2, 0.0, 200.0, h / 2, 0.0, 0.0, 1.0)
        return [(f"{root}/plan", {"depth_base64": b64(npy("<f4", (h, w), depth)),
                                  "cam_K_base64": b64(npy("<f4", (3, 3), cam)),
                                  "segmap_base64": b64(npy("<i4", (h, w), seg)), "segmap_id": 1})]
    return [(f"{root}/ik", {"target_pose_wxyz_xyz": [0.0, 1.0, 0.0, 0.0, 0.45, 0.0, 0.35]})]


def ready(spec: dict, case: dict, host: Host) -> tuple[bool, dict]:
    role = spec.get("role", spec["name"])
    for url, payload in readiness_requests(spec):
        status, body = host.http(url, payload, timeout=300 if payload else 10)
        if status != 200 or not isinstance(body, dict):
            return False, {"url": url, "status": status, "body": str(body)[:500]}
        if role == "qwen":
            models = {m.get("id"): m.get("max_model_len") for m in body.get("data", [])}
            if models != {case["model"]: case["context_tokens"]}:
                raise StartupFailed(f"Qwen serves {models}, not {case['model']} at 1M")
        elif role == "sam3" and "results" not in body:
            return False, {"url": url, "body_keys": sorted(body)}
        elif role == "graspnet" and "grasps_base64" not in body:
            return False, {"url": url, "body_keys": sorted(body)}
        elif role == "pyroki" and len(body.get("joint_positions", [])) < 7:
            return False, {"url": url, "body_keys": sorted(body)}
    return True, {"synthetic_request": role, "status": 200}


# --- probes run as the cell user --------------------------------------------------


IDENTITY_PROBE = r"""
import json, os, sys
spec = json.load(sys.stdin)
can = lambda p, m: os.access(p, m)
print(json.dumps({
    "uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "groups": sorted(os.getgroups()),
    "home": os.environ.get("HOME"),
    "forbidden_accessible": [p for p in spec["forbidden"] if can(p, os.R_OK) or can(p, os.X_OK)],
    "unreadable": [p for p in spec["read"] if not can(p, os.R_OK)],
    "writable_shared": [p for p in spec["no_write"] if can(p, os.W_OK)],
    "control_writable": can(spec["control"], os.W_OK), "home_writable": can(spec["home"], os.W_OK)}))
"""

DEVICE_PROBE = r"""
import json, os
out = {"env": {k: os.environ.get(k) for k in ("CUDA_VISIBLE_DEVICES", "MUJOCO_GL",
       "MUJOCO_EGL_DEVICE_ID", "PYOPENGL_PLATFORM", "__EGL_VENDOR_LIBRARY_FILENAMES")}}
import torch
out["torch"] = torch.__version__
out["cuda_available"] = torch.cuda.is_available()
if out["cuda_available"]:
    out["cuda_devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    x = torch.arange(16, device="cuda", dtype=torch.float32)
    out["cuda_kernel_ok"] = float((x * 2).sum().item()) == 240.0
import mujoco
from mujoco.egl import egl_ext
out["egl_devices"] = len(egl_ext.eglQueryDevicesEXT())
model = mujoco.MjModel.from_xml_string(
    "<mujoco><worldbody><light pos='0 0 2'/><geom type='box' size='.1 .1 .1' rgba='1 0 0 1'/>"
    "</worldbody></mujoco>")
renderer = mujoco.Renderer(model, 64, 64)
data = mujoco.MjData(model)
mujoco.mj_forward(model, data)
renderer.update_scene(data)
pixels = renderer.render()
out["render_nonzero"] = int(pixels.astype("int64").sum()) > 0
from OpenGL import GL
out["gl_vendor"] = GL.glGetString(GL.GL_VENDOR).decode()
out["gl_renderer"] = GL.glGetString(GL.GL_RENDERER).decode()
out["gl_version"] = GL.glGetString(GL.GL_VERSION).decode()
renderer.close()
print(json.dumps(out))
"""

# EGL device index -> CUDA ordinal (EGL_CUDA_DEVICE_NV), with every GPU visible in
# PCI order, so the simulator's EGL device is shown to be its own CUDA GPU.
EGL_MAP_PROBE = r"""
import ctypes, json
from mujoco.egl import egl_ext
from OpenGL import EGL
devices = egl_ext.eglQueryDevicesEXT()
proc = EGL.eglGetProcAddress(b"eglQueryDeviceAttribEXT")
query = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_int,
                         ctypes.POINTER(ctypes.c_ssize_t))(ctypes.cast(proc, ctypes.c_void_p).value)
out = []
for d in devices:
    value = ctypes.c_ssize_t(-1)
    ok = query(ctypes.cast(d, ctypes.c_void_p), 0x323A, ctypes.byref(value))
    out.append(value.value if ok else None)
print(json.dumps({"egl_devices": len(devices), "egl_to_cuda": out}))
"""


def as_user(dep: dict, argv: list[str]) -> list[str]:
    # The sanitized child PATH intentionally omits /usr/sbin.
    return ["/usr/sbin/runuser", "-u", dep["identity"]["user"], "--", *argv]


def cell_env(dep: dict, case: dict, host: Host) -> dict:
    """The campaign's environment: what r1's launcher exported, nothing inherited
    that could add credentials, proxies, Python overrides or device remaps."""
    env = {k: v for k, v in base_env(host.environ).items()
           if k not in ("HOME", "USER", "LOGNAME", "SHELL")}
    qm = json.loads(Path(case["model_service"]["server_manifest"]).read_text())
    ident = dep["identity"]
    env.update(HOME=ident["home"], USER=ident["user"], LOGNAME=ident["user"], SHELL="/bin/bash",
               PATH="/usr/local/bin:/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1",
               TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1",
               LIBERO_CONFIG_PATH=str(case["libero_config_path"]),
               PYTHONPATH=str(Path(dep["runtime_root"])),
               LD_LIBRARY_PATH=qm["env"]["LD_LIBRARY_PATH"],
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", GIT_TERMINAL_PROMPT="0")
    return env


def identity_probe(dep: dict, case: dict, repo: Path, host: Host) -> dict:
    ident = dep["identity"]
    spec = {"forbidden": forbidden_paths(dep, case), "read": must_read(dep, case, repo),
            "no_write": must_not_write(dep, case, repo), "control": case["control"],
            "home": ident["home"]}
    python = str(Path(dep["envs"]["libero"]) / "bin/python3")
    # A complete runtime inventory exceeds Linux's limit for a single argument.
    proc = host.run(as_user(dep, [python, "-I", "-c", IDENTITY_PROBE]),
                    input=json.dumps(spec), env=cell_env(dep, case, host), cwd="/")
    if proc.returncode:
        raise StartupFailed(f"identity probe failed: {proc.stderr[-2000:]}")
    seen = json.loads(proc.stdout)
    problems = []
    if seen["uid"] != ident["uid"] or seen["euid"] != ident["uid"] or seen["gid"] != ident["gid"]:
        problems.append(f"probe ran as uid {seen['uid']} gid {seen['gid']}")
    if set(seen["groups"]) != {ident["gid"], ident["runtime_group"]["gid"]}:
        problems.append(f"cell user groups are {seen['groups']}, not exactly own + runtime")
    if seen["home"] != ident["home"]:
        problems.append("HOME is not the cell user's own passwd HOME")
    for key in ("forbidden_accessible", "unreadable", "writable_shared"):
        if seen[key]:
            problems.append(f"{key}: {seen[key][:5]} ({len(seen[key])})")
    if not (seen["control_writable"] and seen["home_writable"]):
        problems.append("control or HOME is not writable by the cell user")
    require(problems, StartupFailed)
    return {k: v for k, v in seen.items() if k != "unreadable"} | {"checked_reads": len(spec["read"])}


def device_probe(dep: dict, case: dict, host: Host, expected_gpu: str,
                 layout: dict = LAYOUT, gpu_count: int = GPU_COUNT) -> dict:
    env = cell_env(dep, case, host)
    env.update(MUJOCO_GL="egl", PYOPENGL_PLATFORM="egl", CUDA_DEVICE_ORDER="PCI_BUS_ID",
               __EGL_VENDOR_LIBRARY_FILENAMES=str(case["egl_vendor_config"]))
    python = str(Path(dep["envs"]["libero"]) / "bin/python3")
    proc = host.run(as_user(dep, [python, "-I", "-c", EGL_MAP_PROBE]), env=env, cwd="/")
    if proc.returncode:
        raise StartupFailed(f"EGL device mapping probe failed: {proc.stderr[-2000:]}")
    mapping = json.loads(proc.stdout.strip().splitlines()[-1])
    egl_cuda = mapping["egl_to_cuda"]
    if len(egl_cuda) <= layout["egl_device_id"] or \
            egl_cuda[layout["egl_device_id"]] != int(layout["simulator"]):
        raise StartupFailed(f"EGL device {layout['egl_device_id']} is not CUDA GPU "
                            f"{layout['simulator']}: {egl_cuda}")
    env.update(CUDA_VISIBLE_DEVICES=layout["simulator"],
               MUJOCO_EGL_DEVICE_ID=str(layout["egl_device_id"]))
    proc = host.run(as_user(dep, [python, "-I", "-c", DEVICE_PROBE]), env=env, cwd="/")
    if proc.returncode:
        raise StartupFailed(f"CUDA/EGL probe failed as the cell user: {proc.stderr[-2000:]}")
    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    seen["egl_to_cuda"] = egl_cuda
    perception = json.loads(Path(case["perception_manifest"]).read_text())
    problems = []
    if seen.get("torch") != perception["versions"]["torch"]:
        problems.append(f"trial torch {seen.get('torch')} differs from the frozen version")
    if not seen.get("cuda_available") or not seen.get("cuda_kernel_ok") or \
            seen.get("cuda_devices") != [expected_gpu]:
        problems.append(f"CUDA on GPU {layout['simulator']} is not one working {expected_gpu}: "
                        f"{seen.get('cuda_devices')}")
    if seen.get("egl_devices") != gpu_count:
        problems.append(f"EGL sees {seen.get('egl_devices')} devices, not {gpu_count}")
    renderer = f"{seen.get('gl_vendor')} {seen.get('gl_renderer')}"
    if "NVIDIA" not in str(seen.get("gl_vendor")) or expected_gpu.split()[-1] not in renderer or \
            re.search(r"mesa|llvmpipe|softpipe|swrast", renderer, re.I):
        problems.append(f"EGL is not the NVIDIA GPU renderer: {renderer}")
    if not seen.get("render_nonzero"):
        problems.append("EGL render produced an empty frame")
    require(problems, StartupFailed)
    return seen


# --- GPUs, ports, topology --------------------------------------------------------


def gpu_inventory(host: Host, expected_gpu: str, count: int = GPU_COUNT) -> dict:
    gpus = host.run(["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.used",
                     "--format=csv,noheader,nounits"])
    apps = host.run(["nvidia-smi", "--query-compute-apps=pid,gpu_uuid", "--format=csv,noheader"])
    if gpus.returncode or apps.returncode:
        raise EntryRefused(f"nvidia-smi failed: {gpus.stderr or apps.stderr}")
    rows = [[c.strip() for c in line.split(",")] for line in gpus.stdout.splitlines() if line.strip()]
    devices = [{"index": int(r[0]), "uuid": r[1], "name": r[2], "total_mib": int(r[3]),
                "used_mib": int(r[4])} for r in rows]
    busy = [line.strip() for line in apps.stdout.splitlines() if line.strip()]
    problems = []
    if [d["index"] for d in devices] != list(range(count)):
        problems.append(f"expected GPUs 0-{count - 1}, got {[d['index'] for d in devices]}")
    if any(d["name"] != expected_gpu for d in devices):
        problems.append(f"every GPU must be {expected_gpu}: {[d['name'] for d in devices]}")
    if busy or any(d["used_mib"] > IDLE_GPU_MIB for d in devices):
        problems.append(f"GPUs are not idle (another cell or service is active): {busy}")
    require(problems)
    return {"devices": devices}


def gpu_placement(host: Host, inventory: dict) -> list:
    apps = host.run(["nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory",
                     "--format=csv,noheader"])
    index = {d["uuid"]: d["index"] for d in inventory["devices"]}
    return [{"pid": int(p), "gpu": index.get(u.strip()), "used": m.strip()} for p, u, m in
            (line.split(",") for line in apps.stdout.splitlines() if line.count(",") == 2)]


def topology(environ) -> dict:
    seen = {k: environ.get(k) for k in TOPOLOGY if environ.get(k) is not None}
    wrong = {k: v for k, v in seen.items() if v != TOPOLOGY[k]}
    if wrong:
        raise EntryRefused(f"this is a single-worker entry; DLC topology says {wrong}")
    return seen


# --- root setup -------------------------------------------------------------------


def private_dir(path: Path, uid: int, gid: int) -> str:
    """Create one leaf directory 0700 for its owner, or accept an exact existing one."""
    if not path.parent.is_dir():
        raise EntryRefused(f"parent of {path} must already exist (prepared by the outer side)")
    if path.exists():
        st = path.stat()
        if not path.is_dir() or st.st_uid != uid or st.st_mode & 0o077:
            raise EntryRefused(f"{path} exists with owner {st.st_uid} mode {oct(st.st_mode)}; "
                               "expected a private 0700 directory of uid %d" % uid)
        return "existing"
    path.mkdir(mode=0o700)
    os.chown(path, uid, gid)  # this one new directory only
    os.chmod(path, 0o700)
    return "created"


def ensure_identity(dep: dict, case: dict, host: Host) -> dict:
    ident = dep["identity"]
    done = []
    for name, gid in ((ident["runtime_group"]["name"], ident["runtime_group"]["gid"]),
                      (ident["group"], ident["gid"])):
        if host.group(gid=gid) is None:
            proc = host.run(["groupadd", "-g", str(gid), name])
            if proc.returncode:
                raise EntryRefused(f"groupadd {name} failed: {proc.stderr}")
            done.append(f"groupadd {name}:{gid}")
    if host.user(name=ident["user"]) is None:
        proc = host.run(["useradd", "-u", str(ident["uid"]), "-g", str(ident["gid"]),
                         "-G", str(ident["runtime_group"]["gid"]), "-d", ident["home"], "-M",
                         "-s", "/bin/bash", "-l", ident["user"]])
        if proc.returncode:
            raise EntryRefused(f"useradd {ident['user']} failed: {proc.stderr}")
        done.append(f"useradd {ident['user']}:{ident['uid']}")
    entry = host.user(name=ident["user"])
    members = host.group(gid=ident["runtime_group"]["gid"])
    if entry is None or entry.pw_uid != ident["uid"] or entry.pw_gid != ident["gid"] or \
            entry.pw_dir != ident["home"] or members is None or ident["user"] not in members.gr_mem:
        raise EntryRefused("the cell user does not match the declared identity after setup")
    dirs = {"home": private_dir(Path(ident["home"]), ident["uid"], ident["gid"]),
            "control": private_dir(Path(case["control"]), ident["uid"], ident["gid"])}
    return {"actions": done, "dirs": dirs}


# --- lifecycle: start, watch and stop only what this entry started ----------------


def set_subreaper() -> bool:
    """Orphaned descendants reparent to this entry, so teardown still finds them."""
    try:
        return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    except (OSError, AttributeError):
        return False


def proc_table() -> dict[int, tuple[int, str]]:
    table = {}
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            stat = (item / "stat").read_text()
        except OSError:
            continue
        fields = stat[stat.rindex(")") + 2:].split()
        table[int(item.name)] = (int(fields[1]), fields[19])  # ppid, starttime
    return table


def descendants(root: int, table: dict) -> dict[int, str]:
    found, frontier = {}, [root]
    if root in table:
        found[root] = table[root][1]
    while frontier:
        parent = frontier.pop()
        for pid, (ppid, start) in table.items():
            if ppid == parent and pid not in found:
                found[pid] = start
                frontier.append(pid)
    return found


class Lifecycle:
    def __init__(self, host: Host, attempt: Path, grace: float = 30):
        self.host, self.attempt, self.grace = host, attempt, grace
        self.launched: list[dict] = []  # {name, process, pid, pgid, starttime, log}

    def start(self, name: str, argv, *, cwd, env, record: dict,
              log_dir: Path | None = None) -> subprocess.Popen:
        log_dir = log_dir or self.attempt / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        out = (log_dir / f"{name}.log").open("ab")
        process = self.host.popen(argv, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
        out.close()
        start = proc_table().get(process.pid, (None, None))[1]
        item = {"name": name, "process": process, "pid": process.pid, "starttime": start,
                "log": str(log_dir / f"{name}.log")}
        try:
            item["pgid"] = os.getpgid(process.pid)
        except ProcessLookupError:
            item["pgid"] = None
        self.launched.append(item)
        record.update(pid=item["pid"], pgid=item["pgid"], log=item["log"], started_at=now())
        return process

    def owned(self) -> dict[int, str]:
        table = proc_table()
        pids = {}
        for item in self.launched:
            pids.update(descendants(item["pid"], table))
        return pids

    def stop(self, names: set[str] | None = None) -> dict:
        """SIGTERM then SIGKILL to the recorded trees only; never by name or user."""
        items = [i for i in reversed(self.launched) if names is None or i["name"] in names]
        table = proc_table()
        targets = {}
        for item in items:
            targets.update(descendants(item["pid"], table))
        self._signal(targets, signal.SIGTERM)
        deadline = time.monotonic() + self.grace
        while time.monotonic() < deadline and self._alive(targets):
            for item in items:
                item["process"].poll()
            time.sleep(0.2)
        left = self._alive(targets)
        self._signal(left, signal.SIGKILL)
        codes = {}
        for item in items:
            try:
                codes[item["name"]] = item["process"].wait(timeout=10)
            except subprocess.TimeoutExpired:
                codes[item["name"]] = None
        self._reap()
        return {"signalled": sorted(targets), "killed": sorted(left), "exit_codes": codes}

    @staticmethod
    def _alive(targets: dict[int, str]) -> dict[int, str]:
        table = proc_table()
        alive = {}
        for pid, start in targets.items():
            if pid in table and table[pid][1] == start:
                try:
                    state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                except (OSError, IndexError):
                    continue
                if state != "Z":
                    alive[pid] = start
        return alive

    @staticmethod
    def _signal(targets: dict[int, str], sig) -> None:
        table = proc_table()
        for pid, start in targets.items():
            if pid in table and table[pid][1] == start:  # same process, not a reused PID
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass

    @staticmethod
    def _reap() -> None:
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return


# --- entry state ------------------------------------------------------------------


def entry_sha256() -> str:
    return sha256_file(Path(__file__))


def open_state(entry: Path, dep_sha: str, case_sha: str, cell: str) -> dict:
    path = entry / "entry_state.json"
    if path.is_file():
        state = json.loads(path.read_text())
        if state["identity"] != {"cell": cell, "deployment_sha256": dep_sha, "case_sha256": case_sha}:
            raise EntryRefused("deployment or case changed since this cell's first launch; a "
                               "resume keeps the same identity, ledger and deadline")
        return state
    return {"identity": {"cell": cell, "deployment_sha256": dep_sha, "case_sha256": case_sha},
            "created_at": now(), "attempts": []}


def launch(deployment: Path, *, resume: bool, host: Host | None = None) -> int:
    host = host or Host()
    dep, dep_sha = load_deployment(deployment)
    if host.euid() != 0:
        raise EntryRefused("the DLC entry runs as root for minimal setup; the campaign runs "
                           "as the cell user")
    runtime = Path(dep["runtime_root"]) / "aspire/sim"
    mods = host.modules(runtime, libero_config(dep["case"]))
    case, repo, task_dir, case_sha = check_case(dep, mods)
    entry = Path(dep["entry_dir"])
    private_dir(entry, 0, 0)
    lock = (entry / "entry.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise EntryRefused("another entry holds this cell's lock") from None
    state = open_state(entry, dep_sha, case_sha, case["id"])
    open_attempts = [a["n"] for a in state["attempts"] if a.get("status") in
                     ("preflight", "starting_services", "campaign_running")]
    if open_attempts:
        raise EntryRefused(f"attempt(s) {open_attempts} never closed; another worker may still "
                           "run this cell. Close one with --close-stale-attempt N --reason")
    launched = (task_dir / "development_state.json").is_file()
    if launched and not resume:
        raise EntryRefused("this cell's ledger exists; there is no hidden restart (use --resume)")
    if resume and not launched:
        raise EntryRefused("nothing to resume: the ledger was never initialized")
    n = len(state["attempts"]) + 1
    attempt_dir = entry / "attempts" / f"{n:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    attempt = {"n": n, "dir": str(attempt_dir), "resume": resume, "status": "preflight",
               "started_at": now(), "entry_sha256": entry_sha256(), "hostname": socket.gethostname(),
               "pid": os.getpid()}
    state["attempts"].append(attempt)
    write_json(entry / "entry_state.json", state)
    life = Lifecycle(host, attempt_dir)

    def terminate(signum, _frame):
        raise Terminated(f"signal {signum}")
    previous = {s: signal.signal(s, terminate) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    code = 1
    try:
        code = _launch(dep, case, repo, resume, host, mods, entry, attempt, attempt_dir, life, state)
    except EntryRefused as exc:
        attempt.update(status="refused", blocker=str(exc))
        code = 2
    except StartupFailed as exc:
        attempt.update(status="startup_failed", blocker=str(exc))
    except Terminated as exc:
        attempt.update(status="terminated", blocker=str(exc))
    except Exception as exc:  # recorded, never retried
        attempt.update(status="entry_error", blocker=f"{type(exc).__name__}: {exc}")
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, signal.SIG_IGN)
        attempt["teardown"] = life.stop()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        attempt["finished_at"] = now()
        write_json(entry / "entry_state.json", state)
        write_json(attempt_dir / "result.json", attempt)
        print(json.dumps(attempt, indent=2, default=str))
    return code


def _launch(dep, case, repo, resume, host, mods, entry, attempt, attempt_dir, life, state) -> int:
    attempt["subreaper"] = set_subreaper()
    expected_gpu = dep.get("expected_gpu_name", "NVIDIA L20Z")
    pre = {"topology": topology(host.environ)}
    pre["static_identity"] = static_identity(dep, case, repo, host)
    require(pre["static_identity"]["problems"])
    pre["assets"] = verify_assets(dep, case, repo, mods, rehash_weights=True,
                                  workers=dep.get("hash_workers", 8))
    write_json(attempt_dir / "preflight.json", pre)
    require(pre["assets"]["problems"])
    pre["gpus"] = gpu_inventory(host, expected_gpu)
    busy = [p for p in (s["port"] for s in service_specs(dep, case, repo, entry)) if host.listening(p)]
    if busy:
        raise EntryRefused(f"ports already listening (an active cell or service): {busy}")
    pre["setup"] = ensure_identity(dep, case, host)
    pre["identity_probe"] = identity_probe(dep, case, repo, host)
    pre["device_probe"] = device_probe(dep, case, host, expected_gpu)
    write_json(attempt_dir / "preflight.json", pre)

    attempt["status"] = "starting_services"
    specs = service_specs(dep, case, repo, entry)
    services = []
    base = base_env(host.environ)
    for spec in specs:
        seed = spec.get("seed_cache")
        if seed and not Path(seed["to"]).exists():
            shutil.copytree(seed["from"], seed["to"], symlinks=True)  # this cell's own copy
        record = {k: spec[k] for k in ("name", "argv", "cwd", "port", "gpu", "adaptations")}
        record["env"], record["inherited_keys"] = spec["env"], sorted(base)
        process = life.start(spec["name"], spec["argv"], cwd=spec["cwd"],
                             env={**base, **spec["env"]}, record=record)
        record["process"] = process
        services.append(record)
    attempt["services"] = services
    write_json(attempt_dir / "services.json", services)
    timeout = float(dep["service_ready_timeout_seconds"])
    watched = [mods.runtime.Service(s["name"], s["process"], Path(s["log"]), "") for s in services]
    start = host.clock()
    pending = {s["name"]: s for s in services}
    while pending:
        for watch in watched:
            try:
                watch.check()
            except mods.runtime.ServiceFailure as exc:
                raise StartupFailed(f"service failed before readiness: {exc}") from exc
        for name, record in list(pending.items()):
            spec = next(s for s in specs if s["name"] == name)
            ok, detail = ready(spec, case, host)
            record["last_probe"] = detail
            if ok:
                record["ready_at"] = now()
                record["ready_after_seconds"] = round(host.clock() - start, 1)
                pending.pop(name)
        if pending and host.clock() - start > timeout:
            raise StartupFailed(f"services not ready after {timeout:.0f}s: "
                                f"{ {n: r.get('last_probe') for n, r in pending.items()} }")
        if pending:
            host.sleep(10)
    attempt["gpu_placement"] = gpu_placement(host, pre["gpus"])
    write_json(attempt_dir / "services.json", services)

    # Only now, with every service serving, may init start the 14 h development clock.
    argv = as_user(dep, [str(repo / ".venv-libero/bin/python3"), "-u", CAMPAIGN,
                         "--case", str(Path(dep["case"])), *(["--resume"] if resume else []),
                         *([] if dep.get("heldout", True) else ["--no-heldout"])])
    env = cell_env(dep, case, host)
    attempt["campaign"] = {"argv": argv, "cwd": str(repo),
                           "env": {k: env[k] for k in ("PATH", "PYTHONPATH", "LD_LIBRARY_PATH",
                                                       "LIBERO_CONFIG_PATH")},
                           "inherited_keys": sorted(env)}
    attempt["status"] = "campaign_running"
    campaign = life.start("campaign", argv, cwd=str(repo), env=env, record=attempt["campaign"])
    write_json(entry / "entry_state.json", state)
    failed = []
    watch = mods.runtime.ServiceWatch(watched, lambda: failed.append(True) or
                                      life.stop({"campaign"}), interval=10)
    watch.start()
    try:
        code = campaign.wait()
    finally:
        watch.close()
    attempt["campaign"]["exit_code"] = code
    campaign_state = Path(case["control"]) / "campaign_state.json"
    if campaign_state.is_file():
        attempt["campaign"]["final_status"] = json.loads(campaign_state.read_text()).get("status")
    if watch.failure is not None:
        attempt.update(status="service_failed", blocker=str(watch.failure))
        return 1
    attempt["status"] = "campaign_exited"
    return code


def validate_only(deployment: Path, *, rehash_weights: bool, host: Host | None = None) -> int:
    host = host or Host()
    report = {"mode": "validate-only", "checked_at": now(), "services_started": False,
              "user_created": False, "task_actions": False}
    try:
        dep, report["deployment_sha256"] = load_deployment(deployment)
        mods = host.modules(Path(dep["runtime_root"]) / "aspire/sim", libero_config(dep["case"]))
        case, repo, _, report["case_sha256"] = check_case(dep, mods)
        report["static_identity"] = static_identity(dep, case, repo, host)
        report["assets"] = verify_assets(dep, case, repo, mods, rehash_weights=rehash_weights,
                                         workers=dep.get("hash_workers", 8))
        report["services"] = [{k: s[k] for k in ("name", "argv", "cwd", "port", "gpu", "adaptations")}
                              for s in service_specs(dep, case, repo, Path(dep["entry_dir"]))]
        problems = report["static_identity"]["problems"] + report["assets"]["problems"]
        report["status"] = "failed" if problems else "passed"
        report["problems"] = problems
    except EntryRefused as exc:
        report.update(status="refused", problems=[str(exc)])
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["status"] == "passed" else 2


def close_stale(deployment: Path, n: int, reason: str, host: Host | None = None) -> int:
    """Recorded outer action: mark an attempt that never closed. Touches no ledger."""
    host = host or Host()
    dep, dep_sha = load_deployment(deployment)
    entry = Path(dep["entry_dir"])
    with (entry / "entry.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EntryRefused("an entry is running and holds the lock") from None
        state = json.loads((entry / "entry_state.json").read_text())
        if state["identity"]["deployment_sha256"] != dep_sha:
            raise EntryRefused("deployment differs from this cell's recorded identity")
        attempt = next((a for a in state["attempts"] if a["n"] == n), None)
        if attempt is None or attempt.get("finished_at"):
            raise EntryRefused(f"attempt {n} is not an open attempt")
        if attempt.get("hostname") == socket.gethostname() and Path(f"/proc/{attempt['pid']}").exists():
            raise EntryRefused(f"attempt {n}'s entry process {attempt['pid']} is still alive here")
        attempt.update(status="abandoned", closed_at=now(), closed_reason=reason)
        write_json(entry / "entry_state.json", state)
    print(json.dumps(attempt, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true",
                        help="structure/file/hash checks only: no service, user or task action")
    parser.add_argument("--skip-weight-rehash", action="store_true",
                        help="validate-only: check Qwen weight sizes instead of sha256 (recorded)")
    parser.add_argument("--resume", action="store_true",
                        help="recorded outer resume: same identity, ledger and deadline")
    parser.add_argument("--close-stale-attempt", type=int, metavar="N")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    if args.skip_weight_rehash and not args.validate_only:
        parser.error("--skip-weight-rehash is only for --validate-only")
    try:
        if args.validate_only:
            return validate_only(args.deployment, rehash_weights=not args.skip_weight_rehash)
        if args.close_stale_attempt is not None:
            if not args.reason:
                parser.error("--close-stale-attempt needs --reason")
            return close_stale(args.deployment, args.close_stale_attempt, args.reason)
        return launch(args.deployment, resume=args.resume)
    except EntryRefused as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
