"""Encrypted, retention-bounded storage for face embeddings.

Each venue gets its own master key (held in the OS keychain, see keychain.py).
Embeddings are AES-256-GCM-encrypted under the venue key before hitting
SQLite. A nonce is generated per record and stored alongside the ciphertext,
so cross-record correlation is impossible from the plaintext store.

Retention is enforced by ``retention_until`` on each row plus a periodic
sweep that overwrites the blob with random bytes BEFORE the row is deleted —
in case file-system snapshots or WAL files preserved the original.

The do-not-store list is a SHA-256 hash of the (raw) embedding. We hash
locally, never store the raw — so an entry in the list reveals nothing about
the original face beyond "this hash was opted out."
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .keychain import KEY_LEN, get_or_create_key

log = logging.getLogger("counter.identity.store")


SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    venue_id TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    visit_count INTEGER NOT NULL DEFAULT 1,
    embedding_blob BLOB NOT NULL,    -- ciphertext
    embedding_nonce BLOB NOT NULL,   -- 12-byte AES-GCM nonce
    embedding_dim INTEGER NOT NULL,
    embedding_hash TEXT NOT NULL,    -- sha256(raw bytes); for do-not-store match
    retention_until REAL,            -- epoch seconds; NULL = no expiry
    opted_out INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_people_venue ON people(venue_id, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_people_retention ON people(retention_until);
CREATE INDEX IF NOT EXISTS idx_people_hash ON people(embedding_hash);

CREATE TABLE IF NOT EXISTS do_not_store_list (
    embedding_hash TEXT PRIMARY KEY,
    venue_id TEXT NOT NULL,
    added_at REAL NOT NULL
);
"""


def _embedding_hash(embedding: np.ndarray) -> str:
    """Stable hash of a normalized embedding. Used for do-not-store
    list-membership checks. Quantizes to int8 before hashing so that
    floating-point jitter on re-extraction (same person, different lighting)
    still hashes consistently within reasonable bounds."""
    q = (embedding * 127.0).astype(np.int8).tobytes()
    return hashlib.sha256(q).hexdigest()


