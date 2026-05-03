"""Centralized YOLO model loading.

Replaces the per-Counter ``_load_model`` that mutated ``os.chdir`` to coax
ultralytics into auto-downloading into our ``models/`` directory. The
registry instead pre-stages the weight file at an absolute path before
constructing ``YOLO(...)``, so concurrent Counter starts can never race on
process CWD.

This is the foundation for a future shared-instance mode (one ``YOLO``
across many Counters with serialized predict). The current implementation
still hands each Counter a fresh dedicated instance — preserving today's
``model.track(persist=True)`` flow with BoT-SORT-ReID — but the load path
is centralized and clean.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import torch
from ultralytics import YOLO

if TYPE_CHECKING:
    from ..config import Settings

log = logging.getLogger("counter.inference")

# Serializes ALL initial weight downloads. Without this, two Counters
# starting simultaneously for the same model name might both try to
# download to the same path and corrupt each other's writes.
_DOWNLOAD_LOCK = threading.Lock()


def resolve_device(requested: str) -> str:
    """Pick a device when ``requested == 'auto'``: MPS > CUDA > CPU."""
    if requested == "auto":
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    return requested


def resolve_fp16(requested: str | bool, device: str) -> bool:
    """Decide whether to run in FP16. ``requested`` can be:
       - 'auto' (default): on CUDA/MPS yes, on CPU no.
       - True/False: honour explicitly.
       FP16 isn't safe on CPU (lots of ops fall back to FP32 with overhead),
       so we never enable it there even if requested.
    """
    if device == "cpu":
        return False
    if requested == "auto":
        return device in ("cuda", "mps")
    return bool(requested)


class ModelRegistry:
    """Holds the policy for loading YOLO weights into Counter instances.

    Construct once per :class:`CameraManager`; pass into each :class:`Counter`.
    Counters call :meth:`load_dedicated` to receive a fresh ``YOLO`` instance
    they may safely mutate (predictor state, tracker state), without
    affecting other cameras.
    """

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings

    # ---- weight provisioning ------------------------------------------- #

    def ensure_weight_file(self, name: str) -> str:
        """Return an absolute path to a YOLO weight file. If it's already in
        ``models_dir``, return its path; if not, download once (under a
        process-wide lock) into ``models_dir`` and return that.

        On any download failure, returns the bare ``name`` and lets ultralytics
        try to fetch it the legacy way. We never raise from here because
        the model load itself will surface a clearer error.
        """
        if not name:
            return name
        # Treat absolute paths and URLs as-is.
        p = Path(name)
        if p.is_absolute() and p.is_file():
            return str(p)
        if name.startswith(("http://", "https://")):
            return name

        target = self.settings.models_dir / name
        if target.is_file():
            return str(target)

        with _DOWNLOAD_LOCK:
            # Re-check inside lock — another thread may have just landed it.
            if target.is_file():
                return str(target)
            try:
                # ultralytics' canonical asset downloader. dir= controls
                # destination and avoids any CWD dependence.
                from ultralytics.utils.downloads import attempt_download_asset

                got = attempt_download_asset(name, dir=str(self.settings.models_dir))
                return str(got) if got else name
            except Exception:
                log.exception("attempt_download_asset failed for %r", name)
                return name

    # ---- model loading ------------------------------------------------- #

    def load_dedicated(
        self,
        primary_name: str,
        fallback_name: str | None,
        device: str,
        warmup_imgsz: int,
        fp16: bool = False,
    ) -> tuple[YOLO, str]:
        """Load a fresh YOLO instance with a primary→fallback chain.

        Returns ``(model, name_actually_loaded)``. Performs a one-shot
        warmup predict on a zeros frame so the first real frame doesn't
        eat the JIT compile / cuda-graph cost.

        Raises ``RuntimeError`` if every name in the chain fails to load.
        """
        candidates: list[str] = [primary_name]
        if fallback_name and fallback_name not in candidates:
            candidates.append(fallback_name)

        last_err: Exception | None = None
        for name in candidates:
            try:
                weight_path = self.ensure_weight_file(name)
                log.info("loading YOLO %s on %s (fp16=%s)", name, device, fp16)
                model = YOLO(weight_path)
                # Warmup: triggers JIT / kernel compile so the first inference
                # frame doesn't pay that cost. zeros frame is enough — we
                # don't care about output content, only that the path is hot.
                dummy = np.zeros((warmup_imgsz, warmup_imgsz, 3), dtype=np.uint8)
                model.predict(
                    dummy,
                    device=device,
                    imgsz=warmup_imgsz,
                    half=fp16,
                    verbose=False,
                )
                return model, name
            except Exception as e:  # noqa: BLE001
                log.warning("failed to load model %s: %s", name, e)
                last_err = e

        raise RuntimeError(
            f"Could not load any of {candidates}: {last_err}"
        )
