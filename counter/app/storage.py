"""SQLite persistence for sessions and crossing events."""

from __future__ import annotations

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
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


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
                conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def start_session(self, label: str = "") -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sessions (id, label, started_at) VALUES (?, ?, ?)",
                (session_id, label, now),
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
    ) -> dict[str, Any]:
        if kind not in ("in", "out"):
            raise ValueError(f"Invalid event kind: {kind}")
        ts = time.time()
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO events (session_id, ts, kind, tracker_id) VALUES (?, ?, ?, ?)",
                (session_id, ts, kind, tracker_id),
            )
            event_id = cur.lastrowid
        return {
            "id": event_id,
            "session_id": session_id,
            "ts": ts,
            "kind": kind,
            "tracker_id": tracker_id,
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
        yield "id,session_id,timestamp,kind,tracker_id\n"
        cur = self._conn().execute(
            "SELECT id, session_id, ts, kind, tracker_id FROM events WHERE session_id = ? ORDER BY ts ASC",
            (session_id,),
        )
        for row in cur:
            yield (
                f"{row['id']},{row['session_id']},{row['ts']:.3f},"
                f"{row['kind']},{row['tracker_id'] if row['tracker_id'] is not None else ''}\n"
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
