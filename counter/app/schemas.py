from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


CameraKind = Literal["camera", "video", "rtsp"]


class StartSessionRequest(BaseModel):
    label: str = Field(default="", max_length=200)


class UpdateSessionRequest(BaseModel):
    label: str = Field(max_length=200)


class LineRequest(BaseModel):
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)


class LineDef(BaseModel):
    name: str = Field(default="line", max_length=80)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)


class LinesRequest(BaseModel):
    lines: list[LineDef]

    @field_validator("lines")
    @classmethod
    def _at_least_one(cls, v: list[LineDef]) -> list[LineDef]:
        if not v:
            raise ValueError("at least one line is required")
        if len(v) > 8:
            raise ValueError("at most 8 lines per camera")
        return v


class Point(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class ZoneDef(BaseModel):
    name: str = Field(default="zone", max_length=80)
    role: Literal["observer", "interior", "entry", "exit"] = "observer"
    polygon: list[Point]

    @field_validator("polygon")
    @classmethod
    def _enough_points(cls, v: list[Point]) -> list[Point]:
        if len(v) < 3:
            raise ValueError("polygon needs at least 3 points")
        if len(v) > 64:
            raise ValueError("polygon supports at most 64 points")
        return v


class ZonesRequest(BaseModel):
    zones: list[ZoneDef]


class HealthResponse(BaseModel):
    ok: bool
    version: str
    cameras: int


class SessionResponse(BaseModel):
    id: str
    label: str
    started_at: float
    ended_at: Optional[float]
    in_count: int
    out_count: int
    peak_inside: int
    notes: str = ""
    camera_id: Optional[str] = None


class CreateCameraRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=500)
    kind: CameraKind = "camera"
    mirror: bool = False
    enabled: bool = True
    autostart: bool = True
    confidence: Optional[float] = Field(default=None, ge=0.05, le=0.95)
    iou: Optional[float] = Field(default=None, ge=0.1, le=0.95)
    imgsz: Optional[int] = Field(default=None, ge=320, le=1920)
    tracker: Optional[str] = Field(default=None, max_length=80)
    loop_video: Optional[bool] = None


class UpdateCameraRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    source: Optional[str] = Field(default=None, min_length=1, max_length=500)
    kind: Optional[CameraKind] = None
    mirror: Optional[bool] = None
    enabled: Optional[bool] = None
    confidence: Optional[float] = Field(default=None, ge=0.05, le=0.95)
    iou: Optional[float] = Field(default=None, ge=0.1, le=0.95)
    imgsz: Optional[int] = Field(default=None, ge=320, le=1920)
    tracker: Optional[str] = Field(default=None, max_length=80)
    loop_video: Optional[bool] = None
    paused: Optional[bool] = None


class PlaybackRequest(BaseModel):
    action: Literal["play", "pause", "restart"]
