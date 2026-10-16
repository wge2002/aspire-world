"""Outer single-node scheduling guards for the p3 queue. No experiment strategy or trial code.

Derived from the p2 r9 `schedule_r9.py`. Kept: distinct global integer node IDs,
the exact dlc-run argv, write-once worker registration and the no-started-work
check. Dropped: r9's old-job replacement proof (`OLD_JOB`, `job_state`), which
belonged to replacing one specific unstarted p2 gang job and has no p3 analogue.
"""
from pathlib import Path
import hashlib
import json
import os
import time

NAME = "aspire-search-p3-1014"
CELL_ORDER = ("stove_p3_r1", "drawer_p3_r1", "bowl_p3_r1", "stove_p3_r2", "drawer_p3_r2")
MAX_NODES = len(CELL_ORDER)
GPUS_PER_NODE = 8
PRIORITY = 9


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def node_identity(node, nodes):
    if (type(node) is not int or type(nodes) is not int or not 1 <= nodes <= MAX_NODES
            or not 0 <= node < nodes):
        raise ValueError(f"p3 requires 1-{MAX_NODES} nodes with distinct global integer IDs 0..nodes-1")
    return f"worker-p3-node{node}"


def command(mode, node, nodes, root, max_minutes):
    node_identity(node, nodes)
    if mode != "plan":
        # This study's submit tooling generates plans only; a submission is a
        # separate, explicitly approved coordinator action.
        raise ValueError("only `plan` is implemented")
    if type(max_minutes) is not int or max_minutes <= 0:
        raise ValueError("max_minutes must be a positive integer")
    return ["/mnt/home/gewang/.local/bin/dlc-run", mode, "--name", f"{NAME}-node{node}",
            "--gpus", str(GPUS_PER_NODE), "--workdir", str(root),
            "--max-minutes", str(max_minutes), "--priority", str(PRIORITY), "--skip-preflight", "--",
            "bash", str(Path(root) / "launch/dlc-single-node-entry.sh"),
            "--node-id", str(node), "--node-count", str(nodes),
            "--node-window-minutes", str(max_minutes - 10)]


def no_started_work(root):
    root = Path(root)
    if (root / "queue").exists():
        raise RuntimeError("queue exists; a submission requires independently resolving possible startup")
    if {p.parent.name for p in root.glob("*/case.json")} != set(CELL_ORDER):
        raise RuntimeError("frozen five-cell set differs")
    for name in CELL_ORDER:
        for marker in ("campaign_state.json", "queue-result.json"):
            if (root / name / marker).exists():
                raise RuntimeError(f"cell {name} started; cannot submit it as unstarted")


def write_once(path, value):
    with Path(path).open("x") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def register_worker(root, launch, node, nodes):
    """Exclusive persistent global identity before services; reject rank collisions."""
    worker = node_identity(node, nodes)
    root, launch = Path(root), Path(launch)
    registry = root / "queue/p3-worker-registry"
    registry.mkdir(parents=True, exist_ok=True)
    write_once(registry / f"{worker}.json", {"worker": worker, "global_node_id": node,
               "node_count": nodes, "host": os.uname().nodename, "pid": os.getpid(),
               "started_at": time.time(),
               "launch_manifest_sha256": sha(launch / "launch-manifest.json")})
    return worker
