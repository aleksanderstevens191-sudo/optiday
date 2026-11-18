"""At-rest encryption for everything sensitive OptiDay stores.

Window titles, URLs and on-screen text are compressed and encrypted with a
Fernet key (AES-128-CBC + HMAC-SHA256). On macOS the key lives in the login
Keychain; elsewhere (tests, Linux) in a 0600 key file inside the data folder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

KEYCHAIN_SERVICE = "OptiDay"
KEYCHAIN_ACCOUNT = "database-key"


class Cipher:
    def __init__(self, key: bytes | None):
        self._f = Fernet(key) if key else None

    def seal(self, obj: Any) -> bytes:
        raw = zlib.compress(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(), 6)
        return self._f.encrypt(raw) if self._f else raw

    def open(self, blob: bytes | None, default: Any = None) -> Any:
        if blob is None:
            return default
        try:
            raw = self._f.decrypt(bytes(blob)) if self._f else bytes(blob)
            return json.loads(zlib.decompress(raw))
        except (InvalidToken, zlib.error, ValueError):
            return default


def _keychain_get() -> bytes | None:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().encode()
    return None


def _keychain_set(key: bytes) -> bool:
    try:
        r = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
             "-l", "OptiDay database key", "-w", key.decode()],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def load_key(data_dir: Path) -> bytes:
    key_file = data_dir / ".key"
    if key_file.exists():
        return key_file.read_bytes().strip()
    if sys.platform == "darwin" and not os.environ.get("OPTIDAY_HOME"):
        k = _keychain_get()
        if k:
            return k
        k = Fernet.generate_key()
        if _keychain_set(k) and _keychain_get() == k:
            return k
    k = Fernet.generate_key()
    key_file.write_bytes(k)
    os.chmod(key_file, 0o600)
    return k
