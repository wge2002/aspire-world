"""Wait for a task checkpoint creation/update without polling CC progress."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import select
import time

p = argparse.ArgumentParser()
p.add_argument('path', type=Path)
p.add_argument('--seconds', type=int, default=900)
p.add_argument('--updated', action='store_true')
a = p.parse_args()
baseline = a.path.stat().st_mtime_ns if a.updated and a.path.exists() else 0
libc = ctypes.CDLL(None, use_errno=True)
fd = libc.inotify_init1(0)
if fd < 0 or libc.inotify_add_watch(fd, str(a.path.parent).encode(), 0x188) < 0:
    raise OSError(ctypes.get_errno(), 'checkpoint notification setup failed')
deadline = time.monotonic() + a.seconds
try:
    while True:
        if a.path.exists() and a.path.stat().st_size and a.path.stat().st_mtime_ns > baseline:
            print(json.dumps({'checkpoint': str(a.path), 'bytes': a.path.stat().st_size}), flush=True)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(json.dumps({'checkpoint': None, 'timeout': a.seconds}), flush=True)
            break
        if select.select([fd], [], [], remaining)[0]:
            os.read(fd, 65536)
finally:
    os.close(fd)
