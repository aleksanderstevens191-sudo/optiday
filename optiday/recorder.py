"""The recording loop, built to sip battery.

* App switches wake the recorder instantly (macOS notification), so between switches it can
  check rarely: every 5s while things change, backing off to 20-30s when nothing does, and
  30-90s while you're away.
* On-screen text is read when you move to a new window/tab, and only re-read in email/chat/
  AI/doc windows while you're actively using them. Never while you watch a video, never for a
  window you just read a minute ago, and not at all on low battery.
* Writes go to disk in batches every few minutes.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import rules
from .capture.base import Capturer
from .models import Snapshot
from .power import TEXT_HEAVY, EnergyMeter, Policy, PowerMonitor, set_thread_qos
from .storage import Store

log = logging.getLogger("optiday.recorder")

FLUSH_EVERY_S = 300
FLUSH_EVERY_N = 60


class Recorder:
    def __init__(self, cfg, store: Store, capturer: Capturer, data_dir: Path, clock=time.time,
                 power: PowerMonitor | None = None):
        self.cfg = cfg
        self.store = store
        self.cap = capturer
        self.clock = clock
        self.power = power or PowerMonitor(cfg)
        self.energy = EnergyMeter()
        self.pause_flag = data_dir / "paused"
        self.idle_threshold = float(cfg.rec["idle_threshold_seconds"])
        self.wake = threading.Event()
        self.next_delay = float(cfg.rec["interval_seconds"])
        self.policy: Policy = self.power.policy()
        self._last_key = None
        self._last_ocr_key = None
        self._last_ocr_ts = 0.0
        self._last_ocr_text: str | None = None
        self._ocr_seen: OrderedDict = OrderedDict()   # window key -> when last read
        self._buffer: list[Snapshot] = []
        self._buf_lock = threading.Lock()
        self._last_flush = clock()
        self._today = store.today()
        self.active_today_s = store.active_seconds(self._today, float(cfg.rec["interval_seconds"]))
        self.last_error: str | None = None
        self.sleeping = False
        self.stats = {"ticks": 0, "ocr": 0}
        self.listeners: list = []   # e.g. the live view: called with every new check

    # ------------------------------------------------------------- control
    @property
    def paused(self) -> bool:
        return self.pause_flag.exists()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.pause_flag.touch()
        else:
            self.pause_flag.unlink(missing_ok=True)
            self.poke()

    def poke(self) -> None:
        """Called from macOS notifications (app switched, Mac woke): check right now."""
        self.sleeping = False
        self.wake.set()

    def on_sleep(self) -> None:
        self.sleeping = True
        self.flush()

    # ---------------------------------------------------------------- tick
    def tick(self) -> Snapshot | None:
        now = self.clock()
        pol = self.policy = self.power.policy()
        self.stats["ticks"] += 1
        if self.paused:
            self.next_delay = 300  # resuming from the menu wakes us immediately
            return None
        if self.sleeping:
            # Normally a wake notification clears this; if one is ever missed, any input does.
            if self.cap.idle_seconds() >= 30:
                self.next_delay = 120
                return None
            self.sleeping = False
        day = self.store.day_of(now)
        if day != self._today:
            self._today, self.active_today_s = day, 0.0
        win = self.cap.front_window()
        if win is None:  # locked / asleep
            self.next_delay = pol.away_interval
            self._last_key = None
            return None
        idle = self.cap.idle_seconds()
        excluded = rules.is_excluded(win.app, win.url, win.title, self.cfg)
        private = excluded or (self.cfg.privacy.get("skip_private_windows", True)
                               and (win.private_window or rules.looks_private(win.title)))
        passive = rules.is_passive_media(win.app, win.url)
        away = idle >= self.idle_threshold and not passive
        key = (win.app, win.title, win.url)
        changed = key != self._last_key
        self._last_key = key

        # how long until the next check
        if away:
            delay = pol.away_interval
        elif changed or idle < 2:
            delay = pol.base_interval
        else:
            delay = min(pol.max_interval, max(pol.base_interval, self.next_delay * 1.5))
        typing = self.cap.seconds_since_typing() <= min(self.next_delay, 30) + 0.5

        snap = Snapshot(ts=now, app=win.app, bundle_id=win.bundle_id, title=win.title, url=win.url,
                        idle_s=idle, typing=typing, private=private, away=away, span=delay)
        if private:
            snap.title, snap.url = "", ""
        elif not away and self._want_ocr(pol, key, win, now, idle, typing, passive):
            try:
                text = self.cap.read_screen_text(win, fast=pol.ocr_fast)
            except Exception as e:  # noqa: BLE001 - never let OCR kill recording
                log.warning("OCR failed: %s", e)
                text = None
            self.stats["ocr"] += 1
            if text is not None:
                if not (key == self._last_ocr_key and text == self._last_ocr_text):
                    snap.ocr = text
                self._last_ocr_text = text
            self._last_ocr_key, self._last_ocr_ts = key, now
            self._ocr_seen[key] = now
            self._ocr_seen.move_to_end(key)
            while len(self._ocr_seen) > 200:
                self._ocr_seen.popitem(last=False)
        if not away:
            self.active_today_s += delay
        self.next_delay = delay
        for fn in self.listeners:
            try:
                fn(snap)
            except Exception:  # noqa: BLE001 - the live view must never break recording
                log.exception("live listener failed")
        with self._buf_lock:
            self._buffer.append(snap)
            due = len(self._buffer) >= FLUSH_EVERY_N or now - self._last_flush > FLUSH_EVERY_S
        if due:
            self.flush()
        return snap

    def _want_ocr(self, pol: Policy, key, win, now: float, idle: float, typing: bool, passive: bool) -> bool:
        if not pol.ocr:
            return False
        if key != self._last_ocr_key:
            seen = self._ocr_seen.get(key)
            # bouncing back to a window read moments ago: nothing new unless you're typing there
            return seen is None or now - seen >= pol.ocr_revisit_s or typing
        if passive:
            return False  # watching a video / on a call: the title already says what it is
        cat = rules.categorize(win.app, win.bundle_id, win.url, self.cfg.categories)
        refresh = pol.ocr_refresh_text if cat in TEXT_HEAVY else pol.ocr_refresh_other
        return refresh > 0 and idle < 15 and now - self._last_ocr_ts >= refresh

    def flush(self) -> None:
        with self._buf_lock:
            batch, self._buffer = self._buffer, []
            self._last_flush = self.clock()
        if batch:
            self.store.add_snapshots(batch)

    # ---------------------------------------------------------------- loop
    def run(self, stop: threading.Event) -> None:
        # "utility" QoS: macOS may run us on the efficiency cores and batch our wake-ups.
        set_thread_qos("utility")
        log.info("recording (%s)", self.policy.name)
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
                self.last_error = None
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                self.next_delay = 30
                log.exception("tick failed")
            self.wake.wait(max(0.5, self.next_delay - (time.monotonic() - started)))
            self.wake.clear()
            if stop.is_set():
                break
        self.flush()
