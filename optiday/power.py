"""Battery awareness. OptiDay does the minimum work that keeps the report accurate:

* plugged in    → full detail (frequent checks, accurate on-screen text reading, local AI)
* on battery    → battery saver (slower checks, screen text only when you switch windows or
                  type in email/chat, fast OCR, no AI until you plug in)
* low battery / Low Power Mode → titles and URLs only, no screen reading at all
"""
from __future__ import annotations

import re
import resource
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass

# Categories where re-reading the screen while you stay in the same window finds new,
# useful things (emails sent, messages, prompts, doc edits). Elsewhere a title is enough.
TEXT_HEAVY = {"Email", "Communication", "AI Assistants", "Writing", "Planning"}


@dataclass(frozen=True)
class PowerState:
    on_battery: bool = False
    percent: int | None = None
    low_power_mode: bool = False


@dataclass(frozen=True)
class Policy:
    name: str
    base_interval: float        # check this often while things are changing
    max_interval: float         # back off to this when nothing changes
    away_interval: float        # check this often while you're away / screen locked
    ocr: bool                   # read on-screen text at all
    ocr_refresh_text: float     # re-read a text-heavy window you're active in (0 = never)
    ocr_refresh_other: float    # re-read any other window you're active in (0 = never)
    ocr_revisit_s: float        # don't re-read a window you were just in this recently
    ocr_fast: bool              # Vision "fast" mode (much cheaper)
    ai: bool                    # allow the local AI to run now


def policy_for(cfg, state: PowerState) -> Policy:
    p = cfg.get("power", {}) if hasattr(cfg, "get") else {}
    mode = str(p.get("mode", "auto")).lower()
    floor = int(p.get("screen_reading_off_below_percent", 30))
    base = float(cfg.rec.get("interval_seconds", 5))
    refresh = float(cfg.rec.get("ocr_interval_seconds", 20))
    ocr_on = bool(cfg.rec.get("ocr_enabled", True))
    ai_on_battery = bool(p.get("ai_on_battery", False))

    critical = state.on_battery and (state.low_power_mode or (state.percent is not None and state.percent < floor))
    if mode == "max" or (mode == "auto" and not state.on_battery):
        return Policy("plugged in", base, max(base, 20), 30, ocr_on, refresh, 60, 120, False, True)
    if critical and mode != "max":
        return Policy("low battery", max(base, 10), 45, 90, False, 0, 0, 0, True, False)
    return Policy("battery saver", max(base, 8), 30, 60, ocr_on, max(refresh, 45), 0, 300, True,
                  ai_on_battery or not state.on_battery)


_PCT = re.compile(r"(\d{1,3})%")


def parse_pmset(out: str) -> tuple[bool, int | None]:
    on_battery = "'Battery Power'" in out
    m = _PCT.search(out)
    return on_battery, (int(m.group(1)) if m else None)


def probe_power() -> PowerState:
    if sys.platform != "darwin":
        return PowerState()
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return PowerState()
    on_battery, pct = parse_pmset(out)
    low = False
    try:
        from Foundation import NSProcessInfo  # type: ignore
        low = bool(NSProcessInfo.processInfo().isLowPowerModeEnabled())
    except Exception:  # noqa: BLE001
        pass
    return PowerState(on_battery, pct, low)


class PowerMonitor:
    """Caches the power state; checking costs a process spawn, so only every couple of minutes."""

    def __init__(self, cfg, probe=probe_power, ttl: float = 120, clock=time.monotonic):
        self.cfg = cfg
        self.probe = probe
        self.ttl = ttl
        self.clock = clock
        self._state: PowerState | None = None
        self._at = -1e9

    def state(self) -> PowerState:
        now = self.clock()
        if self._state is None or now - self._at >= self.ttl:
            self._state, self._at = self.probe(), now
        return self._state

    def refresh(self) -> None:
        self._at = -1e9

    def policy(self) -> Policy:
        return policy_for(self.cfg, self.state())


class EnergyMeter:
    """OptiDay's own CPU use (including the helper processes it starts), so you can see it's tiny."""

    def __init__(self, window_s: float = 900):
        self.window = window_s
        self.samples: deque[tuple[float, float]] = deque()
        self.start = self._now()

    @staticmethod
    def _cpu() -> float:
        me = resource.getrusage(resource.RUSAGE_SELF)
        kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime

    @staticmethod
    def _now() -> tuple[float, float]:
        return time.monotonic(), EnergyMeter._cpu()

    def sample(self) -> None:
        t, c = self._now()
        self.samples.append((t, c))
        while self.samples and t - self.samples[0][0] > self.window:
            self.samples.popleft()

    def cpu_percent(self) -> float:
        """Average % of one CPU core over the last ~15 minutes."""
        self.sample()
        t1, c1 = self.samples[-1]
        t0, c0 = self.samples[0] if len(self.samples) > 1 else self.start
        return 0.0 if t1 - t0 < 1 else 100.0 * (c1 - c0) / (t1 - t0)


# Quality-of-service classes (sys/qos.h). Lower QoS lets macOS run the work on the efficiency
# cores at low clock speed and coalesce its timers with other background work.
QOS = {"user_initiated": 0x19, "default": 0x15, "utility": 0x11, "background": 0x09}


def set_thread_qos(name: str) -> bool:
    """Set the calling thread's QoS class on macOS. No-op elsewhere."""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
        fn = lib.pthread_set_qos_class_self_np
        fn.argtypes = [ctypes.c_uint, ctypes.c_int]
        fn.restype = ctypes.c_int
        return fn(QOS[name], 0) == 0
    except Exception:  # noqa: BLE001
        return False
