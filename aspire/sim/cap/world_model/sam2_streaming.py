# SPDX-License-Identifier: Apache-2.0
"""Dependency-light adapter to SAM2_streaming's temporal camera predictor.

No static-segmentation fallback. Heavy dependencies are imported only by
from_checkpoint(). Put the selected SAM2_streaming checkout on PYTHONPATH before
starting Python; already-imported packages from another checkout are rejected.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import threading

import numpy as np


class SAM2Streaming:
    def __init__(self, predictor, *, inference_context=None):
        for name in ("load_first_frame", "add_new_mask", "track", "reset_state"):
            if not callable(getattr(predictor, name, None)):
                raise TypeError(f"SAM2_streaming camera predictor must provide {name}()")
        self._predictor = predictor
        self._inference_context = inference_context
        self._lock = threading.RLock()
        self._identities = {}
        self._shape = None
        self._active = False
        self._closed = False

    @classmethod
    def from_checkpoint(cls, *, source_root, config_file, checkpoint, device="cuda"):
        root, weights = Path(source_root).resolve(), Path(checkpoint).resolve()
        if not weights.is_file():
            raise FileNotFoundError(f"SAM2 checkpoint not found: {weights}")
        # find_spec on submodules could import the wrong package; inspect the
        # root module before importing any builder or hydra configuration.
        spec = importlib.util.find_spec("sam2")
        if spec is None or spec.origin is None:
            raise ImportError("SAM2_streaming is not importable; put its checkout on PYTHONPATH")
        location = Path(spec.origin).resolve()
        if not location.is_relative_to(root):
            raise ImportError("sam2 resolves outside the requested SAM2_streaming checkout; start a fresh process with its PYTHONPATH")
        builder = importlib.import_module("sam2.build_sam")
        if not Path(builder.__file__).resolve().is_relative_to(root):
            raise ImportError("cached sam2.build_sam belongs to a different checkout")
        build = getattr(builder, "build_sam2_camera_predictor", None)
        if build is None:
            raise ImportError("selected SAM2 package has no streaming camera predictor")
        import torch
        from contextlib import contextmanager

        @contextmanager
        def inference_context():
            with torch.inference_mode():
                if str(device).startswith("cuda"):
                    with torch.autocast("cuda", dtype=torch.bfloat16):
                        yield
                else:
                    yield

        predictor = build(config_file, str(weights), device=device)
        return cls(predictor, inference_context=inference_context)

    def _context(self):
        from contextlib import nullcontext
        return self._inference_context() if self._inference_context else nullcontext()

    def _open(self):
        if self._closed:
            raise RuntimeError("SAM2 streaming tracker is closed")

    def initialize(self, rgb, masks):
        with self._lock, self._context():
            self._open()
            self._active = False
            self._identities = {i: identity for i, identity in enumerate(masks, 1)}
            self._shape = rgb.shape[:2]
            # In this predictor reset_state/load_first_frame do not reset the
            # stream counter. Reset it explicitly before initializing frame 0.
            self._predictor.frame_idx = 0
            self._predictor.load_first_frame(np.asarray(rgb).copy())
            for obj_id, identity in self._identities.items():
                self._predictor.add_new_mask(frame_idx=0, obj_id=obj_id,
                                             mask=np.asarray(masks[identity], dtype=bool).copy())
            self._active = True

    def track(self, rgb):
        with self._lock, self._context():
            self._open()
            if not self._active or rgb.shape[:2] != self._shape:
                raise RuntimeError("tracker is unseeded or frame shape changed")
            try:
                obj_ids, logits = self._predictor.track(np.asarray(rgb).copy())
                if hasattr(logits, "detach"):
                    logits = logits.detach().float().cpu().numpy()
                logits = np.asarray(logits)
                if logits.shape != (len(obj_ids), 1, *self._shape) or not np.isfinite(logits).all():
                    raise ValueError("SAM2 output masks have unexpected shape or non-finite logits")
                if len(set(obj_ids)) != len(obj_ids) or set(obj_ids) != set(self._identities):
                    raise ValueError("SAM2 output object IDs changed; reacquisition required")
                return {self._identities[obj_id]: (logits[i, 0] > 0).copy()
                        for i, obj_id in enumerate(obj_ids)}
            except Exception:
                self._active = False
                raise

    def reset(self):
        with self._lock, self._context():
            self._open()
            state = getattr(self._predictor, "condition_state", None)
            if self._active or state:
                self._predictor.reset_state()
            self._predictor.frame_idx = 0
            self._identities = {}
            self._shape = None
            self._active = False

    def close(self):
        with self._lock:
            if not self._closed:
                try:
                    self.reset()
                finally:
                    self._predictor = None
                    self._closed = True
