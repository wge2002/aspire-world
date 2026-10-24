# SPDX-License-Identifier: Apache-2.0
"""Dependency-light control checkpoints, separate from legacy world profiles."""
import copy
from contextlib import contextmanager
from functools import wraps


class MotionInterrupted(RuntimeError):
    """An authored checkpoint declined further motion; completion is unproven."""


class ControlProgress:
    def __init__(self):
        # "gate" callbacks run BEFORE every actual motor/gripper step; progress
        # callbacks run after it. A gate refuses the step by raising.
        # "gripper_gate" callbacks (opt-in execution contract) run before a
        # gripper command changes; they receive (requested, current) fractions.
        self._callbacks = {"progress": [], "reset": [], "close": [], "gate": [], "gripper_gate": []}
        self.suspended = False

    @property
    def active(self):
        return bool(self._callbacks["progress"]) and not self.suspended

    def add(self, kind, callback):
        if not callable(callback):
            raise TypeError("checkpoint callback must be callable")
        self._callbacks[kind].append(callback)
        def remove():
            if callback in self._callbacks[kind]:
                self._callbacks[kind].remove(callback)
        return remove

    def before_step(self):
        """Pre-step boundary: any registered gate may refuse the next step.

        Reset settling (``reset_scope``) is not policy motion and is not gated.
        """
        if self.suspended:
            return
        for gate in tuple(self._callbacks["gate"]):
            gate()

    def before_gripper(self, requested, current):
        """Pre-command boundary for gripper changes; a gate refuses by raising."""
        if self.suspended:
            return
        for gate in tuple(self._callbacks["gripper_gate"]):
            gate(requested, current)

    def publish(self, robot, captured_at, frame_id):
        if self.suspended:
            return
        for callback in tuple(self._callbacks["progress"]):
            result = callback(copy.deepcopy(robot), captured_at, frame_id)
            if result is False:
                raise MotionInterrupted("policy checkpoint interrupted further motion")
            if result is not None and type(result) is not bool:
                raise TypeError("checkpoint must return None, True, or False")

    @contextmanager
    def reset_scope(self):
        self.suspended = True
        try:
            for callback in tuple(self._callbacks["reset"]):
                callback()
            yield
        finally:
            self.suspended = False

    def close(self):
        error = None
        try:
            for callback in tuple(self._callbacks["close"]):
                try:
                    callback()
                except Exception as exc:
                    error = error or exc
        finally:
            for callbacks in self._callbacks.values():
                callbacks.clear()
        if error is not None:
            raise error


def reset_scope(fn):
    @wraps(fn)
    def reset(env, *args, **kwargs):
        hooks = getattr(env, "_streaming_progress", None)
        if hooks is None:
            return fn(env, *args, **kwargs)
        with hooks.reset_scope():
            return fn(env, *args, **kwargs)
    return reset
