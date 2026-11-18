"""A realistic simulated workday, used by `optiday demo` and the tests, so the
reports can be tried on any computer before granting any permissions."""
from __future__ import annotations

import datetime as dt
import random

from .models import Snapshot

CH = "com.google.Chrome"
VS = "com.microsoft.VSCode"


def _plan() -> list[dict]:
    """(start "HH:MM", minutes, app, bundle, title, url, typing share, screen text, flags)"""
    P = []

    def add(start, minutes, app, bundle, title, url="", typing=0.0, text=None, **kw):
        P.append(dict(start=start, minutes=minutes, app=app, bundle=bundle, title=title, url=url, typing=typing,
                      text=text or [], **kw))

    add("08:52", 6, "Google Chrome", CH, "Inbox (14) - sam@example.com - Gmail", "https://mail.google.com/mail/u/0/#inbox",
        0.0, ["Primary", "Stripe  Your payout of $1,240.00 is on the way", "Sarah Lee  Re: OptiDay beta pricing",
              "Product Hunt Daily  Top launches today"])
    add("08:58", 4, "Google Chrome", CH, "Re: OptiDay beta pricing - sam@example.com - Gmail",
        "https://mail.google.com/mail/u/0/#inbox/FMfcgz", 0.7,
        ["To  Sarah Lee <sarah@lumen.example>", "Subject  Re: OptiDay beta pricing",
         "Hi Sarah, happy to share the beta numbers. $12/mo for early users, launching Dec 1.", "Send"],
        after=["Message sent", "Undo", "View message"])
    add("09:02", 3, "Slack", "com.tinyspeck.slackmacgap", "general (Channel) - Builders - Slack", "", 0.3,
        ["#general", "Jordan: standup in 10?", "You: yep, shipping the recorder today"])
    add("09:05", 48, "Code", VS, "recorder.py — optiday — Visual Studio Code", "", 0.55,
        ["class Recorder:", "def tick(self) -> Snapshot | None:", "idle = self.cap.idle_seconds()",
         "PROBLEMS 0  OUTPUT  TERMINAL"], flick=[("Slack", "com.tinyspeck.slackmacgap",
                                                    "Jordan Kim (DM) - Builders - Slack", "", 0.6, 0.8)])
    add("09:53", 9, "Terminal", "com.apple.Terminal", "optiday — -zsh — 120×40", "", 0.4,
        ["$ pytest -q", "41 passed in 2.31s", "$ git push origin recorder"])
    add("10:02", 6, "Google Chrome", CH, "Add recorder loop by sam · Pull Request #12 · sam/optiday",
        "https://github.com/sam/optiday/pull/12", 0.1,
        ["Add recorder loop #12", "All checks have passed", "Merge pull request"],
        after=["Pull request successfully merged and closed", "Delete branch"])
    add("10:08", 14, "Google Chrome", CH, "Building a macOS Menu Bar App in Python (rumps) - Full Tutorial - YouTube",
        "https://www.youtube.com/watch?v=Xk3n0rMbar1", 0.0,
        ["Building a macOS Menu Bar App in Python (rumps) - Full Tutorial", "Tech With Tim", "1.2M views"],
        passive=True)
    add("10:22", 22, "Claude", "com.anthropic.claudefordesktop", "Fix rumps menu bar icon not updating", "", 0.35,
        ["How do I update a rumps menu title from a background thread?", "Use rumps.Timer on the main thread..."])
    add("10:44", 31, "Cursor", "com.todesktop.230313mzl4w4u92", "strategy.py — AutoTrader — Cursor", "", 0.5,
        ["def momentum_signal(prices, window=20):", "backtest: Sharpe 1.42"])
    add("11:15", 30, "Google Chrome", CH, "Meet – Investor sync with Lumen VC", "https://meet.google.com/abc-defg-hij",
        0.05, ["Sarah Lee", "You", "Lumen VC weekly"], passive=True)
    add("11:45", 12, "Google Chrome", CH, "OptiDay pricing plan - Google Docs",
        "https://docs.google.com/document/d/1abc/edit", 0.6,
        ["OptiDay pricing plan", "Free: 7-day history", "Pro $12/mo: unlimited history, private VM sync"])
    add("11:57", 9, "Google Chrome", CH, "Home / X", "https://x.com/home", 0.05,
        ["For you  Following", "@levelsio: shipped another app today", "Show 35 posts"])
    add("12:06", 52, "Finder", "com.apple.finder", "", "", 0.0, [], away=True)
    add("12:58", 6, "Google Chrome", CH, "Try Not To Laugh Challenge #47 - YouTube",
        "https://www.youtube.com/watch?v=LoL47aa", 0.0, ["Try Not To Laugh Challenge #47", "Funny Fails"],
        passive=True)
    add("13:04", 11, "Google Chrome", CH, "Shorts - YouTube", "https://www.youtube.com/shorts/s9Hh2kQe", 0.0,
        ["Shorts", "Subscribe"], passive=True)
    add("13:15", 4, "Google Chrome", CH, "Robinhood", "https://robinhood.com/stocks/NVDA", 0.0, ["NVDA $191.20"])
    add("13:19", 45, "MIXED", "", "", "", 0.0, [])  # fragmented afternoon
    add("14:04", 7, "Mail", "com.apple.mail", "Re: Contract for December", "", 0.8,
        ["To: Dana Ortiz <dana@northwind.example>", "Subject: Re: Contract for December",
         "Thanks Dana, signed copy attached."], close_to="Inbox – iCloud")
    add("14:11", 5, "Google Chrome", CH, "r/SaaS - Reddit", "https://www.reddit.com/r/SaaS/", 0.0, ["r/SaaS"])
    add("14:16", 38, "Code", VS, "report_html.py — optiday — Visual Studio Code", "", 0.5,
        ["def render(report: dict) -> str:", "<section class=\"card\">"],
        flick=[("Google Chrome", CH, "Inbox (16) - sam@example.com - Gmail",
                "https://mail.google.com/mail/u/0/#inbox", 0.0, 1.2)])
    add("14:54", 8, "Google Chrome", CH, "How Y Combinator Startups Find Their First 100 Users - YouTube",
        "https://www.youtube.com/watch?v=YC100usr", 0.0, ["How YC Startups Find Their First 100 Users"],
        passive=True)
    add("15:02", 16, "Google Chrome", CH, "Python async help - ChatGPT", "https://chatgpt.com/c/123", 0.3,
        ["Why does my asyncio loop block on subprocess?", "Use asyncio.create_subprocess_exec ..."])
    add("15:18", 25, "Code", VS, "dashboard.py — optiday — Visual Studio Code", "", 0.5,
        ["class Dashboard:", "def start(self):"])
    add("15:43", 6, "Google Chrome", CH, "Home / X", "https://x.com/home", 0.0, ["For you"])
    add("15:49", 20, "Notion", "notion.id", "OptiDay launch checklist", "", 0.4,
        ["Launch checklist", "[ ] Landing page", "[x] Recorder", "[ ] Pricing page"])
    add("16:09", 4, "Slack", "com.tinyspeck.slackmacgap", "Jordan Kim (DM) - Builders - Slack", "", 0.5,
        ["Jordan Kim", "You: demo is ready, want to see it at 5?"])
    add("16:13", 35, "Cursor", "com.todesktop.230313mzl4w4u92", "broker.py — AutoTrader — Cursor", "", 0.45,
        ["class Broker:", "def place_order(self, symbol, qty):"])
    add("16:48", 9, "Google Chrome", CH, "The Craziest NBA Dunks of 2026 - YouTube",
        "https://www.youtube.com/watch?v=dunk2026", 0.0, ["The Craziest NBA Dunks of 2026", "House of Highlights"],
        passive=True)
    add("16:57", 15, "Terminal", "com.apple.Terminal", "~/code/optiday — -zsh — 120×40", "", 0.3,
        ["$ ./install.sh", "OptiDay installed"])
    # end-of-day chore: copying payouts from Stripe into the bookkeeping sheet, one by one
    t = 17 * 60 + 12
    for i in range(7):
        add(f"{t // 60:02d}:{t % 60:02d}", 1.5, "Google Chrome", CH, f"Payments – py_3Nx{i}a – Stripe",
            "https://dashboard.stripe.com/payments", 0.15, ["Payment details", f"$ {120 + 35 * i}.00 USD", "Succeeded"])
        add(f"{(t + 2) // 60:02d}:{(t + 2) % 60:02d}", 2, "Google Chrome", CH, "Bookkeeping 2026 - Google Sheets",
            "https://docs.google.com/spreadsheets/d/1xyz/edit", 0.6, ["Date", "Customer", "Amount", "Stripe fee"])
        t += 4
    return P


