"""CameraManager: orchestrates multiple per-camera Counter instances."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

import numpy as np

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


class VenueDeduper:
    """In-memory rolling window of recent crossings, used to suppress duplicate
    counts when two cameras in the same venue see the same person crossing.

    The match rule is: same direction + same world position (within the venue's
    `dedup_radius_m`) + within the venue's `dedup_window_s` + DIFFERENT camera.
    The first event wins; the second is dropped (and persisted with deduped=1
    so analytics can still see what happened).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # venue_id -> deque of {camera_id, kind, ts, world_x, world_y}
        self._buffers: dict[str, deque[dict[str, Any]]] = {}
        # venue_id -> {dedup_window_s, dedup_radius_m}
        self._venues: dict[str, dict[str, float]] = {}

    def upsert_venue(self, venue: dict[str, Any]) -> None:
        with self._lock:
            self._venues[venue["id"]] = {
                "dedup_window_s": float(venue.get("dedup_window_s", 3.0)),
                "dedup_radius_m": float(venue.get("dedup_radius_m", 1.0)),
            }
            self._buffers.setdefault(venue["id"], deque(maxlen=2048))

    def remove_venue(self, venue_id: str) -> None:
        with self._lock:
            self._venues.pop(venue_id, None)
            self._buffers.pop(venue_id, None)

    def is_duplicate(
        self,
        venue_id: str,
        camera_id: str,
        kind: str,
        world_x: float | None,
        world_y: float | None,
        ts: float,
    ) -> bool:
        if venue_id is None or world_x is None or world_y is None:
            return False
        with self._lock:
            cfg = self._venues.get(venue_id)
            if cfg is None:
                return False
            window = cfg["dedup_window_s"]
            radius = cfg["dedup_radius_m"]
            buf = self._buffers.get(venue_id)
            if buf is None:
                return False
            for ev in buf:
                if ev["camera_id"] == camera_id:
                    continue
                if ev["kind"] != kind:
                    continue
                if ts - ev["ts"] > window:
                    continue
                dx = ev["world_x"] - world_x
                dy = ev["world_y"] - world_y
                if (dx * dx + dy * dy) <= radius * radius:
                    return True
            return False

    def record(
        self,
        venue_id: str,
        camera_id: str,
        kind: str,
        world_x: float,
        world_y: float,
        ts: float,
    ) -> None:
        with self._lock:
            buf = self._buffers.setdefault(venue_id, deque(maxlen=2048))
            buf.append(
                {
                    "camera_id": camera_id,
                    "kind": kind,
                    "ts": ts,
                    "world_x": world_x,
                    "world_y": world_y,
                }
            )
            # Prune anything older than the longest dedup_window we've ever seen.
            cutoff = ts - max(60.0, max(
                (v.get("dedup_window_s", 3.0) for v in self._venues.values()),
                default=3.0,
            ))
            while buf and buf[0]["ts"] < cutoff:
                buf.popleft()


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
        self._deduper = VenueDeduper()

        self._event_listeners: list[EventCallback] = []
        self._state_listeners: list[StateCallback] = []
        self._camera_list_listeners: list[CameraListCallback] = []

        # Hydrate dedupe service with current venues.
        for v in self.storage.list_venues():
            self._deduper.upsert_venue(v)

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

    # ------------------------------------------------------------------ venues

    def list_venues(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.storage.list_venues()

    def get_venue(self, venue_id: str) -> dict[str, Any] | None:
        with self._lock:
            v = self.storage.get_venue(venue_id)
            if not v:
                return None
            v["cameras"] = [
                {
                    "camera_id": c["id"],
                    "name": c["name"],
                    "calibrated": self.storage.get_calibration(c["id"]) is not None
                              and self.storage.get_calibration(c["id"]).get("homography") is not None,
                }
                for c in self.storage.list_cameras_in_venue(venue_id)
            ]
            return v

    def create_venue(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            venue = self.storage.create_venue(**kwargs)
        self._deduper.upsert_venue(venue)
        return venue

    def update_venue(self, venue_id: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            venue = self.storage.update_venue(venue_id, **kwargs)
        if not venue:
            raise KeyError(venue_id)
        self._deduper.upsert_venue(venue)
        return venue

    def delete_venue(self, venue_id: str) -> bool:
        with self._lock:
            removed = self.storage.delete_venue(venue_id)
            if removed:
                # Re-instantiate any counters that were assigned to this venue
                # so their dedupe hooks (which captured the now-deleted venue_id)
                # are detached.
                for cam_id, counter in list(self._counters.items()):
                    if counter.state.venue_id == venue_id:
                        was_running = counter.is_running()
                        if was_running:
                            counter.stop()
                        self._counters.pop(cam_id, None)
                        row = self.storage.get_camera(cam_id)
                        if row is not None:
                            new_counter = self._instantiate(row)
                            self._counters[cam_id] = new_counter
                            for cb in self._event_listeners:
                                new_counter.add_event_listener(cb)
                            for cb in self._state_listeners:
                                new_counter.add_state_listener(cb)
                            if was_running and row["enabled"]:
                                new_counter.start()
        if removed:
            self._deduper.remove_venue(venue_id)
            self._broadcast_camera_list()
        return removed

    def assign_camera_to_venue(self, camera_id: str, venue_id: str | None) -> dict[str, Any]:
        """Move a camera into / out of a venue. Triggers a counter rebuild so
        the new dedupe wiring takes effect."""
        with self._lock:
            row = self.storage.get_camera(camera_id)
            if row is None:
                raise KeyError(camera_id)
            self.storage.assign_camera_to_venue(camera_id, venue_id)
            counter = self._counters.pop(camera_id, None)
            was_running = counter.is_running() if counter else False
            if counter is not None:
                counter.stop()
            updated = self.storage.get_camera(camera_id)
            assert updated is not None
            new_counter = self._instantiate(updated)
            self._counters[camera_id] = new_counter
            for cb in self._event_listeners:
                new_counter.add_event_listener(cb)
            for cb in self._state_listeners:
                new_counter.add_state_listener(cb)
            if was_running and updated["enabled"]:
                new_counter.start()
        self._broadcast_camera_list()
        return self.get_state(camera_id) or {}

    # -------------------------------------------------------- calibration

    def get_calibration(self, camera_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self.storage.get_calibration(camera_id)

    def set_calibration(
        self, camera_id: str, points: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compute homography from `points` and persist alongside the points.

        Each point is `{img: {x, y}, world: {x, y}}` where `img` is normalized
        [0,1] and `world` is meters in the venue's floor plan. We compute H
        directly in NORMALIZED image coords → meters, so the calibration is
        independent of the camera's pixel resolution. At inference time the
        bbox anchor is normalized by the current frame size before applying H,
        meaning a camera can change resolution (or even be replaced with a
        different physical camera at the same vantage point) without breaking
        the world-position math.

        Reprojection error > 2 m is rejected with a 400 — the dedupe radius is
        ≤ 10 m by API limit, so a 2 m+ fit would silently kill cross-camera
        dedupe accuracy.
        """
        if len(points) < 4:
            raise ValueError("Calibration needs at least 4 point pairs")

        try:
            import cv2  # local import keeps cold-start light
        except ImportError as e:
            raise RuntimeError("opencv missing") from e

        img_norm = np.array(
            [[float(p["img"]["x"]), float(p["img"]["y"])] for p in points],
            dtype=np.float32,
        )
        world = np.array(
            [[float(p["world"]["x"]), float(p["world"]["y"])] for p in points],
            dtype=np.float32,
        )

        # Reject geometrically-poor point sets. With exactly 4 pairs,
        # findHomography fits ANY 4 points perfectly (8 DOF, 8 constraints).
        # The H can be valid mathematically but useless in practice if the
        # input points are clustered or near-collinear.
        #   1. Spread per axis ≥ 30% of frame.
        #   2. Convex-hull area ≥ 0.03 of the unit square (rejects near-
        #      collinear sets where 3 of the 4 points sit on one line).
        img_span_x = float(img_norm[:, 0].max() - img_norm[:, 0].min())
        img_span_y = float(img_norm[:, 1].max() - img_norm[:, 1].min())
        if img_span_x < 0.3 or img_span_y < 0.3:
            raise ValueError(
                f"Calibration points are clustered (spread {img_span_x:.0%} × "
                f"{img_span_y:.0%} of the frame). Spread them to at least 4 "
                f"corners of the visible area so the homography reflects the "
                f"real viewing angle."
            )

        # Minimum triangle area across all 3-point subsets. Three points on a
        # single line yield triangle area 0; this catches sets where 3+ of
        # the points are collinear even though the 4th adds spread.
        from itertools import combinations
        def _tri_area(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
            return 0.5 * abs(
                (p2[0] - p1[0]) * (p3[1] - p1[1])
                - (p3[0] - p1[0]) * (p2[1] - p1[1])
            )

        min_tri = min(
            _tri_area(*combo) for combo in combinations(img_norm.tolist(), 3)
        )
        if min_tri < 0.005:
            raise ValueError(
                f"Three or more calibration points lie on a single line "
                f"(min triangle area {min_tri:.4f}). Pick landmarks that form "
                f"a proper quadrilateral on the floor — e.g. corners of a "
                f"rug, base of pillars, or doorway corners."
            )

        # RANSAC threshold is in normalized image units now, not pixels.
        # 0.01 ≈ 1% of frame width, ≈ 19 px on a 1920-wide frame — generous
        # enough to absorb hand-marked imprecision but tight enough to flag
        # mismarked landmarks.
        H, mask = cv2.findHomography(
            img_norm, world, method=cv2.RANSAC, ransacReprojThreshold=0.01
        )
        if H is None:
            raise ValueError(
                "Could not compute homography from these points. Make sure they "
                "lie on a single plane (the floor) and aren't collinear."
            )

        projected = cv2.perspectiveTransform(img_norm.reshape(-1, 1, 2), H).reshape(-1, 2)
        errors = np.linalg.norm(projected - world, axis=1)
        mean_error_m = float(errors.mean())

        if mean_error_m > 2.0:
            raise ValueError(
                f"Calibration fit is too loose (mean error {mean_error_m:.2f} m). "
                "Re-mark points: pick landmarks that are clearly on the floor "
                "(not at different heights) and avoid clustering them in one "
                "corner of the frame."
            )

        with self._lock:
            calib = self.storage.save_calibration(
                camera_id=camera_id,
                points=points,
                homography=[float(x) for x in H.flatten().tolist()],
                reprojection_error_m=mean_error_m,
            )
            # Rebuild counter with the new homography in place.
            counter = self._counters.pop(camera_id, None)
            was_running = counter.is_running() if counter else False
            if counter is not None:
                counter.stop()
            row = self.storage.get_camera(camera_id)
            if row is not None:
                new_counter = self._instantiate(row)
                self._counters[camera_id] = new_counter
                for cb in self._event_listeners:
                    new_counter.add_event_listener(cb)
                for cb in self._state_listeners:
                    new_counter.add_state_listener(cb)
                if was_running and row["enabled"]:
                    new_counter.start()
        return calib

    def clear_calibration(self, camera_id: str) -> bool:
        with self._lock:
            removed = self.storage.delete_calibration(camera_id)
            counter = self._counters.pop(camera_id, None)
            was_running = counter.is_running() if counter else False
            if counter is not None:
                counter.stop()
            row = self.storage.get_camera(camera_id)
            if row is not None:
                new_counter = self._instantiate(row)
                self._counters[camera_id] = new_counter
                for cb in self._event_listeners:
                    new_counter.add_event_listener(cb)
                for cb in self._state_listeners:
                    new_counter.add_state_listener(cb)
                if was_running and row["enabled"]:
                    new_counter.start()
        return removed

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

        # Pull homography for this camera (if calibrated) so the counter can
        # convert pixel anchors to world coordinates at crossing time.
        homography = None
        calib = self.storage.get_calibration(row["id"])
        if calib and calib.get("homography"):
            try:
                homography = np.array(calib["homography"], dtype=np.float64).reshape(3, 3)
            except Exception:
                log.exception("Bad homography for camera %s; ignoring", row["id"])
                homography = None

        venue_id = row.get("venue_id")

        counter = Counter(
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
            venue_id=venue_id,
            homography=homography,
        )

        # Wire dedupe callbacks: counter consults the deduper before persisting
        # an event from a calibrated camera in a venue.
        if venue_id and homography is not None:
            counter.set_dedupe_hooks(
                is_duplicate=lambda kind, wx, wy, ts: self._deduper.is_duplicate(
                    venue_id, row["id"], kind, wx, wy, ts
                ),
                record=lambda kind, wx, wy, ts: self._deduper.record(
                    venue_id, row["id"], kind, wx, wy, ts
                ),
            )
        return counter

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
