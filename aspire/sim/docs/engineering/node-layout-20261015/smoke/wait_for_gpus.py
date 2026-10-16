#!/usr/bin/env python3
"""Read-only: wait until enough DSW GPUs are free, then exit and print them.

A card is free when it hosts no compute process and uses < 2,000 MiB. Exits 0
once >= NEED cards are free on two consecutive checks (so a gap between someone
else's jobs is not mistaken for free cards), or 3 after MAX_HOURS. Starts
nothing on any GPU.

  wait_for_gpus.py NEED LOG [interval_seconds] [max_hours]
"""
import json
from pathlib import Path
import subprocess
import sys
import time

need, log = int(sys.argv[1]), Path(sys.argv[2])
interval = float(sys.argv[3]) if len(sys.argv) > 3 else 120
max_hours = float(sys.argv[4]) if len(sys.argv) > 4 else 6


def query(*fields):
    out = subprocess.run(["nvidia-smi", f"--query-{fields[0]}={fields[1]}", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return [[x.strip() for x in line.split(",")] for line in out.strip().splitlines() if line.strip()]


def free_cards():
    busy = {row[0] for row in query("compute-apps", "gpu_uuid")}
    return [int(i) for i, uuid, used in query("gpu", "index,uuid,memory.used")
            if uuid not in busy and int(used) < 2000]


streak, stop_at = 0, time.time() + max_hours * 3600
while time.time() < stop_at:
    free = free_cards()
    streak = streak + 1 if len(free) >= need else 0
    with log.open("a") as stream:
        stream.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "free": free,
                                 "streak": streak}) + "\n")
    if streak >= 2:
        print(json.dumps({"free": free}))
        sys.exit(0)
    time.sleep(interval)
print(json.dumps({"free": free_cards(), "timed_out_hours": max_hours}))
sys.exit(3)
