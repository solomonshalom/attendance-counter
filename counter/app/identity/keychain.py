"""Master-key management for face-embedding encryption.

Tries (in order):
  1. macOS Keychain via the ``security`` CLI — Mac dev/laptop default.
  2. A 0600-permissioned file under ``data/face_keys/<venue>.key`` —
     fallback for Linux + Docker deployments. NOT as secure as a real
     OS keychain (the key is on disk in plaintext), but better than
     storing the keys in the database next to the data they encrypt.

Each venue gets its own key so a leak of one venue's data can't
decrypt another's.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("counter.identity.keychain")

KEY_LEN = 32  # AES-256 key.
_SERVICE = "attendance-counter-face"


def _macos_security_path() -> str | None:
    """Return path to macOS `security` CLI, or None if not on Mac / not found."""
    if sys.platform != "darwin":
        return None
    return shutil.which("security")


def _kc_get(account: str) -> bytes | None:
    """Read a key from macOS Keychain. Returns None if missing."""
    sec = _macos_security_path()
    if sec is None:
        return None
    try:
        result = subprocess.run(
            [sec, "find-generic-password", "-s", _SERVICE, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        log.exception("security find-generic-password raised")
        return None
    if result.returncode != 0:
        return None
    raw_hex = result.stdout.strip()
    if not raw_hex:
        return None
    try:
        return bytes.fromhex(raw_hex)
    except ValueError:
        log.warning("Keychain returned non-hex value for %s", account)
        return None


def _kc_set(account: str, key_bytes: bytes) -> bool:
    """Store key in macOS Keychain. Overwrites existing if present."""
    sec = _macos_security_path()
    if sec is None:
        return False
    raw_hex = key_bytes.hex()
    try:
        # -U updates if exists, otherwise creates.
        subprocess.run(
            [
                sec,
                "add-generic-password",
                "-U",
                "-s",
                _SERVICE,
                "-a",
                account,
                "-w",
                raw_hex,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return True
    except Exception:
        log.exception("security add-generic-password failed")
        return False


def _file_path(account: str, fallback_dir: Path) -> Path:
    fallback_dir.mkdir(parents=True, exist_ok=True)
    # Strip everything except hex/alpha for filename safety.
    safe = "".join(c for c in account if c.isalnum() or c in "-_") or "default"
    return fallback_dir / f"{safe}.key"


def _file_get(account: str, fallback_dir: Path) -> bytes | None:
    path = _file_path(account, fallback_dir)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            data = f.read()
        return data if len(data) == KEY_LEN else None
    except OSError:
        log.exception("reading key file %s failed", path)
        return None


def _file_set(account: str, key_bytes: bytes, fallback_dir: Path) -> bool:
    path = _file_path(account, fallback_dir)
    try:
        with path.open("wb") as f:
            f.write(key_bytes)
        os.chmod(path, 0o600)
        return True
    except OSError:
        log.exception("writing key file %s failed", path)
        return False


def get_or_create_key(account: str, fallback_dir: Path) -> bytes:
    """Return the master key for ``account`` (typically venue_id), creating
    one on first call. Tries macOS Keychain first, then the fallback dir."""
    # Read.
    key = _kc_get(account)
    if key is not None and len(key) == KEY_LEN:
        return key
    key = _file_get(account, fallback_dir)
    if key is not None and len(key) == KEY_LEN:
        return key

    # Mint.
    new_key = secrets.token_bytes(KEY_LEN)

    if _kc_set(account, new_key):
        log.info("minted face-encryption key for %s in macOS Keychain", account)
        return new_key

    if _file_set(account, new_key, fallback_dir):
        log.info(
            "minted face-encryption key for %s as %s (file fallback; "
            "consider migrating to a hardware-backed keystore for production)",
            account,
            _file_path(account, fallback_dir),
        )
        return new_key

    raise RuntimeError(
        f"Could not store face-encryption key for {account!r} in any backend"
    )
