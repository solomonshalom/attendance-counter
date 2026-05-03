"""SQLite persistence for cameras, sessions, crossing events, lines, zones."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    ended_at REAL,
    in_count INTEGER NOT NULL DEFAULT 0,
    out_count INTEGER NOT NULL DEFAULT 0,
    peak_inside INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('in','out')),
    tracker_id INTEGER,
    line_name TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cameras (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'camera',
    mirror INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    line_x1 REAL NOT NULL DEFAULT 0.5,
    line_y1 REAL NOT NULL DEFAULT 0.0,
    line_x2 REAL NOT NULL DEFAULT 0.5,
    line_y2 REAL NOT NULL DEFAULT 1.0,
    confidence REAL,
    iou REAL,
    imgsz INTEGER,
    tracker TEXT,
    loop_video INTEGER NOT NULL DEFAULT 1,
    paused INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS camera_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    x1 REAL NOT NULL,
    y1 REAL NOT NULL,
    x2 REAL NOT NULL,
    y2 REAL NOT NULL,
    UNIQUE(camera_id, idx),
    FOREIGN KEY(camera_id) REFERENCES cameras(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS camera_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'observer',
    polygon_json TEXT NOT NULL,
    UNIQUE(camera_id, idx),
    FOREIGN KEY(camera_id) REFERENCES cameras(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    floor_plan_w_m REAL NOT NULL DEFAULT 20.0,
    floor_plan_h_m REAL NOT NULL DEFAULT 20.0,
    dedup_window_s REAL NOT NULL DEFAULT 3.0,
    dedup_radius_m REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS camera_calibrations (
    camera_id TEXT PRIMARY KEY,
    points_json TEXT NOT NULL,
    homography_json TEXT,
    reprojection_error_m REAL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(camera_id) REFERENCES cameras(id) ON DELETE CASCADE
);
"""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _camera_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row)
    d["mirror"] = bool(d.get("mirror", 0))
    d["enabled"] = bool(d.get("enabled", 1))
    d["loop_video"] = bool(d.get("loop_video", 1))
    d["paused"] = bool(d.get("paused", 0))
    d["line"] = {
        "x1": float(d.pop("line_x1")),
        "y1": float(d.pop("line_y1")),
        "x2": float(d.pop("line_x2")),
        "y2": float(d.pop("line_y2")),
    }
    if "venue_id" not in d:
        d["venue_id"] = None
    return d


def _venue_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row)
    d["floor_plan_w_m"] = float(d.get("floor_plan_w_m") or 20.0)
    d["floor_plan_h_m"] = float(d.get("floor_plan_h_m") or 20.0)
    d["dedup_window_s"] = float(d.get("dedup_window_s") or 3.0)
    d["dedup_radius_m"] = float(d.get("dedup_radius_m") or 1.0)
    return d


