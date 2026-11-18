from __future__ import annotations

from typing import Protocol

from ..models import WindowInfo


class Capturer(Protocol):
    def front_window(self) -> WindowInfo | None:
        """The app/window/tab in front, or None when the screen is locked/asleep."""

    def idle_seconds(self) -> float:
        """Seconds since the last keyboard/mouse input."""

    def seconds_since_typing(self) -> float:
        """Seconds since the last key press (timing only, keys are never recorded)."""

    def read_screen_text(self, win: WindowInfo, fast: bool = False) -> str | None:
        """OCR of the front window. None if it couldn't be read. fast = cheaper, rougher."""
