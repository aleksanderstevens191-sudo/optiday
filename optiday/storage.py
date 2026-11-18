"""Local SQLite store. Sensitive columns are encrypted (see crypto.py)."""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .crypto import Cipher
from .models import Snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id        INTEGER PRIMARY KEY,
  ts        REAL NOT NULL,
  day       TEXT NOT NULL,
  app       TEXT NOT NULL,
  bundle_id TEXT,
  idle_s    REAL,
  typing    INTEGER,
  private   INTEGER,
  away      INTEGER,
  payload   BLOB,          -- encrypted {title, url}
  ocr       BLOB,          -- encrypted on-screen text, NULL if not read / purged
  span      REAL           -- planned seconds until the next check
);
CREATE INDEX IF NOT EXISTS idx_snap_day_ts ON snapshots(day, ts);
CREATE TABLE IF NOT EXISTS notes (
  key     TEXT PRIMARY KEY,
  day     TEXT NOT NULL,
  payload BLOB
);
CREATE INDEX IF NOT EXISTS idx_notes_day ON notes(day);
CREATE TABLE IF NOT EXISTS overrides (
  kind  TEXT NOT NULL,
  key   TEXT NOT NULL,
  value TEXT,
  PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def day_for(ts: float, day_start_hour: int = 4) -> str:
    return (dt.datetime.fromtimestamp(ts) - dt.timedelta(hours=day_start_hour)).date().isoformat()


class Store:
    def __init__(self, path: Path, cipher: Cipher, day_start_hour: int = 4):
        self.path = path
        self.cipher = cipher
        self.day_start_hour = day_start_hour
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(snapshots)")}
        if "span" not in cols:  # database from v0.1
            self._db.execute("ALTER TABLE snapshots ADD COLUMN span REAL")
        self._db.commit()
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def day_of(self, ts: float) -> str:
        return day_for(ts, self.day_start_hour)

    def today(self) -> str:
        return self.day_of(time.time())

    # ---- snapshots -------------------------------------------------------
    def add_snapshot(self, s: Snapshot) -> None:
        self.add_snapshots([s])

    def add_snapshots(self, snaps: list[Snapshot]) -> None:
        rows = [
            (
                s.ts, self.day_of(s.ts), s.app, s.bundle_id, s.idle_s, int(s.typing), int(s.private), int(s.away),
                self.cipher.seal({"t": s.title, "u": s.url}),
                self.cipher.seal(s.ocr) if s.ocr is not None else None, s.span or None,
            )
            for s in snaps
        ]
        with self._lock:
            self._db.executemany(
                "INSERT INTO snapshots(ts,day,app,bundle_id,idle_s,typing,private,away,payload,ocr,span)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._db.commit()

    def snapshots(self, day: str) -> list[Snapshot]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts,app,bundle_id,idle_s,typing,private,away,payload,ocr,span FROM snapshots"
                " WHERE day=? ORDER BY ts",
                (day,),
            ).fetchall()
        out = []
        for ts, app, bid, idle, typing, private, away, payload, ocr, span in rows:
            p = self.cipher.open(payload, {}) or {}
            out.append(Snapshot(
                ts=ts, app=app, bundle_id=bid or "", title=p.get("t", ""), url=p.get("u", ""),
                idle_s=idle or 0.0, typing=bool(typing), private=bool(private), away=bool(away),
                ocr=self.cipher.open(ocr) if ocr is not None else None, span=span or 0.0,
            ))
        return out

    def days(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT DISTINCT day FROM snapshots ORDER BY day DESC")]

    def count_snapshots(self, day: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM snapshots WHERE day=?", (day,)).fetchone()[0]

    def active_seconds(self, day: str, interval: float) -> float:
        with self._lock:
            r = self._db.execute("SELECT COALESCE(SUM(COALESCE(span, ?)), 0) FROM snapshots WHERE day=? AND away=0",
                                 (interval, day)).fetchone()
        return float(r[0])

    def purge_ocr_older_than(self, days: int) -> int:
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        with self._lock:
            cur = self._db.execute("UPDATE snapshots SET ocr=NULL WHERE day < ? AND ocr IS NOT NULL", (cutoff,))
            self._db.commit()
            return cur.rowcount

    def delete_day(self, day: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM snapshots WHERE day=?", (day,))
            self._db.execute("DELETE FROM notes WHERE day=?", (day,))
            self._db.commit()

    # ---- notes (AI annotations, cached reports) ---------------------------
    def get_note(self, key: str) -> Any:
        with self._lock:
            r = self._db.execute("SELECT payload FROM notes WHERE key=?", (key,)).fetchone()
        return self.cipher.open(r[0]) if r else None

    def put_note(self, key: str, day: str, obj: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO notes(key,day,payload) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, day=excluded.day",
                (key, day, self.cipher.seal(obj)),
            )
            self._db.commit()

    def notes_for_day(self, day: str, prefix: str = "") -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key,payload FROM notes WHERE day=? AND key LIKE ?", (day, prefix + "%")
            ).fetchall()
        return {k: self.cipher.open(p) for k, p in rows}

    def notes_with_prefix(self, prefix: str, limit: int = 500) -> dict[str, Any]:
        """Notes across all days whose key starts with `prefix` (e.g. agent task suggestions)."""
        esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            rows = self._db.execute(
                "SELECT key,payload FROM notes WHERE key LIKE ? ESCAPE '\\' ORDER BY rowid DESC LIMIT ?",
                (esc + "%", limit)).fetchall()
        return {k: self.cipher.open(p) for k, p in rows}

    def day_bounds(self, day: str) -> tuple[float, float]:
        """Start/end timestamps of a recorded day (which starts at day_starts_at_hour)."""
        d = dt.date.fromisoformat(day)
        start = dt.datetime(d.year, d.month, d.day, self.day_start_hour)
        nxt = d + dt.timedelta(days=1)
        return start.timestamp(), dt.datetime(nxt.year, nxt.month, nxt.day, self.day_start_hour).timestamp()

    # ---- user overrides --------------------------------------------------
    def overrides(self, kind: str) -> dict[str, str]:
        with self._lock:
            return dict(self._db.execute("SELECT key,value FROM overrides WHERE kind=?", (kind,)).fetchall())

    def set_override(self, kind: str, key: str, value: str | None) -> None:
        with self._lock:
            if value is None:
                self._db.execute("DELETE FROM overrides WHERE kind=? AND key=?", (kind, key))
            else:
                self._db.execute(
                    "INSERT INTO overrides(kind,key,value) VALUES (?,?,?)"
                    " ON CONFLICT(kind,key) DO UPDATE SET value=excluded.value",
                    (kind, key, value),
                )
            self._db.commit()

    # ---- meta ------------------------------------------------------------
    def get_meta(self, k: str, default: str | None = None) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_meta(self, k: str, v: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO meta(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self._db.commit()


def open_store(cfg, data_dir: Path) -> Store:
    from .crypto import load_key
    return Store(data_dir / "optiday.db", Cipher(load_key(data_dir)), int(cfg.rec["day_starts_at_hour"]))
