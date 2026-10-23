#!/usr/bin/env python3
"""Run one node of the 2026-10-15 unit layout from a shared cell queue.

Every four GPUs form one unit: one Qwen TP4 server (DESIGN §1 config), SAM3,
GraspNet, PyRoKi (CPU) and two cell slots, perception and simulators on the
unit's own cards. A 4-GPU worker runs one unit, an 8-GPU worker two. Units are
independent: own ports, logs, health checks and ServiceWatch. A failed unit
stops only itself; the cells it was running are recorded as infrastructure
failures, archived and requeued FRESH once (`unit_layout.infra_requeue`, cap
`INFRA_REQUEUE_CAP`), and any slot with a sufficient window may claim them.

Each slot claims the next cell atomically (the p3 `mkdir` semantics), binds it
to its unit for the cell's whole life (model endpoint, service URLs, simulator
GPU/EGL device), writes the bound case beside the staged one and records the
binding in the claim and in the cell's `queue-result.json`.

In `native_smoke` mode the supervisor enforces the smoke boundary: it watches
the cell's development ledger and, once the first charged trial is terminal,
ends the driver tree (never the trial in flight) and records the smoke result.
The prompt's pause request is no longer what stops a smoke.

Teardown (CC_TASK_2, 2026-10-17): a unit stops its services by SESSION, with a
SIGKILL escalation and a verified-empty check (`native_cc_runtime.stop_sessions`);
the node exits a bounded time after a unit failure, sweeps every session it
started, records what survived in status.json and ends the process with
`os._exit`, so no orphan or interpreter shutdown can keep the job Running.

Derived from the p3 search-control `dlc-supervisor.py` (2026-10-14). Kept: the
launch/support/runtime pins, nonprivileged and identity gates, SAM3 inference
health before every claim, served-model and native compat checks, the owned
process-tree cleanup and the never-rerun-an-interrupted-cell rule. Changed: the
one-cell-per-node GPU map is replaced by units and slots, and the runtime-family
check no longer compares slot wiring. Contract:
`docs/engineering/node-layout-20261015/DESIGN.md`.

  unit_supervisor.py --root R --gpus 0,1,2,3 --node-id 0 --node-count 1 --node-window-minutes W
  unit_supervisor.py --root R --gpus 0,1,2,3,4,5,6,7 --plan     # print the layout only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import unit_layout as layout
from native_cc_runtime import (Service, ServiceWatch, atomic_json, isolated_jit_env,
                               new_attempt, stop_processes, stop_sessions)

GRASPNET_ROOT = Path("/mnt/home/gewang/code/ASPIRE/aspire/sim/cap/third_party/contact_graspnet_pytorch")
GRASPNET_CHECKPOINT_REL = "checkpoints/contact_graspnet/checkpoints"
SAM3_CHECKPOINT = Path("/mnt/home/gewang/.cache/aspire/sam3/sam3.pt")
SAM3_HEALTH_IMAGE = "reference/sam3-public-dev51-agentview.jpg"
RENDEZVOUS = ("RANK", "LOCAL_RANK", "WORLD_SIZE", "LOCAL_WORLD_SIZE", "MASTER_ADDR",
              "MASTER_PORT", "GROUP_RANK", "ROLE_RANK", "TORCHELASTIC_RUN_ID")
DEV_SEEDS = list(range(51, 66))
HELDOUT_SEEDS = list(range(1, 51))
#: Terminal record state per study mode.
TERMINAL = {"full": "full_complete", "native_smoke": "native_smoke_complete"}
COMPAT_ATTEMPTS = 3
#: Unit startup (Qwen load, SAM3 health, native compat) the node window must allow
#: before a slot can still claim one whole cell. Measured ~13 min on DSW (Task 4).
STARTUP_ALLOWANCE = 1800
#: CC_TASK_5 (2026-10-19): a queue entry carrying this file beside its case.json
#: supplements an existing, preserved held-out sweep in place (its case names the
#: source cell's control dir); the driver runs with `--heldout-supplement`.
SUPPLEMENT_MARKER = layout.SUPPLEMENT_MARKER
#: Checkpoints only an older recovery chain wrote; such a cell is recovered by hand.
RECOVERY_CHECKPOINTS = ("native_smoke_state.json", "native_work_continuation_checkpoint.json")
#: Native smoke ends when the supervisor, not the prompt, sees the first charged
#: ledger row reach a terminal status (`native_world_fixloop_state.begin_trial`
#: writes "running"; `finish_trial` rewrites it in place, atomically).
SMOKE_STOP_REASON = "supervisor_smoke_stop"
#: A smoke passes when its charged trial produced graded or program evidence;
#: `infrastructure_error` (or a still-"running" row) is a failed smoke.
SMOKE_PASS_STATUSES = frozenset({"complete", "world_program_error", "diagnostic_program_error"})
SMOKE_TRIAL_FIELDS = ("phase", "seed", "attempt", "status", "reward", "task_completed",
                      "sandbox_rc", "process_exit_code", "error", "directory", "trial_dir")
#: After a unit fails, the node waits at most this long for that unit's threads
#: (cell cleanup is <= ~2 min) before it stops waiting and exits anyway; the
#: final session sweep then ends whatever those threads still owned.
UNIT_STOP_GRACE = 600
#: How often an idle slot looks for a requeued cell (see `Node.wait_for_requeue`).
REQUEUE_POLL = 60


class UnitDown(RuntimeError):
    """The cell's unit lost a service; the cell is an infrastructure failure."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_env():
    return {k: os.environ[k] for k in (
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
        "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES") if k in os.environ}


def python_entry(py, script, *args):
    # SIGTERM must unwind the native transport's finally block and its separately
    # owned CC process group, rather than orphaning it when the driver is stopped.
    wrapper = ("import runpy,signal,sys; "
               "signal.signal(signal.SIGTERM,lambda *_:signal.raise_signal(signal.SIGINT)); "
               "sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; "
               "runpy.run_path(sys.argv[0],run_name='__main__')")
    return [py, "-u", "-c", wrapper, str(Path(script).parent), str(script), *map(str, args)]


