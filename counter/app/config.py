from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"
VIDEOS_DIR = DATA_DIR / "videos"
TRACKERS_DIR = ROOT_DIR / "app" / "trackers"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="COUNTER_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8765

    source: str = "0"

    model_name: str = "yolo26n.pt"
    fallback_model_name: str = "yolov8n.pt"

    device: Literal["mps", "cpu", "cuda", "auto"] = "auto"

    # Half-precision (FP16) inference: 'auto' (enables on CUDA/MPS, never on
    # CPU), True (force on if device supports it), or False (force off).
    # FP16 typically gives 1.5–2× throughput on NVIDIA GPUs and Apple Silicon
    # with no measurable accuracy loss for person detection at typical sizes.
    fp16: Literal["auto", "true", "false"] = "auto"

    # Detection / tracking knobs (defaults; cameras can override per-camera).
    confidence: float = 0.35
    iou: float = 0.5
    image_size: int = 640
    person_class_id: int = 0
    max_det: int = 300

    # Tracker: 'botsort_reid' (accurate, default), 'bytetrack_fast' (fast),
    # or any built-in ultralytics tracker name like 'bytetrack.yaml'/'botsort.yaml'.
    tracker: str = "botsort_reid"

    # Number of frames for DetectionsSmoother. 0 disables smoothing.
    smoother_length: int = 3

    # Stream sizing for preview & inference.
    capture_width: int = 1280
    capture_height: int = 720
    target_fps: int = 30

    mirror: bool = False

    # Min frames a tracker must persist on the opposite side before counting.
    min_crossing_threshold: int = 3

    # ----- Crossing gates -----
    # These keep counts honest by rejecting line triggers from
    #  (a) very-low-velocity tracks (loiterers oscillating across the line),
    #  (b) low-confidence detections (flickering ghost-detections),
    #  (c) brand-new tracks that materialised on top of the line, and
    #  (d) the same tracker re-crossing in the same direction within a window.

    # Minimum normal-component velocity at the moment of crossing, in units of
    # frame-fraction per frame. 0.005 ≈ walking speed on most cameras (a
    # person at 1 m/s through a 6 m field of view at 30 fps moves ~0.005
    # of the frame per frame). 0.0 disables the gate.
    velocity_gate: float = 0.0

    # Track must have been observed for at least this many frames before any
    # crossing it triggers is counted. Catches the rare "tracker spawned on
    # top of the line" false positive. min_crossing_threshold mostly covers
    # this already, so 0 is safe by default.
    min_track_age: int = 0

    # Reject same-direction re-crossings of the same line by the same tracker
    # within this many seconds. 1.5 s is conservative — a person walking
    # through a doorway and immediately turning around takes longer than that
    # in practice, so this only filters jitter / loitering. 0.0 disables.
    crossing_cooldown_s: float = 1.5

    # Required margin above the camera's confidence threshold for the mean
    # detection confidence over the tracker's recent history at crossing
    # time. 0.05 means "confidence must average ≥ camera_conf + 0.05 over
    # the last few frames." 0.0 disables.
    crossing_confidence_margin: float = 0.0

    # How many recent (frame, x_norm, y_norm, conf) samples we keep per
    # tracker to compute velocity and confidence-mean from.
    track_history_length: int = 10

    default_line_x1: float = 0.5
    default_line_y1: float = 0.0
    default_line_x2: float = 0.5
    default_line_y2: float = 1.0

    db_path: Path = DATA_DIR / "counter.sqlite3"
    models_dir: Path = MODELS_DIR
    videos_dir: Path = VIDEOS_DIR
    trackers_dir: Path = TRACKERS_DIR

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ]
    )

    preview_fps: int = 15
    preview_quality: int = 75

    ws_queue_size: int = 256

    max_camera_failures: int = 30

    # Exponential backoff parameters for capture read failures and
    # inference-loop crashes. Backoff doubles per failure, capped at the max.
    # A small random jitter (≤ 25 %) prevents thundering herds when many
    # cameras share a flaky upstream (e.g. a NVR that briefly dropped them all).
    min_capture_retry_s: float = 0.05
    max_capture_retry_s: float = 30.0

    # Network-source watchdog: if no fresh frame arrives within this many
    # seconds, force the capture to reopen. Catches the silent-stuck-stream
    # failure mode where cv2.VideoCapture.read() blocks forever on a dead
    # RTSP connection. Only applies to URL sources (rtsp/http); USB and
    # local-file sources read frame-by-frame and don't need a watchdog.
    capture_watchdog_s: float = 10.0

    # Max video upload size in bytes (default 500 MB).
    max_video_upload_bytes: int = 500 * 1024 * 1024


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