class Storage:
    """Thread-safe SQLite wrapper. One connection per thread, WAL mode."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _initialize(self) -> None:
        with self._init_lock:
            with self._connect() as conn:
                conn.executescript(SCHEMA)
                self._migrate(conn)
                conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        # Idempotent additive migrations for older databases.
        sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "camera_id" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN camera_id TEXT")

        cam_cols = {row[1] for row in conn.execute("PRAGMA table_info(cameras)").fetchall()}
        for col, decl in [
            ("kind", "TEXT NOT NULL DEFAULT 'camera'"),
            ("confidence", "REAL"),
            ("iou", "REAL"),
            ("imgsz", "INTEGER"),
            ("tracker", "TEXT"),
            ("loop_video", "INTEGER NOT NULL DEFAULT 1"),
            ("paused", "INTEGER NOT NULL DEFAULT 0"),
            ("venue_id", "TEXT"),
        ]:
            if col not in cam_cols:
                conn.execute(f"ALTER TABLE cameras ADD COLUMN {col} {decl}")

        ev_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "line_name" not in ev_cols:
            conn.execute("ALTER TABLE events ADD COLUMN line_name TEXT")
        if "world_x" not in ev_cols:
            conn.execute("ALTER TABLE events ADD COLUMN world_x REAL")
        if "world_y" not in ev_cols:
            conn.execute("ALTER TABLE events ADD COLUMN world_y REAL")
        if "deduped" not in ev_cols:
            conn.execute("ALTER TABLE events ADD COLUMN deduped INTEGER NOT NULL DEFAULT 0")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_camera ON sessions(camera_id, started_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cameras_venue ON cameras(venue_id)"
        )

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # -------------------------------------------------------------- sessions

    def start_session(
        self, label: str = "", camera_id: str | None = None
    ) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sessions (id, label, started_at, camera_id) VALUES (?, ?, ?, ?)",
                (session_id, label, now, camera_id),
            )
        return self.get_session(session_id)  # type: ignore[return-value]

    def end_session(self, session_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (now, session_id),
            )
        return self.get_session(session_id)

    def update_session_counts(
        self,
        session_id: str,
        in_count: int,
        out_count: int,
        peak_inside: int,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE sessions
                   SET in_count = ?, out_count = ?, peak_inside = MAX(peak_inside, ?)
                 WHERE id = ?
                """,
                (in_count, out_count, peak_inside, session_id),
            )

    def update_session_label(self, session_id: str, label: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            conn.execute("UPDATE sessions SET label = ? WHERE id = ?", (label, session_id))
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount > 0

    def append_event(
        self,
        session_id: str,
        kind: str,
        tracker_id: int | None,
        line_name: str | None = None,
        world_x: float | None = None,
        world_y: float | None = None,
        deduped: bool = False,
    ) -> dict[str, Any]:
        if kind not in ("in", "out"):
            raise ValueError(f"Invalid event kind: {kind}")
        ts = time.time()
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (session_id, ts, kind, tracker_id, line_name, world_x, world_y, deduped)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, ts, kind, tracker_id, line_name, world_x, world_y, int(bool(deduped))),
            )
            event_id = cur.lastrowid
        return {
            "id": event_id,
            "session_id": session_id,
            "ts": ts,
            "kind": kind,
            "tracker_id": tracker_id,
            "line_name": line_name,
            "world_x": world_x,
            "world_y": world_y,
            "deduped": bool(deduped),
        }

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        cur = self._conn().execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def latest_open_session(self) -> dict[str, Any] | None:
        cur = self._conn().execute(
            "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def latest_open_session_for_camera(
        self, camera_id: str
    ) -> dict[str, Any] | None:
        cur = self._conn().execute(
            """
            SELECT * FROM sessions
             WHERE ended_at IS NULL AND camera_id = ?
             ORDER BY started_at DESC LIMIT 1
            """,
            (camera_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = self._conn().execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def list_events(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        cur = self._conn().execute(
            """
            SELECT * FROM events
             WHERE session_id = ?
             ORDER BY ts DESC
             LIMIT ?
            """,
            (session_id, limit),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def export_events_csv(self, session_id: str) -> Iterator[str]:
        yield "id,session_id,timestamp,kind,tracker_id,line_name,world_x,world_y,deduped\n"
        cur = self._conn().execute(
            """
            SELECT id, session_id, ts, kind, tracker_id, line_name, world_x, world_y, deduped
              FROM events WHERE session_id = ? ORDER BY ts ASC
            """,
            (session_id,),
        )
        for row in cur:
            wx = f"{row['world_x']:.3f}" if row['world_x'] is not None else ''
            wy = f"{row['world_y']:.3f}" if row['world_y'] is not None else ''
            yield (
                f"{row['id']},{row['session_id']},{row['ts']:.3f},"
                f"{row['kind']},{row['tracker_id'] if row['tracker_id'] is not None else ''},"
                f"{(row['line_name'] or '')},{wx},{wy},{int(row['deduped'] or 0)}\n"
            )

    def get_setting(self, key: str) -> str | None:
        cur = self._conn().execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    # --------------------------------------------------------------- cameras

    def list_cameras(self) -> list[dict[str, Any]]:
        cur = self._conn().execute(
            "SELECT * FROM cameras ORDER BY created_at ASC"
        )
        cams = [_camera_row_to_dict(r) for r in cur.fetchall()]
        for c in cams:
            c["lines"] = self.list_lines(c["id"])
            c["zones"] = self.list_zones(c["id"])
        return cams

    def get_camera(self, camera_id: str) -> dict[str, Any] | None:
        cur = self._conn().execute(
            "SELECT * FROM cameras WHERE id = ?", (camera_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cam = _camera_row_to_dict(row)
        cam["lines"] = self.list_lines(camera_id)
        cam["zones"] = self.list_zones(camera_id)
        return cam

    def get_camera_by_source(self, source: str) -> dict[str, Any] | None:
        cur = self._conn().execute(
            "SELECT * FROM cameras WHERE source = ? LIMIT 1", (source,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cam = _camera_row_to_dict(row)
        cam["lines"] = self.list_lines(cam["id"])
        cam["zones"] = self.list_zones(cam["id"])
        return cam

    def create_camera(
        self,
        name: str,
        source: str,
        mirror: bool = False,
        enabled: bool = True,
        line: dict[str, float] | None = None,
        kind: str = "camera",
        confidence: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
        tracker: str | None = None,
        loop_video: bool = True,
    ) -> dict[str, Any]:
        line = line or {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}
        camera_id = uuid.uuid4().hex
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO cameras
                    (id, name, source, kind, mirror, enabled,
                     line_x1, line_y1, line_x2, line_y2,
                     confidence, iou, imgsz, tracker, loop_video, paused,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    camera_id,
                    name,
                    source,
                    kind,
                    int(mirror),
                    int(enabled),
                    float(line["x1"]),
                    float(line["y1"]),
                    float(line["x2"]),
                    float(line["y2"]),
                    confidence,
                    iou,
                    imgsz,
                    tracker,
                    int(loop_video),
                    now,
                    now,
                ),
            )
            # Mirror the primary line into camera_lines so the multi-line API
            # has something to read on day one.
            conn.execute(
                """
                INSERT INTO camera_lines (camera_id, idx, name, x1, y1, x2, y2)
                VALUES (?, 0, 'doorway', ?, ?, ?, ?)
                """,
                (
                    camera_id,
                    float(line["x1"]),
                    float(line["y1"]),
                    float(line["x2"]),
                    float(line["y2"]),
                ),
            )
        return self.get_camera(camera_id)  # type: ignore[return-value]

    def update_camera(
        self,
        camera_id: str,
        *,
        name: str | None = None,
        source: str | None = None,
        kind: str | None = None,
        mirror: bool | None = None,
        enabled: bool | None = None,
        confidence: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
        tracker: str | None = None,
        loop_video: bool | None = None,
        paused: bool | None = None,
    ) -> dict[str, Any] | None:
        fields: list[str] = []
        values: list[Any] = []
        for col, val, transform in [
            ("name", name, lambda v: v),
            ("source", source, lambda v: v),
            ("kind", kind, lambda v: v),
            ("mirror", mirror, lambda v: int(v)),
            ("enabled", enabled, lambda v: int(v)),
            ("confidence", confidence, lambda v: v),
            ("iou", iou, lambda v: v),
            ("imgsz", imgsz, lambda v: v),
            ("tracker", tracker, lambda v: v),
            ("loop_video", loop_video, lambda v: int(v)),
            ("paused", paused, lambda v: int(v)),
        ]:
            if val is not None:
                fields.append(f"{col} = ?")
                values.append(transform(val))
        if not fields:
            return self.get_camera(camera_id)
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(camera_id)
        with self._tx() as conn:
            conn.execute(
                f"UPDATE cameras SET {', '.join(fields)} WHERE id = ?",
                values,
            )
        return self.get_camera(camera_id)

    def set_camera_line(
        self, camera_id: str, line: dict[str, float]
    ) -> dict[str, Any] | None:
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE cameras
                   SET line_x1 = ?, line_y1 = ?, line_x2 = ?, line_y2 = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    float(line["x1"]),
                    float(line["y1"]),
                    float(line["x2"]),
                    float(line["y2"]),
                    time.time(),
                    camera_id,
                ),
            )
            # Keep the primary line in camera_lines (idx=0) in sync so the
            # multi-line read path returns the same coords as legacy callers.
            conn.execute(
                """
                INSERT INTO camera_lines (camera_id, idx, name, x1, y1, x2, y2)
                VALUES (?, 0, 'doorway', ?, ?, ?, ?)
                ON CONFLICT(camera_id, idx) DO UPDATE SET
                  x1 = excluded.x1, y1 = excluded.y1,
                  x2 = excluded.x2, y2 = excluded.y2
                """,
                (
                    camera_id,
                    float(line["x1"]),
                    float(line["y1"]),
                    float(line["x2"]),
                    float(line["y2"]),
                ),
            )
        return self.get_camera(camera_id)

    def delete_camera(self, camera_id: str) -> bool:
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET camera_id = NULL WHERE camera_id = ?",
                (camera_id,),
            )
            cur = conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            return cur.rowcount > 0

    # --------------------------------------------------------------- lines

    def list_lines(self, camera_id: str) -> list[dict[str, Any]]:
        cur = self._conn().execute(
            "SELECT idx, name, x1, y1, x2, y2 FROM camera_lines WHERE camera_id = ? ORDER BY idx ASC",
            (camera_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def replace_lines(
        self, camera_id: str, lines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        with self._tx() as conn:
            conn.execute("DELETE FROM camera_lines WHERE camera_id = ?", (camera_id,))
            for idx, ln in enumerate(lines):
                conn.execute(
                    """
                    INSERT INTO camera_lines (camera_id, idx, name, x1, y1, x2, y2)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        camera_id,
                        idx,
                        ln.get("name", f"line {idx + 1}"),
                        float(ln["x1"]),
                        float(ln["y1"]),
                        float(ln["x2"]),
                        float(ln["y2"]),
                    ),
                )
            # Sync the legacy single-line columns to the first line so legacy
            # endpoints still see something coherent.
            if lines:
                ln0 = lines[0]
                conn.execute(
                    """
                    UPDATE cameras SET line_x1=?, line_y1=?, line_x2=?, line_y2=?, updated_at=?
                     WHERE id = ?
                    """,
                    (
                        float(ln0["x1"]),
                        float(ln0["y1"]),
                        float(ln0["x2"]),
                        float(ln0["y2"]),
                        time.time(),
                        camera_id,
                    ),
                )
        return self.list_lines(camera_id)

    # --------------------------------------------------------------- zones

    def list_zones(self, camera_id: str) -> list[dict[str, Any]]:
        cur = self._conn().execute(
            "SELECT idx, name, role, polygon_json FROM camera_zones WHERE camera_id = ? ORDER BY idx ASC",
            (camera_id,),
        )
        out: list[dict[str, Any]] = []
        for row in cur.fetchall():
            d = dict(row)
            try:
                d["polygon"] = json.loads(d.pop("polygon_json"))
            except Exception:
                d["polygon"] = []
            out.append(d)
        return out

    def replace_zones(
        self, camera_id: str, zones: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        with self._tx() as conn:
            conn.execute("DELETE FROM camera_zones WHERE camera_id = ?", (camera_id,))
            for idx, z in enumerate(zones):
                polygon = z.get("polygon", [])
                conn.execute(
                    """
                    INSERT INTO camera_zones (camera_id, idx, name, role, polygon_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        camera_id,
                        idx,
                        z.get("name", f"zone {idx + 1}"),
                        z.get("role", "observer"),
                        json.dumps(polygon),
                    ),
                )
        return self.list_zones(camera_id)

    # --------------------------------------------------------------- venues

    def list_venues(self) -> list[dict[str, Any]]:
        cur = self._conn().execute("SELECT * FROM venues ORDER BY created_at ASC")
        return [_venue_row_to_dict(r) for r in cur.fetchall()]

    def get_venue(self, venue_id: str) -> dict[str, Any] | None:
        cur = self._conn().execute("SELECT * FROM venues WHERE id = ?", (venue_id,))
        row = cur.fetchone()
        return _venue_row_to_dict(row) if row else None

    def create_venue(
        self,
        name: str,
        floor_plan_w_m: float = 20.0,
        floor_plan_h_m: float = 20.0,
        dedup_window_s: float = 3.0,
        dedup_radius_m: float = 1.0,
    ) -> dict[str, Any]:
        venue_id = uuid.uuid4().hex
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO venues
                    (id, name, floor_plan_w_m, floor_plan_h_m,
                     dedup_window_s, dedup_radius_m, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    venue_id, name, float(floor_plan_w_m), float(floor_plan_h_m),
                    float(dedup_window_s), float(dedup_radius_m), now, now,
                ),
            )
        return self.get_venue(venue_id)  # type: ignore[return-value]

    def update_venue(
        self,
        venue_id: str,
        *,
        name: str | None = None,
        floor_plan_w_m: float | None = None,
        floor_plan_h_m: float | None = None,
        dedup_window_s: float | None = None,
        dedup_radius_m: float | None = None,
    ) -> dict[str, Any] | None:
        fields: list[str] = []
        values: list[Any] = []
        for col, val in [
            ("name", name),
            ("floor_plan_w_m", floor_plan_w_m),
            ("floor_plan_h_m", floor_plan_h_m),
            ("dedup_window_s", dedup_window_s),
            ("dedup_radius_m", dedup_radius_m),
        ]:
            if val is not None:
                fields.append(f"{col} = ?")
                values.append(val)
        if not fields:
            return self.get_venue(venue_id)
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(venue_id)
        with self._tx() as conn:
            conn.execute(
                f"UPDATE venues SET {', '.join(fields)} WHERE id = ?",
                values,
            )
        return self.get_venue(venue_id)

    def delete_venue(self, venue_id: str) -> bool:
        with self._tx() as conn:
            # Detach cameras from this venue rather than delete them.
            conn.execute(
                "UPDATE cameras SET venue_id = NULL WHERE venue_id = ?",
                (venue_id,),
            )
            cur = conn.execute("DELETE FROM venues WHERE id = ?", (venue_id,))
            return cur.rowcount > 0

    def list_cameras_in_venue(self, venue_id: str) -> list[dict[str, Any]]:
        cur = self._conn().execute(
            "SELECT * FROM cameras WHERE venue_id = ? ORDER BY created_at ASC",
            (venue_id,),
        )
        return [_camera_row_to_dict(r) for r in cur.fetchall()]

    def assign_camera_to_venue(
        self, camera_id: str, venue_id: str | None
    ) -> dict[str, Any] | None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE cameras SET venue_id = ?, updated_at = ? WHERE id = ?",
                (venue_id, time.time(), camera_id),
            )
        return self.get_camera(camera_id)

    # --------------------------------------------------------------- calibrations

    def get_calibration(self, camera_id: str) -> dict[str, Any] | None:
        cur = self._conn().execute(
            "SELECT * FROM camera_calibrations WHERE camera_id = ?",
            (camera_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        try:
            d["points"] = json.loads(d.pop("points_json"))
        except Exception:
            d["points"] = []
        try:
            raw = d.pop("homography_json")
            d["homography"] = json.loads(raw) if raw else None
        except Exception:
            d["homography"] = None
        return d

    def save_calibration(
        self,
        camera_id: str,
        points: list[dict[str, Any]],
        homography: list[float] | None,
        reprojection_error_m: float | None,
    ) -> dict[str, Any]:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO camera_calibrations
                    (camera_id, points_json, homography_json, reprojection_error_m, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(camera_id) DO UPDATE SET
                    points_json = excluded.points_json,
                    homography_json = excluded.homography_json,
                    reprojection_error_m = excluded.reprojection_error_m,
                    updated_at = excluded.updated_at
                """,
                (
                    camera_id,
                    json.dumps(points),
                    json.dumps(homography) if homography is not None else None,
                    reprojection_error_m,
                    time.time(),
                ),
            )
        return self.get_calibration(camera_id)  # type: ignore[return-value]

    def delete_calibration(self, camera_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM camera_calibrations WHERE camera_id = ?",
                (camera_id,),
            )
            return cur.rowcount > 0
