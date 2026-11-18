"""v0.3: email, calendar, hour-by-hour summaries, next-day prep, agent hand-off, energy."""
import datetime as dt
import json
import threading
import time
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from optiday import analyzer, automations, calendar_source, email_source, energy, prep
from optiday.llm import OllamaLLM


# --------------------------------------------------------------- email -----

def _msg(frm, to, subject, body, when, msg_id=None, in_reply_to=None, headers=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    m["Date"] = format_datetime(when)
    m["Message-ID"] = msg_id or make_msgid(domain="example.com")
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    return m


class FakeIMAP:
    """Just enough IMAP4 to look like Gmail. Records every call so tests can prove read-only use."""

    def __init__(self, inbox, sent):
        self.folders = {"INBOX": [m.as_bytes() for m in inbox], "[Gmail]/Sent Mail": [m.as_bytes() for m in sent]}
        self.selected = None
        self.calls = []

    def login(self, user, pw):
        self.calls.append(("login", user))
        return "OK", [b"logged in"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
                      b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"']

    def select(self, name, readonly=False):
        self.calls.append(("select", name, readonly))
        self.selected = name.strip('"')
        return "OK", [str(len(self.folders[self.selected])).encode()]

    def uid(self, cmd, *args):
        self.calls.append(("uid", cmd) + args)
        msgs = self.folders[self.selected]
        if cmd == "SEARCH":
            return "OK", [" ".join(str(i + 1) for i in range(len(msgs))).encode()]
        if cmd == "FETCH":
            out = []
            for i in (int(x) for x in args[0].split(",")):
                out.append((f"{i} (UID {i} BODY[] {{{len(msgs[i - 1])}}}".encode(), msgs[i - 1]))
                out.append(b")")
            return "OK", out
        raise AssertionError(f"unexpected IMAP command {cmd}")

    def logout(self):
        self.calls.append(("logout",))


ME = "sam@example.com"


@pytest.fixture
def mailbox():
    now = dt.datetime.now().astimezone()
    sarah_id = "<sarah-1@lumen.example>"
    inbox = [
        _msg("Sarah Lee <sarah@lumen.example>", ME, "OptiDay beta pricing", "Can you send the pricing deck by Friday?",
             now - dt.timedelta(hours=5), msg_id=sarah_id),
        _msg("Dana Ortiz <dana@northwind.example>", ME, "Contract for December",
             "Could you sign the contract? Let me know.", now - dt.timedelta(hours=3)),
        _msg("Stripe <no-reply@stripe.com>", ME, "Your payout is on the way", "Payout of $1,240.00",
             now - dt.timedelta(hours=4), headers={"List-Unsubscribe": "<mailto:x@stripe.com>"}),
        _msg("Jordan Kim <jordan@builders.example>", ME, "Lunch?", "Lunch tomorrow at noon works for me.\n\nOn Mon Jordan wrote:\n> old",
             now - dt.timedelta(hours=2)),
    ]
    sent = [
        _msg(f"Sam <{ME}>", "Sarah Lee <sarah@lumen.example>", "Re: OptiDay beta pricing", "Sending it now.",
             now - dt.timedelta(hours=1), in_reply_to=sarah_id),
    ]
    return FakeIMAP(inbox, sent)


def test_imap_is_read_only_and_parses(mailbox):
    mail = email_source.fetch(ME, "app-pw", "imap.gmail.com", days_back=2, connect=lambda h: mailbox)
    assert len(mail["inbox"]) == 4 and len(mail["sent"]) == 1
    selects = [c for c in mailbox.calls if c[0] == "select"]
    assert selects and all(c[2] is True for c in selects)          # readonly=True every time
    fetches = [c for c in mailbox.calls if c[:2] == ("uid", "FETCH")]
    assert fetches and all("BODY.PEEK" in c[3] for c in fetches)    # PEEK: messages stay unread
    assert not any(c[1] in ("STORE", "COPY", "MOVE", "EXPUNGE") for c in mailbox.calls if c[0] == "uid")
    assert mailbox.calls[-1] == ("logout",)
    stripe = next(m for m in mail["inbox"] if "stripe" in m.from_addr)
    assert stripe.bulk
    jordan = next(m for m in mail["inbox"] if "jordan" in m.from_addr)
    assert "old" not in jordan.snippet and jordan.snippet.startswith("Lunch tomorrow at noon")


def test_digest_finds_who_needs_you(mailbox):
    mail = email_source.fetch(ME, "pw", "imap.gmail.com", connect=lambda h: mailbox)
    now = time.time()
    dig = email_source.digest(mail, ME, now - 86400, now + 60, now=now)
    who = [n["from_addr"] for n in dig["needs_reply"]]
    assert "sarah@lumen.example" not in who            # already replied (In-Reply-To)
    assert "no-reply@stripe.com" not in who          # newsletter/automated
    assert who[0] == "dana@northwind.example"             # asks something -> higher priority
    assert "jordan@builders.example" in who
    assert len(dig["sent"]) == 1
    acts = email_source.sent_actions(dig)
    assert acts[0]["text"].startswith("Sent email to Sarah Lee") and acts[0]["source"] == "mail"


def test_guess_host():
    assert email_source.guess_host("a@gmail.com") == "imap.gmail.com"
    assert email_source.guess_host("a@icloud.com") == "imap.mail.me.com"
    assert email_source.guess_host("a@acme.example") == "imap.acme.example"


def test_mail_actions_replace_screen_guesses():
    acts = [{"ts": 1000.0, "kind": "email", "source": "rule", "text": "Sent an email (Gmail)"},
            {"ts": 5000.0, "kind": "code", "source": "rule", "text": "Coded"}]
    mail = [{"ts": 1100.0, "kind": "email", "source": "mail", "text": "Sent email to Sarah — “Hi”"}]
    out = analyzer.merge_mail_actions(acts, mail)
    assert [a["source"] for a in out] == ["mail", "rule"]


# ------------------------------------------------------------ calendar -----

ICS = """BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Work
BEGIN:VTIMEZONE
TZID:America/New_York
END:VTIMEZONE
BEGIN:VEVENT
UID:standup@x
DTSTART;TZID=America/New_York:20250106T093000
DTEND;TZID=America/New_York:20250106T094500
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
EXDATE;TZID=America/New_York:20261118T093000
SUMMARY:Daily standup
DESCRIPTION:Join: https://meet.google.com/abc-defg-hij
ATTENDEE;CN=Jordan Kim;PARTSTAT=ACCEPTED:mailto:jordan@builders.example
ATTENDEE;CN=Bob;PARTSTAT=DECLINED:mailto:bob@x.com
BEGIN:VALARM
TRIGGER:-PT10M
SUMMARY:ignore me
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:standup@x
RECURRENCE-ID;TZID=America/New_York:20261119T093000
DTSTART;TZID=America/New_York:20261119T140000
DTEND;TZID=America/New_York:20261119T141500
SUMMARY:Daily standup (moved)
END:VEVENT
BEGIN:VEVENT
UID:board@x
DTSTART:20250121T150000Z
DURATION:PT1H
RRULE:FREQ=MONTHLY;BYDAY=3TU
SUMMARY:Board
  call
END:VEVENT
BEGIN:VEVENT
UID:offsite@x
DTSTART;VALUE=DATE:20261117
DTEND;VALUE=DATE:20261119
SUMMARY:Offsite\\, Tahoe
END:VEVENT
BEGIN:VEVENT
UID:limited@x
DTSTART;TZID=America/New_York:20261116T120000
DTEND;TZID=America/New_York:20261116T130000
RRULE:FREQ=DAILY;COUNT=2
SUMMARY:Workshop
END:VEVENT
BEGIN:VEVENT
UID:cancel@x
DTSTART;TZID=America/New_York:20261117T160000
DTEND;TZID=America/New_York:20261117T170000
STATUS:CANCELLED
SUMMARY:Cancelled thing
END:VEVENT
BEGIN:VEVENT
UID:intro@x
DTSTART;TZID=America/New_York:20261117T110000
DTEND;TZID=America/New_York:20261117T113000
SUMMARY:Intro with Sarah Lee (Lumen VC)
ATTENDEE;CN=Sarah Lee:mailto:sarah@lumen.example
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def ny(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.delenv("TZ")
    time.tzset()


def _day(raw, d):
    s, e = calendar_source.day_window(d)
    return calendar_source.events_between(raw, s, e)


def _hm(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def test_calendar_recurrence_and_exceptions(ny):
    raw = calendar_source.parse_ics(ICS)
    mon = _day(raw, dt.date(2026, 11, 16))
    assert [(e.title, _hm(e.start)) for e in mon] == [("Daily standup", "09:30"), ("Workshop", "12:00")]
    assert mon[0].attendees == ["Jordan Kim"] and mon[0].meet_url.startswith("https://meet.google.com/")
    tue = _day(raw, dt.date(2026, 11, 17))
    titles = [e.title for e in tue]
    assert titles[0] == "Offsite, Tahoe" and tue[0].all_day             # all-day first
    assert "Board call" in titles and "Workshop" in titles               # 3rd Tuesday; COUNT=2 day 2
    assert "Cancelled thing" not in titles
    board = next(e for e in tue if e.title == "Board call")
    assert _hm(board.start) == "10:00"                                    # 15:00Z in New York (EST)
    wed = _day(raw, dt.date(2026, 11, 18))
    assert "Daily standup" not in [e.title for e in wed]                  # EXDATE
    assert "Offsite, Tahoe" in [e.title for e in wed]                     # 2-day all-day event
    assert "Workshop" not in [e.title for e in wed]                       # COUNT exhausted
    thu = _day(raw, dt.date(2026, 11, 19))
    assert [(e.title, _hm(e.start)) for e in thu] == [("Daily standup (moved)", "14:00")]


def test_calendar_keeps_wall_clock_across_dst(ny):
    raw = calendar_source.parse_ics(ICS)
    # standup started in January (EST); in July (EDT) it's still 9:30 local
    jul = _day(raw, dt.date(2026, 7, 14))
    assert ("Daily standup", "09:30") in [(e.title, _hm(e.start)) for e in jul]


def test_events_for_day_survives_a_broken_feed(ny):
    def fetch(url):
        if "bad" in url:
            raise OSError("offline")
        return ICS
    evs, errs = calendar_source.events_for_day(["https://good/x.ics", "https://bad/y.ics", "https://good/x.ics"],
                                               dt.date(2026, 11, 16), fetch=fetch)
    assert [e["title"] for e in evs] == ["Daily standup", "Workshop"]     # duplicates merged
    assert errs and "offline" in errs[0]


def test_fetch_ics_rejects_plain_http():
    with pytest.raises(ValueError):
        calendar_source.fetch_ics("http://example.com/cal.ics")


# ------------------------------------------------------------- summaries ---

def test_hour_by_hour_rules(demo_store, cfg, demo_day):
    hours = analyzer.hours_for(demo_store, cfg, demo_day.isoformat())
    by = {h["label"]: h for h in hours}
    assert "9am–10am" in by and "OptiDay" in by["9am–10am"]["text"]
    assert "Away" in by["12pm–1pm"]["text"]
    assert "YouTube" in by["1pm–2pm"]["text"] and "distracted" in by["1pm–2pm"]["text"]
    assert all(h["ai"] == "" for h in hours)
    total = sum(h["active_s"] + h["away_s"] for h in hours)
    assert 7 * 3600 < total < 10 * 3600


def test_hour_by_hour_ai_is_cached(demo_store, cfg, demo_day, fake_ollama):
    llm = OllamaLLM(fake_ollama.url, "qwen2.5:7b")
    r = analyzer.analyze_day(demo_store, cfg, demo_day.isoformat(), llm=llm)
    ai_hours = [h for h in r["hours"] if h["ai"]]
    assert ai_hours and ai_hours[0]["ai"].startswith("AI hour:")
    assert r["done"] == ["Merged the recorder PR", "Wrote the pricing doc"]
    assert r["prep"]["next_steps"] == ["Finish the menu bar settings screen"]
    calls = fake_ollama.hour_calls
    analyzer.analyze_day(demo_store, cfg, demo_day.isoformat(), llm=llm)
    assert fake_ollama.hour_calls == calls                                 # nothing re-asked


def test_rule_done_without_ai(demo_store, cfg, demo_day):
    r = analyzer.analyze_day(demo_store, cfg, demo_day.isoformat())
    assert r["done"][0].startswith("Worked") and "OptiDay" in r["done"][0]
    assert any("Merged PR #12" in d for d in r["done"])


# ------------------------------------------------------------------ prep ---

def test_prep_with_mail_and_calendar(demo_store, cfg, demo_day, fake_ollama, ny):
    day = demo_day.isoformat()
    nxt = (demo_day + dt.timedelta(days=1)).isoformat()
    raw = calendar_source.parse_ics(ICS.replace("20261117", nxt.replace("-", "")))
    s, e = calendar_source.day_window(demo_day + dt.timedelta(days=1))
    demo_store.put_note(f"calendar:{nxt}", day, {"events": [x.to_dict() for x in calendar_source.events_between(raw, s, e)],
                                                 "errors": [], "fetched_at": time.time()})
    start, end = demo_store.day_bounds(day)
    demo_store.put_note(f"email:{day}", day, {
        "sent": [{"ts": start + 9 * 3600 + 3 * 60 + 5 * 3600, "to": ["sarah@lumen.example"], "to_names": ["Sarah Lee"],
                  "subject": "Re: OptiDay beta pricing"}],
        "needs_reply": [{"msg_id": "<d1@x>", "from_name": "Dana Ortiz", "from_addr": "dana@northwind.example",
                         "subject": "Contract for December", "snippet": "Could you sign it?", "ts": end - 3600,
                         "age_hours": 3.0, "asks": True, "priority": 4}],
        "inbox_count": 9, "human_inbox_count": 3, "error": "", "fetched_at": time.time()})
    llm = OllamaLLM(fake_ollama.url, "qwen2.5:7b")
    r = analyzer.analyze_day(demo_store, cfg, day, llm=llm)
    p = r["prep"]
    intro = next(m for m in p["meetings"] if m["title"].startswith("Intro with Sarah"))
    assert any("Sarah" in c for c in intro["context"])                    # today's email/meeting with Sarah
    assert p["replies"][0]["draft"].startswith("Hi! Re Contract")
    assert fake_ollama.draft_calls == 1
    analyzer.analyze_day(demo_store, cfg, day, llm=llm)
    assert fake_ollama.draft_calls == 1                                   # drafts cached
    assert demo_store.get_note(f"prep:{nxt}")["for_day"] == nxt
    assert r["email"]["connected"] and r["email"]["sent_count"] == 1
    assert "meeting" in prep.brief_line(p) and "email" in prep.brief_line(p)
    assert sum(1 for a in r["actions"] if a.get("source") == "mail") == 1


# ----------------------------------------------------------------- agents --

def test_detects_copy_paste_loop(demo_store, cfg, demo_day):
    analyzer.analyze_day(demo_store, cfg, demo_day.isoformat())
    q = automations.queue(demo_store)
    loop = next(i for i in q if i["kind"] == "loop")
    assert set(loop["evidence"]["places"]) == {"Stripe", "Google Sheets"}
    assert loop["status"] == "suggested" and loop["task"]
    # coding <-> terminal back-and-forth is building, not a chore
    assert not any("Code" in i["evidence"].get("places", []) for i in q)


def test_templated_emails():
    acts = [{"ts": i, "kind": "email", "source": "mail", "text": f"Sent email to C{i} — “Invoice #{100 + i} for October”"}
            for i in range(4)]
    c = automations.find_templated_emails(acts)
    assert c and c[0]["count"] == 4 and c[0]["template"] == "invoice ## for october"


def test_routines_across_days():
    pat = {f"2026-11-0{d}": {"Stripe": {"s": 900, "visits": 4, "typing": 10, "cat": "Finance", "titles": ["Home"]},
                             "Cursor": {"s": 9000, "visits": 9, "typing": 4000, "cat": "Coding", "titles": []}}
           for d in range(1, 6)}
    r = automations.find_routines(pat)
    assert [c["places"] for c in r] == [["Stripe"]]


def test_nothing_runs_without_approval(demo_store, cfg, demo_day):
    analyzer.analyze_day(demo_store, cfg, demo_day.isoformat())
    calls = []
    runner = automations.Runner(demo_store, cfg, run=lambda *a, **k: calls.append(a))
    assert runner.run_due() == 0 and calls == []
    item = automations.queue(demo_store)[0]
    automations.update(demo_store, cfg, item["id"], "approve")
    assert automations.queue(demo_store)[0]["run_after"] > time.time()   # scheduled, not immediate
    assert runner.run_due() == 0 and calls == []


def test_runner_command_gets_task_with_safety_preamble(demo_store, cfg, demo_day, tmp_path):
    analyzer.analyze_day(demo_store, cfg, demo_day.isoformat())
    item = automations.queue(demo_store)[0]
    automations.update(demo_store, cfg, item["id"], "run_now", task="Put Stripe payouts into the sheet.")
    seen = {}

    class R:
        returncode, stdout, stderr = 0, "Done: wrote sync_payouts.py", ""

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return R()
    runner = automations.Runner(demo_store, cfg, run=fake_run)
    assert runner.run_due() == 1
    assert seen["cmd"][:2] == ["claude", "-p"] and seen["cmd"][2].endswith("Put Stripe payouts into the sheet.")
    assert "do not send emails" in seen["cmd"][2]
    assert "/opt/homebrew/bin" in seen["kw"]["env"]["PATH"] or seen["kw"]["env"]["PATH"]
    done = demo_store.get_note(automations.PREFIX + item["id"])
    assert done["status"] == "done" and "sync_payouts" in done["output"]


def test_runner_missing_command_and_webhook(cfg):
    item = {"id": "abc123abc123", "title": "t", "task": "do it", "why": "w", "agent": ""}
    cfg["agents"]["claude"]["command"] = ["definitely-not-installed-xyz", "{task}"]
    res = automations.run_task(cfg, item)
    assert not res["ok"] and "isn't installed" in res["output"]

    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"queued")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg["agents"]["webhook"]["url"] = f"http://127.0.0.1:{srv.server_address[1]}/hook"
    res = automations.run_task(cfg, dict(item, agent="webhook"))
    srv.shutdown()
    assert res["ok"] and res["output"] == "queued" and got["task"].endswith("do it")
    cfg["agents"]["webhook"]["url"] = "http://evil.example/hook"
    assert not automations.run_task(cfg, dict(item, agent="webhook"))["ok"]


def test_ai_writes_agent_task(demo_store, cfg, demo_day, fake_ollama):
    llm = OllamaLLM(fake_ollama.url, "qwen2.5:7b")
    analyzer.analyze_day(demo_store, cfg, demo_day.isoformat(), llm=llm)
    item = next(i for i in automations.queue(demo_store) if i["kind"] == "loop")
    assert item["source"] == "ai" and item["title"] == "Automate the copy-paste loop"
    automations.update(demo_store, cfg, item["id"], "dismiss")
    analyzer.analyze_day(demo_store, cfg, demo_day.isoformat(), llm=llm)
    assert demo_store.get_note(automations.PREFIX + item["id"])["status"] == "dismissed"   # no re-nagging
    assert item["id"] not in [i["id"] for i in automations.queue(demo_store)]


# ----------------------------------------------------------------- energy --

TOP = """Processes: 512 total
PID    COMMAND          %CPU IDLEW POWER
812    Python           0.0  0     0.0
1      launchd          0.0  0     0.0
Processes: 512 total, 3 running
PID    COMMAND          %CPU IDLEW POWER
812    Python           0.4  2     0.6
377    Google Chrome He 18.2 140   21.3
95     WindowServer     6.1  52    8.0
4410   ollama           0.0  0     0.0
"""

PS = """  812 /opt/homebrew/opt/python@3.12/bin/python3.12 -m optiday app
  900 /usr/bin/python3 /Users/a/.local/bin/optiday energy
  377 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
"""


def test_parse_top_uses_last_sample():
    procs = energy.parse_top(TOP)
    by = {p.pid: p for p in procs}
    assert by[812].cpu == 0.4 and by[812].power == 0.6
    assert by[377].command == "Google Chrome He" and by[377].idlew == 140


def test_optiday_pids_skips_this_command():
    assert energy.optiday_pids(PS, me=900) == [812]


def test_energy_report_and_hour_log(store):
    class R:
        stdout = TOP
    cpu = [10.0]
    stats = {"ticks": 0, "ocr": 0}
    now = [dt.datetime(2026, 11, 15, 14, 5).timestamp()]
    log = energy.HourLog(store, lambda: cpu[0], lambda: stats, clock=lambda: now[0])
    log.tick("battery saver", True)
    cpu[0], stats["ticks"], stats["ocr"], now[0] = 12.5, 300, 20, now[0] + 1800
    log.tick("battery saver", True)
    rec = json.loads(store.get_meta("energy:2026-11-15T14"))
    assert rec["cpu_s"] == 2.5 and rec["ticks"] == 300 and rec["ocr"] == 20
    out = energy.report(store, 1, run=lambda *a, **k: R(), ps_text=PS, power_line="Power: plugged in")
    assert "OptiDay (pid 812)" in out and "Local AI (Ollama)" in out
    assert "% of this Mac's total energy use" in out


# -------------------------------------------------------------- service ----

def test_service_refreshes_mail_and_calendar(cfg, mailbox, ny, monkeypatch):
    from optiday import vault
    from optiday.service import Context
    cfg["email"].update(enabled=True, address=ME)
    cfg["calendar"]["ics_urls"] = ["https://cal.example/x.ics"]
    ctx = Context(cfg)
    ctx.llm = None
    today = ctx.store.today()
    # no password yet -> a clear instruction, not a crash
    assert "connect-email" in ctx.refresh_email(today, force=True)["error"]
    vault.set_secret(f"email:{ME}", "app-pw")
    assert vault.get_secret(f"email:{ME}") == "app-pw"
    dig = ctx.refresh_email(today, force=True, fetch=lambda a, p, h, d: email_source.fetch(
        a, p, h, d, connect=lambda _h: mailbox))
    assert dig["error"] == "" and dig["needs_reply"]
    assert ctx.refresh_email(today) is not None                            # cached: no second fetch
    ics_today = ICS.replace("20261117", today.replace("-", ""))
    cal = ctx.refresh_calendar(today, force=True, fetch=lambda u: ics_today)
    assert any(e["title"].startswith("Intro with Sarah") for e in cal["events"])
    # offline later: keep the last good copy
    cal2 = ctx.refresh_calendar(today, force=True, fetch=lambda u: (_ for _ in ()).throw(OSError("offline")))
    assert cal2["events"] == cal["events"] and cal2["errors"]
    plan = ctx.today_plan(use_ai=False)
    assert plan["replies"] and any(m["title"].startswith("Intro") for m in plan["meetings"])


def test_dashboard_plan_and_agent_api(demo_store, cfg, demo_day):
    from optiday.dashboard import Dashboard
    from optiday.service import Context
    analyzer.analyze_day(demo_store, cfg, demo_day.isoformat())
    ctx = Context(cfg)
    ctx.llm = None
    d = Dashboard(ctx, port=18865)
    d.poll_interval = 0.2
    d.start()
    try:
        import urllib.request

        def req(path, method="GET", body=None, headers=None):
            r = urllib.request.Request(d.url + path, data=body, method=method,
                                       headers={"Host": f"127.0.0.1:{d.port}", **(headers or {})})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(r, timeout=20) as resp:
                    return resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, ""
        code, html = req("/plan")
        assert code == 200 and "Your day, ready" in html and "Hand off to an agent" in html
        item = automations.queue(ctx.store)[0]
        body = json.dumps({"id": item["id"], "action": "approve", "task": "Edited task"}).encode()
        assert req("/api/automation", "POST", body, {"Content-Type": "application/json"})[0] == 403
        code, _ = req("/api/automation", "POST", body, {"Content-Type": "application/json", "X-OptiDay": "1"})
        assert code == 200
        saved = ctx.store.get_note(automations.PREFIX + item["id"])
        assert saved["status"] == "approved" and saved["task"] == "Edited task"
        bad = json.dumps({"id": "../../etc", "action": "approve"}).encode()
        assert req("/api/automation", "POST", bad, {"X-OptiDay": "1"})[0] == 400
        code, html = req(f"/day/{demo_day.isoformat()}")
        assert code == 200 and "Hour by hour" in html and "Ready for" in html
    finally:
        d.stop()
