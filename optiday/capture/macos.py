"""macOS capture using public Apple APIs via PyObjC.

* Front app / window title: NSWorkspace + CGWindowList (needs Screen Recording)
* Browser tab URL + title: AppleScript (asks once per browser for Automation access)
* Idle + typing timing:    CGEventSource counters (no keystrokes are ever read)
* On-screen text:          `screencapture` of the front window + Vision OCR, then
                           the image is deleted
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path

import objc  # type: ignore
import Quartz  # type: ignore
import Vision  # type: ignore
from AppKit import NSWorkspace  # type: ignore
import Foundation  # type: ignore
from Foundation import NSURL, NSDate, NSObject, NSRunLoop  # type: ignore

from ..config import data_dir
from ..models import WindowInfo
from ..rules import BROWSER_BUNDLES

log = logging.getLogger("optiday.capture")

_ANY_INPUT = getattr(Quartz, "kCGAnyInputEventType", 0xFFFFFFFF)
_STATE = Quartz.kCGEventSourceStateCombinedSessionState

_CHROMIUM_JS = """tell application id "{bid}"
  if (count of windows) = 0 then return ""
  set w to front window
  set m to "normal"
  try
    set m to mode of w
  end try
  return (URL of active tab of w) & linefeed & (title of active tab of w) & linefeed & m
end tell"""

_SAFARI_JS = """tell application id "{bid}"
  if (count of documents) = 0 then return ""
  return (URL of front document) & linefeed & (name of front document) & linefeed & "normal"