class CellProcessTree:
    """Track only one owned driver and descendants; confirm cleanup before reuse."""

    def __init__(self, process):
        import psutil
        self.psutil = psutil
        self.process = process
        self.root = psutil.Process(process.pid)
        self.members = {(self.root.pid, self.root.create_time()): self.root}
        self.signaled = []
        self.collect()

    def live(self, process):
        try:
            return process.is_running() and process.status() != self.psutil.STATUS_ZOMBIE
        except self.psutil.NoSuchProcess:
            return False

    def collect(self):
        for process in list(self.members.values()):
            if not self.live(process):
                continue
            try:
                for child in process.children(recursive=True):
                    self.members[(child.pid, child.create_time())] = child
            except self.psutil.NoSuchProcess:
                pass
            # AccessDenied is a failed ownership check, never proof of cleanup.

    def survivors(self):
        self.collect()
        return [p for p in self.members.values() if self.live(p)]

    def poll(self):
        self.collect()
        return self.process.poll()

    def signal(self, processes, method):
        for process in processes:
            if not self.live(process):
                continue
            try:
                getattr(process, method)()
                self.signaled.append({"pid": process.pid, "signal": method})
            except self.psutil.NoSuchProcess:
                pass

    def cleanup(self, grace=90):
        # Grace lets the driver unwind native CC's separately owned groups.
        self.collect()
        if self.live(self.root):
            self.signal([self.root], "terminate")
        deadline = time.monotonic() + grace
        while self.survivors() and time.monotonic() < deadline:
            # Give the root 15s to run its finally, then terminate survivors.
            if time.monotonic() >= deadline - max(0, grace - 15):
                self.signal(self.survivors(), "terminate")
            time.sleep(.1)
        left = self.survivors()
        if left:
            self.signal(left, "kill")
            _, alive = self.psutil.wait_procs(left, timeout=15)
            if any(self.live(p) for p in alive):
                raise RuntimeError("owned cell process tree survived cleanup; stop this unit")
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("owned driver was not reaped; stop this unit") from exc
        left = self.survivors()
        if left:
            raise RuntimeError(f"owned live work remains after cleanup: {[p.pid for p in left]}")
        return {"verified_no_live_work": True, "tracked_pids": [p.pid for p in self.members.values()],
                "signals": self.signaled, "driver_exit": self.process.returncode}


