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

    # Minimum normal velocity (px/frame) at the moment of crossing. Filters
    # out loiterers oscillating across the line. 0 disables the gate.
    velocity_gate: float = 0.0

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

    # Max video upload size in bytes (default 500 MB).
    max_video_upload_bytes: int = 500 * 1024 * 1024


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
