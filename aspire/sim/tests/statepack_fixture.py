"""CPU statepack fixture: the real generator over stub task files and a stub env.

No LIBERO, MuJoCo or simulator is used. Test-state files are small torch-saved
arrays standing in for the original 50-state init files.
"""
import sys
from pathlib import Path

import numpy as np
import torch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts/libero"))
import prepare_common_initial_states as gen  # noqa: E402

DIM = 9
VERSIONS = {"packages": {"mujoco": "3.3.0", "robosuite": "1.4.0"}, "python": "3.x"}
SOURCE = {"git_head": "fixture", "files": {}}


class StubEnv:
    """Sampling resamples from numpy's global RNG, like LIBERO's placement samplers."""

    def __init__(self, definition_path, log):
        self.definition_path, self.calls, self.state = definition_path, log, None

    def seed(self, s):
        self.calls.append(("seed", s))

    def reset(self):
        self.calls.append(("reset",))
        self.state = np.random.uniform(-1.0, 1.0, DIM)

    def get_sim_state(self):
        self.calls.append(("get_sim_state",))
        return self.state.copy()

    def close(self):
        self.calls.append(("close",))

    def step(self, action):  # pragma: no cover - must never be reached
        raise AssertionError("generation stepped the env")

    def check_success(self):  # pragma: no cover
        raise AssertionError("generation queried the success predicate")


def task_files(root: Path, tasks=gen.TASKS) -> dict:
    resolved = {}
    for i, name in enumerate(tasks):
        bddl = root / "bddl_files" / f"{name}.bddl"
        init = root / "init_files" / f"{name}.pruned_init"
        bddl.parent.mkdir(parents=True, exist_ok=True)
        init.parent.mkdir(parents=True, exist_ok=True)
        if not bddl.exists():
            bddl.write_text(f"(define (problem {name}))\n")
            torch.save(np.random.default_rng(i).uniform(-1, 1, (50, DIM)), init)
        resolved[name] = {"task_index": i, "definition_path": str(bddl), "test_path": str(init)}
    return resolved


def generate(root: Path, out: Path, tasks=gen.TASKS, log=None, env_class=StubEnv, **kw):
    resolved = task_files(root, tasks)
    log = [] if log is None else log
    return gen.generate(out, tasks=tuple(tasks), resolver=lambda suite, names: resolved,
                        env_factory=lambda path: env_class(path, log),
                        versions_=lambda: VERSIONS, source=lambda: SOURCE, **kw)


def statepack(root: Path, tasks=("put_the_bowl_on_the_plate",)) -> dict:
    """Case keys binding a complete fixture statepack."""
    out = root / "runtime-statepack"
    generate(root / "suite", out, tasks)
    path = out / gen.MANIFEST
    return {"initial_state_manifest": str(path),
            "initial_state_manifest_sha256": gen.ist.file_sha256(path)}
