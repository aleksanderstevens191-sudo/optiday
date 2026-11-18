"""v0.4: the live tracker (in-memory state, long-poll, focus sessions, live page)."""
import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from optiday.live import LiveState
from optiday.models import Snapshot, WindowInfo
from optiday.recorder import Recorder
from test_core import FakeCap

DAY = dt.date(2026, 11, 13)
T0 = dt.datetime(2026, 11, 13, 10).timestamp()

CODE = dict(app="Code", bundle_id="com.microsoft.VSCode", title="a.py — optiday — Visual Studio Code")
REDDIT = dict(app="Google Chrome", bundle_id="com.google.Chrome", title="r/funny",
              url="https://www.reddit.com/r/funny")
MAIL = dict(app="Mail", bundle_id="com.apple.mail", title="Inbox")


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def feed(live, clock, win, seconds, step=5.0, **kw):
    """Simulate the recorder looking every `step` seconds for `seconds`."""
    end = clock.t + seconds
    while clock.t < end:
        live.ingest(Snapshot(ts=clock.t, span=step, **win, **kw))
        clock.t += step


@pytest.fixture
def live(cfg, store, monkeypatch):
    clock = Clock()
    notes = []
    monkeypatch.setattr(store, "today", lambda: DAY.isoformat())
    lv = LiveState(cfg, store, clock=clock, notify=lambda title, text: notes.append((title, text)))
    lv.clock_ = clock
    lv.notes = notes
    return lv


