"""Screen/window capture backends."""
from __future__ import annotations

import sys

from .base import Capturer


def default_capturer(cfg) -> Capturer:
    if sys.platform == "darwin":
        from .macos import MacCapturer
        return MacCapturer(cfg)
    raise RuntimeError("Live recording only works on macOS. Use `optiday demo` to try the reports here.")
