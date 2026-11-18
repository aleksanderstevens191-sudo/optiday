"""Shared app context plus the jobs that run in the background:
hourly AI pre-analysis, the end-of-day report, and data retention."""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import sys
import threading
import json
import time
from pathlib import Path

from . import analyzer, automations, calendar_source, email_source, prep, report_html, videos
from .config import Config, data_dir, load_config, reports_dir
from .llm import LLM, make_llm
from .power import PowerMonitor, set_thread_qos
from .storage import Store, open_store

log = logging.getLogger("optiday.service")


class Context:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or load_config()
        self.data_dir: Path = data_dir()
        self.store: Store = open_store(self.cfg, self.data_dir)
        self.llm: LLM | None = make_llm(self.cfg)
        self.power = PowerMonitor(self.cfg)
        self._report_lock = threading.Lock()
        self._live = None
        self.poke = None          # set by the app: wakes the recorder (e.g. after resuming)

    # ------------------------------------------------------------- live view
    def live_state(self):
        """The in-memory live tracker (created and filled from today's data on first use)."""
        if self._live is None:
            from .live import LiveState
            self._live = LiveState(self.cfg, self.store, notify=self._live_notify,
                                   paused=lambda: (self.data_dir / "paused").exists())
            self._live.seed()
        return self._live

    def _live_notify(self, title: str, text: str) -> None:
        if self.cfg.report.get("notify", True):
            notify(title, text)

    def set_paused(self, paused: bool) -> None:
        flag = self.data_dir / "paused"
        if paused:
            flag.touch()
        else:
            flag.unlink(missing_ok=True)
            if self.poke:
                self.poke()
        if self._live is not None:
            with self._live.cond:
                self._live._bump()

    def ai_allowed(self) -> bool:
        """The local model only runs when it won't drain the battery (see power.py)."""
        return self.llm is not None and self.power.policy().ai

    # ------------------------------------------------------ email & calendar
    def email_cfg(self) -> dict:
        return self.cfg.get("email", {}) or {}

    def refresh_email(self, day: str, force: bool = False, fetch=None) -> dict | None:
        """Read-only IMAP check (at most every 20 minutes). Stores the digest as note email:{day}."""
        ec = self.email_cfg()
        if not ec.get("enabled") or not ec.get("address"):
            return None
        key = f"email:{day}"
        cur = self.store.get_note(key)
        if cur and not force and time.time() - cur.get("fetched_at", 0) < 1200:
            return cur
        from . import vault
        password = vault.get_secret(f"email:{ec['address']}")
        if not password:
            err = "No password saved. Run: optiday connect-email"
            note = dict(cur or {}, error=err, fetched_at=time.time())
            self.store.put_note(key, day, note)
            return note
        try:
            mail = (fetch or email_source.fetch)(ec["address"], password, ec.get("imap_host", ""),
                                                 int(ec.get("days_back", 3)))
            start, end = self.store.day_bounds(day)
            dig = email_source.digest(mail, ec["address"], start, end, now=min(time.time(), end))
            dig.update(fetched_at=time.time(), error="")
        except Exception as e:  # noqa: BLE001 - offline, wrong password, server hiccup
            log.warning("email check failed: %s", e)
            dig = dict(cur or {"sent": [], "needs_reply": []}, error=_friendly_mail_error(e),
                       fetched_at=time.time())
        self.store.put_note(key, day, dig)
        return dig

    def refresh_calendar(self, date: str, force: bool = False, fetch=None) -> dict | None:
        urls = [u for u in (self.cfg.get("calendar", {}) or {}).get("ics_urls", []) if str(u).strip()]
        if not urls:
            return None
        key = f"calendar:{date}"
        cur = self.store.get_note(key)
        if cur and not force and time.time() - cur.get("fetched_at", 0) < 3600:
            return cur
        events, errors = calendar_source.events_for_day(urls, dt.date.fromisoformat(date),
                                                        fetch=fetch or calendar_source.fetch_ics)
        if errors and not events and cur:
            note = dict(cur, errors=errors, fetched_at=time.time())  # keep the last good copy when offline
        else:
            note = {"events": events, "errors": errors, "fetched_at": time.time()}
        self.store.put_note(key, date, note)
        return note

    def refresh_sources(self, day: str) -> None:
        """Fresh mail/calendar for today's (or yesterday's) report. Older days keep what was saved then."""
        d = dt.date.fromisoformat(day)
        if d < dt.date.fromisoformat(self.store.today()) - dt.timedelta(days=1):
            return
        nxt = (d + dt.timedelta(days=1)).isoformat()
        for fn, arg in ((self.refresh_email, day), (self.refresh_calendar, day), (self.refresh_calendar, nxt)):
            try:
                fn(arg)
            except Exception:  # noqa: BLE001
                log.exception("refreshing %s failed", fn.__name__)

    # ------------------------------------------------------------ today page
    def today_plan(self, use_ai: bool | None = None) -> dict:
        """The plan for today: last night's prep, refreshed with this morning's calendar and inbox."""
        today = self.store.today()
        yesterday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
        plan = dict(self.store.get_note(f"prep:{today}") or {})
        plan.update(for_day=today)
        self.refresh_sources(today)
        cal = self.store.get_note(f"calendar:{today}")
        if cal is not None:
            old = {(e.get("uid"), e.get("start")): e for e in plan.get("meetings", [])}
            plan["meetings"] = [dict(e, context=old.get((e.get("uid"), e.get("start")), {}).get("context", []))
                                for e in cal.get("events", [])]
            plan["calendar_connected"] = True
            plan["calendar_error"] = "; ".join(cal.get("errors", []))
        mail = self.store.get_note(f"email:{today}") or self.store.get_note(f"email:{yesterday}")
        if mail is not None:
            replies = [dict(r) for r in mail.get("needs_reply", [])]
            if use_ai is None:
                use_ai = self.ai_allowed()
            prep.add_drafts(replies, self.store, today, self.cfg, self.llm if use_ai else None)
            plan.update(replies=replies, email_connected=True, email_error=mail.get("error", ""))
        plan.setdefault("meetings", [])
        plan.setdefault("replies", [])
        plan.setdefault("left_off", [])
        plan.setdefault("next_steps", [])
        plan.setdefault("priority", "")
        plan["agents"] = automations.queue(self.store)
        plan["agent_names"] = {n: automations.agent_label(self.cfg, n) for n in automations.agents_available(self.cfg)}
        plan["default_agent"] = (self.cfg.get("agents", {}) or {}).get("default", "claude")
        plan["hours"] = analyzer.hours_for(self.store, self.cfg, today, None)
        plan["email_enabled"] = bool(self.email_cfg().get("enabled"))
        plan["calendar_enabled"] = bool((self.cfg.get("calendar", {}) or {}).get("ics_urls"))
        return plan

    def build_report(self, day: str, rebuild: bool = True, progress=None, use_ai: bool | None = None) -> dict:
        if use_ai is None:
            use_ai = self.ai_allowed()
        if rebuild:
            self.refresh_sources(day)
        with self._report_lock:
            report = analyzer.load_or_build(self.store, self.cfg, day, self.llm if use_ai else None,
                                            rebuild=rebuild, progress=progress)
            if not use_ai and self.llm is not None and not report.get("ai", {}).get("narrative"):
                report.setdefault("ai", {})["pending"] = True
                self.store.put_note(f"report:{day}", day, report)
                self.store.set_meta(f"ai-pending:{day}", "1")
            elif use_ai:
                self.store.set_meta(f"ai-pending:{day}", "0")
        self.write_html(report)
        return report

    def agent_view(self) -> dict:
        return {"agents": automations.queue(self.store),
                "agent_names": {n: automations.agent_label(self.cfg, n) for n in automations.agents_available(self.cfg)},
                "default_agent": (self.cfg.get("agents", {}) or {}).get("default", "claude")}

    def write_html(self, report: dict) -> Path:
        path = reports_dir() / f"{report['day']}.html"
        path.write_text(report_html.render(report, interactive=False, **self.agent_view()))
        path.chmod(0o600)
        return path

    def report_path(self, day: str) -> Path:
        return reports_dir() / f"{day}.html"


