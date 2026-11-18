"""Command line: `optiday <command>`. Most people only use the menu bar."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler


def _setup_logging(verbose: bool = False) -> None:
    from .config import logs_dir
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(RotatingFileHandler(logs_dir() / "optiday.log", maxBytes=2_000_000, backupCount=3))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _resolve_day(s: str | None, store) -> str:
    if not s or s == "today":
        return store.today()
    if s == "yesterday":
        return (dt.date.fromisoformat(store.today()) - dt.timedelta(days=1)).isoformat()
    dt.date.fromisoformat(s)
    return s


def cmd_app(a) -> None:
    from .menubar import run_app
    run_app()


def cmd_record(a) -> None:
    from .capture import default_capturer
    from .dashboard import Dashboard
    from .recorder import Recorder
    from .service import Context, Scheduler
    ctx = Context()
    from .automations import Runner
    from .service import notify
    rec = Recorder(ctx.cfg, ctx.store, default_capturer(ctx.cfg), ctx.data_dir, power=ctx.power)
    stop = threading.Event()
    runner = Runner(ctx.store, ctx.cfg, notify=notify)
    ctx.poke = rec.poke
    rec.listeners.append(ctx.live_state().ingest)
    dash = Dashboard(ctx, runner=runner).start()
    ctx.store.set_meta("dashboard-port", str(dash.port))
    threading.Thread(target=Scheduler(ctx, dash.url, before_report=rec.flush, recorder=rec, runner=runner).run,
                     args=(stop,), daemon=True).start()
    print(f"Recording. Dashboard: {dash.url}  (Ctrl-C to stop)")
    try:
        while True:
            snap = rec.tick()
            if a.verbose and snap:
                print(f"{time.strftime('%H:%M:%S')} [{rec.policy.name}, next in {rec.next_delay:.0f}s] "
                      f"{snap.app} | {snap.title[:60]} | {snap.url[:50]}"
                      f"{' | typing' if snap.typing else ''}{' | away' if snap.away else ''}"
                      f"{' | ocr ' + str(len(snap.ocr)) + ' chars' if snap.ocr else ''}")
            time.sleep(rec.next_delay)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        rec.flush()


def cmd_report(a) -> None:
    from .service import Context, open_url
    ctx = Context()
    if a.no_ai:
        ctx.llm = None
    day = _resolve_day(a.day, ctx.store)
    use_ai = True if a.ai else None
    if ctx.store.count_snapshots(day) == 0:
        print(f"No activity recorded for {day}.")
    report = ctx.build_report(day, rebuild=True, progress=lambda m: print("…", m), use_ai=use_ai)
    path = ctx.report_path(day)
    if report.get("ai", {}).get("pending"):
        print("On battery: built without the local AI. Run with --ai to use it anyway.")
    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        ins = report["insights"]
        print(f"\n{day}: {ins.get('headline') or ''}")
        print(f"Report: {path}")
    if a.open:
        open_url(path.as_uri())


def cmd_dashboard(a) -> None:
    from .dashboard import Dashboard
    from .service import Context, open_url
    d = Dashboard(Context(), a.port).start()
    print(f"Dashboard: {d.url}  (Ctrl-C to stop)")
    if a.open:
        open_url(d.url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        d.stop()


def cmd_demo(a) -> None:
    """Simulated workday in a separate demo folder: try the report with zero setup."""
    from .config import data_dir
    os.environ["OPTIDAY_HOME"] = str(data_dir() / "demo")
    from .demo import demo_snapshots
    from .service import Context, open_url
    ctx = Context()
    if a.no_ai:
        ctx.llm = None
    day = (dt.date.today() - dt.timedelta(days=1)).isoformat() if dt.datetime.now().hour < 18 else ctx.store.today()
    if ctx.store.count_snapshots(day) == 0:
        ctx.store.add_snapshots(demo_snapshots(dt.date.fromisoformat(day), float(ctx.cfg.rec["interval_seconds"])))
    report = ctx.build_report(day, rebuild=True, progress=lambda m: print("…", m), use_ai=ctx.llm is not None)
    path = ctx.report_path(day)
    print(f"Demo report ({'with local AI' if report['ai']['used'] else 'rules only'}): {path}")
    if a.open:
        open_url(path.as_uri())


def cmd_doctor(a) -> None:
    from .system import doctor
    bad = 0
    for ok, what, fix in doctor(request=a.request):
        print(("  ✅ " if ok else "  ⚠️  ") + what)
        if not ok and fix:
            print("       → " + fix)
            bad += 1
    print("\nAll good." if not bad else f"\n{bad} thing(s) to fix above.")


def cmd_pause(a) -> None:
    from .config import data_dir
    (data_dir() / "paused").touch()
    print("Recording paused.")


def cmd_resume(a) -> None:
    from .config import data_dir
    (data_dir() / "paused").unlink(missing_ok=True)
    print("Recording resumed.")


def cmd_install_agent(a) -> None:
    from .system import install_agent
    print(f"Installed login item: {install_agent()}")


def cmd_uninstall_agent(a) -> None:
    from .system import TUNNEL_LABEL, uninstall_agent
    uninstall_agent()
    uninstall_agent(TUNNEL_LABEL)
    print("OptiDay will no longer start at login.")


def cmd_restart(a) -> None:
    from .system import restart_agent
    print("Restarted." if restart_agent() else "OptiDay isn't installed as a login item (run ./install.sh).")


def cmd_connect_vm(a) -> None:
    from .system import connect_vm
    cmd = connect_vm(a.target, a.ssh_port, a.remote_port, a.identity, a.model)
    print("Tunnel installed and OptiDay now uses the model on your server.\n  " + cmd)
    print("Check it with: optiday doctor")


def cmd_disconnect_vm(a) -> None:
    from .system import disconnect_vm
    disconnect_vm()
    print("Back to the local model on this Mac.")


def cmd_purge(a) -> None:
    from .service import Context
    ctx = Context()
    if a.day:
        ctx.store.delete_day(_resolve_day(a.day, ctx.store))
        print(f"Deleted everything recorded on {a.day}.")
    else:
        n = ctx.store.purge_ocr_older_than(a.older_than)
        print(f"Removed screen text from {n} old snapshots (kept stats and reports).")


# ------------------------------------------------------------------- v0.3 --

def cmd_live(a) -> None:
    """Open the live view of the running app (or start a dashboard for it)."""
    import urllib.request
    from .config import load_config
    from .service import Context, open_url
    cfg = load_config()
    port = int(cfg.report.get("dashboard_port", 8765))
    try:
        ctx = Context(cfg)
        port = int(ctx.store.get_meta("dashboard-port") or port)
    except Exception:  # noqa: BLE001
        pass
    url = f"http://127.0.0.1:{port}/live"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(f"http://127.0.0.1:{port}/api/status", timeout=3)
        print(f"Live view: {url}")
        open_url(url)
    except OSError:
        print("OptiDay isn't running. Start it from the menu bar app (or run: optiday restart).")


def cmd_energy(a) -> None:
    from . import energy
    from .service import Context
    ctx = Context()
    st = ctx.power.state()
    pol = ctx.power.policy()
    where = ("on battery" + (f" {st.percent}%" if st.percent is not None else "")) if st.on_battery else "plugged in"
    print(f"Measuring for {a.seconds} seconds… (keep using your Mac normally)\n")
    print(energy.report(ctx.store, a.seconds, power_line=f"Power: {where} → OptiDay is in {pol.name} mode"
                        + (" (Low Power Mode on)" if st.low_power_mode else "")))
    if a.powermetrics:
        print("\nDetailed sample from Apple's powermetrics (asks for your Mac password):")
        print(energy.powermetrics(energy.optiday_pids(), a.seconds))


def cmd_connect_email(a) -> None:
    import getpass
    from . import email_source, vault
    from .config import set_config_value
    address = a.address or input("Your email address: ").strip()
    if "@" not in address:
        sys.exit("That doesn't look like an email address.")
    host = a.host or email_source.guess_host(address)
    domain = address.rsplit("@", 1)[-1].lower()
    print("\nOptiDay reads your mail read-only, straight from your provider to this Mac, and never sends,")
    print("deletes or marks anything. It needs an *app password* (not your normal password):")
    if domain in ("gmail.com", "googlemail.com") or "gmail" in host:
        print("  Gmail: https://myaccount.google.com/apppasswords (needs 2-Step Verification). Name it OptiDay.")
    elif "mail.me.com" in host:
        print("  iCloud: https://account.apple.com → Sign-In and Security → App-Specific Passwords.")
    elif "fastmail" in host:
        print("  Fastmail: Settings → Privacy & Security → App passwords (IMAP read access is enough).")
    else:
        print(f"  Create an app password in your provider's security settings. Server: {host}")
    password = getpass.getpass("\nPaste the app password (hidden): ").replace(" ", "").strip()
    if not password:
        sys.exit("No password entered.")
    print(f"Checking {host}…")
    try:
        mail = email_source.fetch(address, password, host, days_back=1)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Couldn't sign in: {e}\nDouble-check the app password (and that IMAP is on for Gmail).")
    vault.set_secret(f"email:{address}", password)
    set_config_value("email", "address", address)
    set_config_value("email", "imap_host", host if host != email_source.guess_host(address) else "")
    set_config_value("email", "enabled", True)
    print(f"Connected. Saw {len(mail['inbox'])} inbox and {len(mail['sent'])} sent emails from the last day.")
    print("The password is saved in your macOS Keychain. Choose Restart OptiDay in the menu bar to start using it.")


def cmd_disconnect_email(a) -> None:
    from . import vault
    from .config import load_config, set_config_value
    addr = (load_config().get("email", {}) or {}).get("address", "")
    if addr:
        vault.delete_secret(f"email:{addr}")
    set_config_value("email", "enabled", False)
    print("Email disconnected and the saved password removed.")


def cmd_connect_calendar(a) -> None:
    from . import calendar_source
    from .config import load_config, set_config_value
    url = a.url or ""
    if not url:
        print("Paste your calendar's private iCal address.")
        print("  Google Calendar: calendar.google.com → Settings → click your calendar →")
        print("                   'Secret address in iCal format' → copy.")
        print("  iCloud: Calendar app → right-click the calendar → Share Calendar → Public Calendar → copy link.")
        print("  Outlook: Settings → Calendar → Shared calendars → Publish a calendar → ICS link.")
        url = input("\niCal address: ").strip()
    try:
        raw = calendar_source.parse_ics(calendar_source.fetch_ics(url))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Couldn't read that calendar: {e}")
    urls = list((load_config().get("calendar", {}) or {}).get("ics_urls", []))
    if url not in urls:
        urls.append(url)
    set_config_value("calendar", "ics_urls", urls)
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    s, e = calendar_source.day_window(tomorrow)
    evs = calendar_source.events_between(raw, s, e)
    print(f"Connected ({len(raw)} events in the feed). Tomorrow: {len(evs)} event(s).")
    for ev in evs[:6]:
        when = "all day" if ev.all_day else dt.datetime.fromtimestamp(ev.start).strftime("%-I:%M %p")
        print(f"  {when:>9}  {ev.title}")
    print("Choose Restart OptiDay in the menu bar to start using it.")


def cmd_plan(a) -> None:
    from . import prep
    from .rules import fmt_time
    from .service import Context
    ctx = Context()
    if a.no_ai:
        ctx.llm = None
    plan = ctx.today_plan(use_ai=False if a.no_ai else None)
    print(f"Today, {plan['for_day']}: {prep.brief_line(plan)}")
    if plan.get("priority"):
        print(f"\nMost important: {plan['priority']}")
    if plan.get("meetings"):
        print("\nMeetings:")
        for ev in plan["meetings"]:
            print("  " + prep.meeting_line(ev))
            for c in ev.get("context", []):
                print("      · " + c)
    if plan.get("replies"):
        print("\nEmails waiting for you:")
        for r in plan["replies"]:
            print(f"  {r['from_name']}: {r['subject'][:70]} ({round(r['age_hours'])}h)")
            if r.get("draft") and a.drafts:
                print("      Draft: " + r["draft"].replace("\n", "\n             "))
    if plan.get("left_off"):
        print("\nWhere you left off:")
        for x in plan["left_off"]:
            print(f"  {x['project']}: {x['app']} · {x['where'][:80]} ({fmt_time(x['at'])})")
    if plan.get("next_steps"):
        print("\nNext steps:")
        for st in plan["next_steps"]:
            print("  - " + st)
    waiting = [x for x in plan.get("agents", []) if x["status"] == "suggested"]
    if waiting:
        print(f"\n{len(waiting)} task(s) an agent could do. See: optiday agents")


def cmd_hours(a) -> None:
    from . import analyzer
    from .service import Context
    ctx = Context()
    day = _resolve_day(a.day, ctx.store)
    llm = ctx.llm if (a.ai or ctx.ai_allowed()) and not a.no_ai else None
    for h in analyzer.hours_for(ctx.store, ctx.cfg, day, llm):
        if h["active_s"] < 60 and h["away_s"] < 600:
            continue
        print(f"{h['label']:>10}  {h['ai'] or h['text']}")


def cmd_agents(a) -> None:
    from . import automations
    from .service import Context
    ctx = Context()
    if a.action in ("approve", "dismiss", "run"):
        if not a.id:
            sys.exit(f"Usage: optiday agents {a.action} <id>")
        item = automations.update(ctx.store, ctx.cfg, a.id, "run_now" if a.action == "run" else a.action)
        if a.action == "run":
            print(f"Running “{item['title']}”… (this can take a while)")
            automations.Runner(ctx.store, ctx.cfg).run_due()
            item = ctx.store.get_note(automations.PREFIX + a.id)
            print(f"{item['status'].upper()}\n{item.get('output', '')}")
        else:
            print(f"{item['title']}: {item['status']}")
        return
    items = automations.queue(ctx.store, include_closed=a.all)
    if not items:
        print("No agent suggestions yet. OptiDay suggests tasks after it sees you repeat something.")
        return
    for it in items:
        print(f"[{it['id']}] {it['status']:<10} {it['title']}")
        print(f"    {it.get('why', '')}")
        if a.verbose_tasks:
            print("    Task: " + it["task"].replace("\n", "\n          "))
    print("\nApprove with: optiday agents approve <id>   (or use the Today page in the dashboard)")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="optiday", description="Private daily activity tracker and workflow coach.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("app", help="run the menu bar app (what starts at login)").set_defaults(fn=cmd_app)
    sub.add_parser("record", help="record in this terminal without the menu bar").set_defaults(fn=cmd_record)
    r = sub.add_parser("report", help="build a day's report")
    r.add_argument("--day", help="YYYY-MM-DD, today (default) or yesterday")
    r.add_argument("--open", action="store_true")
    r.add_argument("--json", action="store_true")
    r.add_argument("--no-ai", action="store_true")
    r.add_argument("--ai", action="store_true", help="use the local AI even on battery")
    r.set_defaults(fn=cmd_report)
    d = sub.add_parser("dashboard", help="open the local dashboard")
    d.add_argument("--port", type=int)
    d.add_argument("--open", action="store_true")
    d.set_defaults(fn=cmd_dashboard)
    de = sub.add_parser("demo", help="build a report from a simulated day")
    de.add_argument("--open", action="store_true")
    de.add_argument("--no-ai", action="store_true")
    de.set_defaults(fn=cmd_demo)
    dc = sub.add_parser("doctor", help="check permissions, AI and setup")
    dc.add_argument("--request", action="store_true", help="also show the macOS permission prompts")
    dc.set_defaults(fn=cmd_doctor)
    sub.add_parser("pause").set_defaults(fn=cmd_pause)
    sub.add_parser("resume").set_defaults(fn=cmd_resume)
    sub.add_parser("restart").set_defaults(fn=cmd_restart)
    sub.add_parser("install-agent", help="start OptiDay at login").set_defaults(fn=cmd_install_agent)
    sub.add_parser("uninstall-agent").set_defaults(fn=cmd_uninstall_agent)
    v = sub.add_parser("connect-vm", help="use the AI model on your own private server via SSH")
    v.add_argument("target", help="user@your-server")
    v.add_argument("--ssh-port", type=int, default=22)
    v.add_argument("--remote-port", type=int, default=11434)
    v.add_argument("--identity", default="", help="SSH key file")
    v.add_argument("--model", default="", help="model name on the server, e.g. qwen2.5:14b")
    v.set_defaults(fn=cmd_connect_vm)
    sub.add_parser("disconnect-vm").set_defaults(fn=cmd_disconnect_vm)
    sub.add_parser("live", help="open the live view (great on a second monitor)").set_defaults(fn=cmd_live)
    en = sub.add_parser("energy", help="measure how much energy OptiDay uses (paste the output to Claude)")
    en.add_argument("--seconds", type=int, default=10)
    en.add_argument("--powermetrics", action="store_true", help="also run Apple's detailed sampler (needs sudo)")
    en.set_defaults(fn=cmd_energy)
    ce = sub.add_parser("connect-email", help="let OptiDay read your email (read-only, app password)")
    ce.add_argument("address", nargs="?")
    ce.add_argument("--host", default="", help="IMAP server if it can't be guessed")
    ce.set_defaults(fn=cmd_connect_email)
    sub.add_parser("disconnect-email").set_defaults(fn=cmd_disconnect_email)
    cc = sub.add_parser("connect-calendar", help="add your calendar's private iCal address")
    cc.add_argument("url", nargs="?")
    cc.set_defaults(fn=cmd_connect_calendar)
    pl = sub.add_parser("plan", help="today's plan: meetings, emails waiting on you, where you left off")
    pl.add_argument("--drafts", action="store_true", help="print reply drafts too")
    pl.add_argument("--no-ai", action="store_true")
    pl.set_defaults(fn=cmd_plan)
    hr = sub.add_parser("hours", help="hour-by-hour summary of a day")
    hr.add_argument("--day")
    hr.add_argument("--ai", action="store_true", help="use the local AI even on battery")
    hr.add_argument("--no-ai", action="store_true")
    hr.set_defaults(fn=cmd_hours)
    ag = sub.add_parser("agents", help="tasks OptiDay suggests handing to an agent")
    ag.add_argument("action", nargs="?", choices=["list", "approve", "dismiss", "run"], default="list")
    ag.add_argument("id", nargs="?")
    ag.add_argument("--all", action="store_true", help="include dismissed and old tasks")
    ag.add_argument("--tasks", dest="verbose_tasks", action="store_true", help="print the full instructions")
    ag.set_defaults(fn=cmd_agents)
    pu = sub.add_parser("purge", help="delete old screen text, or a whole day")
    pu.add_argument("--older-than", type=int, default=30, help="days")
    pu.add_argument("--day")
    pu.set_defaults(fn=cmd_purge)

    a = p.parse_args(argv)
    _setup_logging(a.verbose)
    if not getattr(a, "fn", None):
        a.fn = cmd_app if sys.platform == "darwin" else (lambda _a: p.print_help())
    a.fn(a)


if __name__ == "__main__":
    main()
