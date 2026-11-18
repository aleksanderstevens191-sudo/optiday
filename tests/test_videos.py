"""v0.4.1: YouTube videos sorted into learning / waste / not sure, remembered from your answers."""
import datetime as dt
import json

import pytest

from optiday import rules, videos
from optiday.live import LiveState
from optiday.llm import OllamaLLM
from optiday.models import Snapshot
from test_live import CODE, DAY, Clock, feed

CH = dict(app="Google Chrome", bundle_id="com.google.Chrome")


def yt(vid, title):
    return dict(CH, title=f"{title} - YouTube", url=f"https://www.youtube.com/watch?v={vid}")


def screen(channel, subs="1.2M"):
    return f"{channel}\nSubscribe\n{subs} subscribers\n4.1K Share Download"


@pytest.mark.parametrize("title,label", [
    ("TOO LATE FOR YOU", "unclear"),                       # no clue from the title: ask, don't guess
    ("Lakers vs Celtics", "unclear"),
    ("Lecture 4: Linear Algebra", "informational"),
    ("Python for beginners", "informational"),
    ("How AI agents work", "informational"),               # two weak hints agree
    ("Drake - Hotline Bling (Official Video)", "waste"),
    ("How I started my morning routine vlog", "waste"),    # "started" doesn't contain the word "ted"
    ("Funny fails but it's a tutorial", "unclear"),        # strong hints both ways: ask
])
def test_cautious_title_rules(title, label):
    assert rules.classify_video_heuristic({"title": title, "kind": "video"})[0] == label


@pytest.mark.parametrize("text,channel", [
    ("Big title\nMKBHD ✓\nSubscribe\n19.6M subscribers\n12K Share", "MKBHD"),
    ("Title\nLex Fridman 4.6M subscribers\nJoin Subscribe", "Lex Fridman"),
    ("Title\n3Blue1Brown\n6.9M subscribers Subscribe", "3Blue1Brown"),
    ("Title\n1.2M views 3 days ago\nnothing else", ""),
    ("", ""),
])
def test_channel_from_screen(text, channel):
    assert rules.channel_from_screen(text) == channel


def test_optiday_pages_and_projects():
    assert rules.is_optiday_page("http://127.0.0.1:8765/live", "OptiDay Live")
    assert not rules.is_optiday_page("http://127.0.0.1:3000/", "My app")
    assert rules.categorize("Google Chrome", "com.google.Chrome", "https://iu.instructure.com/courses/1") == "Learning"
    rules._ME.add("sam")
    assert rules.project_from_window("Terminal", "com.apple.Terminal", "sam — -zsh — 80×24", "") == ""
    assert rules.project_from_window("Terminal", "com.apple.Terminal", "optiday — -zsh — 80×24", "") == "optiday"
    assert rules.project_from_window("Cursor", "com.todesktop.1", "Cursor Agents", "") == ""


def test_judge_order_and_memory(cfg, store):
    j = videos.VideoJudge(store, cfg)
    v = {"id": "a1", "title": "TOO LATE FOR YOU", "kind": "video"}
    j.remember(v, "2026-11-13")
    assert j.judge(v)["source"] == "unsure"
    # a channel seen on screen is remembered with the video
    assert j.remember(v, "2026-11-13", "Night Drive FM")
    assert j.channel_of("a1") == "Night Drive FM"
    # your answer wins, and carries over to the channel's other videos
    assert j.label("a1", "waste") == "Night Drive FM"
    assert j.judge(v) | {} == j.judge(v) and j.judge(v)["source"] == "you" and j.judge(v)["label"] == "waste"
    other = {"id": "a2", "title": "Lecture 9: Graphs", "kind": "video"}
    j.remember(other, "2026-11-14", "Night Drive FM")
    got = j.judge(other)
    assert got["label"] == "waste" and got["source"] == "channel"       # your channel call beats title rules
    j.label("a2", "informational")
    assert j.judge(other)["source"] == "you"
    assert j.judge(v)["label"] == "waste"                                # the first video keeps your answer
    # titles like ones you marked
    j.remember({"id": "m1", "title": "Late Night Drive Mix Vol 1"}, "2026-11-14")
    j.label("m1", "waste")
    like = j.judge({"id": "m2", "title": "Late Night Drive Mix Vol 2", "kind": "video"})
    assert like["label"] == "waste" and like["source"] == "learned" and "Vol 1" in like["reason"]
    with pytest.raises(ValueError):
        j.label("m1", "maybe")


