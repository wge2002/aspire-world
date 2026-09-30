"""Process supervision and node-local JIT caches for native CC campaigns."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        json.dump(value, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
        temporary = Path(out.name)
    temporary.replace(path)


def new_attempt(control: Path, attempt_id: str | None = None) -> Path:
    attempt_id = attempt_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", attempt_id):
        raise ValueError("invalid attempt ID")
    directory = control / "attempts" / attempt_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "logs").mkdir()
    previous = control / "status.json"
    if previous.is_file():
        (directory / "previous-status.json").write_bytes(previous.read_bytes())
    return directory


def isolated_jit_env(case_id: str) -> dict[str, str]:
    # /tmp is the container's local filesystem. Do not use TMPDIR (which can
    # point to CPFS), change HOME, or delete another job's shared cache.
    prefix = re.sub(r"[^A-Za-z0-9_.-]", "_", case_id)[:60]
    base = Path(tempfile.mkdtemp(prefix=f"aspire-jit-{prefix}-", dir="/tmp"))
    return {
        "FLASHINFER_WORKSPACE_BASE": str(base),
        "TRITON_CACHE_DIR": str(base / "triton"),
        "TORCHINDUCTOR_CACHE_DIR": str(base / "torchinductor"),
    }


class ServiceFailure(RuntimeError):
    def __init__(self, details: dict):
        self.details = details
        super().__init__(json.dumps(details, ensure_ascii=False))


class Service:
    # These are terminal worker/engine errors, not generic ERROR lines or
    # allocator OOM retry warnings that can occur during successful loading.
    fatal = re.compile(
        r"WorkerProc hit an exception|Worker failed to start|"
        r"Engine core initialization failed|EngineCore encountered a fatal error|"
        r"RuntimeError:.*file too short|torch\.OutOfMemoryError:"
    )

    def __init__(self, name, process, log: Path, url: str, *, allow_404=False):
        self.name, self.process, self.log, self.url = name, process, log, url
        self.allow_404 = allow_404
        self.offset = 0
        self.partial = ""

    def check(self) -> None:
        if self.log.is_file():
            with self.log.open(errors="replace") as stream:
                stream.seek(self.offset)
                while text := stream.read(262144):
                    lines = (self.partial + text).splitlines(keepends=True)
                    self.partial = ""
                    if lines and not lines[-1].endswith("\n"):
                        self.partial = lines.pop()[-8192:]
                    for line in [*lines, self.partial]:
                        if self.fatal.search(line):
                            raise ServiceFailure(dict(service=self.name, reason="fatal_worker_error",
                                                      evidence=line.strip()[:2000], log=str(self.log)))
                self.offset = stream.tell()
        code = self.process.poll() if self.process is not None else None
        if code is not None:
            raise ServiceFailure(dict(service=self.name, reason="process_exited", exit_code=code,
                                      log=str(self.log)))

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(self.url, timeout=3):
                return True
        except urllib.error.HTTPError as exc:
            return self.allow_404 and exc.code == 404
        except (OSError, urllib.error.URLError):
            return False


def stop_processes(processes) -> None:
    # An exited service parent can leave failed vLLM worker children behind.
    # Signal every owned process group, even when the original parent exited.
    for process in reversed(list(processes)):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


class ServiceWatch:
    def __init__(self, services, on_failure, *, interval=2):
        self.services, self.on_failure, self.interval = services, on_failure, interval
        self.failure = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self):
        while not self.done.is_set():
            try:
                for service in self.services:
                    service.check()
            except ServiceFailure as exc:
                self.failure = exc
                self.on_failure()
                return
            self.done.wait(self.interval)

    def start(self):
        self.thread.start()

    def close(self):
        self.done.set()
        self.thread.join(timeout=10)

    def check(self):
        if self.failure:
            raise self.failure

    def wait_ready(self, *, timeout=1800, interval=5):
        deadline = time.monotonic() + timeout
        while True:
            self.check()
            pending = [s.name for s in self.services if not s.ready()]
            self.check()
            if not pending:
                return
            if time.monotonic() >= deadline:
                raise ServiceFailure(dict(reason="startup_timeout", seconds=timeout,
                                          pending_services=pending,
                                          logs={s.name: str(s.log) for s in self.services}))
            self.done.wait(interval)