def read_ledger(path):
    """The cell's development ledger, or None before it exists or mid-write."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def first_charged(ledger):
    """(index, row) of the first retry-spending trial in ledger order, else None."""
    for index, row in enumerate((ledger or {}).get("trials") or []):
        if row.get("spends_retry"):
            return index, row
    return None


def smoke_stop_point(ledger):
    """The first charged row once it is terminal; None while absent or running."""
    found = first_charged(ledger)
    if found is None or found[1].get("status") in (None, "running"):
        return None
    return found


def smoke_trial(row):
    return {key: row.get(key) for key in SMOKE_TRIAL_FIELDS}


def smoke_stop_outcome(ledger, index):
    """What the ledger holds for and after the stop row, read after cleanup.

    The ledger is never edited. A trial the solver began after the smoke trial
    finished and before the stop took effect is recorded here as
    infrastructure_error caused by the smoke stop, whatever its ledger status,
    and is excluded from every statistic.
    """
    trials = (ledger or {}).get("trials") or []
    after = [{**smoke_trial(row), "ledger_index": i, "ledger_status": row.get("status"),
              "recorded_as": "infrastructure_error", "counted": False,
              "note": f"began before {SMOKE_STOP_REASON} took effect; interrupted by the smoke stop"}
             for i, row in enumerate(trials[index + 1:], start=index + 1) if row.get("executed")]
    return {"smoke_attempt": smoke_trial(trials[index]), "ledger_index": index,
            "after_stop": after, "rejected_admissions": len((ledger or {}).get("rejected") or [])}


class UnitState:
    def __init__(self, unit):
        self.unit = unit
        self.down = threading.Event()
        self.failure = None
        self.processes = []
        self.lock = threading.Lock()
        self.state = "pending"
        self.slots = {slot.name: {"state": "idle", "gpu": slot.gpu} for slot in unit.slots}
        self.initial_health = []
        self.watch = None
        self.failed_at = None
        self.teardown = None

    def fail(self, reason):
        with self.lock:
            if self.failure is None:
                self.failure = str(reason)
                self.failed_at = time.monotonic()
        self.down.set()

    def describe(self):
        return {**self.unit.describe(), "state": self.state, "failure": self.failure,
                "slots": self.slots, "owned_pids": [p.pid for p in self.processes],
                "teardown": self.teardown}


class Node:
    """Scheduling, claiming, unit isolation and records. Process work goes through `hooks`."""

    def __init__(self, *, root, cells, units, hooks, worker, status_path, node_stop_at,
                 cell_timeout, mode="full"):
        self.root, self.cells, self.hooks, self.worker = Path(root), list(cells), hooks, worker
        self.status_path, self.node_stop_at, self.cell_timeout = status_path, node_stop_at, cell_timeout
        self.mode, self.terminal = mode, TERMINAL[mode]
        self.units = [UnitState(u) for u in units]
        self.unit_stop_grace = UNIT_STOP_GRACE
        self.requeue_poll = REQUEUE_POLL
        #: Notified whenever a cell finishes (and possibly requeues) or the node stops.
        self.queue_changed = threading.Condition()
        self.stop = threading.Event()
        self.records = []
        self.lock = threading.Lock()
        self.state = {"worker": worker, "mode": mode, "started_at": time.time()}

    # ---- status ----------------------------------------------------------

    def status(self, name=None, **extra):
        with self.lock:
            if name:
                self.state["state"] = name
            self.state.update(extra, updated_at=time.time(),
                              units=[u.describe() for u in self.units],
                              completed=[r["cell"] for r in self.records if r["state"] == self.terminal],
                              failed=[r["cell"] for r in self.records if r["state"] != self.terminal],
                              requeued=[r["cell"] for r in self.records
                                        if (r.get("requeue") or {}).get("requeued")])
            atomic_json(self.status_path, self.state)

    def slot_state(self, unit, slot, state, **extra):
        unit.slots[slot.name] = {"state": state, "gpu": slot.gpu, **extra}
        self.status()

    # ---- units -----------------------------------------------------------

    def run(self) -> int:
        threads = [threading.Thread(target=self.run_unit, args=(u,), name=f"unit{u.unit.index}",
                                    daemon=True) for u in self.units]
        self.status("units_starting")
        for thread in threads:
            thread.start()
        abandoned = []
        while any(t.is_alive() for t in threads):
            for thread in threads:
                thread.join(.5)
            if self.stop.is_set():
                with self.queue_changed:
                    self.queue_changed.notify_all()
                # Units still starting have no cell to unwind; stop them now.
                for unit in self.units:
                    if unit.state in ("pending", "starting_services", "startup_checks"):
                        unit.fail("node stopping")
                        stop_processes(unit.processes)
            alive = [u for t, u in zip(threads, self.units) if t.is_alive()]
            if alive and all(u.failed_at is not None and time.monotonic() - u.failed_at > self.unit_stop_grace
                             for u in alive):
                # Only failed units are left and they overran their grace: a unit
                # that lost a service must not keep the whole node alive.
                abandoned = [u.unit.index for u in alive]
                break
        failed_units = [u.unit.index for u in self.units if u.failure]
        bad = [r for r in self.records if r["state"] != self.terminal]
        name = "node_complete" if not bad and not failed_units else "node_finished_with_failures"
        self.status(name, failed_units=failed_units, abandoned_units=abandoned)
        return 0 if not bad and not failed_units else 1

    def run_unit(self, unit):
        try:
            unit.state = "starting_services"
            self.status()
            services = self.hooks.start_services(unit)

            def on_failure():
                unit.fail(f"service failure: {unit.watch.failure}")
                stop_processes(unit.processes)

            unit.watch = ServiceWatch(services, on_failure)
            unit.watch.start()
            unit.watch.wait_ready(timeout=self.hooks.service_startup)
            unit.state = "startup_checks"
            self.status()
            self.hooks.startup_checks(unit)
            if unit.down.is_set():
                raise UnitDown(unit.failure)
            unit.state = "serving"
            self.status("units_serving")
            slots = [threading.Thread(target=self.run_slot, args=(unit, slot), daemon=True,
                                      name=f"unit{unit.unit.index}-slot{slot.name}")
                     for slot in unit.unit.slots]
            for thread in slots:
                thread.start()
            for thread in slots:
                thread.join()
        except Exception as exc:
            unit.fail(f"{type(exc).__name__}: {exc}")
        finally:
            if unit.watch:
                unit.watch.close()
            # By session, with SIGKILL escalation and a verified-empty check: the
            # group leader exiting is no proof that its TP workers did.
            try:
                unit.teardown = stop_sessions(unit.processes, since=self.state["started_at"])
            except Exception as exc:  # Never let teardown bookkeeping keep the unit alive.
                unit.teardown = {"error": f"{type(exc).__name__}: {exc}"}
                stop_processes(unit.processes)
            unit.state = "failed" if unit.failure else "stopped"
            self.status()

    # ---- slots -----------------------------------------------------------

    def run_slot(self, unit, slot):
        binding = layout.slot_binding(unit.unit, slot)
        name = f"{self.worker}-unit{unit.unit.index}-slot{slot.name}"
        while not (self.stop.is_set() or unit.down.is_set()):
            # Keep an entire unchanged cell deadline plus cleanup before platform expiry.
            if time.monotonic() + self.cell_timeout + 90 >= self.node_stop_at:
                self.slot_state(unit, slot, "time_window_exhausted")
                return
            try:
                self.hooks.boundary_check(unit)  # Before claim/spawn/trial budget.
            except Exception as exc:
                unit.fail(f"boundary health failed in slot {slot.name}: {type(exc).__name__}: {exc}")
                stop_processes(unit.processes)
                return
            if unit.down.is_set() or self.stop.is_set():
                return
            cell = layout.claim_next(self.root, self.cells, name, binding)
            if cell is None:
                if self.wait_for_requeue(unit, slot):
                    continue
                self.slot_state(unit, slot, "drained")
                return
            record = self.run_cell(unit, slot, binding, cell, name)
            self.slot_state(unit, slot, "idle", last_cell=record["cell"], last_state=record["state"])
            if record["state"] == "cleanup_failed":
                # Never claim another cell on this unit with unconfirmed ownership cleanup.
                unit.fail(f"cleanup failed for {record['cell']}: {record.get('cleanup_error')}")
                stop_processes(unit.processes)
                return

    def wait_for_requeue(self, unit, slot) -> bool:
        """Nothing to claim: wait (True) while a requeue can still arrive, else drain.

        A cell running elsewhere may come back requeued after an infrastructure
        failure. A slot keeps polling for it only while its own unit is kept up
        anyway by its sibling slot's cell, so waiting never holds a unit's GPUs
        on its own; the window check at the top of the slot loop still applies.
        """
        while not (self.stop.is_set() or unit.down.is_set()):
            if layout.claimable(self.root, self.cells):
                return True
            flying = layout.in_flight(self.root, self.cells)
            sibling = any(state.get("state") == "cell_running"
                          for name, state in unit.slots.items() if name != slot.name)
            if not (flying and sibling):
                return False
            if time.monotonic() + self.cell_timeout + 90 >= self.node_stop_at:
                return False
            if unit.slots[slot.name].get("state") != "waiting_for_requeue":
                self.slot_state(unit, slot, "waiting_for_requeue", in_flight=flying)
            with self.queue_changed:
                self.queue_changed.wait(self.requeue_poll)
        return False

    def run_cell(self, unit, slot, binding, cell, name):
        claim = self.root / "queue/claims" / cell.name
        record = {"cell": cell.name, "worker": name, "unit": unit.unit.index, "slot": slot.name,
                  "slot_binding": binding, "started_at": time.time()}
        process = tree = None
        try:
            case = self.hooks.verify_cell(cell)
            bound = layout.bind_case(case, binding)
            bound_path = cell / "slot-case.json"
            layout.write_once(bound_path, bound)
            record.update(bound_case=str(bound_path), bound_case_sha256=digest(bound_path))
            self.slot_state(unit, slot, "cell_running", cell=cell.name)
            process = self.hooks.spawn_driver(unit, slot, bound, bound_path, cell)
            tree = CellProcessTree(process)
            deadline = time.monotonic() + self.cell_timeout
            ledger = self.hooks.smoke_ledger(cell, bound) if self.mode == "native_smoke" else None
            smoke_stop = None
            if ledger is not None:
                record["smoke_ledger"] = str(ledger)
            while (code := tree.poll()) is None:
                if unit.down.is_set():
                    raise UnitDown(unit.failure)
                if self.stop.is_set():
                    raise RuntimeError("node stopping; cell interrupted and preserved")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"driver exceeded {self.cell_timeout}s")
                if ledger is not None and (point := smoke_stop_point(read_ledger(ledger))):
                    # The smoke trial is terminal: end the driver whatever the solver does next.
                    smoke_stop = {"reason": SMOKE_STOP_REASON, "ledger_index": point[0],
                                  "trigger": smoke_trial(point[1]), "observed_at": time.time()}
                    record["smoke_stop"] = smoke_stop
                    record["process_cleanup"] = tree.cleanup()
                    code = process.returncode
                    smoke_stop.update(smoke_stop_outcome(read_ledger(ledger), point[0]),
                                      stopped_at=time.time())
                    break
                time.sleep(.5)
            if unit.down.is_set():
                # A service died while this cell ran: its evidence is not clean.
                raise UnitDown(unit.failure)
            record.update(driver_exit=code, summary=self.hooks.accept(cell, code, smoke_stop),
                          state=self.terminal, finished_at=time.time())
        except UnitDown as exc:
            record.update(state="infrastructure", finished_at=time.time(),
                          error=f"unit {unit.unit.index} down: {exc}")
        except Exception as exc:  # The queue continues; the cell keeps its claim and state.
            record.update(state="failed", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())
        finally:
            if process is not None and "process_cleanup" not in record:
                try:
                    record["process_cleanup"] = (tree or CellProcessTree(process)).cleanup()
                except Exception as exc:
                    record.update(state="cleanup_failed", cleanup_error=f"{type(exc).__name__}: {exc}")
        atomic_json(claim / "result.json", record)
        atomic_json(cell / "queue-result.json", record)
        if record["state"] == "infrastructure":
            record["requeue"] = self.requeue(cell, record)
        with self.lock:
            self.records.append(record)
        with self.queue_changed:
            self.queue_changed.notify_all()
        return record

    def requeue(self, cell, record):
        """Return a unit-interrupted cell to the queue, fresh, once its tree is gone."""
        if not (record.get("process_cleanup") or {"verified_no_live_work": True}).get("verified_no_live_work"):
            return {"requeued": False, "reason": "cell process cleanup was not verified; claim kept"}
        try:
            return self.hooks.requeue(self.root, cell, record)
        except Exception as exc:  # The claim stays; the record says why.
            return {"requeued": False, "reason": f"{type(exc).__name__}: {exc}"}


# ---- production hooks --------------------------------------------------------


def accept_full(result: dict, code: int) -> dict:
    """A cell is complete only at `full_complete` with all 50 held-out rows accounted."""
    blocked = (result or {}).get("blocked")
    if blocked:
        raise RuntimeError(json.dumps({"cell_blocked": blocked}, sort_keys=True))
    if code != 0:
        raise RuntimeError(f"driver exited {code} with status {(result or {}).get('status')!r}")
    if (result or {}).get("status") != "full_complete":
        raise RuntimeError(f"driver status {(result or {}).get('status')!r} != 'full_complete'")
    heldout = (result or {}).get("heldout") or {}
    if not heldout.get("performed") or not heldout.get("evaluate_invoked"):
        raise RuntimeError(f"held-out evaluation was not performed: {heldout}")
    if heldout.get("timed_out"):
        raise RuntimeError(f"held-out sweep hit its enforced deadline: {heldout}")
    accounting = heldout.get("accounting") or {}
    if not accounting.get("identity_verified"):
        raise RuntimeError(f"held-out report identity was not verified: {accounting}")
    if accounting.get("rows_accounted") != len(HELDOUT_SEEDS):
        raise RuntimeError(f"held-out accounting covers {accounting.get('rows_accounted')} rows, "
                           f"not {len(HELDOUT_SEEDS)}")
    manifest = (result or {}).get("heldout_manifest") or {}
    if manifest.get("row_count") != len(HELDOUT_SEEDS) or manifest.get("seeds") != HELDOUT_SEEDS:
        raise RuntimeError(f"frozen held-out manifest is not the 1-50 sweep: {manifest}")
    return {"status": "full_complete", "heldout": accounting,
            "selected_bundle": manifest.get("selected_bundle"),
            "heldout_manifest_sha256": manifest.get("sha256"),
            "development_seconds": (result or {}).get("development_seconds"),
            "total_seconds": (result or {}).get("total_seconds")}


def accept_smoke(state: dict, code: int, stop: dict | None = None) -> dict:
    """Native smoke ends after exactly one charged attempt.

    Normally the supervisor ends it (`stop`, see `Node.run_cell`): the first
    charged ledger row reached a terminal status, the driver tree was cleaned
    up, and the smoke passes when that trial produced evidence. A driver that
    paused by itself at `native_smoke_checkpoint` with exit 0 is accepted too.
    """
    if stop is not None:
        attempt = stop.get("smoke_attempt") or {}
        if stop.get("reason") != SMOKE_STOP_REASON or attempt.get("status") not in SMOKE_PASS_STATUSES:
            raise RuntimeError(f"native smoke attempt ended {attempt.get('status')!r} "
                               f"(stop {stop.get('reason')!r}): {attempt}")
        return {"status": "native_smoke_passed", "stop_reason": SMOKE_STOP_REASON,
                "smoke_attempt": attempt, "after_stop": stop.get("after_stop", []),
                "rejected_admissions": stop.get("rejected_admissions"),
                "driver_exit": code, "driver_status": (state or {}).get("status"),
                "native_session": (state or {}).get("native_session")}
    if code != 0 or (state or {}).get("status") != "native_smoke_checkpoint":
        raise RuntimeError(f"native smoke ended with exit {code}, status {(state or {}).get('status')!r}, "
                           f"blocked {(state or {}).get('blocked')}")
    return {"status": state["status"], "smoke_attempt": state.get("smoke_attempt"),
            "elapsed_seconds": state.get("elapsed_seconds"), "native_session": state.get("native_session")}


class RealHooks:
    """Services, health checks and drivers for one node, from the frozen launch dir."""

    def __init__(self, *, root, launch, study, receipt, reference_case, model_reference, util,
                 invocation, deadlines, campaign, ownership, runtime_env):
        self.root, self.launch, self.study, self.receipt = root, launch, study, receipt
        self.reference_case, self.model_reference, self.util = reference_case, model_reference, util
        self.invocation, self.deadlines = invocation, deadlines
        self.campaign, self.ownership, self.runtime_env = campaign, ownership, runtime_env
        self.repo = Path(reference_case["sim"])
        self.py = str(self.repo / ".venv-libero/bin/python3")
        self.service_startup = deadlines.SERVICE_STARTUP
        self.manifest = json.loads((launch / "launch-manifest.json").read_text())
        #: Every process this node started (services, compat, drivers): the
        #: final sweep stops each one's session before the supervisor exits.
        self.spawned = []

    # environment

    def env_for(self, case):
        env = self.runtime_env(case, Path(case["sim"]), clean_env())
        grasp = GRASPNET_ROOT / GRASPNET_CHECKPOINT_REL
        env.update(HF_HUB_OFFLINE="1", SAM3_CHECKPOINT_PATH=str(SAM3_CHECKPOINT),
                   CONTACT_GRASPNET_ROOT=str(GRASPNET_ROOT), CONTACT_GRASPNET_CHECKPOINT_DIR=str(grasp))
        return env

    def unit_case(self, unit):
        """The representative case bound to slot a: used by services and compat."""
        return layout.bind_case(self.reference_case, layout.slot_binding(unit.unit, unit.unit.slots[0]))

    def unit_dir(self, unit):
        folder = self.invocation / f"unit{unit.unit.index}"
        (folder / "logs").mkdir(parents=True, exist_ok=True)
        return folder

    def spawn(self, unit, command, log, env, cwd=None, *, track=True):
        with Path(log).open("x") as output:
            process = subprocess.Popen(command, cwd=cwd or self.repo, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        self.spawned.append(process)
        if track:
            unit.processes.append(process)
        return process

    # services

    def start_services(self, unit):
        u, folder = unit.unit, self.unit_dir(unit)
        logs = folder / "logs"
        cache = isolated_jit_env(f"unit{u.index}")
        model = layout.model_server(self.model_reference, u, self.util)
        atomic_json(folder / "model-server.json", model)
        atomic_json(folder / "jit-cache.json", cache)
        process = self.spawn(unit, model["argv"], logs / "model.log", clean_env() | model["env"] | cache)
        services = [Service("qwen", process, logs / "model.log", u.endpoint + "/health")]
        base_env = self.env_for(self.unit_case(unit))
        # PyRoKi's jaxlib has no CUDA; it gets slot a's card only for parity of env.
        for name, port_key, card, extra in (("sam3", "sam3", u.card("sam3"), ["--device", "cuda"]),
                                            ("contact_graspnet", "graspnet", u.card("graspnet"), []),
                                            ("pyroki", "pyroki", u.card("a"), [])):
            env = base_env | {"CUDA_VISIBLE_DEVICES": str(card), "MUJOCO_EGL_DEVICE_ID": str(card)}
            process = self.spawn(unit, [self.py, "-u", f"cap/serving/launch_{name}_server.py", "--host",
                                        "127.0.0.1", "--port", str(u.ports[port_key]), *extra],
                                 logs / f"{name}.log", env)
            services.append(Service(name, process, logs / f"{name}.log", u.url(port_key) + "/health",
                                    allow_404=True))
        return services

    def sam3_health(self, unit):
        from sam3_service_health import check_inference
        unit.watch.check()
        folder = self.unit_dir(unit)
        with unit.lock:
            unit.health_count = getattr(unit, "health_count", 0) + 1
            index = unit.health_count - 1
        sam3 = next(s for s in unit.watch.services if s.name == "sam3")
        result = check_inference(unit.unit.url("sam3"), self.launch / SAM3_HEALTH_IMAGE,
                                 self.manifest["files"][SAM3_HEALTH_IMAGE],
                                 folder / f"sam3-inference-health-{index:02d}",
                                 service_pid=sam3.process.pid, expected_cuda=unit.unit.card("sam3"))
        atomic_json(folder / "latest-sam3-inference-health.json", result)
        return result

    def startup_checks(self, unit):
        folder, case = self.unit_dir(unit), self.unit_case(unit)
        with urllib.request.urlopen(unit.unit.endpoint + "/v1/models", timeout=15) as response:
            models = json.load(response)
        atomic_json(folder / "served-models.json", models)
        if [m["id"] for m in models["data"]] != [case["model"]]:
            raise RuntimeError(f"served models {models['data']} != [{case['model']}]")
        if models["data"][0].get("max_model_len") != case["context_tokens"]:
            raise RuntimeError(f"served max_model_len differs: {models['data'][0]}")
        unit.initial_health = [self.sam3_health(unit)]  # Before native compat and ANY claim.
        case_path = folder / "unit-case.json"
        atomic_json(case_path, case)
        history, compat = [], None
        for attempt in range(1, COMPAT_ATTEMPTS + 1):
            out = folder / ("native-compat" if attempt == 1 else f"native-compat-retry{attempt}")
            process = self.spawn(unit, python_entry(self.py, self.launch / "qwen-native-compat.py",
                                                    "--case", case_path, "--output", out),
                                 folder / "logs" / f"{out.name}.log", self.env_for(case))
            code = process.wait(timeout=self.deadlines.NATIVE_COMPAT)
            unit.processes.remove(process)
            unit.watch.check()
            try:
                summary = json.loads((out / "summary.json").read_text())
            except (OSError, json.JSONDecodeError):
                summary = {"passed": False, "exit_code": code, "reason": "no summary written"}
            history.append({"attempt": attempt, "exit_code": code, "passed": bool(summary.get("passed")),
                            "folder": str(out)})
            if code == 0 and summary.get("passed") and summary.get("models") == [case["model"]]:
                compat = summary
                break
        atomic_json(folder / "native-compat-history.json", history)
        if compat is None:
            raise RuntimeError(f"native image/Agent/compaction fixture failed {COMPAT_ATTEMPTS}x: {history}")

    def boundary_check(self, unit):
        with unit.lock:
            reused = unit.initial_health.pop() if unit.initial_health else None
        return reused or self.sam3_health(unit)

    # cells

    def verify_cell(self, cell):
        case_path = cell / "case.json"
        case = json.loads(case_path.read_text())
        if digest(case_path) != self.receipt["cells"][case["id"]]["case_sha256"]:
            raise RuntimeError(f"{case['id']}: case.json changed since prepare")
        differing = layout.same_runtime_family(self.reference_case, case)
        if differing:
            raise RuntimeError(f"cell {case['id']} differs from the unit's service family: {differing}")
        check_case(case, self.study)
        check_supplement_entry(cell, case)
        repo = Path(case["sim"])
        self.campaign.verify_runtime(case, repo)
        verify_staged_runtime(repo, self.receipt, case["id"], self.study["required_runtime"])
        self.ownership.verify(case, repo)
        control = Path(case["control"])
        leftover = [n for n in RECOVERY_CHECKPOINTS if (control / n).exists()]
        if leftover:
            raise RuntimeError(f"cell carries recovery checkpoints {leftover}; recover it manually")
        return case

    def driver_args(self, bound_path, supplement=False):
        if self.study["mode"] == "native_smoke":
            return ["--case", bound_path, "--deadline-development", self.study["smoke_deadline_seconds"],
                    "--deadline-heldout", 60, "--native-smoke"]
        if supplement:
            return ["--case", bound_path, "--heldout-supplement"]
        # No --deadline-heldout: the driver derives it from the seeds still to run
        # (`full_deadlines.heldout_deadline`), which `cell_timeout` is sized from.
        return ["--case", bound_path, "--deadline-development", self.deadlines.DEVELOPMENT]

    def spawn_driver(self, unit, slot, bound, bound_path, cell):
        env = self.env_for(bound)
        command = python_entry(self.py, self.root / self.study["driver"],
                               *self.driver_args(bound_path, (cell / SUPPLEMENT_MARKER).is_file()))
        log = self.unit_dir(unit) / "logs" / f"cell-{cell.name}.log"
        return self.spawn(unit, command, log, env, cwd=bound["sim"], track=False)

    def smoke_ledger(self, cell, bound):
        """The driver's development ledger for this cell (its `task_dir`)."""
        return (Path(bound["sim"]) / "outputs/libero_fix_loop" / bound["suite"] / bound["task"]
                / "development_state.json")

    def requeue(self, root, cell, record):
        return layout.infra_requeue(root, cell, record)

    def accept(self, cell, code, smoke_stop=None):
        if self.study["mode"] == "native_smoke":
            state = read_ledger(cell / "native_smoke_state.json")
            if state is None and smoke_stop is None:
                raise RuntimeError(f"native smoke wrote no readable native_smoke_state.json (exit {code})")
            return accept_smoke(state, code, smoke_stop)
        # The case's control dir: the cell itself, or a supplemented source cell.
        control = Path(json.loads((cell / "case.json").read_text())["control"])
        return accept_full(json.loads((control / "campaign_state.json").read_text()), code)


