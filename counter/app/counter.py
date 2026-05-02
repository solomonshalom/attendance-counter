"""Inference loop: read frames, detect+track people, count line crossings."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import cv2
import numpy as np
import supervision as sv
import torch
from ultralytics import YOLO

from .config import Settings
from .storage import Storage

log = logging.getLogger("counter")

EventCallback = Callable[[dict[str, Any]], None]
StateCallback = Callable[[dict[str, Any]], None]


def _resolve_device(requested: str) -> str:
    if requested == "auto":
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    return requested


def _open_capture(source: str, width: int, height: int, fps: int) -> cv2.VideoCapture:
    """Open camera or RTSP/HTTP stream. Index source like '0' becomes int(0)."""
    src: int | str
    try:
        src = int(source)
    except ValueError:
        src = source

    # On macOS, AVFoundation is the right backend for built-in / USB cams.
    backend = cv2.CAP_AVFOUNDATION if isinstance(src, int) else cv2.CAP_ANY
    cap = cv2.VideoCapture(src, backend)
    if not cap.isOpened():
        # Fall back to default backend.
        cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        if isinstance(src, int):
            raise RuntimeError(
                f"Could not open camera index {src}. On macOS, ensure your "
                "terminal has Camera permission (System Settings → Privacy & "
                "Security → Camera). Also verify no other app is using the "
                "camera, or try a different COUNTER_SOURCE value."
            )
        raise RuntimeError(f"Could not open video source: {source}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # minimize latency
    return cap


@dataclass
class LineConfig:
    """Line position in normalized [0..1] coordinates."""
    x1: float
    y1: float
    x2: float
    y2: float

    def to_pixels(self, width: int, height: int) -> tuple[sv.Point, sv.Point]:
        return (
            sv.Point(x=int(self.x1 * width), y=int(self.y1 * height)),
            sv.Point(x=int(self.x2 * width), y=int(self.y2 * height)),
        )

    def as_dict(self) -> dict[str, float]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}


@dataclass
class CounterStatus:
    running: bool = False
    model_loaded: bool = False
    camera_open: bool = False
    fps: float = 0.0
    last_error: str | None = None
    frame_width: int = 0
    frame_height: int = 0
    device: str = "cpu"
    model_name: str = ""
    last_frame_at: float = 0.0


@dataclass
class CounterState:
    """Live counter state shared between inference loop and HTTP/WS clients."""
    session_id: str | None = None
    session_label: str = ""
    session_started_at: float | None = None
    in_count: int = 0
    out_count: int = 0
    peak_inside: int = 0
    line: LineConfig = field(
        default_factory=lambda: LineConfig(0.5, 0.0, 0.5, 1.0)
    )
    status: CounterStatus = field(default_factory=CounterStatus)

    @property
    def inside(self) -> int:
        return max(0, self.in_count - self.out_count)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_label": self.session_label,
            "session_started_at": self.session_started_at,
            "in_count": self.in_count,
            "out_count": self.out_count,
            "inside": self.inside,
            "peak_inside": self.peak_inside,
            "line": self.line.as_dict(),
            "status": {
                "running": self.status.running,
                "model_loaded": self.status.model_loaded,
                "camera_open": self.status.camera_open,
                "fps": round(self.status.fps, 2),
                "last_error": self.status.last_error,
                "frame_width": self.status.frame_width,
                "frame_height": self.status.frame_height,
                "device": self.status.device,
                "model_name": self.status.model_name,
                "last_frame_at": self.status.last_frame_at,
            },
        }


class Counter:
    """Background thread that runs YOLO + supervision LineZone counting."""

    LINE_SETTING_KEY = "line_config"

    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._counting_event = threading.Event()  # set => actively counting
        self._thread: threading.Thread | None = None

        # Latest annotated frame (JPEG-encoded bytes) for MJPEG preview.
        self._latest_jpeg: bytes | None = None
        self._frame_cv = threading.Condition()

        self._event_listeners: list[EventCallback] = []
        self._state_listeners: list[StateCallback] = []

        self.state = CounterState()
        self._restore_line()

        # Track inside-count for each tracker_id we've seen crossing.
        self._tracked_ids_in: set[int] = set()
        self._tracked_ids_out: set[int] = set()

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="counter", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=10.0)

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

    def start_session(self, label: str = "") -> dict[str, Any]:
        with self._lock:
            # Auto-close any prior open session.
            existing = self.storage.latest_open_session()
            if existing:
                self.storage.end_session(existing["id"])

            session = self.storage.start_session(label=label)
            self.state.session_id = session["id"]
            self.state.session_label = session["label"]
            self.state.session_started_at = session["started_at"]
            self.state.in_count = 0
            self.state.out_count = 0
            self.state.peak_inside = 0
            self._tracked_ids_in.clear()
            self._tracked_ids_out.clear()
            self._reset_line_zone()
            self._counting_event.set()
        self._broadcast_state()
        return session

    def stop_session(self) -> dict[str, Any] | None:
        with self._lock:
            if not self.state.session_id:
                return None
            session_id = self.state.session_id
            # Flush final counts before closing.
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
            self._tracked_ids_in.clear()
            self._tracked_ids_out.clear()
            self._reset_line_zone()
            if self.state.session_id:
                self.storage.update_session_counts(
                    self.state.session_id, 0, 0, 0
                )
        self._broadcast_state()

    def set_line(self, line: LineConfig) -> None:
        with self._lock:
            self.state.line = line
            self.storage.set_setting(
                self.LINE_SETTING_KEY,
                json.dumps(line.as_dict()),
            )
            self._reset_line_zone()
            self._tracked_ids_in.clear()
            self._tracked_ids_out.clear()
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

    def _restore_line(self) -> None:
        raw = self.storage.get_setting(self.LINE_SETTING_KEY)
        if raw:
            try:
                data = json.loads(raw)
                self.state.line = LineConfig(**data)
                return
            except Exception:
                log.warning("Could not parse stored line config; using defaults.")
        self.state.line = LineConfig(
            self.settings.default_line_x1,
            self.settings.default_line_y1,
            self.settings.default_line_x2,
            self.settings.default_line_y2,
        )

    def _reset_line_zone(self) -> None:
        self._line_zone = None
        self._line_dirty = True

    def _build_line_zone(self, width: int, height: int) -> sv.LineZone:
        start, end = self.state.line.to_pixels(width, height)
        return sv.LineZone(
            start=start,
            end=end,
            minimum_crossing_threshold=self.settings.min_crossing_threshold,
        )

    def _load_model(self) -> YOLO:
        import os

        device = _resolve_device(self.settings.device)
        self.state.status.device = device

        candidates = [self.settings.model_name]
        if self.settings.fallback_model_name not in candidates:
            candidates.append(self.settings.fallback_model_name)

        models_dir = self.settings.models_dir
        prev_cwd = os.getcwd()
        last_err: Exception | None = None

        try:
            # Ultralytics downloads weights into CWD; chdir into models dir
            # so any auto-download lands there alongside cached weights.
            os.chdir(models_dir)
            for name in candidates:
                try:
                    log.info("Loading YOLO model %s on %s", name, device)
                    weight_path = models_dir / name
                    model_arg = str(weight_path) if weight_path.is_file() else name
                    model = YOLO(model_arg)
                    dummy = np.zeros(
                        (self.settings.image_size, self.settings.image_size, 3),
                        dtype=np.uint8,
                    )
                    model.predict(
                        dummy,
                        device=device,
                        imgsz=self.settings.image_size,
                        verbose=False,
                    )
                    self.state.status.model_name = name
                    return model
                except Exception as e:  # noqa: BLE001
                    log.warning("Failed to load model %s: %s", name, e)
                    last_err = e
        finally:
            try:
                os.chdir(prev_cwd)
            except OSError:
                pass

        raise RuntimeError(f"Could not load any YOLO model: {last_err}")

    def _run(self) -> None:
        log.info("Counter thread starting")
        try:
            model = self._load_model()
            self.state.status.model_loaded = True
        except Exception as e:  # noqa: BLE001
            log.exception("Model load failed")
            self.state.status.last_error = f"model: {e}"
            self._broadcast_state()
            return

        self._broadcast_state()

        while not self._stop_event.is_set():
            try:
                self._inference_loop(model)
            except Exception as e:  # noqa: BLE001
                log.exception("Inference loop crashed; restarting in 2s")
                self.state.status.last_error = str(e)
                self.state.status.camera_open = False
                self.state.status.running = False
                self._broadcast_state()
                # Avoid hot loop on persistent failures.
                if self._stop_event.wait(2.0):
                    break

        log.info("Counter thread exiting")

    def _inference_loop(self, model: YOLO) -> None:
        cap = _open_capture(
            self.settings.source,
            self.settings.capture_width,
            self.settings.capture_height,
            self.settings.target_fps,
        )
        try:
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.settings.capture_width
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.settings.capture_height
            self.state.status.frame_width = actual_w
            self.state.status.frame_height = actual_h
            self.state.status.camera_open = True
            self.state.status.running = True
            self.state.status.last_error = None
            self._broadcast_state()

            box_annotator = sv.BoxAnnotator(thickness=2)
            label_annotator = sv.LabelAnnotator(text_scale=0.5, text_thickness=1)
            line_annotator = sv.LineZoneAnnotator(
                thickness=3, text_thickness=2, text_scale=0.7
            )
            trace_annotator = sv.TraceAnnotator(thickness=2, trace_length=30)

            line_zone: sv.LineZone | None = None
            cached_line_dim = (-1, -1)
            cached_line_cfg: dict[str, float] | None = None

            consecutive_fails = 0
            fps_window: list[float] = []
            last_t = time.time()

            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    consecutive_fails += 1
                    log.warning("Camera read failed (%s)", consecutive_fails)
                    if consecutive_fails >= self.settings.max_camera_failures:
                        raise RuntimeError("Too many consecutive camera read failures")
                    if self._stop_event.wait(0.05):
                        break
                    continue
                consecutive_fails = 0

                if self.settings.mirror:
                    frame = cv2.flip(frame, 1)

                fh, fw = frame.shape[:2]
                current_cfg = self.state.line.as_dict()
                if (
                    line_zone is None
                    or cached_line_dim != (fw, fh)
                    or cached_line_cfg != current_cfg
                    or getattr(self, "_line_dirty", False)
                ):
                    line_zone = self._build_line_zone(fw, fh)
                    cached_line_dim = (fw, fh)
                    cached_line_cfg = current_cfg
                    self._line_dirty = False

                # Only run heavy detection when we're actively counting OR a preview
                # client is watching. We always run detection here because the preview
                # is the source of truth for what's about to be counted.
                results = model.track(
                    frame,
                    persist=True,
                    tracker="bytetrack.yaml",
                    classes=[self.settings.person_class_id],
                    conf=self.settings.confidence,
                    iou=self.settings.iou,
                    imgsz=self.settings.image_size,
                    device=self.state.status.device,
                    verbose=False,
                )
                detections = sv.Detections.from_ultralytics(results[0])

                # Filter: tracker_id is required for LineZone counting.
                if detections.tracker_id is None:
                    detections = detections[np.array([], dtype=int)]
                else:
                    detections = detections[detections.tracker_id != None]  # noqa: E711

                annotated = frame.copy()
                if len(detections) > 0:
                    annotated = trace_annotator.annotate(annotated, detections)
                    annotated = box_annotator.annotate(annotated, detections)
                    labels = self._make_labels(detections)
                    annotated = label_annotator.annotate(annotated, detections, labels=labels)

                # Trigger line zone always so counts update; we conditionally
                # persist them (and emit events) only when a session is active.
                crossed_in, crossed_out = line_zone.trigger(detections)
                annotated = line_annotator.annotate(annotated, line_zone)

                if self._counting_event.is_set() and self.state.session_id:
                    new_in_ids = self._collect_crossed_ids(detections, crossed_in, self._tracked_ids_in)
                    new_out_ids = self._collect_crossed_ids(detections, crossed_out, self._tracked_ids_out)
                    if new_in_ids or new_out_ids:
                        self._record_crossings(new_in_ids, new_out_ids)

                self._encode_preview(annotated)

                # FPS tracking.
                now = time.time()
                fps_window.append(now - last_t)
                last_t = now
                if len(fps_window) > 30:
                    fps_window = fps_window[-30:]
                avg = sum(fps_window) / len(fps_window) if fps_window else 0
                self.state.status.fps = (1.0 / avg) if avg > 0 else 0.0
                self.state.status.last_frame_at = now

        finally:
            cap.release()
            self.state.status.camera_open = False
            self.state.status.running = False
            self._broadcast_state()

    @staticmethod
    def _collect_crossed_ids(
        detections: sv.Detections,
        crossed_mask: Iterable[bool],
        seen: set[int],
    ) -> list[int]:
        if detections.tracker_id is None:
            return []
        new_ids: list[int] = []
        for tracker_id, crossed in zip(detections.tracker_id, crossed_mask):
            if crossed and tracker_id is not None:
                tid = int(tracker_id)
                if tid not in seen:
                    seen.add(tid)
                    new_ids.append(tid)
        return new_ids

    def _record_crossings(self, new_in: list[int], new_out: list[int]) -> None:
        if not self.state.session_id:
            return
        events: list[dict[str, Any]] = []
        with self._lock:
            for tid in new_in:
                self.state.in_count += 1
                event = self.storage.append_event(self.state.session_id, "in", tid)
                events.append(event)
            for tid in new_out:
                self.state.out_count += 1
                event = self.storage.append_event(self.state.session_id, "out", tid)
                events.append(event)

            inside = self.state.inside
            if inside > self.state.peak_inside:
                self.state.peak_inside = inside

            self.storage.update_session_counts(
                self.state.session_id,
                self.state.in_count,
                self.state.out_count,
                self.state.peak_inside,
            )

        for event in events:
            self._broadcast_event(event)
        self._broadcast_state()

    def _encode_preview(self, frame: np.ndarray) -> None:
        # Resize down to ~1280px-wide for cheaper encoding if necessary.
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
                cb(event)
            except Exception:
                log.exception("Event listener failed")

    def _broadcast_state(self) -> None:
        snapshot = self.get_state_dict()
        with self._lock:
            listeners = list(self._state_listeners)
        for cb in listeners:
            try:
                cb(snapshot)
            except Exception:
                log.exception("State listener failed")
