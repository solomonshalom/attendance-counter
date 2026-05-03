"""Face detect → embed → match → bind pipeline.

Wraps insightface's FaceAnalysis (SCRFD detector + ArcFace embedder) and
the encrypted IdentityStore. Soft-fails: if insightface or onnxruntime
aren't installed, :meth:`is_available` returns False and the pipeline is a
no-op. The rest of the counter operates normally.

Per-frame usage from the Counter:
  1. For each tracked person without a person_id binding, crop the bbox.
  2. ``identify(crop, venue_id)`` — runs the face pipeline and returns a
     ``person_id`` (matched existing, or freshly-minted), or ``None`` if no
     usable face was found in the crop.
  3. Counter caches ``tracker_id → person_id`` so the work isn't repeated
     each frame.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..config import Settings
    from .store import IdentityStore

log = logging.getLogger("counter.identity.pipeline")


# Required-quality bar before we even try to embed:
#   - face bbox at least this many pixels tall in the crop
MIN_FACE_HEIGHT_PX = 60
#   - the detector's own confidence above this
MIN_DET_CONF = 0.55


class IdentityPipeline:
    """Face-detection + embedding + match. Constructed once per
    CameraManager. The underlying FaceAnalysis model is loaded lazily on
    first use so boot stays fast even with insightface in requirements."""

    _LOAD_LOCK = threading.Lock()

    def __init__(self, settings: "Settings", store: "IdentityStore") -> None:
        self.settings = settings
        self.store = store
        self._fa = None  # lazy-loaded
        self._available: bool | None = None  # tri-state: None=unchecked
        self._load_failed = False

    # ----- availability ------------------------------------------------ #

    def is_available(self) -> bool:
        """True if insightface + onnxruntime are importable AND the model
        loaded successfully. Cached after the first attempt."""
        if self._available is not None:
            return self._available

        with self._LOAD_LOCK:
            if self._available is not None:
                return self._available
            try:
                import insightface  # noqa: F401
                import onnxruntime  # noqa: F401
            except Exception:
                log.warning(
                    "insightface or onnxruntime not importable; identity "
                    "pipeline disabled. pip install insightface onnxruntime"
                )
                self._available = False
                return False

            try:
                self._load_face_analyzer()
                self._available = True
            except Exception:
                log.exception(
                    "failed to load insightface model; identity pipeline disabled"
                )
                self._available = False
                self._load_failed = True
        return self._available

    def _load_face_analyzer(self) -> None:
        from insightface.app import FaceAnalysis

        # 'buffalo_l' = SCRFD-10g detector + ArcFace r100 embedder. ~250 MB
        # total, downloaded to ~/.insightface/ on first use. For small
        # deployments 'buffalo_sc' is a lighter alternative (~100 MB).
        model_pack = self.settings.face_model_pack
        # ctx_id=-1 → CPU; 0 → first GPU. CPU is fine on Apple Silicon
        # because insightface's onnxruntime build uses CoreML EP automatically
        # when available.
        ctx_id = self.settings.face_model_ctx_id

        # Where to cache models. Default ~/.insightface, but we redirect to
        # our models_dir so Mac sandbox / Docker users keep everything
        # under counter/models.
        root = Path(self.settings.models_dir).resolve() / "insightface"
        root.mkdir(parents=True, exist_ok=True)

        log.info(
            "loading insightface FaceAnalysis pack=%s ctx_id=%s root=%s",
            model_pack, ctx_id, root,
        )
        fa = FaceAnalysis(
            name=model_pack,
            root=str(root),
            allowed_modules=["detection", "recognition"],
        )
        # det_size: input resolution for the detector. 640 is a good
        # balance — bigger means more accuracy on small faces but more CPU.
        fa.prepare(
            ctx_id=ctx_id,
            det_size=(self.settings.face_det_size, self.settings.face_det_size),
            det_thresh=MIN_DET_CONF,
        )
        self._fa = fa

    # ----- public API -------------------------------------------------- #

    def identify(
        self,
        crop_bgr: np.ndarray,
        venue_id: str,
        retention_s: float | None,
    ) -> str | None:
        """Run the pipeline on a person crop. Returns person_id (existing
        or fresh) on success, None when no face / low quality / opted-out.

        The crop should be a BGR ndarray (matching cv2 / ultralytics
        output). The detector runs on the crop, picks the largest face,
        and embeds it.
        """
        if not self.is_available():
            return None
        if self._fa is None:
            return None
        if crop_bgr is None or crop_bgr.size == 0:
            return None

        try:
            faces = self._fa.get(crop_bgr)
        except Exception:
            log.exception("FaceAnalysis.get raised")
            return None

        if not faces:
            return None

        # Pick the largest face by bbox area. In a person-bbox crop, that's
        # almost always the person's own face (other faces leaking in from
        # neighbours are usually smaller).
        face = max(
            faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
        )

        # Quality gate: discard small / low-conf faces.
        x1, y1, x2, y2 = face.bbox
        if (y2 - y1) < MIN_FACE_HEIGHT_PX or float(face.det_score) < MIN_DET_CONF:
            return None

        # ArcFace embeddings ship pre-normalized; defensively re-normalize.
        emb = np.asarray(face.normed_embedding, dtype=np.float32)
        norm = np.linalg.norm(emb)
        if norm < 1e-6:
            return None
        emb = emb / norm

        # Opt-out gate.
        try:
            if self.store.is_blocked(venue_id, emb):
                return None
        except Exception:
            log.exception("opt-out check raised; continuing")

        # Match against existing. If hit, refresh; else create.
        try:
            match_id, sim = self.store.find_match(
                venue_id, emb, threshold=self.settings.face_match_threshold
            )
            return self.store.upsert_person(
                venue_id=venue_id,
                embedding=emb,
                retention_s=retention_s,
                existing_person_id=match_id,
            )
        except Exception:
            log.exception("identity store upsert failed")
            return None

    def opt_out_from_image(self, image_bgr: np.ndarray, venue_id: str) -> int:
        """Compute the embedding of the largest face in the image and add
        it to the venue's do-not-store list. Returns number of person rows
        purged. 0 if no usable face."""
        if not self.is_available():
            return 0
        try:
            faces = self._fa.get(image_bgr)  # type: ignore[union-attr]
        except Exception:
            log.exception("FaceAnalysis.get raised in opt_out")
            return 0
        if not faces:
            return 0
        face = max(
            faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
        )
        emb = np.asarray(face.normed_embedding, dtype=np.float32)
        norm = np.linalg.norm(emb)
        if norm < 1e-6:
            return 0
        emb = emb / norm
        return self.store.opt_out(venue_id, emb)
