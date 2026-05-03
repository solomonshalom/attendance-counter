"""Thread-safe frame source for USB cameras, video files, and network streams.

For network streams (RTSP / HTTP), an internal background thread continuously
pulls frames from the underlying cv2.VideoCapture into a single-slot mailbox.
The inference loop consumes via :meth:`FrameSource.read`; anything not
consumed is dropped. This is the canonical pattern for real-time inference on
RTSP — without it, slow inference accumulates network buffer until frames are
seconds out of date, silently corrupting timestamped events.

For USB cameras and local video files :meth:`FrameSource.read` is a direct
``cv2.VideoCapture.read()`` so frame timing matches the source's native rate.

Hardware-accelerated decode + reliable RTSP transport are configured via the
``OPENCV_FFMPEG_CAPTURE_OPTIONS`` environment variable; see ``run.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from ..config import Settings

log = logging.getLogger("counter.capture")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg"}


def looks_like_video_file(path: str) -> bool:
    if not path:
        return False
    if path.startswith(("rtsp://", "rtmp://", "http://", "https://")):
        return False
    try:
        int(path)
        return False
    except ValueError:
        pass
    p = Path(path).expanduser()
    return p.suffix.lower() in VIDEO_EXTENSIONS or p.is_file()


def _is_network_source(source: str) -> bool:
    return source.startswith(("rtsp://", "rtmp://", "http://", "https://"))


def _is_integer_index(source: str) -> bool:
    try:
        int(source)
        return True
    except ValueError:
        return False


class FrameSource:
    """Abstracts frame acquisition across USB / video / RTSP sources.

    Construct with ``source`` (path, integer index, or URL), ``kind``
    ("camera" / "video" / "rtsp"), and ``settings``. Call :meth:`open` once;
    then call :meth:`read` repeatedly. Use :meth:`is_stuck` from a watchdog
    to detect a network stream that's gone silent without raising errors.

    All public methods are thread-safe. The producer thread (network sources
    only) is started on :meth:`open` and stopped on :meth:`close` /
    :meth:`reopen`.
    """

    def __init__(
        self,
        source: str,
        kind: str,
        settings: "Settings",
        stop_event: threading.Event | None = None,
    ) -> None:
        self.source = source
        self.kind = kind
        self.settings = settings
        self._external_stop = stop_event

        self._cap: cv2.VideoCapture | None = None
        self._frame_width = 0
        self._frame_height = 0
        self._video_fps = 0.0
        self._video_total = 0

        self._is_video = kind == "video" or looks_like_video_file(source)
        self._is_network = _is_network_source(source)

        # Producer thread (network sources only).
        self._producer_thread: threading.Thread | None = None
        self._producer_stop = threading.Event()
        self._mailbox_cv = threading.Condition()
        self._mailbox_ok = False
        self._mailbox_frame: np.ndarray | None = None
        self._last_ok_ts: float = 0.0

    # ----- properties --------------------------------------------------- #

    @property
    def is_video(self) -> bool:
        return self._is_video

    @property
    def is_network(self) -> bool:
        return self._is_network

    @property
    def width(self) -> int:
        return self._frame_width

    @property
    def height(self) -> int:
        return self._frame_height

    @property
    def video_fps(self) -> float:
        return self._video_fps

    @property
    def video_total_frames(self) -> int:
        return self._video_total

    def video_position(self) -> int:
        if self._cap is None or not self._is_video:
            return 0
        try:
            return int(self._cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
        except Exception:
            return 0

    def is_stuck(self, threshold_s: float) -> bool:
        """Network sources only. True when no fresh frame has arrived within
        ``threshold_s``. Used by the inference loop's watchdog to force a
        reopen on a stream that silently went dead."""
        if not self._is_network:
            return False
        last = self._last_ok_ts
        if last <= 0.0:
            # Producer hasn't yielded a single frame yet; let the connection
            # timeout layer handle the initial-failure case.
            return False
        return (time.time() - last) > threshold_s

    # ----- lifecycle ---------------------------------------------------- #

    def open(self) -> None:
        """Open the capture and (for network sources) start the producer
        thread. Raises ``RuntimeError`` on a hard failure."""
        if self._is_video:
            self._open_video_file()
        else:
            self._open_live()

        # Update size from the actual capture (may differ from request on
        # USB cameras that don't honour CAP_PROP_FRAME_WIDTH/HEIGHT).
        assert self._cap is not None
        self._frame_width = (
            int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            or self.settings.capture_width
        )
        self._frame_height = (
            int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            or self.settings.capture_height
        )
        if self._is_video:
            self._video_fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
            self._video_total = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if self._is_network:
            self._start_producer()

    def _open_video_file(self) -> None:
        path = str(Path(self.source).expanduser())
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video file: {self.source}")
        self._cap = cap

    def _open_live(self) -> None:
        if _is_integer_index(self.source):
            src: int | str = int(self.source)
        else:
            src = self.source

        # Pick the best backend for the source kind:
        #   - Integer (USB) on macOS → AVFoundation (native, low-latency).
        #   - Network URL → FFmpeg (honours OPENCV_FFMPEG_CAPTURE_OPTIONS:
        #     hwaccel + rtsp_transport=tcp + stimeout, set in run.py).
        #   - Anything else → CAP_ANY (let OpenCV decide).
        if isinstance(src, int):
            backend = cv2.CAP_AVFOUNDATION
        elif self._is_network:
            backend = cv2.CAP_FFMPEG
        else:
            backend = cv2.CAP_ANY

        cap = cv2.VideoCapture(src, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(src)  # final fallback

        if not cap.isOpened():
            if isinstance(src, int):
                raise RuntimeError(
                    f"Could not open camera index {src}. On macOS, ensure your "
                    "terminal has Camera permission (System Settings → Privacy "
                    "& Security → Camera). Also verify no other app is using "
                    "the camera, or try a different source value."
                )
            raise RuntimeError(f"Could not open video source: {self.source}")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.capture_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.capture_height)
        cap.set(cv2.CAP_PROP_FPS, self.settings.target_fps)
        # Tiny buffer so cv2 doesn't queue frames; we manage staleness in
        # our own producer thread for network sources.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap

    def reopen(self) -> None:
        self.close()
        self.open()

    def close(self) -> None:
        if self._producer_thread is not None:
            self._producer_stop.set()
            with self._mailbox_cv:
                self._mailbox_cv.notify_all()
            self._producer_thread.join(timeout=2.0)
            self._producer_thread = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                log.exception("cv2.VideoCapture.release() raised")
            self._cap = None
        with self._mailbox_cv:
            self._mailbox_ok = False
            self._mailbox_frame = None
            self._last_ok_ts = 0.0

    # ----- read --------------------------------------------------------- #

    def read(self, timeout: float = 0.5) -> tuple[bool, np.ndarray | None]:
        """Read the next frame.

        For network sources: blocks up to ``timeout`` seconds for the
        producer thread to deliver a fresh frame, then returns it. Older
        frames in the mailbox are dropped on consumption so the next call
        sees only newer frames.

        For USB / video: direct ``cv2.VideoCapture.read()``. ``timeout`` is
        ignored.
        """
        if self._is_network:
            return self._read_network(timeout)
        if self._cap is None:
            return False, None
        try:
            return self._cap.read()
        except Exception:
            log.exception("cv2.read() raised on %s", self.source)
            return False, None

    def _read_network(self, timeout: float) -> tuple[bool, np.ndarray | None]:
        with self._mailbox_cv:
            deadline = time.time() + max(0.0, timeout)
            while not (self._mailbox_ok and self._mailbox_frame is not None):
                if self._producer_stop.is_set():
                    return False, None
                if self._external_stop is not None and self._external_stop.is_set():
                    return False, None
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False, None
                self._mailbox_cv.wait(timeout=remaining)
            frame = self._mailbox_frame
            # Mark as consumed so the next call sees only newer frames.
            self._mailbox_ok = False
            self._mailbox_frame = None
            return True, frame

    def seek_start(self) -> None:
        """Video files only: rewind to frame 0. Network sources have no
        notion of seek; calling this is a no-op for them."""
        if self._cap is not None and self._is_video:
            try:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            except Exception:
                log.exception("seek_start failed on %s", self.source)

    # ----- producer thread (network only) ------------------------------- #

    def _start_producer(self) -> None:
        self._producer_stop.clear()
        thread_name = f"frame-producer[{self.source[:32]}]"
        self._producer_thread = threading.Thread(
            target=self._producer_loop,
            name=thread_name,
            daemon=True,
        )
        self._producer_thread.start()

    def _producer_loop(self) -> None:
        log.info("frame producer started for %s", self.source)
        try:
            while not self._producer_stop.is_set():
                if self._external_stop is not None and self._external_stop.is_set():
                    break
                cap = self._cap
                if cap is None:
                    time.sleep(0.02)
                    continue
                try:
                    ok, frame = cap.read()
                except Exception:
                    log.exception("cv2.read() raised in producer for %s", self.source)
                    ok, frame = False, None

                ts = time.time()
                with self._mailbox_cv:
                    self._mailbox_ok = bool(ok and frame is not None)
                    self._mailbox_frame = frame if self._mailbox_ok else None
                    if self._mailbox_ok:
                        self._last_ok_ts = ts
                    self._mailbox_cv.notify()

                if not ok:
                    # Brief pause before retrying — the outer reopen logic in
                    # the inference loop handles long-term failures, but a
                    # tight retry loop here would burn CPU.
                    time.sleep(0.05)
        finally:
            log.info("frame producer exiting for %s", self.source)
