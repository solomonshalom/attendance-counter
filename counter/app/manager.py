"""CameraManager: orchestrates multiple per-camera Counter instances."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

from .config import Settings
from .counter import (
    Counter,
    EventCallback,
    LineConfig,
    StateCallback,
    ZoneConfig,
    _looks_like_video_file,
)
from .storage import Storage

log = logging.getLogger("counter.manager")

CameraListCallback = Callable[[list[dict[str, Any]]], None]


def _normalize_source(source: str) -> str:
    return (source or "").strip()


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("Camera name is required")
    if len(name) > 80:
        raise ValueError("Camera name is too long (max 80 characters)")
    return name


def _validate_source(source: str) -> str:
    source = _normalize_source(source)
    if not source:
        raise ValueError("Camera source is required")
    if len(source) > 500:
        raise ValueError("Camera source is too long (max 500 characters)")
    return source


def _resolve_kind(kind: str | None, source: str) -> str:
    if kind in ("camera", "video", "rtsp"):
        return kind
    if source.startswith(("rtsp://", "rtmp://", "http://", "https://")):
        return "rtsp"
    if _looks_like_video_file(source):
        return "video"
    return "camera"


class CameraManager:
    """Owns and supervises one Counter per configured camera."""

    LEGACY_LINE_KEY = "line_config"

    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self._lock = threading.RLock()
        self._counters: dict[str, Counter] = {}

        self._event_listeners: list[EventCallback] = []
        self._state_listeners: list[StateCallback] = []
        self._camera_list_listeners: list[CameraListCallback] = []

        self._bootstrap_default_if_empty()
        self._load_existing_cameras()

    # ------------------------------------------------------------ listeners

    def add_event_listener(self, cb: EventCallback) -> None:
        with self._lock:
            self._event_listeners.append(cb)
            for counter in self._counters.values():
                counter.add_event_listener(cb)

    def add_state_listener(self, cb: StateCallback) -> None:
        with self._lock:
            self._state_listeners.append(cb)
            for counter in self._counters.values():
                counter.add_state_listener(cb)

    def add_camera_list_listener(self, cb: CameraListCallback) -> None:
        with self._lock:
            self._camera_list_listeners.append(cb)

    # -------------------------------------------------------------- queries

    def list_cameras(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.storage.list_cameras()
            out: list[dict[str, Any]] = []
            for row in rows:
                counter = self._counters.get(row["id"])
                snapshot = counter.get_state_dict() if counter else self._row_state_snapshot(row)
                out.append(snapshot)
            return out

    def get_state(self, camera_id: str) -> dict[str, Any] | None:
        with self._lock:
            counter = self._counters.get(camera_id)
            if counter is not None:
                return counter.get_state_dict()
            row = self.storage.get_camera(camera_id)
            return self._row_state_snapshot(row) if row else None

    def get_counter(self, camera_id: str) -> Counter | None:
        with self._lock:
            return self._counters.get(camera_id)

    def primary_camera_id(self) -> str | None:
        with self._lock:
            rows = self.storage.list_cameras()
            return rows[0]["id"] if rows else None

    # --------------------------------------------------------------- mutations

    def create(
        self,
        name: str,
        source: str,
        mirror: bool = False,
        enabled: bool = True,
        autostart: bool = True,
        kind: str | None = None,
        confidence: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
        tracker: str | None = None,
        loop_video: bool | None = None,
    ) -> dict[str, Any]:
        name = _validate_name(name)
        source = _validate_source(source)
        will_start = bool(enabled and autostart)
        with self._lock:
            existing = self.storage.get_camera_by_source(source)
            if existing is not None:
                raise ValueError(
                    f"A camera with source '{source}' already exists ({existing['name']})."
                )

            row = self.storage.create_camera(
                name=name,
                source=source,
                mirror=mirror,
                enabled=will_start,
                kind=_resolve_kind(kind, source),
                confidence=confidence,
                iou=iou,
                imgsz=imgsz,
                tracker=tracker,
                loop_video=True if loop_video is None else loop_video,
            )
            counter = self._instantiate(row)
            self._counters[row["id"]] = counter
            for cb in self._event_listeners:
                counter.add_event_listener(cb)
            for cb in self._state_listeners:
                counter.add_state_listener(cb)

            if will_start:
                counter.start()

        self._broadcast_camera_list()
        return self.get_state(row["id"]) or row

    def update(
        self,
        camera_id: str,
        *,
        name: str | None = None,
        source: str | None = None,
        kind: str | None = None,
        mirror: bool | None = None,
        enabled: bool | None = None,
        confidence: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
        tracker: str | None = None,
        loop_video: bool | None = None,
        paused: bool | None = None,
    ) -> dict[str, Any]:
        if name is not None:
            name = _validate_name(name)
        if source is not None:
            source = _validate_source(source)

        with self._lock:
            existing = self.storage.get_camera(camera_id)
            if existing is None:
                raise KeyError(camera_id)

            if source is not None and source != existing["source"]:
                duplicate = self.storage.get_camera_by_source(source)
                if duplicate is not None and duplicate["id"] != camera_id:
                    raise ValueError(
                        f"A camera with source '{source}' already exists ({duplicate['name']})."
                    )

            counter = self._counters.get(camera_id)

            # Source or kind changes need a capture re-open.
            source_changed = source is not None and source != existing["source"]
            kind_changed = kind is not None and kind != existing.get("kind")
            tracker_changed = tracker is not None and tracker != existing.get("tracker")
            imgsz_changed = imgsz is not None and imgsz != existing.get("imgsz")

            requires_restart = source_changed or kind_changed or tracker_changed or imgsz_changed
            was_running = counter.is_running() if counter else False

            if requires_restart and counter is not None and was_running:
                counter.stop()

            resolved_kind = _resolve_kind(kind if kind is not None else existing.get("kind"),
                                          source if source is not None else existing["source"])

            self.storage.update_camera(
                camera_id,
                name=name,
                source=source,
                kind=resolved_kind if (source_changed or kind_changed) else None,
                mirror=mirror,
                enabled=enabled,
                confidence=confidence,
                iou=iou,
                imgsz=imgsz,
                tracker=tracker,
                loop_video=loop_video,
                paused=paused,
            )

            updated = self.storage.get_camera(camera_id)
            assert updated is not None

            if counter is None or requires_restart:
                if counter is not None:
                    self._counters.pop(camera_id, None)
                counter = self._instantiate(updated)
                self._counters[camera_id] = counter
                for cb in self._event_listeners:
                    counter.add_event_listener(cb)
                for cb in self._state_listeners:
                    counter.add_state_listener(cb)
            else:
                counter.update_meta(
                    name=name,
                    mirror=mirror,
                    enabled=enabled,
                    kind=resolved_kind if kind_changed else None,
                    confidence=confidence,
                    iou=iou,
                    tracker=tracker,
                    loop_video=loop_video,
                    paused=paused,
                )

            if requires_restart and was_running and updated["enabled"]:
                counter.start()

            if enabled is False and counter.is_running():
                counter.stop()

        self._broadcast_camera_list()
        return self.get_state(camera_id) or updated

    def delete(self, camera_id: str) -> bool:
        with self._lock:
            counter = self._counters.pop(camera_id, None)
            if counter is not None:
                try:
                    counter.stop()
                except Exception:
                    log.exception("Error stopping camera %s during delete", camera_id)
            removed = self.storage.delete_camera(camera_id)
        if removed:
            self._broadcast_camera_list()
        return removed

    def start(self, camera_id: str) -> dict[str, Any]:
        with self._lock:
            counter = self._counters.get(camera_id)
            if counter is None:
                raise KeyError(camera_id)
            if not counter.is_running():
                counter.state.status.last_error = None
                counter.start()
            self.storage.update_camera(camera_id, enabled=True, paused=False)
            counter.update_meta(enabled=True, paused=False)
        self._broadcast_camera_list()
        state = self.get_state(camera_id)
        assert state is not None
        return state

    def stop(self, camera_id: str) -> dict[str, Any]:
        with self._lock:
            counter = self._counters.get(camera_id)
            if counter is None:
                raise KeyError(camera_id)
            if counter.is_running():
                counter.stop()
            self.storage.update_camera(camera_id, enabled=False)
            counter.update_meta(enabled=False)
        self._broadcast_camera_list()
        state = self.get_state(camera_id)
        assert state is not None
        return state

    def playback(self, camera_id: str, action: str) -> dict[str, Any]:
        """Play / pause / restart a video-source camera."""
        with self._lock:
            counter = self._counters.get(camera_id)
            if counter is None:
                raise KeyError(camera_id)
            if action == "play":
                self.storage.update_camera(camera_id, paused=False)
                counter.update_meta(paused=False)
            elif action == "pause":
                self.storage.update_camera(camera_id, paused=True)
                counter.update_meta(paused=True)
            elif action == "restart":
                self.storage.update_camera(camera_id, paused=False)
                counter.update_meta(paused=False)
                counter.request_restart()
            else:
                raise ValueError(f"Unknown playback action: {action}")
        return self.get_state(camera_id) or {}

    def shutdown(self) -> None:
        with self._lock:
            counters = list(self._counters.values())
            self._counters.clear()
        for counter in counters:
            try:
                counter.stop()
            except Exception:
                log.exception("Error stopping counter on shutdown")

    # ----------------------------------------------------------------- helpers

    def _instantiate(self, row: dict[str, Any]) -> Counter:
        primary_line = LineConfig.from_dict(row["line"])
        # Read full lines list; fall back to primary line if list is empty.
        line_rows = row.get("lines") or []
        if line_rows:
            lines = [LineConfig.from_dict(ln) for ln in line_rows]
        else:
            lines = [primary_line]
        zones = [ZoneConfig.from_dict(z) for z in row.get("zones", [])]
        return Counter(
            camera_id=row["id"],
            name=row["name"],
            source=row["source"],
            mirror=bool(row["mirror"]),
            line=lines[0],
            lines=lines,
            zones=zones,
            settings=self.settings,
            storage=self.storage,
            enabled=bool(row["enabled"]),
            kind=row.get("kind") or "camera",
            confidence=row.get("confidence"),
            iou=row.get("iou"),
            imgsz=row.get("imgsz"),
            tracker=row.get("tracker"),
            loop_video=bool(row.get("loop_video", True)),
        )

    def _row_state_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "camera_id": row["id"],
            "camera_name": row["name"],
            "source": row["source"],
            "kind": row.get("kind", "camera"),
            "mirror": bool(row["mirror"]),
            "enabled": bool(row["enabled"]),
            "session_id": None,
            "session_label": "",
            "session_started_at": None,
            "in_count": 0,
            "out_count": 0,
            "inside": 0,
            "peak_inside": 0,
            "line": row["line"],
            "lines": row.get("lines", []),
            "zones": row.get("zones", []),
            "zone_counts": {},
            "confidence": row.get("confidence"),
            "iou": row.get("iou"),
            "imgsz": row.get("imgsz"),
            "tracker": row.get("tracker"),
            "loop_video": bool(row.get("loop_video", True)),
            "paused": bool(row.get("paused", False)),
            "status": {
                "running": False,
                "paused": bool(row.get("paused", False)),
                "model_loaded": False,
                "camera_open": False,
                "fps": 0.0,
                "last_error": None,
                "frame_width": 0,
                "frame_height": 0,
                "device": "",
                "model_name": "",
                "tracker": "",
                "last_frame_at": 0.0,
                "is_video_file": (row.get("kind") == "video"),
                "video_total_frames": 0,
                "video_position": 0,
                "video_fps": 0.0,
            },
        }

    def _bootstrap_default_if_empty(self) -> None:
        if self.storage.list_cameras():
            return
        legacy_line = None
        raw = self.storage.get_setting(self.LEGACY_LINE_KEY)
        if raw:
            try:
                legacy_line = json.loads(raw)
            except Exception:
                log.warning("Could not parse legacy line_config; ignoring.")
        line = legacy_line or {
            "x1": self.settings.default_line_x1,
            "y1": self.settings.default_line_y1,
            "x2": self.settings.default_line_x2,
            "y2": self.settings.default_line_y2,
        }
        try:
            self.storage.create_camera(
                name="Default camera",
                source=self.settings.source,
                mirror=self.settings.mirror,
                enabled=True,
                line=line,
            )
            log.info(
                "Bootstrapped default camera (source=%s, mirror=%s)",
                self.settings.source, self.settings.mirror,
            )
        except Exception:
            log.exception("Failed to bootstrap default camera")

    def _load_existing_cameras(self) -> None:
        for row in self.storage.list_cameras():
            counter = self._instantiate(row)
            self._counters[row["id"]] = counter
            if row["enabled"]:
                counter.start()

    def _broadcast_camera_list(self) -> None:
        with self._lock:
            listeners = list(self._camera_list_listeners)
        snapshot = self.list_cameras()
        for cb in listeners:
            try:
                cb(snapshot)
            except Exception:
                log.exception("Camera-list listener failed")
