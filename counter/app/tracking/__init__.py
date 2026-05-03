"""Per-camera trackers, slicer integration, and venue-level identity fusion."""

from .global_tracker import GlobalTrack, GlobalTracker
from .slicer import SlicerDetector

__all__ = ["GlobalTrack", "GlobalTracker", "SlicerDetector"]