_MIX = [
    ("Slack", "com.tinyspeck.slackmacgap", "general (Channel) - Builders - Slack", "", 0.3),
    ("Google Chrome", CH, "Inbox (18) - sam@example.com - Gmail", "https://mail.google.com/mail/u/0/#inbox", 0.0),
    ("Google Chrome", CH, "Home / X", "https://x.com/home", 0.0),
    ("Code", VS, "metrics.py — optiday — Visual Studio Code", "", 0.5),
    ("Google Chrome", CH, "Hacker News", "https://news.ycombinator.com/", 0.0),
    ("Messages", "com.apple.MobileSMS", "Messages", "", 0.4),
    ("Google Chrome", CH, "OptiDay — Linear", "https://linear.app/optiday/team/OPT/active", 0.1),
]


def demo_snapshots(day: dt.date, interval: float = 5.0, seed: int = 7) -> list[Snapshot]:
    rnd = random.Random(seed)
    snaps: list[Snapshot] = []

    def emit(t0: float, seconds: float, app, bundle, title, url, typing, text, away=False, passive=False,
             after=None):
        n = max(1, int(seconds // interval))
        for i in range(n):
            ts = t0 + i * interval
            ocr = None
            if text and not away and i % 3 == 0:
                lines = list(text)
                if after and i >= n - 3:
                    lines += after
                ocr = "\n".join(lines)
            private = "robinhood" in url
            snaps.append(Snapshot(ts=ts, app=app, bundle_id=bundle, title="" if private else title,
                                  url="" if private else url,
                                  idle_s=999 if away else (rnd.uniform(30, 200) if passive else rnd.uniform(0, 20)),
                                  typing=rnd.random() < typing, ocr=None if private else ocr, away=away,
                                  private=private))

    for item in _plan():
        h, m = map(int, item["start"].split(":"))
        t0 = dt.datetime.combine(day, dt.time(h, m)).timestamp()
        secs = item["minutes"] * 60
        if item["app"] == "MIXED":
            t = t0
            while t < t0 + secs:
                app, bundle, title, url, typing = rnd.choice(_MIX)
                d = rnd.choice([10, 15, 20, 30, 45, 60, 90, 120])
                emit(t, d, app, bundle, title, url, typing, [title, "…"])
                t += d
            continue
        flick = item.get("flick")
        if flick:
            app2, b2, t2, u2, ty2, every_min = flick[0]
            t = t0
            while t < t0 + secs:
                run = min(every_min * 60 * rnd.uniform(6, 14), t0 + secs - t)
                emit(t, run, item["app"], item["bundle"], item["title"], item["url"], item["typing"], item["text"])
                t += run
                if t < t0 + secs - 30:
                    emit(t, 20, app2, b2, t2, u2, ty2, [t2])
                    t += 20
            continue
        emit(t0, secs, item["app"], item["bundle"], item["title"], item["url"], item["typing"], item["text"],
             away=item.get("away", False), passive=item.get("passive", False), after=item.get("after"))
        if item.get("close_to"):
            emit(t0 + secs, 15, item["app"], item["bundle"], item["close_to"], "", 0.0, ["Inbox"])
    snaps.sort(key=lambda s: s.ts)
    # de-duplicate any overlapping ticks from the flick logic
    out, last = [], -1.0
    for s in snaps:
        if s.ts - last >= interval * 0.5:
            out.append(s)
            last = s.ts
    return out
