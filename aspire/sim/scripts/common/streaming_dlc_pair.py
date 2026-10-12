#!/usr/bin/env python3
"""One/two-worker DLC entry: per node ONE shared Qwen TP4 and TWO independent cells.

Operational orchestration only. Each cell is run by the frozen runtime's own
`streaming_fixloop_campaign.py` as that cell's unprivileged user; this file has
no LLM client and no agent loop. It reuses `streaming_dlc_entry` for case
validation, identity, asset checks, service specs, probes and the process
lifecycle, and adds only the pairing:

* The rank comes from the DLC environment and must match the declared workers.
  Those values are recorded. No service or campaign inherits any distributed
  variable (see `streaming_dlc_entry.base_env`).
* The fixed per-node GPU layout:

  | | Qwen (shared) | SAM3 + GraspNet | simulator / SAM2 / EGL | Pyroki | ports |
  | --- | --- | --- | --- | --- | --- |
  | slot A | 2,3,4,5 :8121 | 0 | 1 | CPU | 8214/8215/8216 |
  | slot B | (same Qwen) | 6 | 7 | CPU | 8114/8115/8116 |

  There is exactly one Qwen per node. Two TP4 engines would take the
  perception GPUs, so that layout is never built.
* Each cell has its own user, HOME, Claude config, ledger, outputs, skills,
  World and SAM2 state. Source, weights and common states stay shared read-only
  through the existing runtime group.
* Lifecycle: a slot that fails (preflight, its perception service, its
  campaign) stops only that slot's processes. A Qwen failure stops both
  campaigns. Qwen is stopped only after both cells have ended. Ledgers are
  never edited here.
* Modes per cell:
  - `fresh`: no ledger yet. The campaign's own `init` starts the 14 h clock.
  - `resume`: this cell's ledger already exists (an earlier DLC attempt).
    The campaign runs with `--resume`: same identity, ledger and deadline.
  - `migrate`: a running RS cell (bowl r1) continues here. The ledger clock
    must equal the declared original clock. After this node's services are
    ready, the outer side quiesces the RS worker and writes
    `<control>/dlc_migration_handoff.json`. The campaign starts with `--resume`
    only after that handoff matches the unchanged ledger and no trial is
    running. A stage-1-finalized cell is refused here, because its held-out
    run belongs to the outer side and no solver may start.

`--validate-only` checks structure, sources, assets and safety boundaries. It
starts no service, creates no user or directory and takes no task action.
Nothing here submits or resubmits a DLC job, refunds budget or forges a pass.
This is not a security sandbox; see AGENTS.md.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import socket
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import streaming_dlc_entry as E  # noqa: E402

SCHEMA = "aspire-streaming-dlc-pair-v1"
WORLD_SIZE = 2
QWEN_PORT = 8121
QWEN_ENDPOINT = f"http://127.0.0.1:{QWEN_PORT}"
SLOTS = {
    "A": {"qwen": "2,3,4,5", "sam3": "0", "graspnet": "0", "pyroki": "", "simulator": "1",
          "egl_device_id": 1, "ports": {"sam3": 8214, "graspnet": 8215, "pyroki": 8216}},
    "B": {"qwen": "2,3,4,5", "sam3": "6", "graspnet": "6", "pyroki": "", "simulator": "7",
          "egl_device_id": 7, "ports": {"sam3": 8114, "graspnet": 8115, "pyroki": 8116}},
}
URL_KEYS = {"sam3": "sam3_url", "graspnet": "graspnet_url", "pyroki": "pyroki_url"}
MODES = ("fresh", "resume", "migrate")
HANDOFF = "dlc_migration_handoff.json"
HANDOFF_KEYS = ("cell", "quiesced", "old_host", "old_pids", "ledger_sha256",
                "development_started_at", "development_deadline", "quiesced_at")
# Mirrors streaming_fixloop_campaign.TERMINAL: once stage 1 is finalized, only the
# outer held-out runner may continue; no solver session is started again.
TERMINAL = {"stage1_finalized", "complete", "heldout_incomplete", "heldout_refused"}
DIST_KEYS = ("WORLD_SIZE", "RANK", "LOCAL_RANK", "NODE_RANK", "LOCAL_WORLD_SIZE", "GROUP_RANK",
             "MASTER_ADDR", "MASTER_PORT")
PAIR_KEYS = ("schema", "workers", "gpus_per_worker", "runtime_root", "source_checkout_prefix",
             "envs", "entry_root", "runtime_group", "model_service_manifest",
             "model_service_manifest_sha256", "perception_descriptor",
             "perception_descriptor_sha256", "flashinfer_seed_cache",
             "service_ready_timeout_seconds", "handoff_wait_seconds", "ranks")
CELL_KEYS = ("cell", "case", "case_sha256", "control", "mode", "identity")
EntryRefused, StartupFailed, Terminated = E.EntryRefused, E.StartupFailed, E.Terminated


class PairHost(E.Host):
    def hostname(self) -> str:
        return socket.gethostname()

    def pid_alive(self, pid: int) -> bool:
        return Path(f"/proc/{int(pid)}").exists()


# --- deployment -------------------------------------------------------------------


def cells_of(pair: dict) -> list[tuple[int, str, dict]]:
    return [(int(r), s, pair["ranks"][r][s]) for r in sorted(pair["ranks"])
            for s in sorted(pair["ranks"][r])]


def load_pair(path: Path) -> tuple[dict, str]:
    pair = json.loads(Path(path).read_text())
    problems = [f"pair deployment requires {k}" for k in PAIR_KEYS if k not in pair]
    E.require(problems)
    if pair["schema"] != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    if type(pair["workers"]) is not int or pair["workers"] not in (1, 2) or pair["gpus_per_worker"] != E.GPU_COUNT:
        problems.append("this entry supports 1 or 2 workers, each with exactly 8 GPUs")
    E.require(problems)
    if sorted(pair["ranks"]) != [str(r) for r in range(pair["workers"])] or \
            any(sorted(v) != ["A", "B"] for v in pair["ranks"].values()):
        problems.append("ranks must match declared workers, each with slots A and B")
    E.require(problems)
    group = pair["runtime_group"]
    if not isinstance(group, dict) or set(group) != {"name", "gid"} or group["gid"] == 0:
        problems.append("runtime_group must be {name, gid} of the existing non-root read group")
    seen = {"cell": [], "uid": [], "user": [], "home": [], "control": []}
    for rank, slot, cell in cells_of(pair):
        where = f"rank {rank} slot {slot}"
        missing = [k for k in CELL_KEYS if k not in cell]
        if missing:
            problems.append(f"{where} requires {missing}")
            continue
        ident = cell["identity"]
        if set(ident) != {"user", "uid", "group", "gid", "home"}:
            problems.append(f"{where} identity must be exactly user/uid/group/gid/home")
            continue
        if cell["mode"] not in MODES:
            problems.append(f"{where} mode must be one of {MODES}")
        if ident["uid"] == 0 or ident["gid"] == 0:
            problems.append(f"{where} cell user may not be root")
        # Only the migrated RS cell keeps its own group as the runtime read group.
        if ident["gid"] == group.get("gid") and cell["mode"] != "migrate":
            problems.append(f"{where} a fresh cell's own group must differ from the runtime group")
        if cell["mode"] == "migrate":
            m = cell.get("migration", {})
            if not {"development_started_at", "development_deadline"} <= set(m):
                problems.append(f"{where} migrate requires migration.development_started_at "
                                "and development_deadline (the original clock)")
            elif abs(m["development_deadline"] - m["development_started_at"] - 50400) > 1e-3:
                problems.append(f"{where} declared clock is not the 50,400 s development window")
        for key, value in (("cell", cell["cell"]), ("uid", ident["uid"]), ("user", ident["user"]),
                           ("home", str(Path(ident["home"]).resolve())),
                           ("control", str(Path(cell["control"]).resolve()))):
            if value in seen[key]:
                problems.append(f"{key} {value!r} is used by two cells")
            seen[key].append(value)
    paths = [Path(p) for p in seen["home"] + seen["control"]]
    for a in paths:
        for b in paths:
            if a != b and a.is_relative_to(b):
                problems.append(f"{a} is nested inside another cell's {b}")
    root = Path(pair["entry_root"])
    if not root.is_absolute() or any(root.is_relative_to(p) or p.is_relative_to(root) for p in paths):
        problems.append("entry_root must be absolute and separate from every cell's HOME/control")
    E.require(problems)
    return pair, E.sha256_file(Path(path))


def runtime_modules(pair: dict, host: PairHost):
    cases = [c["case"] for _, _, c in cells_of(pair)]
    missing = [c for c in cases if not Path(c).is_file()]
    if missing:
        raise EntryRefused(f"outer cases are missing: {missing}")
    return host.modules(Path(pair["runtime_root"]) / "aspire/sim", E.libero_config(*cases))


def slot_dep(pair: dict, rank: int, slot: str) -> dict:
    """The single-cell deployment shape of one slot, so the entry's checks apply as is."""
    cell = pair["ranks"][str(rank)][slot]
    others = [c for _, _, c in cells_of(pair) if c["cell"] != cell["cell"]]
    root = Path(pair["entry_root"])
    forbidden = [p for c in others for p in (c["control"], c["identity"]["home"],
                                             str(root / "cells" / c["cell"]))]
    return {"cell": cell["cell"], "case": cell["case"], "case_sha256": cell["case_sha256"],
            "runtime_root": pair["runtime_root"],
            "source_checkout_prefix": pair["source_checkout_prefix"], "envs": pair["envs"],
            "identity": {**cell["identity"], "runtime_group": pair["runtime_group"]},
            "entry_dir": str(root / "cells" / cell["cell"]),
            "forbidden_read_paths": forbidden + cell.get("forbidden_read_paths", []),
            "must_read_paths": pair.get("must_read_paths", []),
            "flashinfer_seed_cache": pair["flashinfer_seed_cache"],
            "heldout": pair.get("heldout", True),
            "expected_gpu_name": pair.get("expected_gpu_name", "NVIDIA L20Z")}


