"""Spot tedious things you repeat, and offer to hand them to an agent.

Three kinds of repetition are detected from the recording:
* loops: going back and forth between the same 2-3 apps/sites many times while typing
  (copying data between a spreadsheet and a CRM, filling the same form...)
* templated emails: several emails sent with the same subject pattern
* routines: checking the same dashboard/site every day for a while without doing much there

Every suggestion lands in an approval queue. NOTHING runs until you approve it, and you see the
exact instructions the agent will get (you can edit them). This matters: suggestions are built
from what was on your screen, so they must never act on their own.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from .models import Segment
from .rules import fmt_dur, fmt_time, place_name

log = logging.getLogger("optiday.agents")

PREFIX = "automation:"
_NOT_TEDIOUS = {"Coding", "Terminal", "Meetings", "Entertainment", "Games", "Social", "Video", "System"}
_ROUTINE_CATS = {"Finance", "Research", "Planning", "Other", "Browsing", "News"}
SAFETY_PREAMBLE = (
    "Context: OptiDay (my private activity tracker) suggested this task from patterns in my own computer "
    "use, and I reviewed and approved it. Ground rules: do not send emails or messages, make purchases, delete "
    "files, or change account settings unless the task below explicitly says to. If something is unclear or "
    "needs a login you don't have, stop and tell me instead of guessing. When you finish, reply with a short "
    "summary of what you did and anything I still need to do.\n\nTask:\n")


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


# --------------------------------------------------------------- detection --

def _places(segments: list[Segment]) -> list[dict]:
    seq: list[dict] = []
    for s in segments:
        if s.away or s.private or s.seconds < 5:
            continue
        place = place_name(s.domain, s.app, s.url)
        if seq and seq[-1]["place"] == place and s.start - seq[-1]["end"] < 120:
            seq[-1]["end"] = s.end
            seq[-1]["secs"] += s.seconds
            seq[-1]["typing"] += s.typing_s
            if s.title and s.title not in seq[-1]["titles"]:
                seq[-1]["titles"].append(s.title)
            continue
        seq.append({"place": place, "cat": s.category, "start": s.start, "end": s.end, "secs": s.seconds,
                    "typing": s.typing_s, "titles": [s.title] if s.title else []})
    return seq


def find_loops(segments: list[Segment], min_count: int = 5, min_total: float = 600) -> list[dict]:
    seq = _places(segments)
    found: dict[tuple, dict] = {}
    for n in (2, 3):
        counts: dict[tuple, list[int]] = defaultdict(list)
        i = 0
        last_end: dict[tuple, int] = {}
        while i + n <= len(seq):
            key = tuple(x["place"] for x in seq[i:i + n])
            if len(set(key)) == n:
                # canonical rotation so A,B,A,B counts as one loop whichever comes first
                r = min(range(n), key=lambda k: key[k:] + key[:k])
                canon = key[r:] + key[:r]
                if i >= last_end.get(canon, -1):
                    counts[canon].append(i)
                    last_end[canon] = i + n
            i += 1
        for key, starts in counts.items():
            if len(starts) < min_count:
                continue
            windows = [seq[i:i + n] for i in starts]
            total = sum(sum(x["secs"] for x in w) for w in windows)
            typing = sum(sum(x["typing"] for x in w) for w in windows)
            per = sorted(sum(x["secs"] for x in w) for w in windows)
            cats = {x["cat"] for w in windows for x in w}
            if total < min_total or per[len(per) // 2] > 900 or typing < 30:
                continue
            if cats & {"Coding", "Terminal"} or cats <= (_NOT_TEDIOUS | {"Communication", "Email"}):
                continue
            titles = []
            for w in windows:
                for x in w:
                    for t in x["titles"][:1]:
                        if t not in titles:
                            titles.append(t)
            found[key] = {"kind": "loop", "places": list(key), "count": len(starts), "seconds": total,
                          "typing_s": typing, "first": windows[0][0]["start"], "last": windows[-1][-1]["end"],
                          "examples": titles[:6]}
    # a 3-app loop that contains a 2-app loop: keep the bigger one
    out = sorted(found.values(), key=lambda c: -c["seconds"])
    keep: list[dict] = []
    for c in out:
        if any(set(c["places"]) <= set(k["places"]) or set(k["places"]) <= set(c["places"]) for k in keep):
            continue
        keep.append(c)
    return keep[:2]


_SUBJECT = re.compile(r"[“\"](.+?)[”\"]")


def _template(subject: str) -> str:
    s = re.sub(r"^((re|fwd?|aw|sv)\s*:\s*)+", "", subject.strip(), flags=re.I).lower()
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "@", s)
    s = re.sub(r"\d[\d,./-]*", "#", s)
    return re.sub(r"\s+", " ", s).strip()


def find_templated_emails(actions: list[dict], min_count: int = 3) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in actions:
        if a["kind"] != "email" or not a["text"].lower().startswith("sent"):
            continue
        m = _SUBJECT.search(a["text"])
        if not m:
            continue
        t = _template(m.group(1))
        if len(t) >= 4:
            groups[t].append(a)
    out = []
    for t, acts in groups.items():
        if len(acts) >= min_count:
            out.append({"kind": "emails", "template": t, "count": len(acts),
                        "examples": [x["text"] for x in acts[:5]], "first": acts[0]["ts"], "last": acts[-1]["ts"],
                        "seconds": 0})
    return sorted(out, key=lambda c: -c["count"])[:2]


def day_pattern(segments: list[Segment]) -> dict:
    """Per place: time, visits and typing for one day (kept to find daily routines)."""
    pat: dict[str, dict] = {}
    for x in _places(segments):
        p = pat.setdefault(x["place"], {"s": 0.0, "visits": 0, "typing": 0.0, "cat": x["cat"], "titles": []})
        p["s"] += x["secs"]
        p["visits"] += 1
        p["typing"] += x["typing"]
        for t in x["titles"][:2]:
            if t not in p["titles"] and len(p["titles"]) < 4:
                p["titles"].append(t)
    return pat


def find_routines(patterns: dict[str, dict], min_days: int = 4, min_avg: float = 480) -> list[dict]:
    """`patterns`: {day: day_pattern} for the last ~5 recorded days."""
    days = sorted(patterns)[-5:]
    if len(days) < min_days:
        return []
    per_place: dict[str, list[dict]] = defaultdict(list)
    for d in days:
        for place, p in patterns[d].items():
            if p["s"] >= 120:
                per_place[place].append(p)
    out = []
    for place, ps in per_place.items():
        if len(ps) < min_days or ps[0]["cat"] not in _ROUTINE_CATS:
            continue
        avg = sum(p["s"] for p in ps) / len(ps)
        typing = sum(p["typing"] for p in ps) / max(1.0, sum(p["s"] for p in ps))
        if avg < min_avg or typing > 0.2:
            continue
        titles = []
        for p in ps:
            for t in p["titles"]:
                if t not in titles:
                    titles.append(t)
        out.append({"kind": "routine", "places": [place], "days": len(ps), "seconds": avg,
                    "visits": round(sum(p["visits"] for p in ps) / len(ps), 1), "examples": titles[:5]})
    return sorted(out, key=lambda c: -c["seconds"])[:2]


def describe(c: dict) -> tuple[str, str]:
    """(title, why) in plain English for a candidate."""
    if c["kind"] == "loop":
        names = " ↔ ".join(c["places"])
        between = ", ".join(c["places"][:-1]) + " and " + c["places"][-1]
        return (f"Repeated back-and-forth: {names}",
                f"You went back and forth between {between} {c['count']} times "
                f"({fmt_time(c['first'])}–{fmt_time(c['last'])}, {fmt_dur(c['seconds'])} total, "
                f"typing for {fmt_dur(c['typing_s'])}). That usually means copying or entering the same kind of "
                "data by hand.")
    if c["kind"] == "emails":
        return (f"Similar emails: “{c['template'][:60]}”",
                f"You sent {c['count']} emails with the same subject pattern today. "
                "An agent can prepare these for you to review instead of writing each one.")
    names = c["places"][0]
    return (f"Daily check: {names}",
            f"You've spent ~{fmt_dur(c['seconds'])} a day on {names} on {c['days']} of the last 5 days "
            f"(~{c['visits']} visits a day), mostly reading. An agent could pull what you look for into one "
            "short morning digest.")


def template_task(c: dict) -> str:
    title, why = describe(c)
    ex = "\n".join(f"- {e}" for e in c.get("examples", [])[:5])
    return (f"{why}\n\nExamples of what was on screen:\n{ex}\n\n"
            "Figure out what I was doing repeatedly and set up the simplest reliable way to do it for me "
            "(a script, a template, or a step-by-step plan). If you can do part of it directly with the access "
            "you have, do it and show me the result. Explain how to run it again next time.")


AGENT_SYSTEM = """You turn an observed repetitive work pattern into a task for an AI agent (like Claude Code)
that will run on the person's Mac once they approve it.
About the person: {about}
Write instructions the agent can act on without asking follow-ups: what to build or do, inputs it should
look for, the output to produce, and how the person re-runs it. Keep it safe: no sending messages, purchases,
deleting data, or changing accounts. If the pattern is too vague to automate, say what the agent should
investigate and propose.
Reply with JSON only: {{"title": "max 8 words", "task": "the full instructions", "saves": "e.g. ~20 min/day"}}"""


def suggest(store, cfg, day: str, segments: list[Segment], actions: list[dict], llm=None) -> list[dict]:
    """Detect candidates for `day`, add new ones to the approval queue, return the open queue."""
    store.put_note(f"patterns:{day}", day, day_pattern(segments))
    recent = {}
    for d in store.days()[:6]:
        p = store.get_note(f"patterns:{d}")
        if p is not None:
            recent[d] = p
    cands = find_loops(segments) + find_templated_emails(actions) + find_routines(recent)
    for c in cands:
        key = c["kind"] + ":" + ",".join(sorted(c.get("places", []))) + c.get("template", "")
        aid = _id(key)
        existing = store.get_note(PREFIX + aid)
        if existing and existing.get("status") != "suggested":
            continue  # approved, dismissed or already ran: don't nag
        title, why = describe(c)
        item = existing or {"id": aid, "created": time.time(), "status": "suggested", "agent": "",
                            "task": "", "source": "rules", "saves": ""}
        item.update(day=day, kind=c["kind"], title=item.get("title") if existing and existing.get("source") == "ai"
                    else title, why=why, evidence=c, updated=time.time())
        if not item.get("task") or (item["source"] == "rules" and llm is not None):
            if llm is not None:
                res = llm.chat_json(AGENT_SYSTEM.format(about=cfg.profile.get("about_me", "")),
                                    json.dumps({"pattern": why, "examples": c.get("examples", [])},
                                               ensure_ascii=False))
                if isinstance(res, dict) and str(res.get("task", "")).strip():
                    item.update(title=str(res.get("title") or title)[:80], task=str(res["task"]).strip()[:4000],
                                saves=str(res.get("saves", ""))[:40], source="ai")
            if not item.get("task"):
                item["task"] = template_task(c)
        store.put_note(PREFIX + aid, day, item)
    return queue(store)


def queue(store, include_closed: bool = False, max_age_days: int = 7) -> list[dict]:
    items = list(store.notes_with_prefix(PREFIX).values())
    now = time.time()
    out = []
    for it in items:
        if it["status"] == "suggested" and now - it.get("updated", it["created"]) > max_age_days * 86400:
            continue
        if not include_closed and it["status"] in ("dismissed",):
            continue
        if not include_closed and it["status"] in ("done", "failed") and now - it.get("finished", now) > 3 * 86400:
            continue
        out.append(it)
    order = {"running": 0, "suggested": 1, "approved": 2, "failed": 3, "done": 4, "dismissed": 5}
    return sorted(out, key=lambda x: (order.get(x["status"], 9), -x.get("updated", 0)))


# ---------------------------------------------------------------- approval --

def next_run_time(cfg, now: float | None = None) -> float:
    now = now or time.time()
    try:
        h, m = (int(x) for x in str(cfg.get("agents", {}).get("run_at", "09:00")).split(":"))
    except ValueError:
        h, m = 9, 0
    d = dt.datetime.fromtimestamp(now)
    t = d.replace(hour=h, minute=m, second=0, microsecond=0)
    if t.timestamp() <= now:
        t += dt.timedelta(days=1)
    return t.timestamp()


def update(store, cfg, aid: str, action: str, task: str | None = None, agent: str | None = None) -> dict:
    item = store.get_note(PREFIX + aid)
    if item is None:
        raise KeyError(aid)
    if task is not None and item["status"] in ("suggested", "approved", "failed", "done"):
        item["task"] = task.strip()[:8000]
    if agent:
        if agent not in agents_available(cfg):
            raise ValueError(f"unknown agent {agent}")
        item["agent"] = agent
    if action == "approve":
        item.update(status="approved", approved_at=time.time(), run_after=next_run_time(cfg))
    elif action == "run_now":
        item.update(status="approved", approved_at=time.time(), run_after=time.time())
    elif action == "dismiss":
        item.update(status="dismissed")
    elif action == "unapprove":
        item.update(status="suggested")
    elif action != "edit":
        raise ValueError(action)
    item["updated"] = time.time()
    store.put_note(PREFIX + aid, item.get("day", store.today()), item)
    return item


def agents_available(cfg) -> list[str]:
    a = cfg.get("agents", {}) or {}
    return [k for k, v in a.items() if isinstance(v, dict) and v.get("type") in ("command", "webhook")]


def agent_label(cfg, name: str) -> str:
    conf = (cfg.get("agents", {}) or {}).get(name) or {}
    if conf.get("type") == "command":
        return f"{name} (runs: {' '.join(conf.get('command', []))})"
    if conf.get("type") == "webhook":
        return f"{name} (POSTs to {conf.get('url') or 'no URL set'})"
    return name


# ----------------------------------------------------------------- running --

_EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.claude/local", "~/.npm-global/bin",
               "~/.bun/bin", "~/.volta/bin"]


def _env() -> dict:
    env = dict(os.environ)
    extra = [os.path.expanduser(p) for p in _EXTRA_PATH]
    env["PATH"] = os.pathsep.join([p for p in extra if os.path.isdir(p)] + [env.get("PATH", "/usr/bin:/bin")])
    return env


def run_task(cfg, item: dict, run=subprocess.run, urlopen=urllib.request.urlopen) -> dict:
    """Runs one approved task. Returns {"ok", "output"}."""
    name = item.get("agent") or (cfg.get("agents", {}) or {}).get("default", "claude")
    conf = (cfg.get("agents", {}) or {}).get(name)
    if not isinstance(conf, dict):
        return {"ok": False, "output": f"No agent named '{name}' in Settings → [agents]."}
    text = SAFETY_PREAMBLE + item["task"]
    if conf.get("type") == "command":
        cmd = [str(a) for a in conf.get("command", [])]
        if not cmd:
            return {"ok": False, "output": f"[agents.{name}] has no command."}
        uses_arg = any("{task}" in a for a in cmd)
        cmd = [a.replace("{task}", text) for a in cmd]
        workdir = Path(os.path.expanduser(str(conf.get("workdir", "~"))))
        if not workdir.is_dir():
            workdir = Path.home()
        try:
            r = run(cmd, input=None if uses_arg else text, capture_output=True, text=True, cwd=str(workdir),
                    env=_env(), timeout=float(conf.get("timeout_minutes", 30)) * 60)
        except FileNotFoundError:
            return {"ok": False, "output": f"'{cmd[0]}' isn't installed or isn't on PATH. For Claude Code run "
                                           "`npm install -g @anthropic-ai/claude-code`, or change [agents] in Settings."}
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": f"Stopped after {conf.get('timeout_minutes', 30)} minutes."}
        out = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
        return {"ok": r.returncode == 0, "output": out[-8000:] or f"(exit code {r.returncode})"}
    if conf.get("type") == "webhook":
        url = str(conf.get("url", ""))
        if not re.match(r"^https://|^http://(127\.0\.0\.1|localhost)[:/]", url):
            return {"ok": False, "output": "Set [agents.webhook] url to an https:// address (or a localhost one)."}
        body = json.dumps({"id": item["id"], "title": item["title"], "why": item.get("why", ""),
                           "task": text}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                              "User-Agent": "OptiDay"}, method="POST")
        try:
            with urlopen(req, timeout=60) as resp:
                return {"ok": True, "output": resp.read(8000).decode("utf-8", "replace") or "Sent to webhook."}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "output": f"Webhook failed: {e}"}
    return {"ok": False, "output": f"[agents.{name}] needs type = \"command\" or \"webhook\"."}


class Runner:
    """Runs due tasks one at a time in a background thread."""

    def __init__(self, store, cfg, notify=None, run=subprocess.run):
        self.store, self.cfg, self.notify, self._run = store, cfg, notify, run
        self._lock = threading.Lock()

    def due(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [i for i in queue(self.store) if i["status"] == "approved" and i.get("run_after", 0) <= now]

    def start_due(self) -> threading.Thread | None:
        if not self.due() or self._lock.locked():
            return None
        t = threading.Thread(target=self.run_due, name="agents", daemon=True)
        t.start()
        return t

    def run_due(self) -> int:
        if not self._lock.acquire(blocking=False):
            return 0
        n = 0
        try:
            for item in self.due():
                item.update(status="running", started=time.time())
                self.store.put_note(PREFIX + item["id"], item.get("day", self.store.today()), item)
                res = run_task(self.cfg, item, run=self._run)
                item.update(status="done" if res["ok"] else "failed", output=res["output"], finished=time.time(),
                            updated=time.time())
                self.store.put_note(PREFIX + item["id"], item.get("day", self.store.today()), item)
                n += 1
                if self.notify:
                    self.notify("OptiDay agent task " + ("finished" if res["ok"] else "failed"), item["title"])
        finally:
            self._lock.release()
        return n

    def recover(self) -> None:
        """A task marked running when OptiDay restarted didn't finish."""
        for it in queue(self.store):
            if it["status"] == "running":
                it.update(status="failed", output=(it.get("output") or "") + "\nOptiDay restarted while this ran.",
                          finished=time.time())
                self.store.put_note(PREFIX + it["id"], it.get("day", self.store.today()), it)
