"""Venue-level cross-camera identity.

Per-camera trackers (BoT-SORT-ReID inside ultralytics, or sv.ByteTrack in
slicer mode) assign IDs valid only within their own camera. When a person
walks from camera A's view into camera B's view, both cameras give them
fresh, unrelated IDs — so a single person becomes two events in any
analytics that group by tracker_id.

GlobalTracker fuses per-camera tracks into venue-wide ``global_id``s using
world-coordinate proximity in time. Each Counter that has a homography
calls :meth:`upsert` for every visible tracker every frame. The GlobalTracker
either finds an existing global track within ``dedup_radius_m`` and
``dedup_window_s`` (and binds the new (camera, tracker_id) pair to it) or
mints a fresh ``global_id``.

Crossings then carry the ``global_id`` into the events table, so analytics
can answer "how many unique people entered" instead of "how many camera-
specific tracker IDs entered."

State is purely in-memory — global_ids are session-local, meant for
cross-camera dedup, not cross-day identity. Cross-day identity (and the
"first-time visitor / regular / lapsed" analytics) is the job of the face
identity layer (P9), which uses a different store.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("counter.tracking.global_tracker")


@dataclass
class GlobalTrack:
    """One person's most-recent venue-wide world position + the per-camera
    bindings that contribute to it."""

    global_id: int
    world_x: float
    world_y: float
    last_ts: float
    last_camera_id: str
    # set of (camera_id, tracker_id) currently bound to this global_id
    bindings: set[tuple[str, int]] = field(default_factory=set)


class GlobalTracker:
    """Maintains a rolling, in-memory map of venue-wide person identities.

    Thread-safe. Designed to be called from inference threads on every
    frame for every visible track — internal data structures are small
    dicts and short lists, so the hot path is microseconds.

    Eviction: tracks that haven't been observed in ``max_window_s``
    (the largest dedup_window_s across all known venues, plus a safety
    margin) are dropped along with their bindings.
    """

    # How often (in number of upsert calls) to sweep stale tracks. Cheap to
    # do every call but pointless — most calls don't change anyone's
    # eviction status.
    _EVICT_EVERY = 256

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # venue_id → {dedup_radius_m, dedup_window_s}
        self._venue_cfg: dict[str, dict[str, float]] = {}
        # venue_id → {global_id → GlobalTrack}
        self._tracks: dict[str, dict[int, GlobalTrack]] = {}
        # (venue_id, camera_id, tracker_id) → global_id
        self._bindings: dict[tuple[str, str, int], int] = {}
        # venue_id → next global_id to mint (monotonic, never reused)
        self._next_gid: dict[str, int] = {}
        # Counts upsert calls so we can sweep periodically.
        self._upsert_count = 0

    # ---- venue lifecycle ---------------------------------------------- #

    def upsert_venue(self, venue: dict) -> None:
        """Register / update a venue's match parameters. Called by the
        manager when a venue is created, updated, or hydrated at startup."""
        with self._lock:
            self._venue_cfg[venue["id"]] = {
                "dedup_window_s": float(venue.get("dedup_window_s", 3.0)),
                "dedup_radius_m": float(venue.get("dedup_radius_m", 1.0)),
            }
            self._tracks.setdefault(venue["id"], {})
            self._next_gid.setdefault(venue["id"], 1)

    def remove_venue(self, venue_id: str) -> None:
        """Drop all state for a venue. Called when a venue is deleted."""
        with self._lock:
            self._venue_cfg.pop(venue_id, None)
            tracks = self._tracks.pop(venue_id, {})
            self._next_gid.pop(venue_id, None)
            # Drop any bindings that referred to this venue.
            for gid in list(tracks.keys()):
                track = tracks[gid]
                for (cam_id, tid) in track.bindings:
                    self._bindings.pop((venue_id, cam_id, tid), None)

    # ---- core API ----------------------------------------------------- #

    def upsert(
        self,
        venue_id: str,
        camera_id: str,
        tracker_id: int,
        world_x: float,
        world_y: float,
        ts: float,
    ) -> int | None:
        """Return the global_id this (camera, tracker_id) maps to.

        - If we already have a binding, refresh its last_ts/position.
        - Else search the venue for an existing track within
          dedup_radius_m and dedup_window_s; if found, bind to it.
        - Else mint a fresh global_id.

        Returns ``None`` if the venue isn't registered (caller's
        homography or venue assignment is missing).
        """
        with self._lock:
            cfg = self._venue_cfg.get(venue_id)
            if cfg is None:
                return None
            window = cfg["dedup_window_s"]
            radius_sq = cfg["dedup_radius_m"] ** 2

            tracks = self._tracks.setdefault(venue_id, {})
            binding_key = (venue_id, camera_id, int(tracker_id))

            # 1. Existing binding — refresh.
            existing_gid = self._bindings.get(binding_key)
            if existing_gid is not None:
                track = tracks.get(existing_gid)
                if track is not None:
                    track.world_x = world_x
                    track.world_y = world_y
                    track.last_ts = ts
                    track.last_camera_id = camera_id
                    self._maybe_evict(ts)
                    return existing_gid
                # Binding pointed at an evicted track — fall through.
                del self._bindings[binding_key]

            # 2. Search for the closest active track within window+radius.
            best_gid: int | None = None
            best_dist_sq = float("inf")
            for gid, track in tracks.items():
                dt = ts - track.last_ts
                if dt < 0 or dt > window:
                    continue
                dx = track.world_x - world_x
                dy = track.world_y - world_y
                d2 = dx * dx + dy * dy
                if d2 < best_dist_sq:
                    best_dist_sq = d2
                    best_gid = gid

            if best_gid is not None and best_dist_sq <= radius_sq:
                track = tracks[best_gid]
                track.world_x = world_x
                track.world_y = world_y
                track.last_ts = ts
                track.last_camera_id = camera_id
                track.bindings.add((camera_id, int(tracker_id)))
                self._bindings[binding_key] = best_gid
                self._maybe_evict(ts)
                return best_gid

            # 3. Mint a fresh global_id.
            gid = self._next_gid.get(venue_id, 1)
            self._next_gid[venue_id] = gid + 1
            track = GlobalTrack(
                global_id=gid,
                world_x=world_x,
                world_y=world_y,
                last_ts=ts,
                last_camera_id=camera_id,
                bindings={(camera_id, int(tracker_id))},
            )
            tracks[gid] = track
            self._bindings[binding_key] = gid
            self._maybe_evict(ts)
            return gid

    def get_binding(
        self, venue_id: str, camera_id: str, tracker_id: int
    ) -> int | None:
        """Cheap lookup: what global_id is currently bound to this
        (venue, camera, tracker_id) tuple? Returns ``None`` if unbound."""
        with self._lock:
            return self._bindings.get((venue_id, camera_id, int(tracker_id)))

    def stats(self) -> dict[str, dict[str, int]]:
        """Snapshot for the metrics endpoint. Returns per-venue active-track
        and binding counts."""
        with self._lock:
            return {
                venue_id: {
                    "active_tracks": len(tracks),
                    "active_bindings": sum(
                        1 for k in self._bindings if k[0] == venue_id
                    ),
                    "next_global_id": self._next_gid.get(venue_id, 1),
                }
                for venue_id, tracks in self._tracks.items()
            }

    # ---- eviction ----------------------------------------------------- #

    def _maybe_evict(self, now: float) -> None:
        """Periodic sweep of stale tracks. Caller already holds the lock."""
        self._upsert_count += 1
        if self._upsert_count % self._EVICT_EVERY != 0:
            return

        # Use the largest configured window across all venues, with a 2× safety
        # margin so brief inference stalls don't prematurely evict.
        if not self._venue_cfg:
            return
        max_window = max(
            cfg["dedup_window_s"] for cfg in self._venue_cfg.values()
        ) * 2.0
        max_window = max(max_window, 30.0)  # always retain ≥ 30 s
        cutoff = now - max_window

        for venue_id, tracks in self._tracks.items():
            stale_gids = [
                gid for gid, t in tracks.items() if t.last_ts < cutoff
            ]
            for gid in stale_gids:
                track = tracks.pop(gid)
                for (cam_id, tid) in track.bindings:
                    self._bindings.pop((venue_id, cam_id, tid), None)

    def clear_camera_bindings(self, camera_id: str) -> None:
        """When a camera goes offline or is removed, drop its bindings so a
        new tracker_id with the same value (after restart) doesn't accidentally
        inherit a stale global_id."""
        with self._lock:
            keys_to_drop = [
                k for k in self._bindings if k[1] == camera_id
            ]
            for k in keys_to_drop:
                gid = self._bindings.pop(k)
                venue_id = k[0]
                tracks = self._tracks.get(venue_id, {})
                track = tracks.get(gid)
                if track is not None:
                    track.bindings.discard((k[1], k[2]))
                    # If the track has no remaining bindings, leave it in
                    # place; another camera might rejoin and rebind. Eviction
                    # by age handles cleanup.
