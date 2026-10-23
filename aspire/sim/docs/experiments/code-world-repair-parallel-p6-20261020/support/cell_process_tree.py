"""Frozen Task8 process tracker, isolated from supervisor/service import dependencies."""
import subprocess
import time

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
