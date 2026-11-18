from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class WindowInfo:
    """What is in front right now, as reported by the OS."""
    app: str
    bundle_id: str = ""
    pid: int = 0
    title: str = ""
    window_id: int = 0
    url: str = ""
    private_window: bool = False


@dataclass
class Snapshot:
    """One recorder tick."""
    ts: float
    app: str
    bundle_id: str = ""
    title: str = ""
    url: str = ""
    idle_s: float = 0.0
    typing: bool = False
    ocr: str | None = None      # None = screen text not read on this tick
    private: bool = False
    away: bool = False          # screen locked / asleep / idle without passive media
    span: float = 0.0           # seconds until the recorder planned to look again (0 = unknown)


@dataclass
class Segment:
    """A continuous stretch of time in the same window/tab."""
    start: float
    end: float
    app: str
    bundle_id: str = ""
    title: str = ""
    url: str = ""
    away: bool = False
    private: bool = False
    typing_s: float = 0.0
    ocr: list[tuple[float, str]] = field(default_factory=list)
    # filled in by enrichment
    domain: str = ""
    category: str = ""
    project: str = ""
    project_source: str = ""     # "rule" | "config" | "ai" | ""
    video: dict | None = None     # {"id","title","label","reason","source"}
    summary: str = ""

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def key(self) -> str:
        h = hashlib.sha1(f"{self.start:.1f}|{self.app}|{self.title}|{self.url}".encode()).hexdigest()
        return h[:20]
