# SPDX-FileCopyrightText: Copyright (c) 2026 Max Fu
# SPDX-License-Identifier: MIT
#
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from aspire.sim.cap.integrations.libero.initial_states import InitialStateRegistry

os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"


@dataclass
class LiberoHandle:
    env: Any
    suite_name: str
    task_id: int
    task_language: str
    init_states: Any
    # Common-environment registry (initial_states.py). When present it is the only
    # source of initial states: test seeds 1-50 -> original slots, development
    # seeds 51-65 -> the common development states; no modulo fallback.
    initial_state_registry: InitialStateRegistry | None = None

    def reset(self, seed: int | None = None) -> tuple[Any, dict[str, Any]]:
        """Reset once, then load the seed's initial state exactly once.

        LIBERO's ``set_init_state`` restores the MuJoCo state, runs forward
        kinematics and returns fresh observations; resetting again afterwards
        would discard the selected state, so the returned observation is the
        one produced by ``set_init_state``.
        """
        if self.initial_state_registry is not None:
            if seed is None:
                # FrankaLiberoEnv's construction-time reset: no protocol state is
                # loaded; the trial's own reset(seed=N) selects the exact slot.
                self.env.seed(seed)
                return self.env.reset(), {"init_state_partition": None}
            # Verify before touching the env: a bad registry or seed fails the reset.
            state, info = self.initial_state_registry.state_for(seed)
            self.env.seed(seed)
            self.env.reset()
            return self.env.set_init_state(state), info
        self.env.seed(seed)
        obs = self.env.reset()
        info: dict[str, Any] = {}
        if self.init_states is not None and len(self.init_states) > 0:
            idx = init_state_index(seed, len(self.init_states))
            obs = self.env.set_init_state(self.init_states[idx])
            info["init_state_index"] = idx
        return obs, info

    def step(self, action: list[float]) -> tuple[Any, float, bool, dict[str, Any]]:
        obs, reward, done, info = self.env.step(action)
        return obs, float(reward), bool(done), info


def init_state_index(seed: int | None, num_init_states: int) -> int:
    """Legacy (no registry) seed -> LIBERO initial-state index: trial seed N
    (1-based) uses ``(N - 1) % num_init_states``; ``seed=None`` uses state 0.
    Formal common-protocol runs use the registry and never reach this."""
    if num_init_states <= 0:
        raise ValueError("num_init_states must be positive")
    if seed is None:
        return 0
    return (int(seed) - 1) % num_init_states


def _extract_language_from_bddl(bddl_path: str) -> str | None:
    try:
        with open(bddl_path, "r") as f:
            content = f.read()
        match = re.search(r"\(:language\s+(.*?)\)", content, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    except Exception as e:
        print(f"Warning: Could not extract language from {bddl_path}: {e}")
    return None


def load_libero_task(
    suite_name: str,
    task_id: int,
    cam_w: int = 128,
    cam_h: int = 128,
    controller: str = "OSC_POSE",
    horizon: int = 1000,
    control_freq: int = 20,
    camera_depths: bool = True,
) -> LiberoHandle:
    """Load a LIBERO task using OffScreenRenderEnv.

    Reference: https://github.com/Lifelong-Robot-Learning/LIBERO
    """
    # Prefer vendored third_party/LIBERO if present, then fall back to installed package
    import os
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    vendor_root = os.path.normpath(os.path.join(here, "..", "..", "third_party", "LIBERO-PRO"))
    if os.path.isdir(vendor_root) and vendor_root not in sys.path:
        sys.path.append(vendor_root)
    try:
        from libero import benchmark  # type: ignore[import-not-found]
        from libero.envs import OffScreenRenderEnv  # type: ignore[import-not-found]
        from libero.utils import get_libero_path  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover - optional dependency
        raise ModuleNotFoundError(
            "LIBERO not available; add submodule or run `uv sync --extra libero`."
        ) from e
    import os

    # setting help=True will print the available benchmarks
    benchmark_dict = benchmark.get_benchmark_dict(help=False)
    task_suite = benchmark_dict[suite_name]()
    task = task_suite.get_task(task_id)

    bddl_file_path = os.path.join(
        get_libero_path("bddl_files"), task.problem_folder, task.bddl_file
    )

    if not os.path.exists(bddl_file_path):
        # Fallback: try to locate BDDL files relative to this file
        # This handles cases where get_libero_path returns incorrect relative paths
        here = os.path.dirname(os.path.abspath(__file__))
        # Path: cap/integrations/libero/../../third_party/LIBERO-PRO/libero/libero/bddl_files
        fallback_bddl_root = os.path.abspath(
            os.path.join(here, "..", "..", "third_party", "LIBERO-PRO", "libero", "libero", "bddl_files")
        )
        fallback_path = os.path.join(fallback_bddl_root, task.problem_folder, task.bddl_file)

        if os.path.exists(fallback_path):
            print(f"Found BDDL file at fallback path: {fallback_path}")
            bddl_file_path = fallback_path
        else:
            print(f"Error: BDDL file not found at {bddl_file_path} OR {fallback_path}")

    env_args = {
        "bddl_file_name": bddl_file_path,
        "camera_heights": cam_h,
        "camera_widths": cam_w,
        "controller": controller,
        "horizon": horizon,
        "control_freq": control_freq,
        "camera_depths": camera_depths,
    }
    env = OffScreenRenderEnv(**env_args)
    env.seed(0)

    # Try to extract language from BDDL file directly
    task_language = _extract_language_from_bddl(bddl_file_path)
    if not task_language:
        task_language = task.language

    suite_init_states_path = os.path.join(
        get_libero_path("init_states"), task.problem_folder, task.init_states_file
    )
    # Trusted boundary: the outer case binds the manifest into the trial env.
    # Raises when required but absent, or when it does not match this task.
    registry = InitialStateRegistry.from_environment(
        suite_name, task.name, definition_path=bddl_file_path,
        test_states_path=suite_init_states_path,
    )

    # Handle init states path resolution
    # Libero's get_task_init_states uses get_libero_path("init_states") internally
    # We need to manually load them if the default path fails
    try:
        init_states = task_suite.get_task_init_states(task_id)
        print(f"Loaded init states for task {task_id} in suite {suite_name}")
    except (FileNotFoundError, OSError):
        print(f"Warning: Could not load init states for task {task_id} in suite {suite_name}")
        # Fallback for init states
        init_states_path = os.path.join(
            get_libero_path("init_states"), task.problem_folder, task.init_states_file
        )

        if not os.path.exists(init_states_path):
             here = os.path.dirname(os.path.abspath(__file__))
             fallback_init_root = os.path.abspath(
                os.path.join(here, "..", "..", "third_party", "LIBERO-PRO", "libero", "libero", "init_files")
             )
             fallback_init_path = os.path.join(fallback_init_root, task.problem_folder, task.init_states_file)

             if os.path.exists(fallback_init_path):
                 print(f"Found init states file at fallback path: {fallback_init_path}")
                 import torch
                 init_states = torch.load(fallback_init_path)
             else:
                 print(f"Error: Init states file not found at {init_states_path} OR {fallback_init_path}")
                 raise
        else:
             # If path exists but load failed for other reasons, try loading directly with explicit path
             import torch
             init_states = torch.load(init_states_path)

    handle = LiberoHandle(
        env=env,
        suite_name=suite_name,
        task_id=task_id,
        task_language=task_language,
        init_states=init_states,
        initial_state_registry=registry,
    )
    return handle
