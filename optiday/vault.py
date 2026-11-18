"""Passwords (e.g. an email app password) live in the macOS Keychain, never in config files."""
from __future__ import annotations

import base64
import os
import subprocess
import sys

from .config import data_dir
from .crypto import KEYCHAIN_SERVICE, Cipher, load_key


def _use_keychain() -> bool:
    return sys.platform == "darwin" and not os.environ.get("OPTIDAY_HOME")


def _vault_path():
    return data_dir() / ".secrets"


def _vault() -> dict:
    p = _vault_path()
    if not p.exists():
        return {}
    return Cipher(load_key(data_dir())).open(base64.b64decode(p.read_bytes()), {}) or {}


def set_secret(name: str, value: str) -> None:
    if _use_keychain():
        subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", name,
                        "-l", f"OptiDay {name}", "-w", value], check=True, capture_output=True, timeout=10)
        return
    v = _vault()
    v[name] = value
    _vault_path().write_bytes(base64.b64encode(Cipher(load_key(data_dir())).seal(v)))
    os.chmod(_vault_path(), 0o600)


def get_secret(name: str) -> str | None:
    if _use_keychain():
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.rstrip("\n") if r.returncode == 0 else None
    return _vault().get(name)


def delete_secret(name: str) -> None:
    if _use_keychain():
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name],
                       capture_output=True, timeout=10)
        return
    v = _vault()
    v.pop(name, None)
    _vault_path().write_bytes(base64.b64encode(Cipher(load_key(data_dir())).seal(v)))