def rank_of(environ, workers=WORLD_SIZE) -> dict:
    """Record and check the DLC rank variables. Nothing is guessed."""
    seen = {k: environ.get(k) for k in DIST_KEYS if environ.get(k) is not None}
    if type(workers) is not int or workers not in (1, 2) or seen.get("WORLD_SIZE") != str(workers) or seen.get("RANK") not in tuple(str(r) for r in range(workers)):
        raise EntryRefused(f"expected WORLD_SIZE={workers} and a declared RANK from DLC, got {seen}")
    for key in ("NODE_RANK", "GROUP_RANK"):
        if key in seen and seen[key] != seen["RANK"]:
            raise EntryRefused(f"{key}={seen[key]} disagrees with RANK={seen['RANK']}")
    if seen.get("LOCAL_RANK", "0") != "0":
        raise EntryRefused(f"one entry per node: LOCAL_RANK {seen['LOCAL_RANK']} exits: {seen}")
    return {"rank": int(seen["RANK"]), "recorded": seen}


# --- per-cell checks --------------------------------------------------------------


def ledger_summary(task_dir: Path) -> dict | None:
    """Clock, counts and status only. Generated programs and feedback are not read."""
    path = Path(task_dir) / "development_state.json"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    data = json.loads(raw)
    trials = data.get("trials", [])
    return {"sha256": hashlib.sha256(raw).hexdigest(), "phase_clock": data.get("phase_clock"),
            "identity_cell": data.get("identity", {}).get("cell"),
            "identity_sha256": hashlib.sha256(json.dumps(data.get("identity"), sort_keys=True)
                                              .encode()).hexdigest(),
            "trials": len(trials), "running": sum(t.get("status") == "running" for t in trials),
            "by_status": dict(Counter(t.get("status") for t in trials)),
            "charged_per_seed": dict(Counter(str(t.get("seed")) for t in trials
                                             if t.get("phase") != "snapshot")),
            "stage1_complete": data.get("stage1_complete"),
            "selected": data.get("selected") is not None}


