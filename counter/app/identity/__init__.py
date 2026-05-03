"""Persistent person identity via face embeddings (P9).

Layered ON TOP of the venue-level GlobalTracker (P6):
  - GlobalTracker fuses per-camera tracker_ids into venue-wide global_ids
    using world-coordinate proximity. Resets every venue restart.
  - This identity layer adds a `person_id` that survives restarts, days,
    weeks. Bound by face embedding cosine similarity. Encrypted at rest,
    auto-purged on a configurable retention schedule.

Soft-required deps: insightface + onnxruntime. If unavailable at boot,
``IdentityPipeline.is_available`` returns False and all identity hooks are
no-ops; the rest of the counter functions normally.
"""

from .pipeline import IdentityPipeline
from .store import IdentityStore

__all__ = ["IdentityPipeline", "IdentityStore"]
