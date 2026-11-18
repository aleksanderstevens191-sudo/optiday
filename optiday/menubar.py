"""The menu bar app: recorder + scheduler + dashboard in one process."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time

import rumps  # type: ignore

from . import automations, system
from .capture import default_capturer
from .config import config_path
from .dashboard import Dashboard
from .recorder import Recorder
from .rules import fmt_dur
from .service import Context, Scheduler, notify, open_url

log = logging.getLogger("optiday.app")


def _hm(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}:{m % 60:02d}"


class OptiDayApp(rumps.App):
    def __init__(self, ctx: Context, recorder: Recorder, dashboard: Dashboard, scheduler: Scheduler):
        super().__init__("OptiDay", title="◉", quit_button=None)
        self.ctx, self.rec, self.dash, self.sched = ctx, recorder, dashboard, scheduler
        self.status_item = rumps.MenuItem("Starting…")
        self.hour_item = rumps.MenuItem("Last hour: summary appears after your first full hour",
                                        callback=self.open_plan)
        self.energy_item = rumps.MenuItem("Energy: measuring…")
        self.agents_item = rumps.MenuItem("Agent tasks", callback=self.open_agents)
        self._agents_checked = -1e9
        self.pause_item = rumps.MenuItem("Pause recording", callback=self.toggle_pause)
        self.menu = [
            self.status_item,
            self.hour_item,
            self.energy_item,
            self.pause_item,
            None,
            rumps.MenuItem("Live view", callback=self.open_live),
            rumps.MenuItem("Today's plan", callback=self.open_plan),
            rumps.MenuItem("Open today's report", callback=self.open_today),
            self.agents_item,
            rumps.MenuItem("Rebuild today's report now", callback=self.build_now),
            rumps.MenuItem("All days…", callback=self.open_dashboard),
            None,
            rumps.MenuItem("Settings…", callback=self.open_settings),
            rumps.MenuItem("Run setup check", callback=self.run_doctor),
            rumps.MenuItem("Restart OptiDay", callback=self.restart),
            None,
            rumps.MenuItem("Quit OptiDay", callback=self.quit),
        ]
        self.timer = rumps.Timer(self.refresh, 20)
        self.timer.start()
        try:  # let macOS batch this wake-up with others
            self.timer._nstimer.setTolerance_(5.0)
        except Exception:  # noqa: BLE001
            pass

    def refresh(self, _=None) -> None:
        if self.rec.paused:
            self.title = "⏸"
            self.status_item.title = "Paused (nothing is being recorded)"
            self.pause_item.title = "Resume recording"
            return
        self.pause_item.title = "Pause recording"
        active = self.rec.active_today_s
        self.title = f"◉ {_hm(active)}"
        status = f"Recording · {fmt_dur(active)} active today"
        if self.rec.last_error:
            status = f"Problem: {self.rec.last_error[:60]} (try Run setup check)"
        if self.sched.status != "idle":
            status += f" · {self.sched.status}…"
        self.status_item.title = status
        pol = self.rec.policy
        self.energy_item.title = (f"Energy: {pol.name} mode · OptiDay uses {self.rec.energy.cpu_percent():.1f}% CPU"
                                  + (" · screen reading off" if not pol.ocr else ""))
        last = self.ctx.store.get_meta("last-hour")
        if last:
            try:
                h = json.loads(last)
                text = h["text"]
                self.hour_item.title = f"{h['label']}: " + (text if len(text) <= 90 else text[:88] + "…")
            except (ValueError, KeyError):
                pass
        now = time.monotonic()
        if now - self._agents_checked > 300:  # cheap, but no need to look every 20s
            self._agents_checked = now
            waiting = [a for a in automations.queue(self.ctx.store) if a["status"] == "suggested"]
            self.agents_item.title = (f"Agent tasks · {len(waiting)} waiting for your OK" if waiting
                                      else "Agent tasks")

    def toggle_pause(self, _):
        self.ctx.set_paused(not self.rec.paused)
        self.refresh()

    def open_today(self, _):
        open_url(f"{self.dash.url}/today")

    def open_live(self, _):
        open_url(f"{self.dash.url}/live")

    def open_plan(self, _):
        open_url(f"{self.dash.url}/plan")

    def open_agents(self, _):
        open_url(f"{self.dash.url}/plan#agents")

    def open_dashboard(self, _):
        open_url(self.dash.url)

    def build_now(self, _):
        day = self.ctx.store.today()
        self.rec.flush()
        notify("OptiDay", "Building today's report…")

        def work():
            self.sched.status = "building report"
            try:
                self.ctx.build_report(day)
            finally:
                self.sched.status = "idle"
            open_url(f"{self.dash.url}/day/{day}")
        threading.Thread(target=work, daemon=True).start()

    def open_settings(self, _):
        subprocess.run(["open", "-t", str(config_path())], check=False)

    def run_doctor(self, _):
        rows = system.doctor(request=True)
        lines = []
        for ok, what, fix in rows:
            lines.append(("✅ " if ok else "⚠️ ") + what + ("" if ok or not fix else f"\n     → {fix}"))
        rumps.alert(title="OptiDay setup check", message="\n".join(lines), ok="Done")

    def restart(self, _):
        self.rec.flush()
        if not system.restart_agent():
            os.execv(sys.executable, [sys.executable, "-m", "optiday", "app"])

    def quit(self, _):
        self.rec.flush()
        rumps.quit_application()


def run_app() -> None:
    ctx = Context()
    cap = default_capturer(ctx.cfg)
    from .capture import macos
    if not macos.screen_recording_granted():
        macos.request_screen_recording()  # shows the macOS permission prompt once
    rec = Recorder(ctx.cfg, ctx.store, cap, ctx.data_dir, power=ctx.power)
    stop = threading.Event()
    keep = [macos.keep_awake_for_recording(),
            macos.watch_workspace(on_switch=rec.poke, on_sleep=rec.on_sleep, on_wake=rec.poke)]
    ctx.poke = rec.poke
    rec.listeners.append(ctx.live_state().ingest)
    threading.Thread(target=rec.run, args=(stop,), name="recorder", daemon=True).start()
    runner = automations.Runner(ctx.store, ctx.cfg, notify=notify)
    dash = Dashboard(ctx, runner=runner).start()
    ctx.store.set_meta("dashboard-port", str(dash.port))
    sched = Scheduler(ctx, dash.url, before_report=rec.flush, recorder=rec, runner=runner)
    threading.Thread(target=sched.run, args=(stop,), name="scheduler", daemon=True).start()
    try:
        from AppKit import NSApplication  # type: ignore
        NSApplication.sharedApplication().setActivationPolicy_(1)  # menu bar only, no Dock icon
    except Exception:  # noqa: BLE001
        pass
    app = OptiDayApp(ctx, rec, dash, sched)
    try:
        app.run()
    finally:
        stop.set()
        rec.poke()
        rec.flush()
        del keep
