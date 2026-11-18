import json
import urllib.request

import pytest

from optiday import analyzer, report_html
from optiday.dashboard import Dashboard
from optiday.llm import OllamaLLM, OpenAICompatLLM, make_llm, parse_json
from optiday.service import Context


def _texts(report, kind=None):
    return [a["text"] for a in report["actions"] if kind is None or a["kind"] == kind]


def test_rules_only_report(demo_store, cfg, demo_day):
    r = analyzer.analyze_day(demo_store, cfg, demo_day.isoformat(), llm=None)
    m = r["metrics"]
    assert 6.5 * 3600 < m["active_s"] < 8.5 * 3600
    names = [p["name"] for p in m["by_project"]]
    assert names[0] == "OptiDay" and "AutoTrader" in names and "optiday" not in names
    emails = _texts(r, "email")
    assert any("sarah@lumen.example" in t and "OptiDay beta pricing" in t for t in emails)
    assert any("dana@northwind.example" in t for t in emails)
    assert any("Merged PR #12" in t for t in _texts(r, "code"))
    assert any(t.startswith("Google Meet: Investor sync") for t in _texts(r, "meeting"))
    vids = {v["title"]: v["label"] for v in m["videos"]}
    assert vids["Try Not To Laugh Challenge #47"] == "waste"
    assert vids["How Y Combinator Startups Find Their First 100 Users"] == "informational"
    assert m["focus_blocks"] and m["switches"] > 20
    assert r["insights"]["suggestions"] and r["insights"]["source"] == "rules"
    # private (excluded) sites never show up by name
    assert "robinhood" not in json.dumps(r).lower()
    html = report_html.render(r)
    assert "Try Not To Laugh" in html and "<script" not in html


def test_ai_report_and_caching(demo_store, cfg, demo_day, fake_ollama):
    llm = OllamaLLM(fake_ollama.url, "qwen2.5:7b")
    day = demo_day.isoformat()
    r = analyzer.analyze_day(demo_store, cfg, day, llm=llm)
    assert r["ai"]["used"] and r["insights"]["source"] == "ai"
    assert r["insights"]["headline"].startswith("Shipped the recorder")
    assert fake_ollama.unit_calls > 0 and fake_ollama.narrative_calls == 1
    # AI wording replaces the rule wording for the same email, keeping one entry
    emails = _texts(r, "email")
    assert sum("Sarah" in t for t in emails) == 1
    assert any(t == "Sent email to Sarah Lee about OptiDay beta pricing" for t in emails)
    # "rumps" chat in Claude got assigned to the project by the AI, with the configured spelling
    assert all(p["name"] != "optiday" for p in r["metrics"]["by_project"])
    calls = fake_ollama.unit_calls
    analyzer.analyze_day(demo_store, cfg, day, llm=llm)
    assert fake_ollama.unit_calls == calls  # cached: no repeated work


def test_user_label_beats_ai(demo_store, cfg, demo_day, fake_ollama):
    day = demo_day.isoformat()
    r = analyzer.analyze_day(demo_store, cfg, day, llm=None)
    vid = next(v["id"] for v in r["metrics"]["videos"] if "Laugh" in v["title"])
    demo_store.set_override("video", vid, "informational")
    r = analyzer.analyze_day(demo_store, cfg, day, llm=OllamaLLM(fake_ollama.url, "qwen2.5:7b"))
    v = next(v for v in r["metrics"]["videos"] if v["id"] == vid)
    assert v["label"] == "informational" and v["source"] == "you"


def test_broken_model_falls_back_to_rules(demo_store, cfg, demo_day):
    from conftest import FakeOllama
    f = FakeOllama(broken=True)
    try:
        r = analyzer.analyze_day(demo_store, cfg, demo_day.isoformat(), llm=OllamaLLM(f.url, "qwen2.5:7b"))
    finally:
        f.close()
    assert r["insights"]["suggestions"] and r["insights"]["source"] == "rules"
    assert _texts(r, "email")


def test_unreachable_model_is_reported(cfg):
    ok, msg = OllamaLLM("http://127.0.0.1:9", "qwen2.5:7b").status()
    assert not ok and "Can't reach" in msg


def test_missing_model_is_reported(fake_ollama):
    ok, msg = OllamaLLM(fake_ollama.url, "llama3.1:70b").status()
    assert not ok and "ollama pull llama3.1:70b" in msg


def test_make_llm(cfg):
    assert isinstance(make_llm(cfg), OllamaLLM)
    cfg.ai["provider"] = "openai"
    assert isinstance(make_llm(cfg), OpenAICompatLLM)
    cfg.ai["provider"] = "none"
    assert make_llm(cfg) is None


@pytest.mark.parametrize("text", [
    '{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! Here you go: {"a": 1} hope it helps',
    '<think>hmm</think>{"a": 1}',
])
def test_parse_json(text):
    assert parse_json(text) == {"a": 1}


def test_parse_clock():
    import datetime as dt
    near = dt.datetime(2026, 11, 13, 14, 5).timestamp()
    assert dt.datetime.fromtimestamp(analyzer._parse_clock("2:14pm", near)).strftime("%H:%M") == "14:14"
    assert dt.datetime.fromtimestamp(analyzer._parse_clock("2:14", near)).strftime("%H:%M") == "14:14"
    assert analyzer._parse_clock("noonish", near) == near


def test_empty_day_renders(store, cfg):
    r = analyzer.analyze_day(store, cfg, "2026-01-01", llm=None)
    assert r["metrics"].get("empty")
    assert "No activity recorded" in report_html.render(r)


def test_html_escapes_window_titles(store, cfg):
    import datetime as dt
    from optiday.models import Snapshot
    t = dt.datetime(2026, 11, 13, 10).timestamp()
    store.add_snapshots([Snapshot(ts=t + i * 5, app="Safari", title="<img src=x onerror=alert(1)>",
                                  url="https://example.com") for i in range(20)])
    html = report_html.render(analyzer.analyze_day(store, cfg, "2026-11-13", llm=None), interactive=True)
    assert "<img src=x" not in html and "&lt;img src=x" in html


def _req(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_dashboard(demo_store, cfg, demo_day):
    ctx = Context(cfg)
    ctx.store = demo_store
    ctx.llm = None
    d = Dashboard(ctx, port=0)
    d.port = 18765
    d.poll_interval = 0.2
    d.start()
    try:
        day = demo_day.isoformat()
        code, html = _req(f"{d.url}/day/{day}")
        assert code == 200 and "What you did, with times" in html and "relabel" in html
        code, html = _req(f"{d.url}/")
        assert code == 200 and day in html
        # DNS-rebinding / foreign host is refused
        assert _req(f"{d.url}/", headers={"Host": "evil.example:18765"})[0] == 403
        report = ctx.store.get_note(f"report:{day}")
        vid = next(v["id"] for v in report["metrics"]["videos"] if "Laugh" in v["title"])
        body = json.dumps({"video_id": vid, "label": "informational", "day": day}).encode()
        # cross-site form posts can't add the custom header -> refused
        assert _req(f"{d.url}/api/label", "POST", body, {"Content-Type": "application/json"})[0] == 403
        code, _ = _req(f"{d.url}/api/label", "POST", body, {"Content-Type": "application/json", "X-OptiDay": "1"})
        assert code == 200
        report = ctx.store.get_note(f"report:{day}")
        v = next(v for v in report["metrics"]["videos"] if v["id"] == vid)
        assert v["label"] == "informational" and v["source"] == "you"
    finally:
        d.stop()