def check_case(case, study):
    """Generic study gates plus the study's declared expectations."""
    problems = []
    if sorted(int(s) for s in case["dev_seeds"]) != DEV_SEEDS:
        problems.append(f"dev seeds {case['dev_seeds']}")
    if list(case.get("heldout_seeds", HELDOUT_SEEDS)) != HELDOUT_SEEDS:
        problems.append("held-out seeds are not 1-50")
    if case.get("model_provider") != "local-vllm" or "api_key_helper" in case:
        problems.append("not a keyless local-vllm cell")
    models = {case.get(k) for k in ("model", "model_tag", "expected_served_model")}
    if len(models) != 1:
        problems.append(f"model fields disagree: {models}")
    if "judge" in case:
        problems.append("unit layout studies run no judge")
    problems += [f"case[{k!r}] is {case.get(k)!r}, expected {v!r}"
                 for k, v in study.get("expect", {}).items() if case.get(k) != v]
    if problems:
        raise RuntimeError(f"{case.get('id')}: " + "; ".join(problems))


def check_supplement_entry(cell: Path, case: dict) -> dict | None:
    """A supplement entry must name, and match, the source cell it continues."""
    marker_path = Path(cell) / SUPPLEMENT_MARKER
    if not marker_path.is_file():
        if Path(case["control"]).resolve() != Path(cell).resolve():
            raise RuntimeError(f"{case['id']}: case control {case['control']} is not its queue cell")
        return None
    marker = json.loads(marker_path.read_text())
    problems = []
    if marker.get("cell") != case["id"]:
        problems.append(f"marker names {marker.get('cell')!r}")
    if Path(marker.get("source_control", "")).resolve() != Path(case["control"]).resolve():
        problems.append(f"marker source {marker.get('source_control')!r} != case control")
    if Path(case["control"]).resolve() == Path(cell).resolve():
        problems.append("a supplement entry must not be its own source cell")
    if marker.get("source_case_sha256") != digest(Path(cell) / "case.json"):
        problems.append("case.json is not the source cell's staged case")
    if problems:
        raise RuntimeError(f"{case['id']}: invalid held-out supplement entry: {problems}")
    return marker


