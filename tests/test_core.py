import datetime as dt
import sqlite3

from optiday import activity, config
from optiday.models import Snapshot, WindowInfo
from optiday.recorder import Recorder


def test_default_config_parses_and_is_written(home):
    cfg = config.load_config()
    assert cfg.rec["interval_seconds"] == 5
    assert (home / "config.toml").exists()
    assert "OptiDay" in cfg.projects


def test_set_config_value_keeps_comments(home):
    config.load_config()
    config.set_config_value("ai", "base_url", "http://127.0.0.1:11435")
    config.set_config_value("ai", "brand_new", True)
    text = (home / "config.toml").read_text()
    assert "# Fastest check rate" in text
    cfg = config.load_config()
    assert cfg.ai["base_url"] == "http://127.0.0.1:11435"
    assert cfg.ai["brand_new"] is True
    assert cfg.ai["model"] == "qwen2.5:7b"


def test_broken_config_falls_back_to_defaults(home):
    (home).mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text("this is = = not toml")
    assert config.load_config().rec["interval_seconds"] == 5


def test_store_encrypts_sensitive_fields(store, home):
    store.add_snapshot(Snapshot(ts=dt.datetime(2026, 11, 13, 10).timestamp(), app="Google Chrome",
                                title="Secret plan for Acme", url="https://docs.google.com/x",
                                ocr="Password reset code 123456"))
    raw = sqlite3.connect(home / "optiday.db").execute("SELECT payload, ocr FROM snapshots").fetchone()
    blob = b"".join(bytes(x) for x in raw)
    assert b"Secret plan" not in blob and b"123456" not in blob
    got = store.snapshots("2026-11-13")[0]
    assert got.title == "Secret plan for Acme" and got.ocr == "Password reset code 123456"


def test_day_boundary(store):
    late = dt.datetime(2026, 11, 14, 2, 30).timestamp()   # 2:30am counts as the previous day
    assert store.day_of(late) == "2026-11-13"
    assert store.day_of(dt.datetime(2026, 11, 14, 4, 1).timestamp()) == "2026-11-14"


def test_overrides_and_notes(store):
    store.set_override("video", "abc", "waste")
    assert store.overrides("video") == {"abc": "waste"}
    store.set_override("video", "abc", None)
    assert store.overrides("video") == {}
    store.put_note("k", "2026-11-13", {"a": [1, 2]})
    assert store.get_note("k") == {"a": [1, 2]}


def test_purge_ocr(store):
    store.add_snapshot(Snapshot(ts=dt.datetime(2020, 1, 1, 10).timestamp(), app="X", ocr="old text"))
    assert store.purge_ocr_older_than(30) == 1
    assert store.snapshots("2020-01-01")[0].ocr is None


def _snap(t, app="Code", title="a.py — proj — Visual Studio Code", **kw):
    return Snapshot(ts=t, app=app, title=title, **kw)


def test_build_segments_merges_and_caps_gaps():
    t0 = 1_000_000.0
    snaps = [_snap(t0), _snap(t0 + 5, typing=True), _snap(t0 + 10), _snap(t0 + 15, app="Slack", title="x"),
             _snap(t0 + 500, app="Slack", title="x")]  # big gap: Mac asleep
    segs = activity.build_segments(snaps, 5)
    assert [s.app for s in segs] == ["Code", "Slack", "Slack"]
    assert segs[0].seconds == 15 and segs[0].typing_s == 5
    assert segs[1].seconds == 15  # capped at 3 intervals, not 485s


def test_unread_counts_dont_split_segments():
    t0 = 1_000_000.0
    snaps = [_snap(t0, app="Slack", title="(3) general - Slack"), _snap(t0 + 5, app="Slack", title="general - Slack")]
    assert len(activity.build_segments(snaps, 5)) == 1


class FakeCap:
    def __init__(self, script):
        self.script = list(script)
        self.ocr_calls = 0
        self.fast = []
        self.cur = None

    def front_window(self):
        self.cur = self.script.pop(0) if self.script else self.cur
        return self.cur["win"]

    def idle_seconds(self):
        return self.cur.get("idle", 1)

    def seconds_since_typing(self):
        return self.cur.get("since_key", 100)

    def read_screen_text(self, win, fast=False):
        self.ocr_calls += 1
        self.fast.append(fast)
        return f"text of {win.title}"


def test_recorder_privacy_idle_and_ocr_throttle(cfg, store, home):
    clock = {"t": dt.datetime(2026, 11, 13, 10).timestamp()}
    code = WindowInfo(app="Code", bundle_id="com.microsoft.VSCode", title="a.py — proj — Visual Studio Code")
    vault = WindowInfo(app="1Password", title="Bank login")
    yt = WindowInfo(app="Google Chrome", bundle_id="com.google.Chrome", title="Talk",
                    url="https://www.youtube.com/watch?v=1")
    incog = WindowInfo(app="Google Chrome", bundle_id="com.google.Chrome", title="Shh", url="https://a.com",
                       private_window=True)
    script = [{"win": code, "since_key": 1}, {"win": code}, {"win": code}, {"win": vault},
              {"win": yt, "idle": 600}, {"win": code, "idle": 600}, {"win": incog}, {"win": None}]
    cap = FakeCap(script)
    rec = Recorder(cfg, store, cap, home, clock=lambda: clock["t"])
    snaps = []
    for _ in script:
        snaps.append(rec.tick())
        clock["t"] += 5
    rec.flush()
    code1, code2, code3, pw, video, idle, inc, locked = snaps
    assert code1.typing and code1.ocr == "text of a.py — proj — Visual Studio Code"
    assert code2.ocr is None and code3.ocr is None          # same window, OCR throttled
    assert pw.private and pw.title == "" and pw.ocr is None  # excluded app: nothing captured
    assert not video.away                                    # watching = active even without input
    assert idle.away and idle.ocr is None
    assert inc.private and inc.url == ""
    assert locked is None
    assert cap.ocr_calls == 2
    assert len(store.snapshots("2026-11-13")) == 7


def test_pause(cfg, store, home):
    cap = FakeCap([{"win": WindowInfo(app="Code")}])
    rec = Recorder(cfg, store, cap, home)
    rec.set_paused(True)
    assert rec.tick() is None
    rec.set_paused(False)
    assert rec.tick() is not None
