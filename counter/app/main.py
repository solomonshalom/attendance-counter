"""FastAPI app exposing the counter via REST + WebSocket + MJPEG preview."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .broadcaster import Broadcaster
from .config import settings
from .counter import Counter, LineConfig
from .schemas import (
    HealthResponse,
    LineRequest,
    SessionResponse,
    StartSessionRequest,
    UpdateSessionRequest,
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
    counter = Counter(settings, storage)
    broadcaster = Broadcaster(queue_size=settings.ws_queue_size)
    broadcaster.attach_loop(asyncio.get_running_loop())

    def on_event(event: dict[str, Any]) -> None:
        broadcaster.publish_threadsafe({"type": "event", "data": event})

    def on_state(state: dict[str, Any]) -> None:
        broadcaster.publish_threadsafe({"type": "state", "data": state})

    counter.add_event_listener(on_event)
    counter.add_state_listener(on_state)
    counter.start()

    app.state.storage = storage
    app.state.counter = counter
    app.state.broadcaster = broadcaster

    log.info("Counter API ready on %s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        log.info("Shutting down counter")
        counter.stop()


app = FastAPI(title="Church Attendance Counter", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _counter(app: FastAPI) -> Counter:
    return app.state.counter  # type: ignore[no-any-return]


def _storage(app: FastAPI) -> Storage:
    return app.state.storage  # type: ignore[no-any-return]


def _broadcaster(app: FastAPI) -> Broadcaster:
    return app.state.broadcaster  # type: ignore[no-any-return]


# ---------------------------------------------------------------------- health


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    counter = _counter(app)
    state = counter.get_state_dict()
    return HealthResponse(
        ok=True,
        version=__version__,
        model_loaded=state["status"]["model_loaded"],
        camera_open=state["status"]["camera_open"],
        device=state["status"]["device"],
    )


# ----------------------------------------------------------------------- state


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return _counter(app).get_state_dict()


# --------------------------------------------------------------------- session


@app.post("/api/session/start")
async def start_session(req: StartSessionRequest) -> dict[str, Any]:
    session = _counter(app).start_session(label=req.label)
    return session


@app.post("/api/session/stop")
async def stop_session() -> dict[str, Any]:
    session = _counter(app).stop_session()
    if session is None:
        raise HTTPException(status_code=409, detail="No active session")
    return session


@app.post("/api/session/reset")
async def reset_counts() -> dict[str, Any]:
    _counter(app).reset_counts()
    return _counter(app).get_state_dict()


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
    counter = _counter(app)
    if counter.state.session_id == session_id:
        counter.stop_session()
    deleted = _storage(app).delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
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


# ------------------------------------------------------------------------ line


@app.post("/api/line")
async def set_line(req: LineRequest) -> dict[str, Any]:
    _counter(app).set_line(LineConfig(req.x1, req.y1, req.x2, req.y2))
    return _counter(app).get_state_dict()


# ---------------------------------------------------------------------- preview


def _mjpeg_generator(counter: Counter):
    boundary = b"--frame"
    interval = 1.0 / max(1, settings.preview_fps)
    import time

    last_emit = 0.0
    while True:
        jpeg = counter.wait_for_frame(timeout=2.0)
        now = time.time()
        if jpeg is None:
            # Send a keep-alive comment line so curl/clients don't time out.
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


@app.get("/api/preview.mjpg")
async def preview() -> StreamingResponse:
    counter = _counter(app)
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
    counter = _counter(app)
    queue = await broadcaster.subscribe()
    # Send initial state snapshot.
    try:
        await ws.send_json({"type": "state", "data": counter.get_state_dict()})
    except WebSocketDisconnect:
        await broadcaster.unsubscribe(queue)
        return

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
    # Serve static files (CSS, JS, fonts, images) from the SvelteKit build.
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
        # Reserve API/WS/preview namespaces.
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
                "service": "church-attendance-counter",
                "version": __version__,
                "frontend_build": str(FRONTEND_BUILD),
                "frontend_built": False,
                "hint": "Run `cd frontend && npm install && npm run build` to enable the dashboard.",
                "endpoints": [
                    "/api/health",
                    "/api/state",
                    "/api/session/start",
                    "/api/session/stop",
                    "/api/session/reset",
                    "/api/sessions",
                    "/api/line",
                    "/api/preview.mjpg",
                    "/ws",
                ],
            }
        )
