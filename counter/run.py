"""Entrypoint for `python -m counter` / `python run.py`."""

from __future__ import annotations

import os

# OpenCV's AVFoundation backend tries to request camera permission on the main
# run loop, which doesn't exist when the capture is opened from a worker thread.
# We pre-disable that prompt so the worker can fail cleanly if permission is
# missing — and document granting access via System Settings in the README.
os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

# Silence verbose third-party warnings unrelated to user-actionable issues.
os.environ.setdefault("YOLO_VERBOSE", "False")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

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
