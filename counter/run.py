"""Entrypoint for `python -m counter` / `python run.py`."""

from __future__ import annotations

import os
import platform

# OpenCV's AVFoundation backend tries to request camera permission on the main
# run loop, which doesn't exist when the capture is opened from a worker thread.
# We pre-disable that prompt so the worker can fail cleanly if permission is
# missing — and document granting access via System Settings in the README.
os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

# Silence verbose third-party warnings unrelated to user-actionable issues.
os.environ.setdefault("YOLO_VERBOSE", "False")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

# Hardware-accelerated decode + reliable RTSP transport for OpenCV's FFmpeg
# backend. MUST be set BEFORE OpenCV is imported in any thread, so this lives
# at the top of run.py rather than inside Settings or app.main.
#
#   - hwaccel: routes H.264/H.265 decode to the GPU. videotoolbox (Apple
#     Silicon / Intel Mac), vaapi (Linux Intel), cuda (NVIDIA). Massive CPU
#     savings on 1080p RTSP streams.
#   - rtsp_transport=tcp: UDP loses packets and silently corrupts streams.
#     TCP is slightly higher latency but reliable. Most modern IP cameras
#     accept it; if yours doesn't, override with COUNTER_FFMPEG_OPTIONS in .env.
#   - stimeout (microseconds): how long FFmpeg waits on a single I/O before
#     declaring the connection dead. 5 s is enough for most networks.
_default_opts = ["rtsp_transport;tcp", "stimeout;5000000"]
_system = platform.system()
if _system == "Darwin":
    _default_opts.insert(0, "hwaccel;videotoolbox")
elif _system == "Linux":
    # vaapi works on Intel/AMD Linux; for NVIDIA, override to "hwaccel;cuda".
    _default_opts.insert(0, "hwaccel;vaapi")
# Windows: leave default (DXVA2 / D3D11VA selected automatically when the
# decoder supports it).
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "|".join(_default_opts),
)

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        reload=False,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
