"""Count LIBERO-Pro init states per (suite, task) and report seed->index aliasing.

Read-only: loads benchmark metadata and init-state tensors; never builds an env.
Seeds map to init_states[(seed-1) % n] (cap/envs/simulators/libero.py:169).
"""
import json, sys, hashlib
import os
sys.path.append(os.environ["LIBERO_PRO_ROOT"])
from libero import benchmark

PAIRS = [tuple(a.split(":", 1)) for a in sys.argv[1:]]
bd = benchmark.get_benchmark_dict()
out = []
for suite, task in PAIRS:
    s = bd[suite]()
    names = [t.name for t in s.tasks]
    tid = names.index(task)
    st = s.get_task_init_states(tid)
    n = len(st)
    h = [hashlib.sha256(st[i].tobytes()).hexdigest()[:12] for i in range(n)]
    def idx(seed): return (seed - 1) % n
    alias = {str(sd): idx(sd) + 1 for sd in range(51, 81)}
    out.append({"suite": suite, "task": task, "n_init_states": n,
                "dev51_65_equals_heldout": sorted({idx(s) + 1 for s in range(51, 66)}),
                "unseen66_80_equals_heldout": sorted({idx(s) + 1 for s in range(66, 81)}),
                "n_unique_states": len(set(h)),
                "dev_vs_heldout_bytes_equal": all(h[idx(s)] == h[idx(s - 50)] for s in range(51, 66)) if n == 50 else None})
print(json.dumps(out, indent=1))