def campaign_status(case: dict) -> str | None:
    path = Path(case["control"]) / "campaign_state.json"
    return json.loads(path.read_text()).get("status") if path.is_file() else None


def mode_check(cell: dict, case: dict, task_dir: Path, recorded: dict | None, *,
               pending_migration: bool = False) -> dict:
    """Fresh/resume/migrate preconditions on the ledger, without touching it."""
    mode, ledger, status = cell["mode"], ledger_summary(task_dir), campaign_status(case)
    out = {"mode": mode, "ledger": ledger, "campaign_status": status}
    if mode == "fresh":
        if ledger is not None:
            raise EntryRefused(f"{cell['cell']} already has a ledger; a fresh start would hide a "
                               "restart (declare resume)")
        return out
    if ledger is None:
        raise EntryRefused(f"{cell['cell']} has no ledger to {mode}")
    if ledger["identity_cell"] != case["id"]:
        raise EntryRefused("ledger belongs to another cell")
    clock = ledger["phase_clock"]["development"]
    if status in TERMINAL or ledger["stage1_complete"]:
        raise EntryRefused(f"{cell['cell']} stage 1 is finalized ({status}); its held-out run is "
                           "an outer resume, no solver is started")
    # A live RS worker remains active while the destination services warm up.
    # Only preflight may defer these two checks; check_handoff is strict before
    # any resumed campaign is started.
    pending_migration = pending_migration and mode == "migrate"
    if status == "running" and not pending_migration:
        raise EntryRefused(f"{cell['cell']} campaign_state is still `running`: the previous "
                           "worker is active or was killed without closing its session")
    if ledger["running"] and not pending_migration:
        raise EntryRefused(f"{cell['cell']} has {ledger['running']} running trial row(s)")
    if mode == "migrate":
        m = cell["migration"]
        if (clock["started_at"], clock["deadline"]) != \
                (m["development_started_at"], m["development_deadline"]) or \
                clock.get("watchdog_seconds") != 50400:
            raise EntryRefused(f"{cell['cell']} ledger clock {clock} differs from the declared "
                               "original development clock")
    if recorded and recorded.get("phase_clock") and recorded["phase_clock"] != ledger["phase_clock"]:
        raise EntryRefused(f"{cell['cell']} phase clock changed since this entry first saw it")
    return out


