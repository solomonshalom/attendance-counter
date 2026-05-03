"""FastAPI app exposing the multi-camera counter via REST + WebSocket + MJPEG."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .broadcaster import Broadcaster
from .config import settings
from .counter import Counter, LineConfig, ZoneConfig, VIDEO_EXTENSIONS
from .manager import CameraManager
from .schemas import (
    AssignVenueRequest,
    CalibrationRequest,
    CreateCameraRequest,
    CreateVenueRequest,
    HealthResponse,
    LineRequest,
    LinesRequest,
    PlaybackRequest,
    SessionResponse,
    StartSessionRequest,
    UpdateCameraRequest,
    UpdateSessionRequest,
    UpdateVenueRequest,
    ZonesRequest,
)
from .storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("counter.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = Storage(settings.db_path)
    manager = CameraManager(settings, storage)
    broadcaster = Broadcaster(queue_size=settings.ws_queue_size)
    broadcaster.attach_loop(asyncio.get_running_loop())

    def on_event(camera_id: str, event: dict[str, Any]) -> None:
        broadcaster.publish_threadsafe(
            {"type": "event", "camera_id": camera_id, "data": event}
        )

    def on_state(camera_id: str, state: dict[str, Any]) -> None:
        broadcaster.publish_threadsafe(
            {"type": "state", "camera_id": camera_id, "data": state}
        )

    def on_camera_list(cameras: list[dict[str, Any]]) -> None:
        broadcaster.publish_threadsafe({"type": "cameras", "data": cameras})

    manager.add_event_listener(on_event)
    manager.add_state_listener(on_state)
    manager.add_camera_list_listener(on_camera_list)

    app.state.storage = storage
    app.state.manager = manager
    app.state.broadcaster = broadcaster

    log.info("Counter API ready on %s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        log.info("Shutting down counter")
        manager.shutdown()


app = FastAPI(title="Attendance Counter", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _manager(app: FastAPI) -> CameraManager:
    return app.state.manager  # type: ignore[no-any-return]


def _storage(app: FastAPI) -> Storage:
    return app.state.storage  # type: ignore[no-any-return]


def _broadcaster(app: FastAPI) -> Broadcaster:
    return app.state.broadcaster  # type: ignore[no-any-return]


def _require_counter(camera_id: str) -> Counter:
    counter = _manager(app).get_counter(camera_id)
    if counter is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return counter


# ---------------------------------------------------------------------- health


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    cameras = _manager(app).list_cameras()
    return HealthResponse(ok=True, version=__version__, cameras=len(cameras))


# ----------------------------------------------------------------- cameras


@app.get("/api/cameras")
async def list_cameras() -> list[dict[str, Any]]:
    return _manager(app).list_cameras()


@app.post("/api/cameras", status_code=201)
async def create_camera(req: CreateCameraRequest) -> dict[str, Any]:
    try:
        return _manager(app).create(
            name=req.name,
            source=req.source,
            mirror=req.mirror,
            enabled=req.enabled,
            autostart=req.autostart,
            kind=req.kind,
            confidence=req.confidence,
            iou=req.iou,
            imgsz=req.imgsz,
            tracker=req.tracker,
            loop_video=req.loop_video,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/cameras/{camera_id}")
async def get_camera(camera_id: str) -> dict[str, Any]:
    state = _manager(app).get_state(camera_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return state


@app.patch("/api/cameras/{camera_id}")
async def update_camera(camera_id: str, req: UpdateCameraRequest) -> dict[str, Any]:
    try:
        return _manager(app).update(
            camera_id,
            name=req.name,
            source=req.source,
            kind=req.kind,
            mirror=req.mirror,
            enabled=req.enabled,
            confidence=req.confidence,
            iou=req.iou,
            imgsz=req.imgsz,
            tracker=req.tracker,
            loop_video=req.loop_video,
            paused=req.paused,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Camera not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/cameras/{camera_id}")
async def delete_camera(camera_id: str) -> dict[str, str]:
    removed = _manager(app).delete(camera_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"status": "deleted"}


@app.post("/api/cameras/{camera_id}/start")
async def start_camera(camera_id: str) -> dict[str, Any]:
    try:
        return _manager(app).start(camera_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Camera not found")


@app.post("/api/cameras/{camera_id}/stop")
async def stop_camera(camera_id: str) -> dict[str, Any]:
    try:
        return _manager(app).stop(camera_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Camera not found")


@app.post("/api/cameras/{camera_id}/playback")
async def camera_playback(camera_id: str, req: PlaybackRequest) -> dict[str, Any]:
    try:
        return _manager(app).playback(camera_id, req.action)
    except KeyError:
        raise HTTPException(status_code=404, detail="Camera not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -------------------------------------------------------- per-camera session


@app.post("/api/cameras/{camera_id}/session/start")
async def camera_start_session(
    camera_id: str, req: StartSessionRequest
) -> dict[str, Any]:
    counter = _require_counter(camera_id)
    if not counter.is_running():
        raise HTTPException(
            status_code=409,
            detail="Camera is not running. Start the camera first.",
        )
    if not counter.state.status.model_loaded:
        raise HTTPException(
            status_code=409,
            detail="Camera is starting up. Try again in a few seconds.",
        )
    return counter.start_session(label=req.label)


@app.post("/api/cameras/{camera_id}/session/stop")
async def camera_stop_session(camera_id: str) -> dict[str, Any]:
    counter = _require_counter(camera_id)
    session = counter.stop_session()
    if session is None:
        raise HTTPException(status_code=409, detail="No active session for this camera")
    return session


@app.post("/api/cameras/{camera_id}/session/reset")
async def camera_reset_counts(camera_id: str) -> dict[str, Any]:
    counter = _require_counter(camera_id)
    counter.reset_counts()
    return counter.get_state_dict()


@app.post("/api/cameras/{camera_id}/line")
async def camera_set_line(camera_id: str, req: LineRequest) -> dict[str, Any]:
    counter = _require_counter(camera_id)
    counter.set_line(LineConfig(req.x1, req.y1, req.x2, req.y2))
    return counter.get_state_dict()


@app.post("/api/cameras/{camera_id}/lines")
async def camera_set_lines(camera_id: str, req: LinesRequest) -> dict[str, Any]:
    counter = _require_counter(camera_id)
    counter.set_lines(
        [LineConfig(ln.x1, ln.y1, ln.x2, ln.y2, ln.name) for ln in req.lines]
    )
    return counter.get_state_dict()


@app.post("/api/cameras/{camera_id}/zones")
async def camera_set_zones(camera_id: str, req: ZonesRequest) -> dict[str, Any]:
    counter = _require_counter(camera_id)
    counter.set_zones(
        [
            ZoneConfig(
                name=z.name,
                role=z.role,
                polygon=[(p.x, p.y) for p in z.polygon],
            )
            for z in req.zones
        ]
    )
    return counter.get_state_dict()


# ------------------------------------------------------ per-camera preview


def _mjpeg_generator(counter: Counter):
    boundary = b"--frame"
    interval = 1.0 / max(1, settings.preview_fps)
    import time

    last_emit = 0.0
    while True:
        jpeg = counter.wait_for_frame(timeout=2.0)
        now = time.time()
        if jpeg is None:
            yield b"--frame\r\nContent-Type: text/plain\r\n\r\nwaiting\r\n"
            continue
        if now - last_emit < interval:
            continue
        last_emit = now
        yield boundary + b"\r\n"
        yield b"Content-Type: image/jpeg\r\n"
        yield f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
        yield jpeg
        yield b"\r\n"


@app.get("/api/cameras/{camera_id}/preview.mjpg")
async def camera_preview(camera_id: str) -> StreamingResponse:
    counter = _require_counter(camera_id)
    return StreamingResponse(
        _mjpeg_generator(counter),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Connection": "close",
        },
    )


# ----------------------------------------------------------------- videos

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename(name: str) -> str:
    name = (name or "").strip().split("/")[-1].split("\\")[-1]
    name = _SAFE_NAME.sub("_", name)
    return name[:120] or "video"


@app.post("/api/videos/upload", status_code=201)
async def upload_video(file: UploadFile = File(...)) -> dict[str, Any]:
    """Save an uploaded video into counter/data/videos/ for use as a camera source."""
    raw_name = _sanitize_filename(file.filename or "video")
    suffix = Path(raw_name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix!r}. "
                   f"Try one of: {', '.join(sorted(VIDEO_EXTENSIONS))}",
        )

    videos_dir = settings.videos_dir
    videos_dir.mkdir(parents=True, exist_ok=True)
    # uuid prefix avoids collisions and prevents callers from overwriting each
    # other's uploads — the human-readable suffix stays in the filename for UX.
    target_name = f"{uuid.uuid4().hex[:8]}-{raw_name}"
    target = videos_dir / target_name

    max_bytes = settings.max_video_upload_bytes
    written = 0
    chunk_size = 1024 * 1024
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Video larger than {max_bytes // (1024 * 1024)} MB",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"upload failed: {e}")

    return {
        "filename": target_name,
        "path": str(target.resolve()),
        "size": written,
    }


@app.get("/api/videos")
async def list_videos() -> list[dict[str, Any]]:
    videos_dir = settings.videos_dir
    if not videos_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(videos_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            st = p.stat()
            out.append({
                "filename": p.name,
                "path": str(p.resolve()),
                "size": st.st_size,
                "modified": st.st_mtime,
            })
        except OSError:
            continue
    return out


@app.delete("/api/videos/{filename}")
async def delete_video(filename: str) -> dict[str, str]:
    safe = _sanitize_filename(filename)
    target = (settings.videos_dir / safe).resolve()
    if not target.is_file() or settings.videos_dir.resolve() not in target.parents:
        raise HTTPException(status_code=404, detail="Video not found")
    # Refuse if any camera is currently using this file as its source — silent
    # deletion would leave the camera retrying read failures forever.
    target_str = str(target)
    in_use = [
        c for c in _manager(app).list_cameras()
        if (c.get("source") or "") == target_str
    ]
    if in_use:
        names = ", ".join(c["camera_name"] for c in in_use)
        raise HTTPException(
            status_code=409,
            detail=f"Video is in use by camera(s): {names}. "
                   f"Delete or repoint those cameras first.",
        )
    target.unlink()
    return {"status": "deleted"}


# ------------------------------------------------------------------ venues


@app.get("/api/venues")
async def list_venues() -> list[dict[str, Any]]:
    return _manager(app).list_venues()


@app.post("/api/venues", status_code=201)
async def create_venue(req: CreateVenueRequest) -> dict[str, Any]:
    return _manager(app).create_venue(
        name=req.name,
        floor_plan_w_m=req.floor_plan_w_m,
        floor_plan_h_m=req.floor_plan_h_m,
        dedup_window_s=req.dedup_window_s,
        dedup_radius_m=req.dedup_radius_m,
    )


@app.get("/api/venues/{venue_id}")
async def get_venue(venue_id: str) -> dict[str, Any]:
    venue = _manager(app).get_venue(venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")
    return venue


@app.patch("/api/venues/{venue_id}")
async def update_venue(venue_id: str, req: UpdateVenueRequest) -> dict[str, Any]:
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        return _manager(app).update_venue(venue_id, **payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="Venue not found")


@app.delete("/api/venues/{venue_id}")
async def delete_venue(venue_id: str) -> dict[str, str]:
    if not _manager(app).delete_venue(venue_id):
        raise HTTPException(status_code=404, detail="Venue not found")
    return {"status": "deleted"}


@app.post("/api/cameras/{camera_id}/venue")
async def assign_camera_to_venue(camera_id: str, req: AssignVenueRequest) -> dict[str, Any]:
    try:
        return _manager(app).assign_camera_to_venue(camera_id, req.venue_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Camera not found")


# ------------------------------------------------------------- calibration


@app.get("/api/cameras/{camera_id}/calibration")
async def get_calibration(camera_id: str) -> dict[str, Any]:
    calib = _manager(app).get_calibration(camera_id)
    if not calib:
        return {"camera_id": camera_id, "points": [], "homography": None, "reprojection_error_m": None}
    return calib


@app.post("/api/cameras/{camera_id}/calibration")
async def set_calibration(camera_id: str, req: CalibrationRequest) -> dict[str, Any]:
    points = [
        {"img": {"x": p.img.x, "y": p.img.y}, "world": {"x": p.world.x, "y": p.world.y}}
        for p in req.points
    ]
    try:
        return _manager(app).set_calibration(camera_id, points)
    except ValueError as e:
        # `set_calibration` rejects degenerate points and high-error fits.
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/cameras/{camera_id}/calibration")
async def clear_calibration(camera_id: str) -> dict[str, str]:
    _manager(app).clear_calibration(camera_id)
    return {"status": "cleared"}


# --------------------------------------------------------------- sessions


@app.get("/api/sessions", response_model=list[SessionResponse])
async def list_sessions(limit: int = 100) -> list[dict[str, Any]]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit out of range")
    return _storage(app).list_sessions(limit=limit)


@app.get("/api/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> dict[str, Any]:
    session = _storage(app).get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.patch("/api/sessions/{session_id}", response_model=SessionResponse)
async def update_session(session_id: str, req: UpdateSessionRequest) -> dict[str, Any]:
    session = _storage(app).update_session_label(session_id, req.label)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, str]:
    storage = _storage(app)
    target = storage.get_session(session_id)
    if not target:
        raise HTTPException(status_code=404, detail="Session not found")
    cam_id = target.get("camera_id")
    if cam_id:
        counter = _manager(app).get_counter(cam_id)
        if counter and counter.state.session_id == session_id:
            counter.stop_session()
    storage.delete_session(session_id)
    return {"status": "deleted"}


@app.get("/api/sessions/{session_id}/events")
async def list_events(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    if not _storage(app).get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit out of range")
    return _storage(app).list_events(session_id, limit=limit)


@app.get("/api/sessions/{session_id}/export.csv")
async def export_session_csv(session_id: str):
    if not _storage(app).get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    headers = {
        "Content-Disposition": f'attachment; filename="session-{session_id}.csv"'
    }
    return StreamingResponse(
        _storage(app).export_events_csv(session_id),
        media_type="text/csv",
        headers=headers,
    )


# ------------------------------------------------------ legacy single-camera


def _primary_counter() -> Counter:
    cam_id = _manager(app).primary_camera_id()
    if not cam_id:
        raise HTTPException(status_code=404, detail="No cameras configured")
    counter = _manager(app).get_counter(cam_id)
    if counter is None:
        raise HTTPException(status_code=404, detail="Primary camera not available")
    return counter


@app.get("/api/state")
async def state() -> dict[str, Any]:
    counter = _primary_counter()
    return counter.get_state_dict()


@app.post("/api/session/start")
async def start_session(req: StartSessionRequest) -> dict[str, Any]:
    return _primary_counter().start_session(label=req.label)


@app.post("/api/session/stop")
async def stop_session() -> dict[str, Any]:
    counter = _primary_counter()
    session = counter.stop_session()
    if session is None:
        raise HTTPException(status_code=409, detail="No active session")
    return session


@app.post("/api/session/reset")
async def reset_counts() -> dict[str, Any]:
    counter = _primary_counter()
    counter.reset_counts()
    return counter.get_state_dict()


@app.post("/api/line")
async def set_line(req: LineRequest) -> dict[str, Any]:
    counter = _primary_counter()
    counter.set_line(LineConfig(req.x1, req.y1, req.x2, req.y2))
    return counter.get_state_dict()


@app.get("/api/preview.mjpg")
async def preview() -> StreamingResponse:
    counter = _primary_counter()
    return StreamingResponse(
        _mjpeg_generator(counter),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Connection": "close",
        },
    )


# ---------------------------------------------------------------------- ws


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    broadcaster = _broadcaster(app)
    manager = _manager(app)
    queue = await broadcaster.subscribe()

    try:
        cameras = manager.list_cameras()
        await ws.send_json({"type": "cameras", "data": cameras})
        for cam in cameras:
            await ws.send_json(
                {"type": "state", "camera_id": cam["camera_id"], "data": cam}
            )
    except WebSocketDisconnect:
        await broadcaster.unsubscribe(queue)
        return
    except Exception:
        log.exception("Error sending initial WS snapshot")

    try:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WebSocket handler crashed")
    finally:
        await broadcaster.unsubscribe(queue)


# ---------------------------------------------------------------------- root


FRONTEND_BUILD = Path(__file__).resolve().parent.parent.parent / "frontend" / "build"


if FRONTEND_BUILD.is_dir():
    app.mount(
        "/_app",
        StaticFiles(directory=FRONTEND_BUILD / "_app"),
        name="svelte-app",
    )

    static_assets = FRONTEND_BUILD / "assets"
    if static_assets.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=static_assets),
            name="static-assets",
        )

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith(("api/", "ws", "_app/", "assets/")):
            raise HTTPException(status_code=404)

        candidate = FRONTEND_BUILD / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)

        index = FRONTEND_BUILD / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404)

else:

    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "service": "attendance-counter",
                "version": __version__,
                "frontend_build": str(FRONTEND_BUILD),
                "frontend_built": False,
                "hint": "Run `cd frontend && npm install && npm run build` to enable the dashboard.",
                "endpoints": [
                    "/api/health",
                    "/api/cameras",
                    "/api/sessions",
                    "/api/videos",
                    "/ws",
                ],
            }
        )
