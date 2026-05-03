"""Tile-based inference for distant / wide-angle scenes.

Wraps :class:`supervision.InferenceSlicer` so the per-camera inference loop
can opt into tiling without any awareness of slice geometry. The slicer
splits each frame into overlapping tiles, runs YOLO predict on each tile in
parallel threads, and merges results with NMS or non-max merge.

This finds people that occupy < 40 px tall on a 1080p frame — a common
failure mode for nano YOLO on wide-angle cameras viewing > 10 m halls.
The cost is N × inference latency per frame (N = number of tiles), so it's
only worth enabling on cameras that genuinely have small-people detection
problems.

Slicing produces detections without tracker IDs (the YOLO predict per tile
doesn't track). The caller is expected to feed the result through a
per-camera tracker (``sv.ByteTrack``) to assign IDs.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING

import numpy as np
import supervision as sv

if TYPE_CHECKING:
    from ultralytics import YOLO

    from ..config import Settings

log = logging.getLogger("counter.tracking.slicer")

# supervision 0.28 marks ByteTrack deprecated for removal in 0.30; we suppress
# the warning here because (a) requirements.txt pins to <0.29 and (b) there's
# no successor class shipped yet — the migration is a follow-up when 0.30 lands.
warnings.filterwarnings(
    "ignore",
    message=".*ByteTrack.*deprecated.*",
    category=FutureWarning,
)


class SlicerDetector:
    """Wraps an InferenceSlicer + a per-camera ByteTrack tracker.

    Construct once per Counter that has slicer_enabled=true; call
    :meth:`infer` per frame. Returns ``sv.Detections`` with ``tracker_id``
    populated, ready for the LineZone counters.

    The model and prediction args (conf, iou, imgsz, device, fp16) are
    captured by the slicer's per-tile callback at construction time. If
    these change at runtime (per-camera override updates), the slicer must
    be rebuilt — call :meth:`needs_rebuild` to check.
    """

    def __init__(
        self,
        settings: "Settings",
        model: "YOLO",
        person_class_id: int,
        conf: float,
        iou: float,
        imgsz: int,
        device: str,
        fp16: bool,
    ) -> None:
        self._settings = settings
        self._model = model
        self._person_class_id = person_class_id
        self._conf = conf
        self._iou = iou
        self._imgsz = imgsz
        self._device = device
        self._fp16 = fp16

        self._slicer = self._build_slicer()
        self._tracker = sv.ByteTrack(
            track_activation_threshold=0.30,
            lost_track_buffer=60,
            minimum_matching_threshold=0.85,
            frame_rate=max(1, int(settings.target_fps)),
        )

    # ----- public API --------------------------------------------------- #

    def infer(self, frame: np.ndarray) -> sv.Detections:
        """Run sliced predict + per-camera ByteTrack on a single frame.

        Returns detections with tracker_ids assigned. Empty Detections
        on failure or no detections.
        """
        try:
            detections = self._slicer(frame)
        except Exception:
            log.exception("InferenceSlicer raised; returning empty detections")
            return sv.Detections.empty()

        if len(detections) == 0:
            # ByteTrack still wants an update every frame so existing tracks
            # can age out; pass the empty detections through.
            return self._tracker.update_with_detections(detections)

        # Filter to person class. The per-tile callback already restricts via
        # `classes=` but defensively re-filter post-merge in case a later
        # supervision update changes that contract.
        if detections.class_id is not None:
            mask = detections.class_id == self._person_class_id
            if not mask.all():
                detections = detections[mask]

        return self._tracker.update_with_detections(detections)

    def reset_tracker(self) -> None:
        """Reset the internal ByteTrack state. Use on session boundaries
        if you want fresh tracker IDs without rebuilding the whole detector."""
        self._tracker = sv.ByteTrack(
            track_activation_threshold=0.30,
            lost_track_buffer=60,
            minimum_matching_threshold=0.85,
            frame_rate=max(1, int(self._settings.target_fps)),
        )

    def params_signature(self) -> tuple[float, float, int, str, bool]:
        """Tuple identifying the predict args. The Counter compares this
        against current per-camera settings to decide whether to rebuild."""
        return (self._conf, self._iou, self._imgsz, self._device, self._fp16)

    # ----- private ------------------------------------------------------ #

    def _build_slicer(self) -> sv.InferenceSlicer:
        tile = max(64, int(self._settings.slicer_tile_size))
        overlap = max(0, int(self._settings.slicer_overlap))
        workers = max(1, int(self._settings.slicer_thread_workers))
        # NON_MAX_MERGE preserves overlapping people that NON_MAX_SUPPRESSION
        # would discard — important when two people stand side-by-side and
        # one is partly cut by a tile boundary.
        overlap_filter = sv.OverlapFilter.NON_MAX_MERGE

        def _predict_tile(image_slice: np.ndarray) -> sv.Detections:
            # Per-tile YOLO predict. classes restricts to person at the
            # detector level so the slicer's NMS only deals with persons.
            try:
                results = self._model.predict(
                    image_slice,
                    conf=self._conf,
                    iou=self._iou,
                    imgsz=self._imgsz,
                    classes=[self._person_class_id],
                    max_det=self._settings.max_det,
                    device=self._device,
                    half=self._fp16,
                    verbose=False,
                )
            except Exception:
                log.exception("per-tile predict failed; returning empty detections")
                return sv.Detections.empty()
            if not results:
                return sv.Detections.empty()
            return sv.Detections.from_ultralytics(results[0])

        return sv.InferenceSlicer(
            callback=_predict_tile,
            slice_wh=(tile, tile),
            overlap_wh=(overlap, overlap),
            overlap_filter=overlap_filter,
            iou_threshold=float(self._settings.slicer_iou_threshold),
            thread_workers=workers,
        )
