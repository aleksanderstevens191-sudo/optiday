import datetime as dt
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIDAY_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def cfg():
    from optiday.config import load_config
    return load_config()


@pytest.fixture
def store(cfg):
    from optiday.config import data_dir
    from optiday.storage import open_store
    s = open_store(cfg, data_dir())
    yield s
    s.close()


@pytest.fixture
def demo_day():
    return dt.date(2026, 11, 13)


@pytest.fixture
def demo_store(store, demo_day, cfg):
    from optiday.demo import demo_snapshots
    store.add_snapshots(demo_snapshots(demo_day, float(cfg.rec["interval_seconds"])))
    return store


class FakeOllama:
    """Speaks the Ollama HTTP API and answers like a well-behaved small model."""

    def __init__(self, model="qwen2.5:7b", broken=False):
        self.model = model
        self.broken = broken
        self.unit_calls = 0
        self.narrative_calls = 0
        self.hour_calls = 0
        self.draft_calls = 0
        self.agent_calls = 0
        self.video_calls = 0
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    return self._json({"models": [{"name": fake.model}]})
                self._json({}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                system = body["messages"][0]["content"]
                user = body["messages"][1]["content"]
                if fake.broken:
                    return self._json({"message": {"content": "sorry, I can't do JSON today"}})
                if "diary" in system:
                    fake.unit_calls += 1
                    content = json.dumps({"chunks": fake.units(user)})
                elif "hour-by-hour log" in system:
                    fake.hour_calls += 1
                    hours = json.loads(user)
                    content = json.dumps({"hours": [{"hour": h["hour"], "summary": f"AI hour: {h['hour']} "
                                                     f"({h['active_minutes']}m)"} for h in hours]})
                elif "draft email replies" in system:
                    fake.draft_calls += 1
                    items = json.loads(user)
                    content = json.dumps({"drafts": [{"id": i["id"], "draft": f"Hi! Re {i['subject']}: yes, ___ works.",
                                                      "next_step": "Pick a time"} for i in items]})
                elif "You sort YouTube videos" in system:
                    fake.video_calls += 1
                    out = []
                    for n, title in re.findall(r"^(\d+)\. “(.*?)”", user, re.M):
                        low = title.lower()
                        lab = "informational" if "algebra" in low else "waste" if "mix" in low else "unclear"
                        out.append({"n": int(n), "label": lab, "reason": "fake model verdict"})
                    content = json.dumps({"videos": out})
                elif "repetitive work pattern" in system:
                    fake.agent_calls += 1
                    content = json.dumps({"title": "Automate the copy-paste loop",
                                          "task": "Write a script that moves the rows for me.", "saves": "~20 min/day"})
                else:
                    fake.narrative_calls += 1
                    content = "```json\n" + json.dumps({
                        "headline": "Shipped the recorder, then lost the afternoon to tabs",
                        "summary": "You merged the recorder PR before 10:10am and wrote the pricing doc.",
                        "went_well": ["2h focus block on OptiDay from 9:05am"],
                        "time_sinks": ["26m of YouTube Shorts and fails videos"],
                        "suggestions": [{"title": "Close Slack after lunch",
                                         "detail": "1pm–2pm had 39 switches; mute Slack until 2:30pm."}],
                        "tomorrow": "Start on OptiDay at 9am with Slack closed.",
                        "done": ["Merged the recorder PR", "Wrote the pricing doc"],
                        "next_steps": ["Finish the menu bar settings screen"],
                    }) + "\n```"
                self._json({"message": {"role": "assistant", "content": content}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def units(self, text):
        out = []
        for block in re.split(r"\n\n(?=\[u\d+\])", text.split("\n\n", 1)[1]):
            m = re.match(r"\[(u\d+)\] (\S+)", block)
            if not m:
                continue
            uid, start = m.group(1), m.group(2).split("–")[0]
            low = block.lower()
            item = {"id": uid, "project": "", "summary": "Did something", "actions": []}
            if "optiday" in low or "rumps" in low:
                item["project"] = "optiday"
            if "sarah@lumen.example" in low and "message sent" in low:
                item["actions"].append({"time": start, "kind": "email",
                                        "text": "Sent email to Sarah Lee about OptiDay beta pricing"})
            if "youtube video" in low:
                item["video_label"] = "waste" if "laugh" in low or "dunks" in low or "shorts" in low \
                    else "informational"
                item["video_reason"] = "fake model verdict"
            if "lumen vc" in low and "meet" in low:
                item["summary"] = "Investor sync with Sarah from Lumen VC"
            out.append(item)
        return out

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def fake_ollama():
    f = FakeOllama()
    yield f
    f.close()
