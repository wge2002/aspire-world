#!/usr/bin/env python3
"""Sample nvidia-smi every N seconds into a CSV until STOP_FILE exists.

  gpu_sampler.py OUT.csv STOP_FILE [interval_seconds]
"""
import csv
from pathlib import Path
import subprocess
import sys
import time

out, stop = Path(sys.argv[1]), Path(sys.argv[2])
interval = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
with out.open("x", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(["t", "gpu", "mem_mib", "util_pct"])
    while not stop.exists():
        result = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used,utilization.gpu",
                                 "--format=csv,noheader,nounits"], capture_output=True, text=True)
        now = round(time.time(), 1)
        for line in result.stdout.strip().splitlines():
            writer.writerow([now, *[x.strip() for x in line.split(",")]])
        stream.flush()
        time.sleep(interval)
