#!/usr/bin/env python3
"""Collect the Task 5 smoke results from a unit-layout attempt dir (read-only).

Per unit: request counts per service log (POST lines by route), vLLM decode
windows (Avg generation throughput by Running reqs) and KV usage peak, KV pool
size, error lines (OOM / Traceback / CUDA error), served models, SAM3 health and
native compat. Per cell: queue result state, unit/slot binding, smoke attempt and
after-stop rows. Optional `--samples CSV` (gpu_sampler.py output) adds per-card
peak MiB.

  collect_smoke_results.py ROOT [--attempt DIR] [--samples CSV] > results.json
"""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
import statistics

THROUGHPUT = re.compile(r"Avg generation throughput: ([\d.]+) tokens/s, Running: (\d+) reqs.*?"
                        r"GPU KV cache usage: ([\d.]+)%")
POST = re.compile(r'"POST ([^ ?"]+)')
ERRORS = re.compile(r"out of memory|OutOfMemory|Traceback|CUDA error|RuntimeError: CUDA", re.I)
KV_POOL = re.compile(r"GPU KV cache size: ([\d,]+) tokens")


def unit_summary(folder: Path) -> dict:
    logs = folder / "logs"
    requests, errors = {}, {}
    for log in sorted(logs.glob("*.log")):
        text = log.read_text(errors="replace")
        requests[log.stem] = dict(Counter(POST.findall(text)))
        hits = [line.strip()[:300] for line in text.splitlines() if ERRORS.search(line)]
        if hits:
            errors[log.stem] = {"count": len(hits), "first": hits[:5]}
    model = (logs / "model.log").read_text(errors="replace")
    windows = defaultdict(list)
    kv = []
    for tok, running, usage in THROUGHPUT.findall(model):
        kv.append(float(usage))
        if float(tok) > 0:
            windows[int(running)].append(float(tok))
    decode = {f"{n}_running": {"median": round(statistics.median(v), 1), "samples": len(v),
                               "min": min(v), "max": max(v)} for n, v in sorted(windows.items()) if n}
    pool = KV_POOL.findall(model)

    def load(name):
        path = folder / name
        return json.loads(path.read_text()) if path.is_file() else None

    served = load("served-models.json")
    health = sorted(folder.glob("sam3-inference-health-*"))
    return {"folder": str(folder), "requests": requests,
            "graspnet_requests": sum(requests.get("contact_graspnet", {}).values()),
            "decode_tok_s": decode, "kv_usage_peak_pct": max(kv) if kv else None, "kv_windows": len(kv),
            "kv_pool_tokens": sorted({int(p.replace(",", "")) for p in pool}),
            "errors": errors, "served_models": [m["id"] for m in (served or {}).get("data", [])],
            "sam3_health_checks": len(health), "native_compat": load("native-compat-history.json"),
            "model_server": load("model-server.json")}


def cell_summary(root: Path, name: str) -> dict:
    record_path = root / name / "queue-result.json"
    if not record_path.is_file():
        return {"cell": name, "state": "no queue-result"}
    record = json.loads(record_path.read_text())
    stop = record.get("smoke_stop") or {}
    return {"cell": name, "state": record.get("state"), "error": record.get("error"),
            "unit": record.get("unit"), "slot": record.get("slot"),
            "binding": {k: record["slot_binding"].get(k) for k in (
                "gpu", "egl_device_id", "inference_endpoint", "sam3_url", "graspnet_url", "pyroki_url",
                "model_gpus", "sam3_gpu", "graspnet_gpu")} if record.get("slot_binding") else None,
            "driver_exit": record.get("driver_exit"), "summary_status": (record.get("summary") or {}).get("status"),
            "stop_reason": stop.get("reason"), "smoke_attempt": stop.get("smoke_attempt"),
            "after_stop": stop.get("after_stop"), "rejected_admissions": stop.get("rejected_admissions"),
            "cleanup_verified": (record.get("process_cleanup") or {}).get("verified_no_live_work"),
            "minutes": round((record.get("finished_at", 0) - record.get("started_at", 0)) / 60, 1)}


def card_peaks(samples: Path) -> dict:
    peaks = defaultdict(int)
    with samples.open() as stream:
        for row in csv.DictReader(stream):
            peaks[int(row["gpu"])] = max(peaks[int(row["gpu"])], int(row["mem_mib"]))
    return dict(sorted(peaks.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--attempt", type=Path)
    parser.add_argument("--samples", type=Path)
    args = parser.parse_args()
    attempt = args.attempt or sorted((args.root / "queue/worker-node0/attempts").iterdir())[-1]
    status = json.loads((attempt / "status.json").read_text())
    study = json.loads((args.root / "launch/study.json").read_text())
    result = {"attempt": str(attempt), "node_state": status.get("state"), "failed_units": status.get("failed_units"),
              "units": {u["unit"]: {"state": u["state"], "failure": u["failure"], "ports": u["ports"],
                                    "gpus": u["gpus"], **unit_summary(attempt / f"unit{u['unit']}")}
                        for u in status.get("units", [])},
              "cells": [cell_summary(args.root, name) for name in study["cells"]]}
    if args.samples:
        result["per_card_peak_mib"] = card_peaks(args.samples)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