def test_ai_only_asks_about_unsure_videos(cfg, store, fake_ollama):
    day = "2026-11-13"
    j = videos.VideoJudge(store, cfg)
    for vid, title in (("v1", "Linear algebra recap"), ("v2", "Sunset mix"), ("v3", "Something else"),
                       ("v4", "Python for beginners")):
        j.remember({"id": vid, "title": title}, day)
    j.label("v3", "unclear")                     # you said "not sure": the AI isn't asked to overrule you
    llm = OllamaLLM(fake_ollama.url, "qwen2.5:7b")
    assert videos.ai_judge_unsure(store, cfg, llm, day) == 3
    j.refresh(force=True)
    assert j.judge({"id": "v1", "title": "Linear algebra recap"})["source"] == "ai"
    assert j.judge({"id": "v1", "title": "Linear algebra recap"})["label"] == "informational"
    assert j.judge({"id": "v2", "title": "Sunset mix"})["label"] == "waste"
    assert j.judge({"id": "v3", "title": "Something else"})["source"] == "you"
    calls = fake_ollama.video_calls
    assert videos.ai_judge_unsure(store, cfg, llm, day) == 0 and fake_ollama.video_calls == calls  # asked once
    # model not downloaded: nothing is asked, videos stay "not sure"
    j.remember({"id": "v5", "title": "Another one"}, day)
    missing = OllamaLLM(fake_ollama.url, "llama-not-pulled:1b")
    assert videos.ai_judge_unsure(store, cfg, missing, day) == 0