def test_totals_flow_and_timeline(live):
    clock = live.clock_
    feed(live, clock, CODE, 15 * 60)
    s = live.snapshot()
    assert s["day"] == DAY.isoformat() and s["live"]
    assert s["state"] == "flow" and s["since"] == pytest.approx(T0)
    assert s["current"]["place"] == "Code" and s["current"]["project"] == "OptiDay"
    t = s["totals"]
    assert t["productive_s"] == pytest.approx(15 * 60, abs=6) and t["distracting_s"] == 0
    assert t["score"] == 100 and t["switches"] == 0
    assert t["focus_s"] == 0 and t["longest_flow_s"] >= 14 * 60   # deep focus = blocks of 25+ minutes
    assert s["projects"][0]["name"] == "OptiDay"
    # 5-minute buckets from 10:00, each fully productive
    first = int((T0 - s["day_start"]) // s["bucket"])
    assert [b[0] for b in s["timeline"]] == [first, first + 1, first + 2]
    assert all(b[1] > 0 and b[2] == b[3] == 0 for b in s["timeline"])
    assert s["nudges"] and s["nudges"][0]["tone"] == "good"
    feed(live, clock, CODE, 15 * 60)
    assert live.snapshot()["totals"]["focus_s"] >= 29 * 60


def test_drift_is_flagged_and_nudges_back(live):
    clock = live.clock_
    feed(live, clock, CODE, 12 * 60)
    feed(live, clock, REDDIT, 4 * 60)
    s = live.snapshot()
    assert s["state"] == "drift" and s["current"]["cls"] == "distracting"
    assert s["totals"]["distracting_s"] == pytest.approx(4 * 60, abs=6)
    assert s["totals"]["switches"] == 1
    assert s["recent"][0]["place"] == "Code"           # "Before this" chips
    assert any("Back to OptiDay?" in n["text"] for n in s["nudges"])
    places = {p["name"]: p for p in s["places"]}
    assert places["Reddit"]["cls"] == "distracting"


def test_short_comms_check_keeps_flow(live):
    clock = live.clock_
    feed(live, clock, CODE, 11 * 60)
    feed(live, clock, MAIL, 40)                          # a quick look at mail…
    feed(live, clock, CODE, 60)
    assert live.snapshot()["state"] == "flow"            # …doesn't break the flow


def test_many_switches_get_a_nudge(live):
    clock = live.clock_
    for _ in range(9):
        feed(live, clock, CODE, 20)
        feed(live, clock, MAIL, 20)
    s = live.snapshot()
    assert s["totals"]["switches_10m"] >= 15
    assert any("app switches" in n["text"] for n in s["nudges"])


def test_away_paused_and_stale(cfg, store, monkeypatch):
    clock = Clock()
    paused = {"on": False}
    monkeypatch.setattr(store, "today", lambda: DAY.isoformat())
    live = LiveState(cfg, store, clock=clock, paused=lambda: paused["on"])
    feed(live, clock, CODE, 60)
    assert live.snapshot()["state"] == "focus"
    clock.t += 200                                       # Mac asleep, no checks
    assert live.snapshot()["state"] == "away"
    feed(live, clock, CODE, 10, away=True)
    assert live.snapshot()["state"] == "away"
    paused["on"] = True
    s = live.snapshot()
    assert s["state"] == "paused" and s["paused"]


def test_new_day_starts_fresh(live):
    clock = live.clock_
    feed(live, clock, CODE, 5 * 60)
    clock.t = dt.datetime(2026, 11, 14, 9).timestamp()
    feed(live, clock, MAIL, 60)
    s = live.snapshot()
    assert s["day"] == "2026-11-14"
    assert s["totals"]["productive_s"] == 0 and s["totals"]["neutral_s"] == pytest.approx(55, abs=6)


def test_focus_session_drift_alert_and_end(live, cfg, store):
    clock = live.clock_
    feed(live, clock, CODE, 5 * 60)
    f = live.start_focus(25)
    assert f["label"] == "OptiDay" and f["minutes"] == 25
    assert live.start_focus(1)["minutes"] == 5 and live.start_focus(999)["minutes"] == 240  # clamped
    live.start_focus(25)
    s = live.snapshot()["focus_session"]
    assert s and s["end"] - s["start"] == 25 * 60
    # survives a restart
    again = LiveState(cfg, store, clock=clock)
    assert again.focus and again.focus["label"] == "OptiDay"

    feed(live, clock, CODE, 5 * 60)
    feed(live, clock, REDDIT, 90)
    drift = [n for n in live.notes if n[0].startswith("You drifted")]
    assert len(drift) == 1 and "Reddit" in drift[0][1]
    feed(live, clock, REDDIT, 120)
    assert len([n for n in live.notes if n[0].startswith("You drifted")]) == 1  # at most every 5 min
    fs = live.snapshot()["focus_session"]
    assert fs["drift_s"] >= 150 and fs["focused_s"] >= 4 * 60

    feed(live, clock, CODE, 20 * 60)                    # session runs out
    assert live.focus is None and live.snapshot()["focus_session"] is None
    assert live.notes[-1][0] == "Focus session done"
    assert live.snapshot()["last_focus"]["early"] is False

    live.start_focus(50, "Pitch deck")
    done = live.stop_focus()
    assert done["early"] and done["label"] == "Pitch deck"
    assert live.notes[-1][0] == "Focus session stopped"
    assert not store.get_meta("focus-session")


def test_long_poll_returns_on_change(live):
    clock = live.clock_
    feed(live, clock, CODE, 10)
    v = live.snapshot()["version"]
    t = time.monotonic()
    assert live.wait(v, timeout=0.2)["version"] == v     # nothing new: times out
    assert time.monotonic() - t >= 0.15
    got = {}
    th = threading.Thread(target=lambda: got.update(live.wait(v, timeout=10)))
    th.start()
    time.sleep(0.1)
    t = time.monotonic()
    live.ingest(Snapshot(ts=clock.t, span=5, **MAIL))
    th.join(5)
    assert got["version"] > v and got["current"]["place"] == "Mail"
    assert time.monotonic() - t < 2


def test_seed_matches_live_totals(live, cfg, store, monkeypatch):
    clock = live.clock_
    snaps = []
    for win, secs in ((CODE, 600), (REDDIT, 120), (MAIL, 60), (CODE, 300)):
        end = clock.t + secs
        while clock.t < end:
            snaps.append(Snapshot(ts=clock.t, span=5, **win))
            clock.t += 5
    for s in snaps:
        live.ingest(s)
    store.add_snapshots(snaps)
    fresh = LiveState(cfg, store, clock=clock)
    fresh.seed()
    a, b = live.snapshot()["totals"], fresh.snapshot()["totals"]
    for k in ("productive_s", "neutral_s", "distracting_s"):
        assert a[k] == pytest.approx(b[k], abs=12), k
    assert a["switches"] == b["switches"] == 3


def test_recorder_feeds_listeners(cfg, store, home):
    clock = {"t": T0}
    win = WindowInfo(app="Code", bundle_id="com.microsoft.VSCode", title="a.py — optiday — Visual Studio Code")
    rec = Recorder(cfg, store, FakeCap([{"win": win}] * 5), home, clock=lambda: clock["t"])
    seen = []

    def broken(_snap):
        raise RuntimeError("boom")
    rec.listeners += [broken, seen.append]
    for _ in range(5):
        assert rec.tick() is not None                    # a failing listener never stops recording
        clock["t"] += 5
    assert len(seen) == 5 and seen[0].app == "Code"


@pytest.fixture
def server(cfg, store, monkeypatch):
    from optiday.dashboard import Dashboard
    from optiday.service import Context
    ctx = Context(cfg)
    ctx.llm = None
    d = Dashboard(ctx, port=18866)
    d.poll_interval = 0.2
    d.start()

    def req(path, method="GET", body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(d.url + path, data=data, method=method,
                                   headers={"Host": f"127.0.0.1:{d.port}", **(headers or {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(r, timeout=20) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, b"", e.headers
    yield ctx, req
    d.stop()


def test_live_page_and_api(server):
    ctx, req = server
    H = {"X-OptiDay": "1"}
    code, html, headers = req("/live")
    assert code == 200 and b"OptiDay" in html and b'rel="manifest"' in html
    assert "default-src" in headers["Content-Security-Policy"]
    assert req("/api/live")[0] == 403                     # other sites can't read it
    code, body, _ = req("/api/live", headers=H)
    st = json.loads(body)
    assert code == 200 and {"state", "totals", "timeline", "nudges", "current"} <= st.keys()

    # long poll wakes up as soon as the recorder sees something new
    got = {}
    th = threading.Thread(target=lambda: got.update(json.loads(req(f"/api/live?v={st['version']}", headers=H)[1])))
    th.start()
    time.sleep(0.2)
    ctx.live_state().ingest(Snapshot(ts=time.time(), span=5, **CODE))
    th.join(5)
    assert got and got["version"] > st["version"] and got["current"]["place"] == "Code"

    code, body, _ = req("/api/focus", "POST", {"action": "start", "minutes": 25, "label": "Deck"}, H)
    assert code == 200 and json.loads(body)["focus_session"]["label"] == "Deck"
    assert req("/api/focus", "POST", {"action": "start", "minutes": "lots"}, H)[0] == 400
    assert req("/api/focus", "POST", {"action": "stop"})[0] == 403
    code, body, _ = req("/api/focus", "POST", {"action": "stop"}, H)
    assert code == 200 and json.loads(body)["focus_session"] is None

    code, body, _ = req("/api/pause", "POST", {"paused": True}, H)
    assert code == 200 and json.loads(body)["state"] == "paused"
    assert (ctx.data_dir / "paused").exists()
    assert req("/api/pause", "POST", {"paused": "yes"}, H)[0] == 400
    code, body, _ = req("/api/pause", "POST", {"paused": False}, H)
    assert code == 200 and json.loads(body)["state"] != "paused"
    assert not (ctx.data_dir / "paused").exists()

    code, body, _ = req("/manifest.webmanifest")
    assert code == 200 and json.loads(body)["start_url"] == "/live"
    code, body, _ = req("/icon-512.png")
    assert code == 200 and body[:8] == b"\x89PNG\r\n\x1a\n"


def test_live_html_escapes_boot_data():
    from optiday.live_html import render_live
    html = render_live({"state": "focus", "current": {"title": "</script><script>alert(1)</script>"}})
    assert "</script><script>alert(1)" not in html
