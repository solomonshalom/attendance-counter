"""Capture sources: USB cameras, video files, RTSP/HTTP network streams."""

from .source import FrameSource, VIDEO_EXTENSIONS, looks_like_video_file

__all__ = ["FrameSource", "VIDEO_EXTENSIONS", "looks_like_video_file"]