def check_handoff(cell: dict, case: dict, task_dir: Path, host: PairHost) -> dict | None:
    """None while the outer handoff is absent; refused if present but inconsistent."""
    path = Path(case["control"]) / HANDOFF
    if not path.is_file():
        return None
    handoff = json.loads(path.read_text())
    problems = [f"handoff requires {k}" for k in HANDOFF_KEYS if k not in handoff]
    E.require(problems)
    ledger = ledger_summary(task_dir)
    clock = ledger["phase_clock"]["development"]
    m = cell["migration"]
    if handoff["cell"] != cell["cell"] or handoff["quiesced"] is not True:
        problems.append("handoff is not a quiesce record for this cell")
    if handoff["ledger_sha256"] != ledger["sha256"]:
        problems.append("ledger changed after the handoff (the old worker may still be running)")
    if (handoff["development_started_at"], handoff["development_deadline"]) != \
            (clock["started_at"], clock["deadline"]) or \
            (clock["started_at"], clock["deadline"]) != \
            (m["development_started_at"], m["development_deadline"]):
        problems.append("handoff, ledger and declared development clocks differ")
    if ledger["running"]:
        problems.append("a trial row is still running")
    status = campaign_status(case)
    if status == "running" or status in TERMINAL:
        problems.append(f"campaign_state is {status}; not a resumable quiesced state")
    if handoff["old_host"] == host.hostname():
        alive = [p for p in handoff["old_pids"] if host.pid_alive(p)]
        if alive:
            problems.append(f"old worker PIDs still alive on this host: {alive}")
    E.require(problems)
    return {"handoff": handoff, "handoff_sha256": E.sha256_file(path), "ledger": ledger}


def perception_equivalent(descriptor: dict, manifest: dict) -> list[str]:
    """A cell's own perception manifest may differ from the common one only in its URLs."""
    skip = {"sam3_url", "graspnet_url", "pyroki_url", "startup_manifest"}
    return [k for k in descriptor if k not in skip and manifest.get(k) != descriptor[k]]


def preflight_slot(pair, rank, slot, mods, host, recorded) -> dict:
    layout = SLOTS[slot]
    cell = pair["ranks"][str(rank)][slot]
    dep = slot_dep(pair, rank, slot)
    case, repo, task_dir, case_sha = E.check_case(dep, mods, layout)
    problems = []
    if str(Path(case["control"]).resolve()) != str(Path(cell["control"]).resolve()):
        problems.append("case control differs from the deployment's control")
    for role, key in URL_KEYS.items():
        if case[key] != f"http://127.0.0.1:{layout['ports'][role]}":
            problems.append(f"slot {slot} {key} must be port {layout['ports'][role]}")
    service = case["model_service"]
    if service["endpoint"] != QWEN_ENDPOINT or \
            service["server_manifest_sha256"] != pair["model_service_manifest_sha256"] or \
            Path(service["server_manifest"]).resolve() != Path(pair["model_service_manifest"]).resolve():
        problems.append("every cell binds the one shared Qwen descriptor at :8121")
    descriptor = json.loads(Path(pair["perception_descriptor"]).read_text())
    if E.sha256_file(Path(pair["perception_descriptor"])) != pair["perception_descriptor_sha256"]:
        problems.append("perception descriptor sha256 differs from the deployment binding")
    differ = perception_equivalent(descriptor, json.loads(Path(case["perception_manifest"]).read_text()))
    if differ:
        problems.append(f"cell perception manifest differs from the common descriptor in {differ}")
    E.require(problems)
    if recorded and recorded.get("case_sha256") not in (None, case_sha):
        raise EntryRefused(f"{cell['cell']} case changed since its first DLC attempt")
    modes = mode_check(cell, case, task_dir, recorded,
                       pending_migration=cell["mode"] == "migrate")
    static = E.static_identity(dep, case, repo, host)
    E.require(static["problems"])
    assets = E.verify_cell_assets(dep, case, repo, mods)
    E.require(assets["problems"])
    return {"slot": slot, "cell": cell, "dep": dep, "case": case, "repo": repo,
            "task_dir": task_dir, "case_sha256": case_sha, "mode": modes, "static": static,
            "assets": assets, "layout": layout, "status": "preflight_ok"}