end tell"""

_LOCK_APPS = {"loginwindow", "ScreenSaverEngine"}


class MacCapturer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tmp = data_dir() / "tmp"
        self.tmp.mkdir(exist_ok=True)
        os.chmod(self.tmp, 0o700)
        self._ws = NSWorkspace.sharedWorkspace()
        self._script_blocked_until: dict[str, float] = {}
        self._tab_cache: dict[str, tuple[str, str, str, bool, float]] = {}
        self._keep = bool(cfg.rec.get("keep_screenshots"))
        self._last_shot: tuple[int, str, str | None] | None = None  # (window id, image hash, text)

    # ---------------------------------------------------------------- state
    def _pump_runloop(self) -> None:
        # Lets NSWorkspace notice app switches when we're not inside an NSApp loop.
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))

    def screen_locked(self) -> bool:
        try:
            d = Quartz.CGSessionCopyCurrentDictionary()
            return bool(d and d.get("CGSSessionScreenIsLocked", False))
        except Exception:  # noqa: BLE001
            return False

    def idle_seconds(self) -> float:
        return float(Quartz.CGEventSourceSecondsSinceLastEventType(_STATE, _ANY_INPUT))

    def seconds_since_typing(self) -> float:
        return float(Quartz.CGEventSourceSecondsSinceLastEventType(_STATE, Quartz.kCGEventKeyDown))

    def front_window(self) -> WindowInfo | None:
        with objc.autorelease_pool():
            self._pump_runloop()
            if self.screen_locked():
                return None
            app = self._ws.frontmostApplication()
            if app is None:
                return None
            name = str(app.localizedName() or "")
            if name in _LOCK_APPS:
                return None
            bid = str(app.bundleIdentifier() or "")
            pid = int(app.processIdentifier())
            title, wid = self._window_for_pid(pid)
            if not title:
                title = self._ax_title(pid)
            info = WindowInfo(app=name, bundle_id=bid, pid=pid, title=title, window_id=wid)
            if bid in BROWSER_BUNDLES:
                self._add_browser_tab(info)
            return info

    def _window_for_pid(self, pid: int) -> tuple[str, int]:
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
        for w in wins:
            if w.get(Quartz.kCGWindowOwnerPID) != pid or w.get(Quartz.kCGWindowLayer, 0) != 0:
                continue
            b = w.get(Quartz.kCGWindowBounds) or {}
            if b.get("Height", 0) < 60 or b.get("Width", 0) < 60:
                continue
            return str(w.get(Quartz.kCGWindowName) or ""), int(w.get(Quartz.kCGWindowNumber) or 0)
        return "", 0

    def _ax_title(self, pid: int) -> str:
        try:
            from ApplicationServices import (  # type: ignore
                AXIsProcessTrusted, AXUIElementCopyAttributeValue, AXUIElementCreateApplication,
                kAXFocusedWindowAttribute, kAXTitleAttribute)
        except ImportError:
            return ""
        if not AXIsProcessTrusted():
            return ""
        try:
            el = AXUIElementCreateApplication(pid)
            err, win = AXUIElementCopyAttributeValue(el, kAXFocusedWindowAttribute, None)
            if err or win is None:
                return ""
            err, title = AXUIElementCopyAttributeValue(win, kAXTitleAttribute, None)
            return str(title or "") if not err else ""
        except Exception:  # noqa: BLE001
            return ""

    def _add_browser_tab(self, info: WindowInfo) -> None:
        kind = BROWSER_BUNDLES[info.bundle_id]
        if kind == "firefox":
            return  # Firefox has no AppleScript tab API; window title + OCR still work
        now = time.time()
        # Asking the browser costs a helper process, so only ask when the tab (window title)
        # changed. Without window titles (no permission yet) ask at most every 30s.
        cached = self._tab_cache.get(info.bundle_id)
        if cached:
            c_wtitle, c_url, c_title, c_priv, c_at = cached
            fresh = (info.title and c_wtitle == info.title and now - c_at < 600) or \
                    (not info.title and now - c_at < 30)
            if fresh:
                info.url, info.title, info.private_window = c_url, c_title or info.title, c_priv
                return
        window_title = info.title
        if now < self._script_blocked_until.get(info.bundle_id, 0):
            return
        src = (_SAFARI_JS if kind == "safari" else _CHROMIUM_JS).format(bid=info.bundle_id)
        try:
            r = subprocess.run(["osascript", "-e", src], capture_output=True, text=True, timeout=3)
        except subprocess.TimeoutExpired:
            return
        if r.returncode != 0:
            # -1743 = user hasn't allowed Automation yet; back off instead of nagging every tick
            backoff = 300 if "-1743" in r.stderr else 30
            self._script_blocked_until[info.bundle_id] = time.time() + backoff
            log.debug("tab lookup failed for %s: %s", info.app, r.stderr.strip())
            return
        parts = r.stdout.rstrip("\n").split("\n")
        if len(parts) >= 2:
            info.url = parts[0].strip()
            info.title = parts[1].strip() or info.title
            info.private_window = len(parts) >= 3 and parts[2].strip().lower() == "incognito"
            self._tab_cache[info.bundle_id] = (window_title, info.url, info.title, info.private_window, now)

    # ------------------------------------------------------------------ OCR
    def read_screen_text(self, win: WindowInfo, fast: bool = False) -> str | None:
        path = self.tmp / f"{uuid.uuid4().hex}.jpg"
        cmd = ["screencapture", "-x", "-o", "-t", "jpg"]
        cmd += ["-l", str(win.window_id)] if win.window_id else ["-m"]
        try:
            subprocess.run(cmd + [str(path)], capture_output=True, timeout=8)
            if not path.exists() or path.stat().st_size == 0:
                return None
            # Text recognition is the expensive part. If the window looks exactly like last
            # time (pixel-identical capture), reuse the text instead of recognizing it again.
            digest = hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
            if self._last_shot and self._last_shot[:2] == (win.window_id, digest):
                return self._last_shot[2]
            text = self._ocr(path, fast)
            self._last_shot = (win.window_id, digest, text)
            return text
        except subprocess.TimeoutExpired:
            return None
        finally:
            self._dispose(path)

    def _dispose(self, path: Path) -> None:
        if not path.exists():
            return
        if self._keep:
            dest = data_dir() / "screenshots" / time.strftime("%Y-%m-%d")
            dest.mkdir(parents=True, exist_ok=True)
            path.rename(dest / f"{time.strftime('%H%M%S')}.jpg")
        else:
            path.unlink(missing_ok=True)

    def _ocr(self, path: Path, fast: bool = False) -> str | None:
        with objc.autorelease_pool():
            url = NSURL.fileURLWithPath_(str(path))
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
            req = Vision.VNRecognizeTextRequest.alloc().init()
            # "fast" skips the language model pass: a fraction of the energy, slightly noisier text
            req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast if fast
                                     else Vision.VNRequestTextRecognitionLevelAccurate)
            req.setUsesLanguageCorrection_(not fast)
            ok, _err = handler.performRequests_error_([req], None)
            if not ok:
                return None
            items = []
            for obs in req.results() or []:
                cands = obs.topCandidates_(1)
                if not cands:
                    continue
                box = obs.boundingBox()
                top = 1.0 - (box.origin.y + box.size.height)
                items.append((round(top * 60), box.origin.x, str(cands[0].string())))
            items.sort()
            return "\n".join(t for _, _, t in items)


# ------------------------------------------------------------ permissions ----

def screen_recording_granted() -> bool:
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except AttributeError:
        return True


def request_screen_recording() -> bool:
    try:
        return bool(Quartz.CGRequestScreenCaptureAccess())
    except AttributeError:
        return True


def accessibility_granted(prompt: bool = False) -> bool:
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt  # type: ignore
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    except Exception:  # noqa: BLE001
        return False


def open_privacy_pane(name: str) -> None:
    """name: ScreenCapture | Accessibility | Automation"""
    subprocess.run(["open", f"x-apple.systempreferences:com.apple.preference.security?Privacy_{name}"],
                   check=False)


# ------------------------------------------------------- events & App Nap ----

_CALLBACKS: dict[str, object] = {}


class _WorkspaceObserver(NSObject):  # type: ignore[misc]
    """Turns macOS workspace notifications into Python callbacks."""

    def handle_(self, note):  # noqa: N802 - ObjC selector "handle:"
        try:
            cb = _CALLBACKS.get(str(note.name()))
            if cb:
                cb()
        except Exception:  # noqa: BLE001
            log.exception("notification handler failed")


def watch_workspace(on_switch, on_sleep, on_wake):
    """App switches, sleep and wake arrive as events, so the recorder doesn't need to poll fast.
    Keep the returned object alive for as long as you want notifications."""
    import AppKit  # type: ignore
    names = {
        "NSWorkspaceDidActivateApplicationNotification": on_switch,
        "NSWorkspaceActiveSpaceDidChangeNotification": on_switch,
        "NSWorkspaceWillSleepNotification": on_sleep,
        "NSWorkspaceScreensDidSleepNotification": on_sleep,
        "NSWorkspaceDidWakeNotification": on_wake,
        "NSWorkspaceScreensDidWakeNotification": on_wake,
        "NSWorkspaceSessionDidBecomeActiveNotification": on_wake,
    }
    obs = _WorkspaceObserver.alloc().init()
    center = NSWorkspace.sharedWorkspace().notificationCenter()
    for name, cb in names.items():
        const = getattr(AppKit, name, name)
        _CALLBACKS[str(const)] = cb
        center.addObserver_selector_name_object_(obs, "handle:", const, None)
    return obs


def keep_awake_for_recording():
    """Stop App Nap from freezing the recorder (it would miss what you do). The system can
    still sleep normally; our own scheduling keeps energy use low."""
    from Foundation import NSProcessInfo  # type: ignore
    opts = getattr(Foundation, "NSActivityUserInitiatedAllowingIdleSystemSleep", 0x00EFFFFF)
    return NSProcessInfo.processInfo().beginActivityWithOptions_reason_(opts, "Recording your day for OptiDay")