@pytest.fixture
def live(cfg, store, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(store, "today", lambda: DAY.isoformat())
    lv = LiveState(cfg, store, clock=clock)
    lv.clock_ = clock
    return lv


def test_unsure_video_is_not_counted_as_distraction(live):
    clock = live.clock_
    feed(live, clock, CODE, 10 * 60)
    feed(live, clock, yt("tl1", "TOO LATE FOR YOU"), 6 * 60)
    s = live.snapshot()
    assert s["state"] == "watch" and s["current"]["video"]["label"] == "unclear"
    assert sum(b[5] for b in s["timeline"]) == pytest.approx(6 * 60, abs=10)   # amber on the timeline
    assert s["totals"]["distracting_s"] == 0
    y = s["youtube"]
    assert y["unsure_s"] == pytest.approx(6 * 60, abs=10) and y["unsure"] == 1
    assert y["videos"][0]["id"] == "tl1" and y["videos"][0]["now"]
    assert any("need" in n["text"] and "learning or waste" in n["text"] for n in s["nudges"])
    yt_place = next(p for p in s["places"] if p["name"] == "YouTube")
    assert yt_place["video"] and yt_place["split"]["neutral"] > 0


def test_labeling_moves_time_and_remembers_channel(live, cfg, store):
    clock = live.clock_
    feed(live, clock, CODE, 10 * 60)
    feed(live, clock, yt("tl1", "TOO LATE FOR YOU"), 2 * 60)
    live.ingest(Snapshot(ts=clock.t, span=5, ocr=screen("Night Drive FM"), **yt("tl1", "TOO LATE FOR YOU")))
    clock.t += 5
    feed(live, clock, yt("tl1", "TOO LATE FOR YOU"), 3 * 60)
    before = live.snapshot()
    assert before["current"]["video"]["channel"] == "Night Drive FM"
    unsure = before["youtube"]["unsure_s"]

    live.label_video("tl1", "waste")
    s = live.snapshot()
    assert s["youtube"]["waste_s"] == unsure and s["youtube"]["unsure_s"] == 0 and s["youtube"]["unsure"] == 0
    assert s["totals"]["distracting_s"] == pytest.approx(unsure, abs=6)
    assert s["state"] == "drift"                                     # watching it now counts as drifting
    assert sum(b[3] for b in s["timeline"]) == pytest.approx(unsure, abs=6)   # the timeline turned red too
    assert sum(b[5] for b in s["timeline"]) == 0
    assert store.overrides("channel") == {"night drive fm": "waste"}

    # the next video from that channel is sorted for you
    live.ingest(Snapshot(ts=clock.t, span=5, ocr=screen("Night Drive FM"), **yt("tl2", "MIDNIGHT")))
    clock.t += 5
    feed(live, clock, yt("tl2", "MIDNIGHT"), 60)
    v = live.snapshot()["current"]["video"]
    assert v["label"] == "waste" and v["source"] == "channel"

    # change your mind: learning moves the time to productive
    live.label_video("tl1", "informational")
    s = live.snapshot()
    assert s["youtube"]["learn_s"] >= unsure and s["totals"]["productive_s"] >= 10 * 60 + unsure - 6

    # a fresh start (restart) remembers everything
    again = LiveState(cfg, store, clock=clock)
    j = again.judge.judge({"id": "tl2", "title": "MIDNIGHT"})
    assert j["label"] == "informational" and j["source"] == "channel"   # follows your latest call for the channel
    assert j["channel"] == "Night Drive FM"


def test_looking_at_optiday_is_not_coding(live):
    clock = live.clock_
    feed(live, clock, dict(CH, title="OptiDay Live", url="http://127.0.0.1:8765/live"), 60)
    s = live.snapshot()
    assert s["current"]["place"] == "OptiDay" and s["current"]["category"] == "Other"
    assert s["current"]["project"] == "" and s["totals"]["productive_s"] == 0


def test_label_api_from_live_view(cfg, store):
    import urllib.request
    from optiday.dashboard import Dashboard
    from optiday.service import Context
    ctx = Context(cfg)
    ctx.llm = None
    live = ctx.live_state()
    now = dt.datetime.now().timestamp()
    for i in range(12):
        live.ingest(Snapshot(ts=now - 60 + i * 5, span=5, **yt("abc123", "TOO LATE FOR YOU")))
    d = Dashboard(ctx, port=18867)
    d.poll_interval = 0.2
    d.start()
    try:
        def post(body, hdr=True):
            r = urllib.request.Request(d.url + "/api/label", data=json.dumps(body).encode(), method="POST",
                                       headers={"Host": f"127.0.0.1:{d.port}", **({"X-OptiDay": "1"} if hdr else {})})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(r, timeout=20) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, None
        assert post({"video_id": "abc123", "label": "waste"}, hdr=False)[0] == 403
        assert post({"video_id": "../x", "label": "waste"})[0] == 400
        assert post({"video_id": "abc123", "label": "meh"})[0] == 400
        code, snap = post({"video_id": "abc123", "label": "informational"})
        assert code == 200 and snap["ok"] and snap["youtube"]["learn_s"] > 0
        assert store.overrides("video")["abc123"] == "informational"
    finally:
        d.stop()


def test_reports_use_the_same_verdicts(demo_store, cfg, demo_day):
    from optiday import analyzer
    day = demo_day.isoformat()
    r = analyzer.analyze_day(demo_store, cfg, day)
    assert r["metrics"]["videos"], "demo day has videos"
    vid = next(v for v in r["metrics"]["videos"] if v["label"] != "informational")
    demo_store.set_override("video", vid["id"], "informational")
    r2 = analyzer.analyze_day(demo_store, cfg, day)
    again = {v["id"]: v for v in r2["metrics"]["videos"]}
    assert again[vid["id"]]["label"] == "informational" and again[vid["id"]]["source"] == "you"
    # the report remembered each video's title, so your answers can teach OptiDay
    assert demo_store.get_note(videos.META + vid["id"])["title"]
