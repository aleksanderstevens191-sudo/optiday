"""Tiny local web dashboard on 127.0.0.1 (never exposed to the network)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from importlib import resources

from . import automations, live_html, report_html
from .rules import fmt_dur
from .service import Context

log = logging.getLogger("optiday.dashboard")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _nav(day: str, days: list[str]) -> str:
    i = days.index(day) if day in days else -1
    newer = days[i - 1] if i > 0 else None
    older = days[i + 1] if 0 <= i < len(days) - 1 else None
    parts = ['<nav class="nav"><a href="/live">Live</a><a href="/plan">Today</a><a href="/">All days</a>']
    if older:
        parts.append(f'<a href="/day/{older}">← {older}</a>')
    if newer:
        parts.append(f'<a href="/day/{newer}">{newer} →</a>')
    parts.append(f'<span class="sp"></span><a href="/day/{day}?refresh=1">Refresh with latest activity</a></nav>')
    return "".join(parts)


def make_handler(ctx: Context, port: int, runner=None):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "OptiDay"
        server_runner = runner

        def log_message(self, fmt, *args):  # quiet
            log.debug(fmt, *args)

        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding: only answer requests addressed to localhost.
            return self.headers.get("Host", "") in allowed_hosts

        def _send(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8") -> None:
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; manifest-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, "forbidden", "text/plain")
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):
                return self._send(200, self._index())
            if path == "/today":
                self.send_response(302)
                self.send_header("Location", f"/day/{ctx.store.today()}")
                self.end_headers()
                return
            if path == "/live":
                return self._send(200, live_html.render_live(ctx.live_state().snapshot()))
            if path == "/api/live":
                if self.headers.get("X-OptiDay") != "1":
                    return self._send(403, "forbidden", "text/plain")
                m = re.search(r"(?:^|&)v=(\d+)", query)
                st = ctx.live_state().wait(int(m.group(1)) if m else -1, timeout=25.0)
                return self._send(200, json.dumps(st), "application/json")
            if path == "/manifest.webmanifest":
                return self._send(200, live_html.MANIFEST, "application/manifest+json")
            if path == "/icon.svg":
                return self._send(200, live_html.ICON_SVG, "image/svg+xml")
            if path == "/icon-512.png":
                data = resources.files("optiday").joinpath("static/icon-512.png").read_bytes()
                return self._send(200, data, "image/png")
            if path == "/plan":
                plan = ctx.today_plan(use_ai=False)  # drafts are prepared in the background, not here
                nav = '<nav class="nav"><a href="/">All days</a><span class="sp"></span>' \
                      f'<a href="/day/{ctx.store.today()}">Today\'s full report</a></nav>'
                return self._send(200, report_html.render_plan(plan, nav=nav))
            m = re.match(r"^/day/(\d{4}-\d{2}-\d{2})$", path)
            if m:
                day = m.group(1)
                refresh = "refresh=1" in query
                report = ctx.store.get_note(f"report:{day}")
                stale = report is not None and day == ctx.store.today() and \
                    time.time() - report.get("generated_at", 0) > 600
                if report is None or refresh or stale:
                    report = ctx.build_report(day, rebuild=True)
                days = ctx.store.days()
                return self._send(200, report_html.render(report, interactive=True, nav=_nav(day, days),
                                                          **ctx.agent_view()))
            if path == "/api/status":
                return self._send(200, json.dumps({"ok": True, "today": ctx.store.today()}), "application/json")
            return self._send(404, "not found", "text/plain")

        def do_POST(self):  # noqa: N802
            # Custom header => browsers must preflight cross-site requests, which we never approve.
            if not self._host_ok() or self.headers.get("X-OptiDay") != "1":
                return self._send(403, "forbidden", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(min(n, 65_536)) or b"{}")
            except ValueError:
                return self._send(400, "bad json", "text/plain")
            if self.path == "/api/label":
                vid, label, day = str(body.get("video_id", "")), body.get("label"), str(body.get("day", ""))
                if not re.fullmatch(r"[\w-]{1,40}", vid) or label not in ("informational", "waste", "unclear") \
                        or (day and not _DAY.match(day)):
                    return self._send(400, "bad request", "text/plain")
                live = ctx.live_state()
                live.label_video(vid, label)   # remembered for this video and its channel
                if day:                        # from a report page: rebuild that report
                    ctx.build_report(day, rebuild=True)
                return self._send(200, json.dumps({"ok": True} | live.snapshot()), "application/json")
            if self.path == "/api/focus":
                live = ctx.live_state()
                if body.get("action") == "start":
                    try:
                        minutes = int(body.get("minutes", 50))
                    except (TypeError, ValueError):
                        return self._send(400, "bad minutes", "text/plain")
                    live.start_focus(minutes, str(body.get("label") or "")[:60])
                elif body.get("action") == "stop":
                    live.stop_focus()
                else:
                    return self._send(400, "bad action", "text/plain")
                return self._send(200, json.dumps(live.snapshot()), "application/json")
            if self.path == "/api/pause":
                if not isinstance(body.get("paused"), bool):
                    return self._send(400, "bad request", "text/plain")
                ctx.set_paused(body["paused"])
                return self._send(200, json.dumps(ctx.live_state().snapshot()), "application/json")
            if self.path == "/api/automation":
                aid, action = str(body.get("id", "")), str(body.get("action", ""))
                task = body.get("task")
                if not re.fullmatch(r"[0-9a-f]{12}", aid) or action not in (
                        "approve", "run_now", "dismiss", "unapprove", "edit"):
                    return self._send(400, "bad request", "text/plain")
                if task is not None and (not isinstance(task, str) or not task.strip()):
                    return self._send(400, "empty task", "text/plain")
                try:
                    automations.update(ctx.store, ctx.cfg, aid, action, task=task)
                except KeyError:
                    return self._send(404, "no such task", "text/plain")
                if action == "run_now" and self.server_runner is not None:
                    self.server_runner.start_due()
                return self._send(200, '{"ok":true}', "application/json")
            return self._send(404, "not found", "text/plain")

        def _index(self) -> str:
            rows = []
            for day in ctx.store.days():
                r = ctx.store.get_note(f"report:{day}")
                label = dt.date.fromisoformat(day).strftime("%a %b %-d, %Y")
                if r and not r["metrics"].get("empty"):
                    m = r["metrics"]
                    rows.append({"day": day, "label": label, "active": fmt_dur(m["active_s"]),
                                 "score": f"{m['score']}%", "headline": r["insights"].get("headline", "")})
                else:
                    rows.append({"day": day, "label": label, "headline": "Open to build this report"})
            return report_html.render_index(rows)

    return Handler


class Dashboard:
    def __init__(self, ctx: Context, port: int | None = None, runner=None):
        self.ctx = ctx
        self.runner = runner
        self.port = int(port or ctx.cfg.report.get("dashboard_port", 8765))
        self.httpd: ThreadingHTTPServer | None = None
        self.poll_interval = 60.0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "Dashboard":
        for attempt in range(10):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(self.ctx, self.port, self.runner))
                break
            except OSError:
                self.port += 1
        if self.httpd is None:
            raise RuntimeError("couldn't open a local port for the dashboard")
        # Requests still answer instantly; the long poll interval just avoids waking the CPU
        # twice a second (the default) while nobody is looking at the dashboard.
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": self.poll_interval}, name="dashboard",
                         daemon=True).start()
        log.info("dashboard at %s", self.url)
        return self

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