def _friendly_mail_error(e: Exception) -> str:
    msg = str(e)
    if "AUTHENTICATIONFAILED" in msg.upper() or "invalid credentials" in msg.lower() or "LOGIN failed" in msg:
        return "Your email provider rejected the app password. Make a new one and run: optiday connect-email"
    if "timed out" in msg.lower() or "nodename" in msg.lower() or "network" in msg.lower():
        return "Couldn't reach the mail server (offline?). Will retry."
    return f"Email check failed: {msg[:160]}"


def notify(title: str, text: str) -> None:
    if sys.platform != "darwin":
        log.info("%s: %s", title, text)
        return
    t = text.replace('"', "'")
    ti = title.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{t}" with title "{ti}"'], check=False,
                   capture_output=True)


def open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    else:
        log.info("open %s", url)


def _parse_hhmm(s: str) -> tuple[int, int]:
    try:
        h, m = s.split(":")
        return int(h), int(m)
    except ValueError:
        return 18, 0


class Scheduler:
    """Runs alongside the recorder. Wakes every couple of minutes; the heavy work (local AI)
    only runs while the Mac is plugged in."""

    def __init__(self, ctx: Context, dashboard_url: str | None = None, on_report=None, before_report=None,
                 recorder=None, runner=None):
        self.ctx = ctx
        self.dashboard_url = dashboard_url
        self.on_report = on_report
        self.before_report = before_report
        self.recorder = recorder
        self._last_bg = 0.0
        self._last_videos = 0.0
        self._hour = dt.datetime.now().strftime("%Y-%m-%dT%H")
        self.status = "idle"
        self.runner = runner or automations.Runner(ctx.store, ctx.cfg, notify=self._notify_agent)
        self.energy = None
        if recorder is not None:
            from .energy import HourLog
            self.energy = HourLog(ctx.store, recorder.energy._cpu, lambda: dict(recorder.stats))

    def _notify_agent(self, title: str, text: str) -> None:
        if self.ctx.cfg.report.get("notify", True):
            notify(title, text)

    def run(self, stop: threading.Event) -> None:
        # "background" QoS: report building and AI requests run on the efficiency cores.
        set_thread_qos("background")
        self.runner.recover()
        stop.wait(30)
        self._catch_up()
        while not stop.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001
                log.exception("scheduler step failed")
            stop.wait(120)

    def _catch_up(self) -> None:
        """If the Mac slept through yesterday's report time, build it now."""
        store = self.ctx.store
        today = store.today()
        for day in store.days()[:3]:
            if day < today and store.get_note(f"report:{day}") is None:
                log.info("catching up report for %s", day)
                self._generate(day, announce=False)

    def step(self) -> None:
        cfg = self.ctx.cfg
        store = self.ctx.store
        now = dt.datetime.now()
        today = store.today()
        ai_ok = self.ctx.ai_allowed()
        if self.energy is not None:
            pol = self.ctx.power.policy()
            self.energy.tick(pol.name, self.ctx.power.state().on_battery)
        self.runner.start_due()
        hour = now.strftime("%Y-%m-%dT%H")
        if hour != self._hour:
            self._hour = hour
            self._hour_done(today, ai_ok)
        if self._morning_due(now, today):
            self._morning_brief(today)
        h, m = _parse_hhmm(cfg.report.get("time", "18:00"))
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        done_key = f"report-sent:{today}"
        if now >= due and store.get_meta(done_key) is None and store.count_snapshots(today) > 0:
            if self.before_report:
                self.before_report()
            self._generate(today, announce=True)
            store.set_meta(done_key, str(time.time()))
            store.purge_ocr_older_than(int(cfg.rec.get("retention_days", 30)))
            return
        if not ai_ok:
            return
        # Plugged in: ask the local model about videos that are still "not sure" (each one once).
        if time.time() - self._last_videos > 600:
            self._last_videos = time.time()
            try:
                if videos.ai_judge_unsure(store, cfg, self.ctx.llm, today) and self.ctx._live is not None:
                    self.ctx._live.rejudge()
            except Exception:  # noqa: BLE001
                log.exception("video check failed")
        # Plugged in again: add the AI summary to any report that was built on battery.
        for day in store.days()[:3]:
            if store.get_meta(f"ai-pending:{day}") == "1":
                self.status = "adding AI summary"
                try:
                    report = self.ctx.build_report(day, use_ai=True)
                finally:
                    self.status = "idle"
                if report.get("ai", {}).get("narrative") and cfg.report.get("notify", True):
                    notify("OptiDay", f"AI summary added to your {day} report")
                return
        if cfg.ai.get("background_analysis", True) and time.time() - self._last_bg > 3600:
            self._last_bg = time.time()
            self.status = "pre-analyzing"
            try:
                analyzer.analyze_day(store, cfg, today, self.ctx.llm, narrative=False, background=True)
            finally:
                self.status = "idle"

    # ------------------------------------------------------------ hourly line
    def _hour_done(self, today: str, ai_ok: bool) -> None:
        """An hour just ended: summarize it for the menu bar (and a notification if you want one)."""
        if self.before_report:
            self.before_report()
        prev = dt.datetime.now().replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
        day = self.ctx.store.day_of(prev.timestamp() + 1)
        try:
            hours = analyzer.hours_for(self.ctx.store, self.ctx.cfg, day, self.ctx.llm if ai_ok else None)
        except Exception:  # noqa: BLE001
            log.exception("hour summary failed")
            return
        h = next((x for x in hours if abs(x["start"] - prev.timestamp()) < 1), None)
        if not h or h["active_s"] < 120:
            return
        line = h["ai"] or h["text"]
        self.ctx.store.set_meta("last-hour", json.dumps({"label": h["label"], "text": line, "day": day}))
        if self.ctx.cfg.report.get("hourly_notification", False):
            notify(f"OptiDay · {h['label']}", line)

    # ---------------------------------------------------------- morning brief
    def _morning_due(self, now: dt.datetime, today: str) -> bool:
        mode = str(self.ctx.cfg.report.get("morning_brief", "open")).lower()
        if mode == "off" or self.ctx.store.get_meta(f"brief-sent:{today}"):
            return False
        h, m = _parse_hhmm(self.ctx.cfg.report.get("morning_brief_after", "06:00"))
        if now < now.replace(hour=h, minute=m, second=0, microsecond=0) or now.hour >= 12:
            return False
        # only once you're actually at the Mac
        if self.recorder is not None:
            return not self.recorder.paused and self.recorder.cap.idle_seconds() < 60
        return True

    def _morning_brief(self, today: str) -> None:
        self.ctx.store.set_meta(f"brief-sent:{today}", str(time.time()))
        self.status = "preparing your day"
        try:
            plan = self.ctx.today_plan()
        except Exception:  # noqa: BLE001
            log.exception("morning brief failed")
            return
        finally:
            self.status = "idle"
        if not (plan.get("meetings") or plan.get("replies") or plan.get("left_off") or plan.get("agents")):
            return
        if self.ctx.cfg.report.get("notify", True):
            notify("Your day, ready", prep.brief_line(plan))
        if str(self.ctx.cfg.report.get("morning_brief", "open")).lower() == "open" and self.dashboard_url:
            open_url(f"{self.dashboard_url}/plan")

    def _generate(self, day: str, announce: bool) -> dict:
        self.status = "building report"
        try:
            report = self.ctx.build_report(day)
        finally:
            self.status = "idle"
        if announce:
            m = report["metrics"]
            from .rules import fmt_dur
            headline = report["insights"].get("headline") or (
                f"{fmt_dur(m.get('active_s', 0))} active, {m.get('score', 0)}% productive")
            if report.get("ai", {}).get("pending"):
                headline += " (AI summary added when you plug in)"
            if self.ctx.cfg.report.get("notify", True):
                notify("Your OptiDay report is ready", headline)
            if self.ctx.cfg.report.get("open_automatically", True):
                open_url(f"{self.dashboard_url}/day/{day}" if self.dashboard_url
                         else self.ctx.report_path(day).as_uri())
        if self.on_report:
            self.on_report(report)
        return report