# --- node state -------------------------------------------------------------------


def open_node(pair: dict, rank: int) -> tuple[Path, dict]:
    root = Path(pair["entry_root"])
    E.private_dir(root, 0, 0)
    node = root / f"rank{rank}"
    E.private_dir(node, 0, 0)
    E.private_dir(root / "cells", 0, 0)
    path = node / "pair_state.json"
    state = json.loads(path.read_text()) if path.is_file() else {"rank": rank, "attempts": [],
                                                                 "cells": {}}
    return node, state


def public(slot: dict) -> dict:
    return {k: v for k, v in slot.items()
            if k not in ("dep", "cell", "repo", "layout", "specs", "process")}


def remember_clock(state: dict, cell_id: str, ledger: dict | None) -> None:
    """The first clock seen for a cell; every later attempt must show the same one."""
    if ledger and ledger.get("phase_clock"):
        state["cells"].setdefault(cell_id, {}).setdefault("phase_clock", ledger["phase_clock"])


def launch(path: Path, host: PairHost | None = None) -> int:
    host = host or PairHost()
    pair, pair_sha = load_pair(path)
    if host.euid() != 0:
        raise EntryRefused("the pair entry runs as root for minimal setup only")
    rank = rank_of(host.environ, pair["workers"])
    mods = runtime_modules(pair, host)
    node, state = open_node(pair, rank["rank"])
    lock = (node / "entry.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise EntryRefused(f"another entry holds rank {rank['rank']}'s lock") from None
    stale = [a["n"] for a in state["attempts"] if not a.get("finished_at")]
    if stale:
        raise EntryRefused(f"rank {rank['rank']} attempt(s) {stale} never closed; another "
                           "worker may still run these cells")
    n = len(state["attempts"]) + 1
    attempt_dir = node / "attempts" / f"{n:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    attempt = {"n": n, "dir": str(attempt_dir), "deployment_sha256": pair_sha, "rank": rank,
               "hostname": host.hostname(), "pid": os.getpid(), "started_at": E.now(),
               "entry_sha256": E.entry_sha256(),
               "pair_sha256": E.sha256_file(Path(__file__)), "status": "preflight", "slots": {}}
    state["attempts"].append(attempt)
    E.write_json(node / "pair_state.json", state)
    life = E.Lifecycle(host, attempt_dir)

    def terminate(signum, _frame):
        raise Terminated(f"signal {signum}")
    previous = {s: signal.signal(s, terminate) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    code = 1
    try:
        code = _run(pair, rank["rank"], host, mods, node, state, attempt, attempt_dir, life)
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
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        attempt["teardown"] = life.stop()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        for slot in attempt["slots"].values():
            if slot.get("campaign") and "ledger_after" not in slot:
                slot["ledger_after"] = ledger_summary(Path(slot["task_dir"]))
                remember_clock(state, slot["case"]["id"], slot["ledger_after"])
        attempt["finished_at"] = E.now()
        E.write_json(node / "pair_state.json", state)
        E.write_json(attempt_dir / "result.json", attempt)
        print(json.dumps(attempt, indent=2, default=str))
    return code


def _run(pair, rank, host, mods, node, state, attempt, attempt_dir, life) -> int:
    attempt["subreaper"] = E.set_subreaper()
    expected_gpu = pair.get("expected_gpu_name", "NVIDIA L20Z")
    slots = {}
    for name in ("A", "B"):
        cell_id = pair["ranks"][str(rank)][name]["cell"]
        try:
            slots[name] = preflight_slot(pair, rank, name, mods, host, state["cells"].get(cell_id))
            state["cells"].setdefault(cell_id, {"case_sha256": slots[name]["case_sha256"],
                                                "first_attempt": attempt["n"]})
            remember_clock(state, cell_id, slots[name]["mode"]["ledger"])
        except Exception as exc:  # one slot's refusal never stops the other slot
            slots[name] = {"slot": name, "status": "refused", "cell_id": cell_id,
                           "blocker": f"{type(exc).__name__}: {exc}"}
        attempt["slots"][name] = public(slots[name])
    E.write_json(attempt_dir / "preflight.json", attempt["slots"])
    viable = [s for s in slots.values() if s["status"] == "preflight_ok"]
    if not viable:
        raise EntryRefused("no cell of this rank passed preflight")
    first = viable[0]
    model = E.verify_model(first["dep"], first["case"], mods, rehash_weights=True,
                           workers=pair.get("hash_workers", 8))
    attempt["model"] = model
    E.require(model["problems"])
    attempt["gpus"] = E.gpu_inventory(host, expected_gpu)
    qwen = E.qwen_spec(first["dep"], first["case"], node / "cache")
    E.check_specs([qwen], first["dep"], SLOTS["A"])
    for slot in viable:
        cache = Path(slot["dep"]["entry_dir"]) / "cache"
        slot["specs"] = E.perception_specs(slot["dep"], slot["case"], slot["repo"], cache,
                                           slot["layout"], pair["perception_descriptor"],
                                           prefix_name=f"{slot['slot']}-")
        E.check_specs(slot["specs"], slot["dep"], slot["layout"])
    every = [qwen, *(s for slot in viable for s in slot["specs"])]
    missing = E.missing_spec_paths(every)
    if missing:
        raise EntryRefused(f"recorded service paths are missing on this node: {missing[:8]}")
    busy = [s["port"] for s in every if host.listening(s["port"])]
    if busy:
        raise EntryRefused(f"ports already listening (an active cell or service): {busy}")
    for slot in viable:
        try:
            E.private_dir(Path(slot["dep"]["entry_dir"]), 0, 0)
            slot["setup"] = E.ensure_identity(slot["dep"], slot["case"], host)
            slot["identity_probe"] = E.identity_probe(slot["dep"], slot["case"], slot["repo"], host)
            slot["device_probe"] = E.device_probe(slot["dep"], slot["case"], host, expected_gpu,
                                                  slot["layout"])
        except (EntryRefused, StartupFailed) as exc:
            slot.update(status="startup_failed", blocker=str(exc))
        attempt["slots"][slot["slot"]] = public(slot)
    viable = [s for s in viable if s["status"] == "preflight_ok"]
    if not viable:
        raise StartupFailed("no cell of this rank passed its identity/device probes")

    attempt["status"] = "starting_services"
    services = {}
    seed = qwen["seed_cache"]
    if not Path(seed["to"]).exists():
        E.shutil.copytree(seed["from"], seed["to"], symlinks=True)  # this node's own copy
    for spec, slot in [(qwen, None), *((s, slot) for slot in viable for s in slot["specs"])]:
        record = {k: spec[k] for k in ("name", "argv", "cwd", "port", "gpu", "adaptations",
                                       "descriptor")}
        record["env"] = spec["env"]
        process = life.start(spec["name"], spec["argv"], cwd=spec["cwd"],
                             env={**E.base_env(host.environ), **spec["env"]}, record=record,
                             log_dir=Path(slot["dep"]["entry_dir"]) / "logs" / attempt_dir.name
                             if slot else None)
        services[spec["name"]] = {"spec": spec, "slot": slot, "record": record,
                                  "watch": mods.runtime.Service(spec["name"], process,
                                                                Path(record["log"]), "")}
    attempt["services"] = {n: s["record"] for n, s in services.items()}
    E.write_json(attempt_dir / "services.json", attempt["services"])

    def fail_slot(slot, status, reason):
        slot.update(status=status, blocker=reason)
        attempt["slots"][slot["slot"]] = public(slot)
        E.write_json(node / "pair_state.json", state)
        life.stop({f"{slot['slot']}-campaign", *(s["name"] for s in slot["specs"])})

    def check_services() -> None:
        for name, svc in services.items():
            slot = svc["slot"]
            if slot is not None and slot["status"] not in ("preflight_ok", "campaign_running"):
                continue
            try:
                svc["watch"].check()
            except mods.runtime.ServiceFailure as exc:
                if slot is None:  # Qwen: every campaign depends on it
                    for other in viable:
                        if other["status"] in ("preflight_ok", "campaign_running"):
                            other.update(status="service_failed", blocker=f"shared Qwen: {exc}")
                            attempt["slots"][other["slot"]] = public(other)
                    E.write_json(node / "pair_state.json", state)
                    raise StartupFailed(f"shared Qwen failed: {exc}") from exc
                fail_slot(slot, "service_failed" if slot.get("campaign") else "startup_failed",
                          f"{name} failed: {exc}")
        attempt["health_checked_at"] = E.now()
        E.write_json(node / "pair_state.json", state)

    start = host.clock()
    timeout = float(pair["service_ready_timeout_seconds"])
    pending = set(services)
    while pending:
        check_services()
        for name in sorted(pending):
            svc = services[name]
            if svc["slot"] is not None and svc["slot"]["status"] != "preflight_ok":
                pending.discard(name)
                continue
            ok, detail = E.ready(svc["spec"], svc["slot"]["case"] if svc["slot"] else first["case"],
                                 host)
            svc["record"]["last_probe"] = detail
            if ok:
                svc["record"].update(ready_at=E.now(),
                                     ready_after_seconds=round(host.clock() - start, 1))
                pending.discard(name)
        if pending and host.clock() - start > timeout:
            late = sorted(pending)
            if "qwen" in late:
                raise StartupFailed(f"shared Qwen not ready after {timeout:.0f}s")
            for slot in viable:
                if any(s["name"] in late for s in slot["specs"]):
                    fail_slot(slot, "startup_failed", f"services not ready after {timeout:.0f}s")
            pending.clear()
        if pending:
            host.sleep(10)
    attempt["gpu_placement"] = E.gpu_placement(host, attempt["gpus"])
    attempt["services"] = {n: s["record"] for n, s in services.items()}
    E.write_json(attempt_dir / "services.json", attempt["services"])
    E.write_json(node / "services_ready.json", {
        "attempt": attempt["n"], "hostname": attempt["hostname"], "at": E.now(),
        "ready": {n: {"port": s["record"]["port"], "pid": s["record"]["pid"]}
                  for n, s in services.items() if "ready_at" in s["record"]},
        "slots": {s["slot"]: {"cell": s["case"]["id"], "status": s["status"],
                              "awaiting_handoff": s["cell"]["mode"] == "migrate"}
                  for s in viable}})

    # Base readiness is complete; only now may a campaign (and its init clock) start.
    attempt["status"] = "campaigns_running"
    poll = float(pair.get("poll_seconds", 15))
    waiting_since = host.clock()
    while True:
        check_services()
        active = False
        for slot in viable:
            if slot["status"] == "preflight_ok":
                if slot["cell"]["mode"] == "migrate":
                    try:
                        handoff = check_handoff(slot["cell"], slot["case"], slot["task_dir"], host)
                    except EntryRefused as exc:
                        fail_slot(slot, "refused", f"handoff refused: {exc}")
                        continue
                    if handoff is None:
                        if host.clock() - waiting_since > float(pair["handoff_wait_seconds"]):
                            fail_slot(slot, "refused", "no migration handoff within "
                                      f"{pair['handoff_wait_seconds']}s; RS worker not quiesced")
                        else:
                            active = True
                        continue
                    slot["handoff"] = handoff
                start_campaign(slot, host, life)
                attempt["slots"][slot["slot"]] = public(slot)
                E.write_json(node / "pair_state.json", state)
            if slot["status"] == "campaign_running":
                code = slot["process"].poll()
                if code is None:
                    active = True
                    continue
                finish_campaign(slot, code)
                remember_clock(state, slot["case"]["id"], slot["ledger_after"])
                attempt["slots"][slot["slot"]] = public(slot)
                E.write_json(node / "pair_state.json", state)
        if not active:
            break
        host.sleep(poll)
    attempt["status"] = "campaigns_exited"
    codes = [s.get("campaign", {}).get("exit_code") for s in slots.values()]
    return 0 if codes and all(c == 0 for c in codes) else 1


def start_campaign(slot: dict, host: PairHost, life: E.Lifecycle) -> None:
    resume = slot["cell"]["mode"] in ("resume", "migrate")
    slot["ledger_before"] = ledger_summary(slot["task_dir"])
    dep, repo = slot["dep"], slot["repo"]
    argv = E.as_user(dep, [str(repo / ".venv-libero/bin/python3"), "-u", E.CAMPAIGN,
                           "--case", str(Path(dep["case"])), *(["--resume"] if resume else []),
                           *([] if dep.get("heldout", True) else ["--no-heldout"])])
    env = E.cell_env(dep, slot["case"], host)
    slot["campaign"] = {"argv": argv, "cwd": str(repo), "env_keys": sorted(env),
                        "env": {k: env[k] for k in ("HOME", "PATH", "PYTHONPATH",
                                                    "LD_LIBRARY_PATH", "LIBERO_CONFIG_PATH")}}
    slot["process"] = life.start(f"{slot['slot']}-campaign", argv, cwd=str(repo), env=env,
                                 record=slot["campaign"])
    slot["status"] = "campaign_running"


def finish_campaign(slot: dict, code: int) -> None:
    slot["campaign"]["exit_code"] = code
    slot["campaign"]["final_status"] = campaign_status(slot["case"])
    before, after = slot["ledger_before"], ledger_summary(slot["task_dir"])
    slot["ledger_after"] = after
    violations = []
    if before and after:
        if after["phase_clock"].get("development") != before["phase_clock"].get("development"):
            violations.append("development phase clock changed")
        for phase, old_clock in before["phase_clock"].items():
            if phase != "development" and after["phase_clock"].get(phase) != old_clock:
                violations.append(f"{phase} phase clock changed")
        if after["trials"] < before["trials"]:
            violations.append("ledger lost trial rows")
    slot["ledger_invariants"] = violations or "kept (same clock, no rows removed)"
    slot["status"] = "campaign_exited"


def validate_only(path: Path, *, rehash_weights: bool, host: PairHost | None = None) -> int:
    host = host or PairHost()
    report = {"mode": "validate-only", "checked_at": E.now(), "services_started": False,
              "users_created": False, "directories_created": False, "task_actions": False,
              "cells": {}}
    try:
        pair, report["deployment_sha256"] = load_pair(path)
        parent = Path(pair["entry_root"]).parent
        if not parent.is_dir():
            raise EntryRefused(f"parent of {pair['entry_root']} must already exist "
                               "(prepared by the outer side)")
        mods = runtime_modules(pair, host)
        first = None
        for rank, slot, cell in cells_of(pair):
            key = f"rank{rank}/{slot}/{cell['cell']}"
            try:
                result = preflight_slot(pair, rank, slot, mods, host, None)
                specs = E.perception_specs(result["dep"], result["case"], result["repo"],
                                           Path(result["dep"]["entry_dir"]) / "cache",
                                           SLOTS[slot], pair["perception_descriptor"],
                                           prefix_name=f"{slot}-")
                E.check_specs(specs, result["dep"], SLOTS[slot])
                entry = {"status": "passed", "mode": result["mode"],
                         "services": [{k: s[k] for k in ("name", "argv", "port", "gpu",
                                                         "adaptations")} for s in specs],
                         "missing_service_paths": E.missing_spec_paths(specs)}
                if cell["mode"] == "migrate":
                    try:
                        handoff = check_handoff(cell, result["case"], result["task_dir"], host)
                        entry["handoff"] = handoff or "pending (written by the outer side after "\
                                                      "this node's services are ready)"
                    except EntryRefused as exc:
                        entry.update(status="failed", handoff=f"refused: {exc}")
                first = first or result
            except Exception as exc:
                entry = {"status": "failed", "problems": [f"{type(exc).__name__}: {exc}"]}
            report["cells"][key] = entry
        # The shared Qwen is checked even when no cell passes: every case binds the
        # same sha-pinned descriptor (preflight_slot), so any case file serves.
        if first is None:
            _, _, cell = cells_of(pair)[0]
            first = {"dep": slot_dep(pair, 0, "A"), "case": json.loads(Path(cell["case"]).read_text())}
        if first:
            model = E.verify_model(first["dep"], first["case"], mods,
                                   rehash_weights=rehash_weights, workers=pair.get("hash_workers", 8))
            qwen = E.qwen_spec(first["dep"], first["case"], Path(pair["entry_root"]) / "rank0/cache")
            E.check_specs([qwen], first["dep"], SLOTS["A"])
            report["model"] = model
            report["qwen_service"] = {k: qwen[k] for k in ("argv", "port", "gpu", "adaptations")}
            report["qwen_missing_paths"] = E.missing_spec_paths([qwen])
        problems = [k for k, v in report["cells"].items() if v["status"] != "passed"]
        problems += report.get("model", {}).get("problems", ["model not checked"])
        problems += report.get("qwen_missing_paths", [])
        report["status"] = "failed" if problems else "passed"
        report["problems"] = problems
    except EntryRefused as exc:
        report.update(status="refused", problems=[str(exc)])
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["status"] == "passed" else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true",
                        help="structure/source/asset/boundary checks; no service, user or task")
    parser.add_argument("--skip-weight-rehash", action="store_true",
                        help="validate-only: Qwen weight sizes instead of sha256 (recorded)")
    args = parser.parse_args(argv)
    if args.skip_weight_rehash and not args.validate_only:
        parser.error("--skip-weight-rehash is only for --validate-only")
    try:
        if args.validate_only:
            return validate_only(args.deployment, rehash_weights=not args.skip_weight_rehash)
        return launch(args.deployment)
    except EntryRefused as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