def verify_staged_runtime(repo: Path, receipt: dict, cell_id: str, required) -> dict:
    """Every required runtime source is present AND pinned by the receipt."""
    pinned = (receipt.get("runtime_sha256") or {}).get(cell_id)
    if not pinned:
        raise RuntimeError(f"prepare-receipt.json pins no runtime for {cell_id!r}")
    measured, problems = {}, []
    for relative in required:
        path = Path(repo) / relative
        if not path.is_file():
            problems.append(f"missing from the staged overlay: {relative}")
            continue
        measured[relative] = digest(path)
        if pinned.get(relative) != measured[relative]:
            problems.append(f"{relative}: staged {measured[relative]} != pinned {pinned.get(relative)}")
    if problems:
        raise RuntimeError(json.dumps({"staged_runtime_failed": problems}, indent=2))
    return measured


def verify_launch(launch: Path, root: Path) -> tuple[dict, dict, dict]:
    manifest = json.loads((launch / "launch-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if digest(launch / name) != expected:
            raise RuntimeError(f"launch file changed: {name}")
    study = json.loads((launch / "study.json").read_text())
    receipt = json.loads((root / "prepare-receipt.json").read_text())
    if receipt.get("launch_manifest_sha256") != digest(launch / "launch-manifest.json"):
        raise RuntimeError("launch manifest changed since prepare")
    for name, expected in receipt["frozen_support"].items():
        if digest(root / "support" / name) != expected:
            raise RuntimeError(f"frozen support changed: {name}")
    for name in study["required_support"]:
        if name not in receipt["frozen_support"]:
            raise RuntimeError(f"support file not frozen: {name}")
    return manifest, study, receipt


#: The window the node keeps back from claims before platform expiry (`main`).
CLAIM_STOP_RESERVE = 15 * 60


def minimum_window_minutes(cell_timeout: int) -> int:
    """Smallest --node-window-minutes in which a slot can still claim one whole cell."""
    return -(-(cell_timeout + 90 + STARTUP_ALLOWANCE + CLAIM_STOP_RESERVE) // 60)


def node_cell_timeout(cases, deadlines):
    """One node reserves enough time for its slowest assigned case (mixed-N P6).

    Old studies have no per-case sizing function and retain their original bound.
    """
    if hasattr(deadlines, "cell_timeout"):
        return max(deadlines.cell_timeout(case) for case in cases)
    return deadlines.DEVELOPMENT + deadlines.HELDOUT + deadlines.FINALIZE_MARGIN


def queue_cells(root: Path, study: dict) -> list[Path]:
    found = {p.parent.name: p.parent for p in Path(root).glob("*/case.json")}
    if set(found) != set(study["cells"]):
        raise RuntimeError(f"queue cells {sorted(found)} differ from the study's {study['cells']}")
    return [found[name] for name in study["cells"]]


def environment_preflight(py, repo, env):
    # No simulator/environment construction, reset, trial, model startup or inference.
    check = ("import os,getpass,json,torch,mujoco,jax; "
             "assert os.access('.',os.R_OK); assert getpass.getuser(); "
             "assert os.environ.get('USER') and os.environ.get('LOGNAME'); "
             "assert not any(k in os.environ for k in ('RANK','WORLD_SIZE','MASTER_ADDR')); "
             "c=mujoco.GLContext(96,96); c.make_current(); c.free(); "
             "print(json.dumps(dict(environment_preflight=True,torch=torch.__version__,"
             "jax_backend=jax.default_backend(),cuda=os.environ['CUDA_VISIBLE_DEVICES'],"
             "egl=os.environ['MUJOCO_EGL_DEVICE_ID'])))")
    out = subprocess.run([py, "-c", check], cwd=repo, env=env, check=True, timeout=120,
                         capture_output=True, text=True)
    subprocess.run([py, "cap/serving/launch_pyroki_server.py", "--help"], cwd=repo,
                   env=env, check=True, timeout=120, stdout=subprocess.DEVNULL)
    return json.loads(out.stdout.strip().splitlines()[-1])


def plan(args, reference) -> dict:
    found = layout.units(args.gpus, ports=args.ports, stride=args.port_stride, placement=args.placement)
    return {"gpus": layout.parse_gpus(args.gpus), "util": args.util,
            "units": [{**u.describe(),
                       "model_server": layout.model_server(reference, u, args.util),
                       "slots": [layout.slot_binding(u, s) for s in u.slots]} for u in found]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True,
                        help="study result root: <cell>/case.json, support/, launch/, prepare-receipt.json")
    parser.add_argument("--gpus", required=True, help="physical GPU indices, 4 or 8")
    parser.add_argument("--ports", default=None,
                        help="unit-0 ports, e.g. model=8121,sam3=8114,graspnet=8115,pyroki=8116")
    parser.add_argument("--port-stride", type=int, default=layout.UNIT_PORT_STRIDE)
    parser.add_argument("--placement", default=None,
                        help="unit-relative cards, e.g. sam3=0,graspnet=1,a=2,b=3")
    parser.add_argument("--util", type=float, default=None,
                        help=f"gpu-memory-utilization per unit (default: launch study.json, else {layout.DEFAULT_UTIL})")
    parser.add_argument("--node-id", type=int, default=0)
    parser.add_argument("--node-count", type=int, default=1)
    parser.add_argument("--node-window-minutes", type=int, default=80 * 60)
    parser.add_argument("--plan", action="store_true", help="print units, ports and argv; start nothing")
    parser.add_argument("--preflight", action="store_true",
                        help="run every launch/cell/identity/environment check, then exit before "
                             "registering the worker or starting any service (dlc-preflight gate)")
    args = parser.parse_args(argv)
    launch = HERE
    reference = json.loads((launch / "reference/model-server.json").read_text())
    study = json.loads((launch / "study.json").read_text()) if (launch / "study.json").is_file() else {}
    args.ports = (layout.parse_mapping(args.ports, layout.DEFAULT_PORTS) if args.ports
                  else study.get("ports", layout.DEFAULT_PORTS))
    args.placement = (layout.parse_mapping(args.placement, layout.DEFAULT_PLACEMENT) if args.placement
                      else study.get("placement", layout.DEFAULT_PLACEMENT))
    args.util = args.util if args.util is not None else study.get("util", layout.DEFAULT_UTIL)
    if args.plan:
        print(json.dumps(plan(args, reference), indent=2))
        return 0
    node_stop_at = time.monotonic() + args.node_window_minutes * 60 - CLAIM_STOP_RESERVE
    root = args.root.resolve()
    if launch != root / "launch":
        raise RuntimeError(f"run the supervisor frozen at {root / 'launch'}, not {launch}")
    manifest, study, receipt = verify_launch(launch, root)
    cells = queue_cells(root, study)
    reference_case = json.loads((cells[0] / "case.json").read_text())
    repo = Path(reference_case["sim"])
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign
    from native_world_protocol import runtime_env
    sys.path.insert(0, str(root / "support"))
    import full_deadlines
    import output_ownership
    if not hasattr(full_deadlines, "heldout_deadline"):
        raise RuntimeError("frozen support/full_deadlines.py predates the derived held-out deadline "
                           "(CC_TASK_5); restage with the p4 support overlay")
    for cell in cells:
        case = json.loads((cell / "case.json").read_text())
        check_case(case, study)
        if hasattr(full_deadlines, "cell_timeout"):
            problems = full_deadlines.check_case(case)
            if problems:
                raise RuntimeError(f"{case['id']}: " + "; ".join(problems))
        check_supplement_entry(cell, case)
        if case["trial_timeout"] > full_deadlines.TRIAL_TIMEOUT:
            # The driver's derived held-out bound must fit inside cell_timeout.
            raise RuntimeError(f"{case['id']}: trial_timeout {case['trial_timeout']} exceeds the "
                               f"{full_deadlines.TRIAL_TIMEOUT}s the cell allowance is sized for")
        if (cell / SUPPLEMENT_MARKER).is_file() and "heldout_supplement.py" not in receipt["frozen_support"]:
            raise RuntimeError(f"{case['id']} is a held-out supplement but support/heldout_supplement.py "
                               "is not frozen")
    campaign.local_vllm_pilot_check(reference_case)
    import yaml
    env_config = yaml.safe_load((repo / reference_case["env_config"]).read_text())["env"]["cfg"]
    assert env_config["privileged"] is False and env_config["low_level"]["privileged"] is False
    assert env_config["apis"] == ["FrankaLiberoApiReducedSkillLibraryTraced"]
    layout.check_reference(reference)
    if reference["model"] != reference_case["model"] or reference["context_tokens"] != reference_case["context_tokens"]:
        raise RuntimeError("model reference and cells disagree on model or context")
    grasp = GRASPNET_ROOT / GRASPNET_CHECKPOINT_REL
    for path in (GRASPNET_ROOT / "contact_graspnet_pytorch", GRASPNET_ROOT / "Pointnet_Pointnet2_pytorch"):
        assert path.is_dir(), str(path)
    for path in (repo / ".venv-libero/bin/python3", Path(reference_case["claude_bin"]),
                 Path(reference["argv"][0]), Path(reference["argv"][3]) / "config.json", SAM3_CHECKPOINT,
                 grasp / "model.pt", grasp.parent / "config.yaml", root / study["driver"],
                 launch / "qwen-native-compat.py"):
        assert path.is_file(), str(path)
    assert os.getuid() == 10011 and os.getgid() == 10011
    assert os.environ.get("USER") and os.environ.get("LOGNAME")
    assert not any(key in os.environ for key in RENDEZVOUS)
    units = layout.units(args.gpus, ports=args.ports, stride=args.port_stride, placement=args.placement)
    py = str(repo / ".venv-libero/bin/python3")
    preflight = {}
    for unit in units:
        case = layout.bind_case(reference_case, layout.slot_binding(unit, unit.slots[0]))
        preflight[unit.index] = environment_preflight(py, repo, runtime_env(case, repo, clean_env()))
    claim_cells = layout.node_cells(study, cells, args.node_id, args.node_count)
    cell_timeout = (study["smoke_deadline_seconds"] + 600 if study["mode"] == "native_smoke" else
                    node_cell_timeout([json.loads((c / "case.json").read_text()) for c in claim_cells],
                                      full_deadlines))
    window = node_stop_at - time.monotonic()
    if cell_timeout + 90 + STARTUP_ALLOWANCE > window:
        # Otherwise every slot reports time_window_exhausted and the node ends "cleanly" with no work.
        raise RuntimeError(f"node window leaves {window:.0f}s before the claim stop, less than one cell "
                           f"({cell_timeout}s + 90s cleanup) after {STARTUP_ALLOWANCE}s of unit startup; "
                           f"--node-window-minutes must be >= {minimum_window_minutes(cell_timeout)}")
    # CC_TASK_6: a study may pin cells to nodes (`node_cells`); otherwise every
    # node claims from the whole queue. Every cell was checked above either way.
    if args.preflight:
        # Writes nothing under the root: the real run must still find no worker registered.
        print(json.dumps({"preflight": "passed", "cells": [c.name for c in cells],
                          "node_cells": [c.name for c in claim_cells], "node_id": args.node_id,
                          "mode": study["mode"], "cell_timeout_seconds": cell_timeout,
                          "units": [u.describe() for u in units], "util": args.util,
                          "environment_preflight": preflight}, indent=2))
        return 0
    worker = layout.register_worker(root, launch, args.node_id, args.node_count,
                                    layout.parse_gpus(args.gpus))
    invocation = new_attempt(root / "queue" / worker)
    hooks = RealHooks(root=root, launch=launch, study=study, receipt=receipt,
                      reference_case=reference_case, model_reference=reference, util=args.util,
                      invocation=invocation, deadlines=full_deadlines, campaign=campaign,
                      ownership=output_ownership, runtime_env=runtime_env)
    node = Node(root=root, cells=claim_cells, units=units, hooks=hooks, worker=worker,
                status_path=invocation / "status.json", node_stop_at=node_stop_at,
                cell_timeout=cell_timeout, mode=study["mode"])
    node.state.update(host=os.uname().nodename, supervisor_pid=os.getpid(), attempt_dir=str(invocation),
                      gpus=layout.parse_gpus(args.gpus), util=args.util, ports=args.ports,
                      placement=args.placement, environment_preflight=preflight,
                      launch_manifest_sha256=digest(launch / "launch-manifest.json"),
                      dev_seeds=DEV_SEEDS, heldout_seeds=HELDOUT_SEEDS)

    def interrupted(signum, frame):
        node.stop.set()

    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    code = 1
    try:
        code = node.run()
        return code
    finally:
        node.status(exit_cleanup=final_sweep(hooks.spawned, since=node.state["started_at"]),
                    exit_code=code)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def final_sweep(spawned, *, since=None) -> dict:
    """Before the supervisor exits: no session it started and no descendant survives.

    Covers what per-unit teardown cannot reach: a cell driver's separately owned
    sessions, and anything a unit thread abandoned past UNIT_STOP_GRACE.
    """
    import psutil
    result = {"sessions": stop_sessions(spawned, grace=10, kill_wait=10, since=since)}
    children = psutil.Process().children(recursive=True)
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(children, timeout=10)
    result["descendants_killed"] = [c.pid for c in children]
    result["descendants_surviving"] = [c.pid for c in alive]
    result["verified_empty"] = result["sessions"]["verified_empty"] and not alive
    return result


def run_and_exit(argv=None):
    """Run, flush, and end the PROCESS: interpreter shutdown cannot hang the job."""
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(code or 0))


if __name__ == "__main__":
    run_and_exit()
