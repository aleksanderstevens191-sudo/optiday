import datetime as dt
import sqlite3
import threading
import time


from optiday import activity
from optiday.models import Snapshot, WindowInfo
from optiday.power import EnergyMeter, PowerMonitor, PowerState, parse_pmset, policy_for
from optiday.recorder import Recorder
from test_core import FakeCap

AC = PowerState(on_battery=False, percent=100)
BATT = PowerState(on_battery=True, percent=80)
LOW = PowerState(on_battery=True, percent=12)
LPM = PowerState(on_battery=True, percent=90, low_power_mode=True)


def test_parse_pmset():
    assert parse_pmset("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t85%; discharging;") == (True, 85)
    assert parse_pmset("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;") == (False, 100)
    assert parse_pmset("Now drawing from 'AC Power'") == (False, None)  # desktop Mac


def test_policies(cfg):
    ac, batt, low, lpm = (policy_for(cfg, s) for s in (AC, BATT, LOW, LPM))
    assert ac.ai and ac.ocr and not ac.ocr_fast and ac.base_interval == 5
    assert not batt.ai and batt.ocr and batt.ocr_fast and batt.ocr_refresh_other == 0
    assert batt.base_interval > ac.base_interval and batt.max_interval >= ac.max_interval
    assert not low.ocr and not low.ai and not lpm.ocr
    cfg["power"]["mode"] = "max"
    assert policy_for(cfg, LOW).ocr
    cfg["power"]["mode"] = "saver"
    assert policy_for(cfg, AC).name == "battery saver" and policy_for(cfg, AC).ai
    cfg["power"]["mode"] = "auto"
    cfg["power"]["ai_on_battery"] = True
    assert policy_for(cfg, BATT).ai


def test_power_monitor_caches(cfg):
    calls = []
    t = {"now": 0.0}
    mon = PowerMonitor(cfg, probe=lambda: calls.append(1) or BATT, ttl=120, clock=lambda: t["now"])
    for _ in range(50):
        mon.policy()
        t["now"] += 2
    assert len(calls) == 1  # one `pmset` per two minutes, not per tick


def test_energy_meter():
    m = EnergyMeter()
    sum(i * i for i in range(200_000))
    assert m.cpu_percent() >= 0


class FixedPower(PowerMonitor):
    def __init__(self, cfg, state):
        super().__init__(cfg, probe=lambda: state)


def _rec(cfg, store, home, script, state=AC):
    clock = {"t": dt.datetime(2026, 11, 13, 10).timestamp()}
    cap = FakeCap(script)
    rec = Recorder(cfg, store, cap, home, clock=lambda: clock["t"], power=FixedPower(cfg, state))

    def run():
        out = []
        for _ in script:
            out.append((rec.tick(), rec.next_delay))
            clock["t"] += rec.next_delay
        return out
    return rec, cap, run


CODE = WindowInfo(app="Code", bundle_id="com.microsoft.VSCode", title="a.py — proj — Visual Studio Code")
MAIL = WindowInfo(app="Google Chrome", bundle_id="com.google.Chrome", title="Inbox",
                  url="https://mail.google.com/mail/u/0/#inbox")
VIDEO = WindowInfo(app="Google Chrome", bundle_id="com.google.Chrome", title="Talk",
                   url="https://www.youtube.com/watch?v=1")


def test_backs_off_when_nothing_changes(cfg, store, home):
    rec, cap, run = _rec(cfg, store, home, [{"win": CODE, "idle": 5}] * 8 + [{"win": MAIL, "idle": 1}])
    delays = [d for _, d in run()]
    assert delays[0] == 5 and delays[1] > 5 and max(delays) == 20
    assert delays[-1] == 5  # something changed: back to fast checks
    total_wakeups_per_hour = 3600 / 20
    assert total_wakeups_per_hour <= 180


def test_away_and_locked_check_rarely(cfg, store, home):
    rec, cap, run = _rec(cfg, store, home, [{"win": CODE, "idle": 600}, {"win": None}], BATT)
    (s1, d1), (s2, d2) = run()
    assert s1.away and d1 == 60 and s2 is None and d2 == 60


def test_ocr_budget_plugged_in(cfg, store, home):
    script = ([{"win": MAIL, "idle": 1, "since_key": 1}] * 6 + [{"win": VIDEO, "idle": 100}] * 6
              + [{"win": MAIL, "idle": 1}])
    rec, cap, run = _rec(cfg, store, home, script)
    run()
    # mail: read on arrival + re-read every ~20s while typing; video: once; back to mail
    # within 2 minutes without typing: skipped
    assert 2 <= cap.ocr_calls <= 5
    assert not any(cap.fast)


