"""Outer single-node scheduling guards. No experiment strategy or trial code."""
from pathlib import Path
import hashlib
import json
import os
import time

OLD_JOB = "dlczt7imjbl21jbp"
CELL_ORDER = [f"{task}_p2_r{repeat}" for repeat in (1, 2)
              for task in ("bowl", "bowldrawer", "drawer", "wine", "stove")]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def node_identity(node, nodes):
    if type(node) is not int or type(nodes) is not int or not 1 <= nodes <= 4 or not 0 <= node < nodes:
        raise ValueError("r9 requires 1–4 nodes with distinct global integer IDs 0..nodes-1")
    return f"worker-r9-node{node}"


def command(mode, node, nodes, root):
    node_identity(node, nodes)
    if mode not in {"plan", "submit"}:
        raise ValueError("unsupported operation")
    return ["/mnt/home/gewang/.local/bin/dlc-run", mode, "--name",
            f"aspire-runtime-p2-1011-r9-node{node}", "--gpus", "8", "--workdir", str(root),
            "--max-minutes", "4800", "--priority", "9", "--skip-preflight", "--", "bash",
            str(Path(root) / "launch-20261011-r9/dlc-single-node-entry.sh"),
            "--node-id", str(node), "--node-count", str(nodes)]


def job_state(text, job_id=OLD_JOB):
    """Parse only the exact ListJobs table row, never a substring/CLI claim."""
    rows = []
    for line in text.splitlines():
        fields = [x.strip() for x in line.split("|")]
        if len(fields) > 5 and fields[2] == job_id:
            rows.append(fields[4])
    if len(rows) != 1:
        raise ValueError("ListJobs did not return exactly one exact old-job row")
    return rows[0]


def no_started_work(root):
    root = Path(root)
    if (root / "queue").exists():
        raise RuntimeError("old queue exists; replacement requires independently resolving possible startup")
    if {p.parent.name for p in root.glob("*/case.json")} != set(CELL_ORDER):
        raise RuntimeError("frozen ten-cell set differs")
    for name in CELL_ORDER:
        for marker in ("campaign_state.json", "queue-result.json"):
            if (root / name / marker).exists():
                raise RuntimeError(f"cell {name} started; cannot replace as unstarted queue")


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
    proof = json.loads((root / "coordination/replacement-r9-old-job-stopped.json").read_text())
    if not (proof.get("old_job_id") == OLD_JOB and proof.get("old_status") == "Stopped"
            and proof.get("no_old_queue_or_cell_started") is True and proof.get("nodes") == nodes
            and proof.get("launch_manifest_sha256") == sha(launch / "launch-manifest.json")):
        raise RuntimeError("replacement lacks bound confirmed Stopped/no-start proof")
    registry = root / "queue/r9-worker-registry"
    registry.mkdir(parents=True, exist_ok=True)
    write_once(registry / f"{worker}.json", {"worker": worker, "global_node_id": node,
               "node_count": nodes, "host": os.uname().nodename, "pid": os.getpid(),
               "started_at": time.time(), "old_job_id": OLD_JOB,
               "launch_manifest_sha256": proof["launch_manifest_sha256"]})
    return worker