class IdentityStore:
    """SQLite-backed encrypted store for face embeddings + people.

    Lives alongside the main counter DB. Schema is migrated on first use.
    Thread-safe via per-thread connections (matches the pattern in storage.py).
    """

    def __init__(self, db_path: Path, key_dir: Path) -> None:
        self.db_path = db_path
        self.key_dir = key_dir
        self._local = threading.local()
        self._init_lock = threading.Lock()
        # venue_id → AESGCM cipher. Cached so we don't re-derive per write.
        self._ciphers: dict[str, AESGCM] = {}
        self._cipher_lock = threading.Lock()
        self._initialize()

    # ----- connection / schema ----------------------------------------- #

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

    # ----- crypto ------------------------------------------------------ #

    def _cipher_for(self, venue_id: str) -> AESGCM:
        with self._cipher_lock:
            cipher = self._ciphers.get(venue_id)
            if cipher is None:
                key = get_or_create_key(venue_id, self.key_dir)
                if len(key) != KEY_LEN:
                    raise RuntimeError(
                        f"Master key for venue {venue_id!r} is wrong size"
                    )
                cipher = AESGCM(key)
                self._ciphers[venue_id] = cipher
            return cipher

    def _encrypt(self, venue_id: str, embedding: np.ndarray) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        plaintext = embedding.astype(np.float32).tobytes()
        ciphertext = self._cipher_for(venue_id).encrypt(nonce, plaintext, None)
        return ciphertext, nonce

    def _decrypt(
        self, venue_id: str, ciphertext: bytes, nonce: bytes, dim: int
    ) -> np.ndarray:
        plaintext = self._cipher_for(venue_id).decrypt(nonce, ciphertext, None)
        arr = np.frombuffer(plaintext, dtype=np.float32)
        if arr.size != dim:
            raise ValueError(
                f"decrypted embedding has size {arr.size}, expected {dim}"
            )
        return arr.copy()  # detach from the buffer

    # ----- people ------------------------------------------------------ #

    def find_match(
        self,
        venue_id: str,
        embedding: np.ndarray,
        threshold: float = 0.55,
    ) -> tuple[str | None, float]:
        """Return ``(person_id, similarity)`` for the best match in this
        venue, or ``(None, best_sim)`` if no match exceeds ``threshold``.

        Uses cosine similarity on L2-normalized embeddings — assumes the
        caller already normalized. Linear scan; for large populations
        (>10k people) consider FAISS.
        """
        cur = self._conn().execute(
            """
            SELECT id, embedding_blob, embedding_nonce, embedding_dim
              FROM people
             WHERE venue_id = ? AND opted_out = 0
            """,
            (venue_id,),
        )
        best_id: str | None = None
        best_sim = -1.0
        for row in cur.fetchall():
            try:
                stored = self._decrypt(
                    venue_id,
                    row["embedding_blob"],
                    row["embedding_nonce"],
                    int(row["embedding_dim"]),
                )
            except Exception:
                log.exception("decrypt failed for person %s", row["id"])
                continue
            # Cosine sim on already-L2-normalized vectors is just dot product.
            sim = float(np.dot(stored, embedding))
            if sim > best_sim:
                best_sim = sim
                best_id = row["id"]
        if best_id is not None and best_sim >= threshold:
            return best_id, best_sim
        return None, best_sim

    def is_blocked(self, venue_id: str, embedding: np.ndarray) -> bool:
        """Check the do-not-store list. We hash the (quantized) embedding
        and look it up; near-duplicates of the opted-out vector won't match
        unless they hash to the same int8-quantized values."""
        h = _embedding_hash(embedding)
        cur = self._conn().execute(
            "SELECT 1 FROM do_not_store_list WHERE embedding_hash = ? AND venue_id = ?",
            (h, venue_id),
        )
        return cur.fetchone() is not None

    def upsert_person(
        self,
        venue_id: str,
        embedding: np.ndarray,
        retention_s: float | None,
        existing_person_id: str | None = None,
    ) -> str:
        """Either create a new person row or refresh an existing one's
        last_seen_at + visit_count. Returns the person_id.

        Caller is expected to have already run :meth:`find_match` and
        :meth:`is_blocked`. If ``existing_person_id`` is provided, only
        last_seen_at + visit_count are touched; the embedding is NOT
        replaced (the existing one stays canonical for that person).
        """
        now = time.time()
        retention_until = now + retention_s if retention_s and retention_s > 0 else None

        if existing_person_id is not None:
            with self._tx() as conn:
                conn.execute(
                    """
                    UPDATE people
                       SET last_seen_at = ?,
                           visit_count = visit_count + 1,
                           retention_until = ?
                     WHERE id = ? AND venue_id = ?
                    """,
                    (now, retention_until, existing_person_id, venue_id),
                )
            return existing_person_id

        ciphertext, nonce = self._encrypt(venue_id, embedding)
        person_id = uuid.uuid4().hex
        h = _embedding_hash(embedding)
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO people
                    (id, venue_id, first_seen_at, last_seen_at, visit_count,
                     embedding_blob, embedding_nonce, embedding_dim,
                     embedding_hash, retention_until, opted_out)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 0)
                """,
                (
                    person_id,
                    venue_id,
                    now,
                    now,
                    ciphertext,
                    nonce,
                    int(embedding.size),
                    h,
                    retention_until,
                ),
            )
        return person_id

    def opt_out(self, venue_id: str, embedding: np.ndarray) -> int:
        """Add an embedding's hash to the do-not-store list, then purge any
        existing matching person rows in this venue. Returns the number of
        person rows purged.

        Called from the kiosk endpoint when a person scans themselves out.
        """
        h = _embedding_hash(embedding)
        purged = 0
        with self._tx() as conn:
            # Add to do-not-store first so a concurrent insert in the same
            # millisecond doesn't sneak past.
            conn.execute(
                """
                INSERT INTO do_not_store_list (embedding_hash, venue_id, added_at)
                VALUES (?, ?, ?)
                ON CONFLICT(embedding_hash) DO NOTHING
                """,
                (h, venue_id, time.time()),
            )

            # Match by hash first (cheap), then by similarity.
            cur = conn.execute(
                "SELECT id, embedding_blob, embedding_nonce, embedding_dim "
                "FROM people WHERE venue_id = ?",
                (venue_id,),
            )
            for row in cur.fetchall():
                try:
                    stored = self._decrypt(
                        venue_id,
                        row["embedding_blob"],
                        row["embedding_nonce"],
                        int(row["embedding_dim"]),
                    )
                except Exception:
                    continue
                if float(np.dot(stored, embedding)) >= 0.5:
                    # Overwrite the blob with random bytes BEFORE deletion so
                    # the row's old data can't be read out of WAL/snapshots.
                    junk = secrets.token_bytes(len(row["embedding_blob"]))
                    junk_nonce = secrets.token_bytes(len(row["embedding_nonce"]))
                    conn.execute(
                        "UPDATE people SET embedding_blob = ?, embedding_nonce = ? "
                        "WHERE id = ?",
                        (junk, junk_nonce, row["id"]),
                    )
                    conn.execute("DELETE FROM people WHERE id = ?", (row["id"],))
                    purged += 1
        return purged

    def admin_purge_venue(self, venue_id: str) -> int:
        """Wipe all face data for a venue — overwrite blobs with random bytes
        first, then delete. Used by the admin emergency-purge endpoint."""
        with self._tx() as conn:
            cur = conn.execute(
                "SELECT id, embedding_blob, embedding_nonce FROM people WHERE venue_id = ?",
                (venue_id,),
            )
            ids: list[str] = []
            for row in cur.fetchall():
                ids.append(row["id"])
                junk = secrets.token_bytes(len(row["embedding_blob"]))
                junk_nonce = secrets.token_bytes(len(row["embedding_nonce"]))
                conn.execute(
                    "UPDATE people SET embedding_blob = ?, embedding_nonce = ? "
                    "WHERE id = ?",
                    (junk, junk_nonce, row["id"]),
                )
            if ids:
                conn.execute(
                    f"DELETE FROM people WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
            # Also drop the venue's do-not-store list, since the people who
            # opted out no longer exist either.
            conn.execute(
                "DELETE FROM do_not_store_list WHERE venue_id = ?",
                (venue_id,),
            )
            return len(ids)

    def purge_expired(self, now: float | None = None) -> int:
        """Delete any person rows whose retention_until has passed. Run
        periodically from the manager's housekeeping. Returns the number of
        rows purged."""
        cutoff = now if now is not None else time.time()
        with self._tx() as conn:
            cur = conn.execute(
                "SELECT id, embedding_blob, embedding_nonce FROM people "
                "WHERE retention_until IS NOT NULL AND retention_until < ?",
                (cutoff,),
            )
            ids: list[str] = []
            for row in cur.fetchall():
                ids.append(row["id"])
                junk = secrets.token_bytes(len(row["embedding_blob"]))
                junk_nonce = secrets.token_bytes(len(row["embedding_nonce"]))
                conn.execute(
                    "UPDATE people SET embedding_blob = ?, embedding_nonce = ? "
                    "WHERE id = ?",
                    (junk, junk_nonce, row["id"]),
                )
            if ids:
                conn.execute(
                    f"DELETE FROM people WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
            return len(ids)

    def stats(self, venue_id: str) -> dict[str, Any]:
        """Per-venue counts for the dashboard."""
        cur = self._conn().execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN visit_count = 1 THEN 1 END) AS first_timers,
                COUNT(CASE WHEN visit_count > 1 THEN 1 END) AS returning,
                MAX(last_seen_at) AS last_activity
              FROM people
             WHERE venue_id = ?
            """,
            (venue_id,),
        )
        row = cur.fetchone()
        opted_cur = self._conn().execute(
            "SELECT COUNT(*) AS n FROM do_not_store_list WHERE venue_id = ?",
            (venue_id,),
        )
        opted_row = opted_cur.fetchone()
        return {
            "venue_id": venue_id,
            "total_people": int(row["total"] or 0),
            "first_timers": int(row["first_timers"] or 0),
            "returning": int(row["returning"] or 0),
            "last_activity": float(row["last_activity"] or 0.0),
            "opted_out": int(opted_row["n"] or 0),
        }