def test_ocr_budget_on_battery(cfg, store, home):
    script = [{"win": CODE, "idle": 1}] * 10 + [{"win": MAIL, "idle": 1}] + [{"win": CODE, "idle": 1}]
    rec, cap, run = _rec(cfg, store, home, script, BATT)
    run()
    assert cap.ocr_calls == 2 and all(cap.fast)  # code once, mail once; no periodic code re-reads
    rec2, cap2, run2 = _rec(cfg, store, home, [{"win": MAIL, "idle": 1}] * 5, LOW)
    run2()
    assert cap2.ocr_calls == 0


def test_poke_wakes_the_loop(cfg, store, home):
    cap = FakeCap([{"win": CODE, "idle": 5}] * 1000)
    rec = Recorder(cfg, store, cap, home, power=FixedPower(cfg, BATT))
    stop = threading.Event()
    th = threading.Thread(target=rec.run, args=(stop,), daemon=True)
    th.start()
    time.sleep(0.3)
    before = rec.stats["ticks"]
    rec.poke()
    time.sleep(0.3)
    assert rec.stats["ticks"] == before + 1   # app switch noticed immediately, not after 8-30s
    stop.set()
    rec.poke()
    th.join(2)
    assert not th.is_alive()


def test_sleep_pauses_checks(cfg, store, home):
    rec, cap, run = _rec(cfg, store, home, [{"win": CODE}])
    rec.on_sleep()
    cap.cur = {"win": CODE, "idle": 400}
    assert rec.tick() is None and rec.next_delay == 120
    rec.poke()
    assert rec.tick() is not None
    rec.on_sleep()
    cap.script = [{"win": CODE, "idle": 2}]
    cap.cur = {"win": CODE, "idle": 2}   # wake notification missed, but you're typing again
    assert rec.tick() is not None and not rec.sleeping


def test_segments_use_adaptive_span():
    t0 = 1_000_000.0
    snaps = [Snapshot(ts=t0, app="Code", title="x", span=20), Snapshot(ts=t0 + 20, app="Code", title="x", span=30),
             Snapshot(ts=t0 + 50, app="Code", title="x", span=30), Snapshot(ts=t0 + 4000, app="Code", title="x",
                                                                            span=5)]
    segs = activity.build_segments(snaps, 5)
    assert segs[0].seconds == 50 + 47  # last tick before sleep counts at most 1.5x its span
    assert len(segs) == 2


def test_v01_database_is_migrated(home, cfg):
    from optiday.config import data_dir
    from optiday.storage import open_store
    db = sqlite3.connect(data_dir() / "optiday.db")
    db.executescript("""CREATE TABLE snapshots (id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL,
        app TEXT NOT NULL, bundle_id TEXT, idle_s REAL, typing INTEGER, private INTEGER, away INTEGER,
        payload BLOB, ocr BLOB);
        INSERT INTO snapshots(ts,day,app,away) VALUES (1794564000, '2026-11-13', 'Code', 0);""")
    db.commit()
    db.close()
    s = open_store(cfg, data_dir())
    s.add_snapshot(Snapshot(ts=1794564005, app="Code", span=12))
    snaps = s.snapshots("2026-11-13")
    assert [x.span for x in snaps] == [0.0, 12]
    assert s.active_seconds("2026-11-13", 5) == 17


def test_battery_report_gets_ai_later(demo_store, cfg, demo_day, fake_ollama, monkeypatch):
    from optiday.llm import OllamaLLM
    from optiday.service import Context, Scheduler
    ctx = Context(cfg)
    ctx.store = demo_store
    ctx.llm = OllamaLLM(fake_ollama.url, "qwen2.5:7b")
    state = {"s": BATT}
    ctx.power = PowerMonitor(cfg, probe=lambda: state["s"], ttl=0)
    day = demo_day.isoformat()
    r = ctx.build_report(day)
    assert r["ai"]["pending"] and fake_ollama.unit_calls == 0 and fake_ollama.narrative_calls == 0
    sched = Scheduler(ctx)
    monkeypatch.setattr("optiday.service.notify", lambda *a: None)
    sched.step()   # still on battery: nothing heavy runs
    assert fake_ollama.unit_calls == 0
    state["s"] = AC
    sched.step()   # plugged in: AI summary gets added
    r = ctx.store.get_note(f"report:{day}")
    assert r["ai"]["narrative"] and not r["ai"]["pending"] and fake_ollama.unit_calls > 0
    assert ctx.store.get_meta(f"ai-pending:{day}") == "0"
