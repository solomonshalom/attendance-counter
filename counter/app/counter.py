"""Per-camera inference loop: detect+track people, count line crossings & zones."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import cv2
import numpy as np
import supervision as sv
import torch
from ultralytics import YOLO

from .capture import FrameSource, VIDEO_EXTENSIONS, looks_like_video_file
from .config import Settings
from .inference import ModelRegistry, resolve_device as _resolve_device_helper
from .inference.registry import resolve_fp16
from .storage import Storage
from .tracking import SlicerDetector

log = logging.getLogger("counter")

EventCallback = Callable[[str, dict[str, Any]], None]
StateCallback = Callable[[str, dict[str, Any]], None]

# Backwards-compat alias for callers that still import _looks_like_video_file
# from this module (manager.py).
_looks_like_video_file = looks_like_video_file


def _resolve_device(requested: str) -> str:
    """Backwards-compat alias; the canonical implementation now lives in
    ``inference.registry`` so the registry can use it during warmup."""
    return _resolve_device_helper(requested)


def _resolve_tracker_path(name: str, settings: Settings) -> str:
    """Return an absolute path/identifier ultralytics can use for tracker config.

    Accepts:
      - shipped names: 'botsort_reid', 'bytetrack_fast' (our YAMLs in trackers_dir)
      - upstream names: 'bytetrack.yaml', 'botsort.yaml' (ultralytics built-ins)
      - absolute paths to a yaml file.
    """
    name = (name or "").strip() or "botsort_reid"
    if name.endswith(".yaml") and Path(name).is_file():
        return str(Path(name).resolve())
    if name in ("bytetrack.yaml", "botsort.yaml"):
        return name
    candidate = settings.trackers_dir / f"{name}.yaml"
    if candidate.is_file():
        return str(candidate.resolve())
    log.warning("Tracker %r not found; falling back to bytetrack.yaml", name)
    return "bytetrack.yaml"


@dataclass
class LineConfig:
    x1: float
    y1: float
    x2: float
    y2: float
    name: str = "doorway"

    def to_pixels(self, width: int, height: int) -> tuple[sv.Point, sv.Point]:
        return (
            sv.Point(x=int(self.x1 * width), y=int(self.y1 * height)),
            sv.Point(x=int(self.x2 * width), y=int(self.y2 * height)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LineConfig":
        return cls(
            x1=float(d.get("x1", 0.5)),
            y1=float(d.get("y1", 0.0)),
            x2=float(d.get("x2", 0.5)),
            y2=float(d.get("y2", 1.0)),
            name=str(d.get("name", "doorway")),
        )


@dataclass
class ZoneConfig:
    name: str
    role: str  # observer | interior | entry | exit
    polygon: list[tuple[float, float]]  # normalized [0..1]

    def to_pixels(self, width: int, height: int) -> np.ndarray:
        pts = np.array(
            [(int(x * width), int(y * height)) for x, y in self.polygon],
            dtype=np.int64,
        )
        return pts

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "polygon": [{"x": x, "y": y} for x, y in self.polygon],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ZoneConfig":
        polygon_raw = d.get("polygon", [])
        polygon: list[tuple[float, float]] = []
        for p in polygon_raw:
            if isinstance(p, dict):
                polygon.append((float(p.get("x", 0.0)), float(p.get("y", 0.0))))
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                polygon.append((float(p[0]), float(p[1])))
        return cls(
            name=str(d.get("name", "zone")),
            role=str(d.get("role", "observer")),
            polygon=polygon,
        )


@dataclass
class CounterStatus:
    running: bool = False
    paused: bool = False
    model_loaded: bool = False
    camera_open: bool = False
    fps: float = 0.0
    last_error: str | None = None
    frame_width: int = 0
    frame_height: int = 0
    device: str = "cpu"
    model_name: str = ""
    tracker: str = ""
    last_frame_at: float = 0.0
    # Video file state.
    is_video_file: bool = False
    video_total_frames: int = 0
    video_position: int = 0
    video_fps: float = 0.0


@dataclass
class CounterState:
    camera_id: str = ""
    camera_name: str = ""
    source: str = ""
    kind: str = "camera"
    mirror: bool = False
    enabled: bool = True
    session_id: str | None = None
    session_label: str = ""
    session_started_at: float | None = None
    in_count: int = 0
    out_count: int = 0
    peak_inside: int = 0
    line: LineConfig = field(
        default_factory=lambda: LineConfig(0.5, 0.0, 0.5, 1.0, "doorway")
    )
    lines: list[LineConfig] = field(default_factory=list)
    zones: list[ZoneConfig] = field(default_factory=list)
    zone_counts: dict[str, int] = field(default_factory=dict)
    confidence: float | None = None
    iou: float | None = None
    imgsz: int | None = None
    tracker: str | None = None
    loop_video: bool = True
    paused: bool = False
    venue_id: str | None = None
    calibrated: bool = False
    status: CounterStatus = field(default_factory=CounterStatus)

    @property
    def inside(self) -> int:
        return max(0, self.in_count - self.out_count)

    def as_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "source": self.source,
            "kind": self.kind,
            "mirror": self.mirror,
            "enabled": self.enabled,
            "session_id": self.session_id,
            "session_label": self.session_label,
            "session_started_at": self.session_started_at,
            "in_count": self.in_count,
            "out_count": self.out_count,
            "inside": self.inside,
            "peak_inside": self.peak_inside,
            "line": self.line.as_dict(),
            "lines": [ln.as_dict() for ln in self.lines],
            "zones": [z.as_dict() for z in self.zones],
            "zone_counts": dict(self.zone_counts),
            "confidence": self.confidence,
            "iou": self.iou,
            "imgsz": self.imgsz,
            "tracker": self.tracker,
            "loop_video": self.loop_video,
            "paused": self.paused,
            "venue_id": self.venue_id,
            "calibrated": self.calibrated,
            "status": {
                "running": self.status.running,
                "paused": self.status.paused,
                "model_loaded": self.status.model_loaded,
                "camera_open": self.status.camera_open,
                "fps": round(self.status.fps, 2),
                "last_error": self.status.last_error,
                "frame_width": self.status.frame_width,
                "frame_height": self.status.frame_height,
                "device": self.status.device,
                "model_name": self.status.model_name,
                "tracker": self.status.tracker,
                "last_frame_at": self.status.last_frame_at,
                "is_video_file": self.status.is_video_file,
                "video_total_frames": self.status.video_total_frames,
                "video_position": self.status.video_position,
                "video_fps": self.status.video_fps,
            },
        }


class Counter:
    """Background thread: reads frames, runs detection+tracking, counts line crossings & zones."""

    _model_load_lock = threading.Lock()

    def __init__(
        self,
        camera_id: str,
        name: str,
        source: str,
        mirror: bool,
        line: LineConfig,
        settings: Settings,
        storage: Storage,
        enabled: bool = True,
        kind: str = "camera",
        confidence: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
        tracker: str | None = None,
        loop_video: bool = True,
        lines: list[LineConfig] | None = None,
        zones: list[ZoneConfig] | None = None,
        venue_id: str | None = None,
        homography: np.ndarray | None = None,
        registry: "ModelRegistry | None" = None,
    ) -> None:
        self.camera_id = camera_id
        self.settings = settings
        self.storage = storage
        # Registry handles model load + warmup + download. Constructed lazily
        # if not injected so single-Counter test paths still work.
        from .inference import ModelRegistry as _MR
        self._registry = registry if registry is not None else _MR(settings)

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._counting_event = threading.Event()
        self._restart_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._latest_jpeg: bytes | None = None
        self._frame_cv = threading.Condition()

        self._event_listeners: list[EventCallback] = []
        self._state_listeners: list[StateCallback] = []

        all_lines = lines if lines is not None else [line]
        self.state = CounterState(
            camera_id=camera_id,
            camera_name=name,
            source=source,
            kind=kind,
            mirror=mirror,
            enabled=enabled,
            line=line,
            lines=all_lines,
            zones=zones or [],
            confidence=confidence,
            iou=iou,
            imgsz=imgsz,
            tracker=tracker,
            loop_video=loop_video,
            venue_id=venue_id,
            calibrated=homography is not None,
        )

        self._homography: np.ndarray | None = homography
        self._dedupe_check: Callable[[str, float | None, float | None, float], bool] | None = None
        self._dedupe_record: Callable[[str, float, float, float], None] | None = None

        # name → (LineZone, LineConfig) so the crossing-gate code can read the
        # original normalized-coord endpoints (LineZone only stores pixels).
        self._line_zones: dict[str, tuple[sv.LineZone, LineConfig]] = {}
        self._dirty_geometry = True

        # Polygon zones for occupancy display (all zones) and counting
        # (zones with role='interior'). The state machine below is what makes
        # interior zones generate in/out events.
        self._zone_states: dict[str, sv.PolygonZone] = {}
        self._zone_roles: dict[str, str] = {}
        # For each interior zone, tracker_ids currently considered "inside",
        # plus a per-tracker persistence counter (positive = consecutive frames
        # seen inside, negative = consecutive frames missing). This filters
        # detection flicker without losing legitimate transitions.
        self._zone_inside_ids: dict[str, set[int]] = {}
        self._zone_persist: dict[str, dict[int, int]] = {}
        # Last known world position per (zone, tracker) so an "out" event still
        # carries a world position for cross-camera dedupe even when the
        # tracker is no longer detected anywhere on screen.
        self._zone_last_pos: dict[str, dict[int, tuple[float, float]]] = {}

        # Smoother for stable bboxes/anchor positions across frames.
        self._smoother: sv.DetectionsSmoother | None = None

        # FP16 flag, resolved by the registry at model-load time. Read by the
        # inference loop's model.track call.
        self._fp16: bool = False

        # Slicer detector — only constructed when settings.slicer_enabled, in
        # which case it OWNS the per-camera tracker (sv.ByteTrack). When the
        # slicer is disabled (default), tracking is via model.track(persist=True)
        # with BoT-SORT-ReID inside ultralytics.
        self._slicer_detector: SlicerDetector | None = None

        # ----- Crossing-gate state -----
        # Per-tracker recent history: deque of (frame_idx, x_norm, y_norm, conf).
        # Used by the velocity gate (positions over time) and the confidence
        # gate (mean conf over the recent window). Bounded by config.
        self._track_history: dict[int, deque[tuple[int, float, float, float | None]]] = {}
        # First frame index a tracker_id was seen on this camera. Used by the
        # min_track_age gate. Note: BoT-SORT-ReID can re-bind a previously-seen
        # ID after a brief absence, so this tracks first-ever-seen, not
        # first-seen-this-burst — which is the conservative interpretation
        # (older tracks are MORE trustworthy, not less).
        self._track_first_frame_idx: dict[int, int] = {}
        # Last successful crossing per (tracker_id, line_name, kind) so we can
        # debounce same-direction re-crossings within crossing_cooldown_s.
        self._last_cross_ts: dict[tuple[int, str, str], float] = {}
        # Monotonic frame counter; bumped once per inference iteration.
        self._frame_idx: int = 0
        # Counters for gate-rejected crossings, for the metrics endpoint.
        self._gate_rejections: dict[str, int] = {
            "velocity": 0,
            "confidence": 0,
            "track_age": 0,
            "cooldown": 0,
        }

    def set_dedupe_hooks(
        self,
        is_duplicate: Callable[[str, float | None, float | None, float], bool] | None,
        record: Callable[[str, float, float, float], None] | None,
    ) -> None:
        with self._lock:
            self._dedupe_check = is_duplicate
            self._dedupe_record = record

    def update_homography(self, H: np.ndarray | None) -> None:
        """Replace the homography matrix used for image→world projection.

        Hot-swap-safe: existing tracker state, line counters, peak_inside, and
        the active session's running totals are preserved. Only the world-coord
        projection result changes from this frame onward. Use this from the
        manager when calibration is updated mid-session instead of stop+restart,
        which would reset all tracker IDs.
        """
        with self._lock:
            self._homography = H
            self.state.calibrated = H is not None
        self._broadcast_state()

    def has_homography(self) -> bool:
        """Cheap, lock-free check for whether a homography is currently
        attached. Used by the manager when deciding whether to wire dedupe
        hooks. CPython attribute reads are atomic so no lock is required."""
        return self._homography is not None

    def update_venue_id(self, venue_id: str | None) -> None:
        """Reassign this counter to a venue (or detach with None).

        Only updates the displayed state; the manager is responsible for
        rewiring the dedupe hooks separately because those hooks need to know
        the deduper instance, not just the venue id.
        """
        with self._lock:
            self.state.venue_id = venue_id
        self._broadcast_state()

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._restart_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"counter[{self.camera_id[:8]}]",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        if self.state.session_id:
            try:
                self.stop_session()
            except Exception:
                log.exception("Error stopping session for camera %s", self.camera_id)
        self._stop_event.set()
        with self._frame_cv:
            self._frame_cv.notify_all()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None
            self.state.status.running = False
            self.state.status.camera_open = False
            self.state.status.model_loaded = False
            self.state.status.fps = 0.0
        self._broadcast_state()

    def request_restart(self) -> None:
        """Ask the inference loop to reopen its capture (e.g. after seek/restart)."""
        self._restart_event.set()

    def is_running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def add_event_listener(self, cb: EventCallback) -> None:
        with self._lock:
            self._event_listeners.append(cb)

    def remove_event_listener(self, cb: EventCallback) -> None:
        with self._lock:
            if cb in self._event_listeners:
                self._event_listeners.remove(cb)

    def add_state_listener(self, cb: StateCallback) -> None:
        with self._lock:
            self._state_listeners.append(cb)

    def remove_state_listener(self, cb: StateCallback) -> None:
        with self._lock:
            if cb in self._state_listeners:
                self._state_listeners.remove(cb)

    def update_meta(
        self,
        *,
        name: str | None = None,
        mirror: bool | None = None,
        enabled: bool | None = None,
        kind: str | None = None,
        confidence: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
        tracker: str | None = None,
        loop_video: bool | None = None,
        paused: bool | None = None,
    ) -> None:
        with self._lock:
            if name is not None:
                self.state.camera_name = name
            if mirror is not None:
                self.state.mirror = mirror
            if enabled is not None:
                self.state.enabled = enabled
            if kind is not None:
                self.state.kind = kind
            if confidence is not None:
                self.state.confidence = confidence
            if iou is not None:
                self.state.iou = iou
            if imgsz is not None:
                self.state.imgsz = imgsz
            if tracker is not None:
                self.state.tracker = tracker
            if loop_video is not None:
                self.state.loop_video = loop_video
            if paused is not None:
                self.state.paused = paused
                self.state.status.paused = paused
        self._broadcast_state()

    def start_session(self, label: str = "") -> dict[str, Any]:
        with self._lock:
            existing = self.storage.latest_open_session_for_camera(self.camera_id)
            if existing:
                self.storage.end_session(existing["id"])

            session = self.storage.start_session(label=label, camera_id=self.camera_id)
            self.state.session_id = session["id"]
            self.state.session_label = session["label"]
            self.state.session_started_at = session["started_at"]
            self.state.in_count = 0
            self.state.out_count = 0
            self.state.peak_inside = 0
            # Reset zone-state machine so people already on screen at session
            # start have to actually transition to be counted.
            self._zone_inside_ids.clear()
            self._zone_persist.clear()
            self._zone_last_pos.clear()
            # Reset crossing-gate bookkeeping. Cooldowns and gate-rejection
            # counters are session-scoped diagnostics; track history persists
            # because it tracks tracker objects, not events.
            self._last_cross_ts.clear()
            for k in self._gate_rejections:
                self._gate_rejections[k] = 0
            self._dirty_geometry = True
            self._counting_event.set()
        self._broadcast_state()
        return session

    def stop_session(self) -> dict[str, Any] | None:
        with self._lock:
            if not self.state.session_id:
                return None
            session_id = self.state.session_id
            self.storage.update_session_counts(
                session_id,
                self.state.in_count,
                self.state.out_count,
                self.state.peak_inside,
            )
            session = self.storage.end_session(session_id)
            self.state.session_id = None
            self.state.session_label = ""
            self.state.session_started_at = None
            self._counting_event.clear()
        self._broadcast_state()
        return session

    def reset_counts(self) -> None:
        with self._lock:
            self.state.in_count = 0
            self.state.out_count = 0
            self.state.peak_inside = 0
            self._zone_inside_ids.clear()
            self._zone_persist.clear()
            self._zone_last_pos.clear()
            self._last_cross_ts.clear()
            for k in self._gate_rejections:
                self._gate_rejections[k] = 0
            self._dirty_geometry = True
            if self.state.session_id:
                self.storage.update_session_counts(
                    self.state.session_id, 0, 0, 0
                )
        self._broadcast_state()

    def set_line(self, line: LineConfig) -> None:
        """Legacy single-line setter; mirrors into the lines list at index 0."""
        with self._lock:
            self.state.line = line
            if self.state.lines:
                self.state.lines[0] = line
            else:
                self.state.lines = [line]
            self.storage.set_camera_line(self.camera_id, line.as_dict())
            self._dirty_geometry = True
        self._broadcast_state()

    def set_lines(self, lines: list[LineConfig]) -> None:
        if not lines:
            raise ValueError("at least one line required")
        with self._lock:
            self.state.lines = lines
            self.state.line = lines[0]
            self.storage.replace_lines(
                self.camera_id, [ln.as_dict() for ln in lines]
            )
            self._dirty_geometry = True
        self._broadcast_state()

    def set_zones(self, zones: list[ZoneConfig]) -> None:
        with self._lock:
            self.state.zones = zones
            self.storage.replace_zones(
                self.camera_id, [z.as_dict() for z in zones]
            )
            # Geometry rebuild also rebuilds the zone-state machine state.
            self._zone_inside_ids.clear()
            self._zone_persist.clear()
            self._zone_last_pos.clear()
            self._dirty_geometry = True
        self._broadcast_state()

    def get_state_dict(self) -> dict[str, Any]:
        with self._lock:
            return self.state.as_dict()

    def latest_jpeg(self) -> bytes | None:
        return self._latest_jpeg

    def wait_for_frame(self, timeout: float = 1.0) -> bytes | None:
        with self._frame_cv:
            self._frame_cv.wait(timeout=timeout)
            return self._latest_jpeg

    # ---------------------------------------------------------------- internal

    def _build_line_zones(
        self, width: int, height: int
    ) -> dict[str, tuple[sv.LineZone, LineConfig]]:
        out: dict[str, tuple[sv.LineZone, LineConfig]] = {}
        names_seen: set[str] = set()
        min_thresh = max(1, self.settings.min_crossing_threshold)
        for ln in self.state.lines:
            base = ln.name or "doorway"
            name = base
            n = 1
            while name in names_seen:
                n += 1
                name = f"{base} #{n}"
            names_seen.add(name)
            start, end = ln.to_pixels(width, height)
            # BOTTOM_CENTER ≈ feet; the standard single-anchor for people counting.
            # Default 4-corner trigger silently skips while the bbox straddles
            # the line, which dropped real crossings on long-bodied detections.
            zone = sv.LineZone(
                start=start,
                end=end,
                triggering_anchors=(sv.Position.BOTTOM_CENTER,),
                minimum_crossing_threshold=min_thresh,
            )
            out[name] = (zone, ln)
        return out

    def _build_polygon_zones(self, width: int, height: int) -> dict[str, sv.PolygonZone]:
        out: dict[str, sv.PolygonZone] = {}
        roles: dict[str, str] = {}
        names_seen: set[str] = set()
        for z in self.state.zones:
            if len(z.polygon) < 3:
                continue
            base = z.name or "zone"
            name = base
            n = 1
            while name in names_seen:
                n += 1
                name = f"{base} #{n}"
            names_seen.add(name)
            try:
                pts = z.to_pixels(width, height)
                out[name] = sv.PolygonZone(
                    polygon=pts,
                    triggering_anchors=(sv.Position.BOTTOM_CENTER,),
                )
                roles[name] = z.role or "observer"
            except Exception:
                log.exception("Failed to build polygon zone %r", name)
        # Replace role map atomically so the inference loop sees a coherent set.
        self._zone_roles = roles
        return out

    def _resolve_settings(self) -> tuple[float, float, int]:
        """Detection settings with per-camera overrides on top of globals."""
        s = self.settings
        conf = self.state.confidence if self.state.confidence is not None else s.confidence
        iou = self.state.iou if self.state.iou is not None else s.iou
        imgsz = self.state.imgsz if self.state.imgsz is not None else s.image_size
        return float(conf), float(iou), int(imgsz)

    def _resolve_tracker_name(self) -> str:
        return (self.state.tracker or self.settings.tracker or "botsort_reid")

    def _load_model(self) -> YOLO:
        """Load this camera's YOLO model via the shared registry.

        The registry handles weight provisioning (download to absolute path,
        no chdir-magic), the primary→fallback chain, and a one-shot warmup
        predict so the first real frame doesn't pay the JIT cost. We just
        record the resolved device + fp16 + actual model name into
        state.status for the dashboard.
        """
        device = _resolve_device(self.settings.device)
        fp16 = resolve_fp16(self.settings.fp16, device)
        self.state.status.device = device
        # Surface the precision in the status string for operators (e.g.
        # "mps/fp16" vs "cpu"). No new field in CounterStatus needed.
        if fp16:
            self.state.status.device = f"{device}/fp16"

        _, _, imgsz = self._resolve_settings()
        model, loaded_name = self._registry.load_dedicated(
            primary_name=self.settings.model_name,
            fallback_name=self.settings.fallback_model_name,
            device=device,
            warmup_imgsz=imgsz,
            fp16=fp16,
        )
        self.state.status.model_name = loaded_name
        # Cache the resolved fp16 flag for the inference loop's predict call.
        self._fp16 = fp16
        return model

    def _run(self) -> None:
        log.info("[%s] Counter thread starting", self.camera_id[:8])
        try:
            model = self._load_model()
            self.state.status.model_loaded = True
            self.state.status.last_error = None
        except Exception as e:  # noqa: BLE001
            log.exception("[%s] Model load failed", self.camera_id[:8])
            self.state.status.last_error = f"model: {e}"
            self.state.status.model_loaded = False
            self._broadcast_state()
            return

        self._broadcast_state()

        # Exponential backoff for inference-loop crashes (capture errors, model
        # surprises, transient OS issues). Doubles per crash, jitter ≤ 25 %,
        # resets on a successful re-entry to _inference_loop.
        backoff_s = self.settings.min_capture_retry_s
        last_crash_at = 0.0

        while not self._stop_event.is_set():
            try:
                self._inference_loop(model)
            except Exception as e:  # noqa: BLE001
                now = time.time()
                # If the last crash was long enough ago, treat this as a fresh
                # incident and reset backoff. Without this, a camera that's
                # been healthy for hours but glitches once would inherit the
                # previous incident's max backoff.
                if now - last_crash_at > 60.0:
                    backoff_s = self.settings.min_capture_retry_s
                last_crash_at = now

                wait_s = min(backoff_s, self.settings.max_capture_retry_s)
                wait_s += random.uniform(0.0, 0.25 * wait_s)
                log.exception(
                    "[%s] Inference loop crashed; reopening in %.2fs",
                    self.camera_id[:8],
                    wait_s,
                )
                self.state.status.last_error = str(e)
                self.state.status.camera_open = False
                self.state.status.running = False
                self._broadcast_state()
                if self._stop_event.wait(wait_s):
                    break
                backoff_s = min(backoff_s * 2.0, self.settings.max_capture_retry_s)

        log.info("[%s] Counter thread exiting", self.camera_id[:8])

    def _inference_loop(self, model: YOLO) -> None:
        # FrameSource picks the right backend (AVFoundation / FFmpeg) and runs
        # a producer thread for network sources so stale frames are dropped
        # rather than queued.
        source = FrameSource(
            self.state.source,
            self.state.kind,
            self.settings,
            stop_event=self._stop_event,
        )
        source.open()
        is_video = source.is_video

        try:
            self.state.status.frame_width = source.width
            self.state.status.frame_height = source.height
            self.state.status.camera_open = True
            self.state.status.running = True
            self.state.status.last_error = None
            self.state.status.is_video_file = is_video

            video_fps = source.video_fps if is_video else 0.0
            video_total = source.video_total_frames if is_video else 0
            self.state.status.video_fps = video_fps
            self.state.status.video_total_frames = video_total
            self.state.status.video_position = 0
            self._broadcast_state()

            box_annotator = sv.BoxAnnotator(thickness=2)
            label_annotator = sv.LabelAnnotator(text_scale=0.5, text_thickness=1)
            line_annotator = sv.LineZoneAnnotator(
                thickness=3, text_thickness=2, text_scale=0.7
            )
            trace_annotator = sv.TraceAnnotator(thickness=2, trace_length=30)
            zone_annotators: dict[str, sv.PolygonZoneAnnotator] = {}

            cached_dim = (-1, -1)
            consecutive_fails = 0
            fps_window: list[float] = []
            last_t = time.time()
            frame_period = 1.0 / video_fps if video_fps > 1.0 else 0.0
            playback_anchor = time.time()

            # Smoother is rebuilt per-loop because length is global.
            sm_len = max(0, int(self.settings.smoother_length))
            self._smoother = sv.DetectionsSmoother(length=sm_len) if sm_len > 0 else None

            tracker_path = _resolve_tracker_path(self._resolve_tracker_name(), self.settings)
            self.state.status.tracker = Path(tracker_path).stem

            while not self._stop_event.is_set():
                # External restart request (e.g. after a seek/restart) — break to
                # re-enter the inference loop with a fresh capture.
                if self._restart_event.is_set():
                    self._restart_event.clear()
                    break

                # Pause for video sources.
                if self.state.paused and is_video:
                    if self._stop_event.wait(0.1):
                        break
                    playback_anchor = time.time()  # don't accumulate sleep debt
                    continue

                ok, frame = source.read()
                if not ok or frame is None:
                    if is_video:
                        # End of file.
                        if self.state.loop_video:
                            source.seek_start()
                            playback_anchor = time.time()
                            continue
                        # No loop -> stay idle but keep last frame visible until restart.
                        self.state.paused = True
                        self.state.status.paused = True
                        self._broadcast_state()
                        if self._stop_event.wait(0.2):
                            break
                        continue

                    # Watchdog: a network stream that silently went dead
                    # (read returns False but TCP didn't drop) needs a hard
                    # reopen to recover. Catches the dead-RTSP failure mode
                    # where cv2 happily returns False forever.
                    if source.is_network and source.is_stuck(
                        self.settings.capture_watchdog_s
                    ):
                        raise RuntimeError(
                            f"No frame in {self.settings.capture_watchdog_s}s "
                            f"on {self.state.source!r}; reopening capture"
                        )

                    consecutive_fails += 1
                    log.warning(
                        "[%s] Camera read failed (%s)",
                        self.camera_id[:8], consecutive_fails,
                    )
                    if consecutive_fails >= self.settings.max_camera_failures:
                        # Bail to the outer loop so the capture is reopened
                        # from scratch — staying in the inner loop here would
                        # spin forever against a broken cv2.VideoCapture handle.
                        raise RuntimeError(
                            f"Too many consecutive camera read failures "
                            f"({consecutive_fails}); reopening capture"
                        )
                    # Exponential backoff with ≤ 10 % jitter. The base unit is
                    # min_capture_retry_s (50 ms); each consecutive fail
                    # doubles, capped at max_capture_retry_s. So at fail #1 we
                    # wait 50 ms, #5 wait ~800 ms, #10 wait ~25 s.
                    backoff = min(
                        self.settings.min_capture_retry_s
                        * (2 ** (consecutive_fails - 1)),
                        self.settings.max_capture_retry_s,
                    )
                    backoff += random.uniform(0.0, 0.1 * backoff)
                    if self._stop_event.wait(backoff):
                        break
                    continue
                consecutive_fails = 0

                if self.state.mirror:
                    frame = cv2.flip(frame, 1)

                fh, fw = frame.shape[:2]
                if (
                    self._dirty_geometry
                    or cached_dim != (fw, fh)
                    or not self._line_zones
                ):
                    self._line_zones = self._build_line_zones(fw, fh)
                    self._zone_states = self._build_polygon_zones(fw, fh)
                    zone_annotators = {
                        name: sv.PolygonZoneAnnotator(zone=zone, color=sv.Color.GREEN, thickness=2)
                        for name, zone in self._zone_states.items()
                    }
                    cached_dim = (fw, fh)
                    self._dirty_geometry = False

                conf, iou, imgsz = self._resolve_settings()

                # state.status.device may be "<dev>/fp16" cosmetically; the
                # actual ultralytics device arg must be the bare device name.
                device_arg = self.state.status.device.split("/")[0]

                if self.settings.slicer_enabled:
                    # Tile-based path: split frame, predict per tile, NMS-merge,
                    # then per-camera ByteTrack assigns IDs. We rebuild the
                    # SlicerDetector when predict args have drifted (per-camera
                    # override edits), since the slicer captures them at
                    # construction time.
                    sig = (conf, iou, imgsz, device_arg, self._fp16)
                    if (
                        self._slicer_detector is None
                        or self._slicer_detector.params_signature() != sig
                    ):
                        self._slicer_detector = SlicerDetector(
                            settings=self.settings,
                            model=model,
                            person_class_id=self.settings.person_class_id,
                            conf=conf,
                            iou=iou,
                            imgsz=imgsz,
                            device=device_arg,
                            fp16=self._fp16,
                        )
                    detections = self._slicer_detector.infer(frame)
                else:
                    # Direct path: model.track keeps BoT-SORT-ReID state on the
                    # ultralytics predictor, giving us ReID across brief
                    # occlusions for the typical doorway camera shot.
                    results = model.track(
                        frame,
                        persist=True,
                        tracker=tracker_path,
                        classes=[self.settings.person_class_id],
                        conf=conf,
                        iou=iou,
                        imgsz=imgsz,
                        max_det=self.settings.max_det,
                        device=device_arg,
                        half=self._fp16,
                        verbose=False,
                    )
                    detections = sv.Detections.from_ultralytics(results[0])

                # Drop anything without a tracker_id — line/zone counters need stable
                # IDs to debounce. Both paths above can produce tracker_id=None
                # (slicer's ByteTrack on its very first call, or model.track
                # before initial track binding); we stay defensive against
                # object-array None entries from any integration.
                if detections.tracker_id is None:
                    detections = sv.Detections.empty()
                elif detections.tracker_id.dtype == object:
                    valid = np.array(
                        [t is not None for t in detections.tracker_id], dtype=bool
                    )
                    detections = detections[valid]

                if self._smoother is not None and len(detections) > 0:
                    try:
                        detections = self._smoother.update_with_detections(detections)
                    except Exception:
                        log.exception("smoother failed; disabling for this loop")
                        self._smoother = None

                # Bump the per-camera frame counter (used by track-age gate)
                # and refresh the per-tracker history windows used by the
                # velocity / confidence gates. This must run AFTER the
                # smoother so the gates use the same anchor positions that
                # LineZone.trigger sees.
                self._frame_idx += 1
                self._update_track_history(detections, fw, fh)
                if self._frame_idx % 1000 == 0:
                    self._gc_track_state()

                annotated = frame.copy()
                if len(detections) > 0:
                    annotated = trace_annotator.annotate(annotated, detections)
                    annotated = box_annotator.annotate(annotated, detections)
                    labels = self._make_labels(detections)
                    annotated = label_annotator.annotate(annotated, detections, labels=labels)

                # Counting per line. sv.LineZone's minimum_crossing_threshold
                # already filters bbox jitter; we simply emit one event per
                # `crossed` mask entry — but only if the tracker passes our
                # crossing gates (velocity / confidence / age / cooldown).
                # Back-and-forth movement legitimately produces multiple events
                # (in → out → in), which is correct for occupancy tracking;
                # the cooldown gate only filters same-direction re-crossings.
                # Each entry is (kind, tracker_id, line_name, world_x|None, world_y|None).
                events_buffer: list[tuple[str, int, str, float | None, float | None]] = []
                counting_active = self._counting_event.is_set() and self.state.session_id

                for line_name, (line_zone, line_cfg) in self._line_zones.items():
                    crossed_in, crossed_out = line_zone.trigger(detections)
                    annotated = line_annotator.annotate(annotated, line_zone)
                    if not counting_active:
                        continue
                    now_ts = time.time()
                    for tid, det_idx in self._collect_crossed_pairs(detections, crossed_in):
                        if not self._passes_crossing_gates(
                            tid, line_name, line_cfg, "in", now_ts
                        ):
                            continue
                        wx, wy = self._world_pos(detections, det_idx)
                        events_buffer.append(("in", tid, line_name, wx, wy))
                        self._last_cross_ts[(tid, line_name, "in")] = now_ts
                    for tid, det_idx in self._collect_crossed_pairs(detections, crossed_out):
                        if not self._passes_crossing_gates(
                            tid, line_name, line_cfg, "out", now_ts
                        ):
                            continue
                        wx, wy = self._world_pos(detections, det_idx)
                        events_buffer.append(("out", tid, line_name, wx, wy))
                        self._last_cross_ts[(tid, line_name, "out")] = now_ts

                # Polygon zones: occupancy display for all zones, plus a
                # state-machine counter for zones with role='interior'. The
                # state machine emits "in" the first time a tracker has been
                # seen inside for `min_crossing_threshold` consecutive frames,
                # and "out" the first time it's been absent for the same
                # number of frames after being inside. This gives reliable
                # entry counts even without a precisely-placed line — useful
                # for rooms with no clear doorway boundary.
                zone_counts: dict[str, int] = {}
                threshold = max(1, self.settings.min_crossing_threshold)
                for zone_name, zone in self._zone_states.items():
                    mask = zone.trigger(detections)
                    count_now = int(mask.sum()) if mask is not None else 0
                    zone_counts[zone_name] = count_now
                    ann = zone_annotators.get(zone_name)
                    if ann is not None:
                        try:
                            annotated = ann.annotate(annotated, label=str(count_now))
                        except Exception:
                            log.exception("zone annotator failed")

                    role = self._zone_roles.get(zone_name, "observer")
                    if role != "interior" or not counting_active:
                        continue

                    self._update_zone_state_machine(
                        zone_name, detections, mask, threshold, events_buffer
                    )

                self.state.zone_counts = zone_counts

                if events_buffer:
                    self._record_crossings(events_buffer)

                self._encode_preview(annotated)

                now = time.time()
                fps_window.append(now - last_t)
                last_t = now
                if len(fps_window) > 30:
                    fps_window = fps_window[-30:]
                avg = sum(fps_window) / len(fps_window) if fps_window else 0
                self.state.status.fps = (1.0 / avg) if avg > 0 else 0.0
                self.state.status.last_frame_at = now

                if is_video:
                    self.state.status.video_position = source.video_position()
                    if frame_period > 0:
                        # Pace playback to the file's native FPS so detection
                        # timing matches what a live camera would look like.
                        playback_anchor += frame_period
                        delay = playback_anchor - time.time()
                        if delay > 0:
                            if self._stop_event.wait(min(delay, 1.0)):
                                break
                        elif delay < -1.0:
                            # Fell badly behind — resync rather than spin forever.
                            playback_anchor = time.time()

        finally:
            source.close()
            self.state.status.camera_open = False
            self.state.status.running = False
            self._broadcast_state()

    @staticmethod
    def _collect_crossed_pairs(
        detections: sv.Detections,
        crossed_mask: Iterable[bool],
    ) -> list[tuple[int, int]]:
        """Return list of (tracker_id, detection_index) for everything that
        crossed THIS frame. sv.LineZone's internal threshold filters jitter;
        the mask is sparse (only true on the actual crossing frame), so a
        legitimate back-and-forth will produce multiple separate events."""
        if detections.tracker_id is None:
            return []
        out: list[tuple[int, int]] = []
        for det_idx, (tracker_id, crossed) in enumerate(zip(detections.tracker_id, crossed_mask)):
            if crossed and tracker_id is not None:
                out.append((int(tracker_id), det_idx))
        return out

    def _update_track_history(
        self, detections: sv.Detections, fw: int, fh: int
    ) -> None:
        """Append the current-frame anchor + confidence to each tracker's
        rolling history window. Anchors are stored in normalized (0..1) coords
        so the velocity gate has resolution-independent units."""
        if detections.tracker_id is None or len(detections) == 0:
            return
        if fw <= 0 or fh <= 0:
            return

        history_len = max(2, int(self.settings.track_history_length))
        confs = (
            detections.confidence
            if detections.confidence is not None
            else [None] * len(detections)
        )

        for det_idx, tid in enumerate(detections.tracker_id):
            if tid is None:
                continue
            tid_i = int(tid)
            box = detections.xyxy[det_idx]
            ax_norm = float((box[0] + box[2]) * 0.5) / fw
            ay_norm = float(box[3]) / fh
            conf = float(confs[det_idx]) if confs[det_idx] is not None else None

            hist = self._track_history.get(tid_i)
            if hist is None or hist.maxlen != history_len:
                hist = deque(maxlen=history_len)
                self._track_history[tid_i] = hist
            hist.append((self._frame_idx, ax_norm, ay_norm, conf))

            if tid_i not in self._track_first_frame_idx:
                self._track_first_frame_idx[tid_i] = self._frame_idx

    def _gc_track_state(self) -> None:
        """Drop history for trackers not seen recently and crossings older than
        the cooldown's relevance window. Bounded growth for long sessions."""
        # Tracks not updated in 5× the history-length frames are stale.
        retain_frames = max(self.settings.track_history_length * 5, 200)
        cutoff_frame = self._frame_idx - retain_frames
        stale = [
            tid
            for tid, h in self._track_history.items()
            if h and h[-1][0] < cutoff_frame
        ]
        for tid in stale:
            self._track_history.pop(tid, None)
            self._track_first_frame_idx.pop(tid, None)

        cooldown = self.settings.crossing_cooldown_s
        if cooldown > 0:
            ts_cutoff = time.time() - max(cooldown * 5, 30.0)
            self._last_cross_ts = {
                k: ts for k, ts in self._last_cross_ts.items() if ts >= ts_cutoff
            }

    def _passes_crossing_gates(
        self,
        tid: int,
        line_name: str,
        line: LineConfig,
        kind: str,
        ts: float,
    ) -> bool:
        """Return True if a tracker's crossing of this line in this direction
        should be counted, after applying the velocity / confidence / age /
        cooldown gates. Defaults preserve current behaviour: with all gates at
        their disable values, this always returns True."""
        # Track-age gate: tracker must have been observed for at least N frames.
        min_age = max(0, int(self.settings.min_track_age))
        if min_age > 0:
            first = self._track_first_frame_idx.get(tid)
            if first is None or (self._frame_idx - first) < min_age:
                self._gate_rejections["track_age"] += 1
                return False

        # Cooldown gate: same (tid, line, kind) cannot re-fire within window.
        cooldown = float(self.settings.crossing_cooldown_s)
        if cooldown > 0.0:
            last = self._last_cross_ts.get((tid, line_name, kind))
            if last is not None and (ts - last) < cooldown:
                self._gate_rejections["cooldown"] += 1
                return False

        # Confidence gate: mean recent detection confidence ≥ camera_conf + margin.
        margin = float(self.settings.crossing_confidence_margin)
        if margin > 0.0:
            cam_conf = (
                self.state.confidence
                if self.state.confidence is not None
                else self.settings.confidence
            )
            confs = [
                c
                for (_, _, _, c) in self._track_history.get(tid, ())
                if c is not None
            ]
            if confs and (sum(confs) / len(confs)) < (cam_conf + margin):
                self._gate_rejections["confidence"] += 1
                return False

        # Velocity gate: |normal-component velocity| ≥ threshold.
        v_thresh = float(self.settings.velocity_gate)
        if v_thresh > 0.0:
            v_normal = self._normal_velocity(tid, line)
            if v_normal < v_thresh:
                self._gate_rejections["velocity"] += 1
                return False

        return True

    def _normal_velocity(self, tid: int, line: LineConfig) -> float:
        """Return |projection of tracker's recent velocity onto the line's
        normal direction|, in normalized units (frame-fraction per frame).
        Returns 0 when there's not enough history to compute meaningfully."""
        hist = self._track_history.get(tid)
        if not hist or len(hist) < 2:
            return 0.0
        f0, x0, y0, _ = hist[0]
        f1, x1, y1, _ = hist[-1]
        frames = f1 - f0
        if frames <= 0:
            return 0.0
        vx = (x1 - x0) / frames
        vy = (y1 - y0) / frames
        # Line direction → its unit normal (rotated 90°).
        lx = line.x2 - line.x1
        ly = line.y2 - line.y1
        L = (lx * lx + ly * ly) ** 0.5
        if L < 1e-9:
            return 0.0
        nx = -ly / L
        ny = lx / L
        return abs(vx * nx + vy * ny)

    def _update_zone_state_machine(
        self,
        zone_name: str,
        detections: sv.Detections,
        mask: np.ndarray | None,
        threshold: int,
        events_buffer: list[tuple[str, int, str, float | None, float | None]],
    ) -> None:
        """Track entry/exit transitions for a single interior zone.

        State per tracker_id, kept across frames:
          - persist > 0: that many consecutive frames seen inside
          - persist < 0: that many consecutive frames missing (after having
            been inside)
        A tracker enters the zone's "inside" set when persist hits +threshold;
        it leaves when persist hits -threshold. Once an in/out transition
        fires, persist resets to ±1 in the matching direction so back-to-back
        re-entries within the same session legitimately count again."""
        persist = self._zone_persist.setdefault(zone_name, {})
        inside = self._zone_inside_ids.setdefault(zone_name, set())
        last_pos = self._zone_last_pos.setdefault(zone_name, {})

        # Tracker IDs visible inside the polygon this frame.
        now_inside_ids: set[int] = set()
        if detections.tracker_id is not None and mask is not None:
            for det_idx, in_zone in enumerate(mask):
                tid = detections.tracker_id[det_idx]
                if in_zone and tid is not None:
                    tid_i = int(tid)
                    now_inside_ids.add(tid_i)
                    # Cache last known position so an "out" event still has
                    # world coords for cross-camera dedup, even if the tracker
                    # is no longer visible.
                    wx, wy = self._world_pos(detections, det_idx)
                    if wx is not None and wy is not None:
                        last_pos[tid_i] = (wx, wy)

                    persist[tid_i] = max(0, persist.get(tid_i, 0)) + 1
                    if persist[tid_i] >= threshold and tid_i not in inside:
                        inside.add(tid_i)
                        events_buffer.append(("in", tid_i, f"zone:{zone_name}", wx, wy))

        # Trackers that were inside but aren't in this frame: count down.
        for tid_i in list(inside):
            if tid_i in now_inside_ids:
                continue
            persist[tid_i] = min(0, persist.get(tid_i, 0)) - 1
            if persist[tid_i] <= -threshold:
                inside.discard(tid_i)
                # Don't pop persist here — keep the negative counter so a
                # quick re-entry doesn't re-trigger from stale positive count.
                persist[tid_i] = 0
                wx, wy = last_pos.pop(tid_i, (None, None))
                events_buffer.append(("out", tid_i, f"zone:{zone_name}", wx, wy))

        # Garbage-collect stale persist entries we know nothing about anymore
        # (not inside, not now-detected, fully decayed). Bound is implicit but
        # this keeps the dict small for long sessions.
        if len(persist) > 1024:
            for tid_i in list(persist.keys()):
                if (
                    tid_i not in inside
                    and tid_i not in now_inside_ids
                    and persist[tid_i] == 0
                ):
                    del persist[tid_i]

    def _world_pos(
        self, detections: sv.Detections, det_idx: int
    ) -> tuple[float | None, float | None]:
        """Project a detection's BOTTOM_CENTER anchor through the camera's
        homography (which expects normalized [0..1] image coords), returning
        meters in the venue's floor plane.

        Normalizing here makes the calibration resolution-independent: the
        same H works regardless of how the camera reports its frame size.
        """
        if self._homography is None:
            return (None, None)
        fw = self.state.status.frame_width
        fh = self.state.status.frame_height
        if fw <= 0 or fh <= 0:
            return (None, None)
        try:
            box = detections.xyxy[det_idx]
            bx = float((box[0] + box[2]) / 2.0) / fw
            by = float(box[3]) / fh
            pt = np.array([[[bx, by]]], dtype=np.float64)
            world = cv2.perspectiveTransform(pt, self._homography)
            return float(world[0][0][0]), float(world[0][0][1])
        except Exception:
            log.exception("world projection failed")
            return (None, None)

    def _record_crossings(
        self,
        events: list[tuple[str, int, str, float | None, float | None]],
    ) -> None:
        """Persist a batch of crossing events.

        We split the work into two phases so the inference thread isn't blocked
        on disk fsync:
          - Phase 1 (under lock): pure in-memory mutations — counter increments,
            dedupe-buffer maintenance, peak_inside snapshot.
          - Phase 2 (lock released): SQLite writes + WS broadcast. Each event
            is written with the timestamp captured during phase 1 so the
            persisted ts matches the dedupe window we measured against.
        """
        if not events:
            return

        work: list[dict[str, Any]] = []
        session_id: str
        in_count: int
        out_count: int
        peak_inside: int

        with self._lock:
            if not self.state.session_id:
                return
            session_id = self.state.session_id

            now = time.time()
            for kind, tid, line_name, wx, wy in events:
                # Cross-camera dedupe: if another camera in the same venue saw
                # this person crossing the matching line direction within the
                # dedup window/radius, we still persist the event (with
                # deduped=1 for analytics) but don't bump the live counter.
                is_dup = False
                if self._dedupe_check is not None:
                    try:
                        is_dup = bool(self._dedupe_check(kind, wx, wy, now))
                    except Exception:
                        log.exception("dedupe check failed; treating as non-duplicate")
                        is_dup = False

                if not is_dup:
                    if kind == "in":
                        self.state.in_count += 1
                    else:
                        self.state.out_count += 1

                work.append(
                    {
                        "ts": now,
                        "kind": kind,
                        "tracker_id": tid,
                        "line_name": line_name,
                        "world_x": wx,
                        "world_y": wy,
                        "deduped": is_dup,
                    }
                )

                # Record in dedupe buffer regardless — duplicates from a third
                # camera in the same window should still match.
                if (
                    self._dedupe_record is not None
                    and wx is not None
                    and wy is not None
                ):
                    try:
                        self._dedupe_record(kind, wx, wy, now)
                    except Exception:
                        log.exception("dedupe record failed")

            inside = self.state.inside
            if inside > self.state.peak_inside:
                self.state.peak_inside = inside

            in_count = self.state.in_count
            out_count = self.state.out_count
            peak_inside = self.state.peak_inside

        # Phase 2: lock released. Disk I/O and broadcast happen here so the
        # inference loop can race ahead while SQLite is fsyncing.
        persisted: list[dict[str, Any]] = []
        for w in work:
            try:
                event = self.storage.append_event(
                    session_id,
                    w["kind"],
                    w["tracker_id"],
                    line_name=w["line_name"],
                    world_x=w["world_x"],
                    world_y=w["world_y"],
                    deduped=w["deduped"],
                    ts=w["ts"],
                )
                persisted.append(event)
            except Exception:
                log.exception("failed to persist crossing event")

        try:
            self.storage.update_session_counts(
                session_id, in_count, out_count, peak_inside
            )
        except Exception:
            log.exception("failed to update session counts")

        for event in persisted:
            self._broadcast_event(event)
        self._broadcast_state()

    def _encode_preview(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        max_w = 1280
        if w > max_w:
            scale = max_w / w
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))

        ok, buf = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.settings.preview_quality],
        )
        if not ok:
            return
        with self._frame_cv:
            self._latest_jpeg = buf.tobytes()
            self._frame_cv.notify_all()

    @staticmethod
    def _make_labels(detections: sv.Detections) -> list[str]:
        labels: list[str] = []
        ids = detections.tracker_id if detections.tracker_id is not None else [None] * len(detections)
        confs = detections.confidence if detections.confidence is not None else [None] * len(detections)
        for tid, conf in zip(ids, confs):
            tid_str = f"#{int(tid)}" if tid is not None else "#?"
            conf_str = f" {conf:.0%}" if conf is not None else ""
            labels.append(f"person {tid_str}{conf_str}")
        return labels

    def _broadcast_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._event_listeners)
        for cb in listeners:
            try:
                cb(self.camera_id, event)
            except Exception:
                log.exception("Event listener failed")

    def _broadcast_state(self) -> None:
        snapshot = self.get_state_dict()
        with self._lock:
            listeners = list(self._state_listeners)
        for cb in listeners:
            try:
                cb(self.camera_id, snapshot)
            except Exception:
                log.exception("State listener failed")
