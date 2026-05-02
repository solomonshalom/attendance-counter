from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class StartSessionRequest(BaseModel):
    label: str = Field(default="", max_length=200)


class UpdateSessionRequest(BaseModel):
    label: str = Field(max_length=200)


class LineRequest(BaseModel):
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)


class HealthResponse(BaseModel):
    ok: bool
    version: str
    model_loaded: bool
    camera_open: bool
    device: str


class SessionResponse(BaseModel):
    id: str
    label: str
    started_at: float
    ended_at: Optional[float]
    in_count: int
    out_count: int
    peak_inside: int
    notes: str = ""
