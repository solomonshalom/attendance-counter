from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="COUNTER_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8765

    # Camera source: integer index ("0") or RTSP/HTTP URL.
    source: str = "0"

    # YOLO model: yolo26n.pt | yolo26s.pt | yolov8n.pt etc. Auto-downloaded.
    model_name: str = "yolo26n.pt"
    fallback_model_name: str = "yolov8n.pt"

    # Inference device: "mps" (Apple GPU), "cpu", "cuda".
    device: Literal["mps", "cpu", "cuda", "auto"] = "auto"

    # Detection / tracking knobs.
    confidence: float = 0.35
    iou: float = 0.5
    image_size: int = 640
    person_class_id: int = 0  # COCO 'person'.

    # Stream sizing for preview & inference.
    capture_width: int = 1280
    capture_height: int = 720
    target_fps: int = 30

    # Whether to flip the preview horizontally (mirror selfie cams).
    mirror: bool = False

    # Min frames a tracker must persist on the opposite side before counting.
    min_crossing_threshold: int = 2

    # Default line in normalized [0..1] frame coords (vertical line in middle).
    default_line_x1: float = 0.5
    default_line_y1: float = 0.0
    default_line_x2: float = 0.5
    default_line_y2: float = 1.0

    # SQLite database path.
    db_path: Path = DATA_DIR / "counter.sqlite3"

    # Models directory (where YOLO caches .pt files).
    models_dir: Path = MODELS_DIR

    # CORS allowed origins (frontend dev server).
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ]
    )

    # Maximum FPS for the MJPEG preview endpoint.
    preview_fps: int = 15
    preview_quality: int = 75

    # Maximum size of in-memory event broadcast queue per client.
    ws_queue_size: int = 256

    # Restart inference loop after N consecutive camera read failures.
    max_camera_failures: int = 30


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
