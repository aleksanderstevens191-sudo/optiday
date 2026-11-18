"""Hour-by-hour summaries and the "what you got done" list.

Every hour gets a plain-English line built from the recording itself (works offline, on
battery, with no AI). When the local AI is available it rewrites each finished hour into a
richer one or two sentences, using the notes it already made for that part of the day.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter, defaultdict

from .models import Segment
from .rules import fmt_dur, fmt_time, place_name, productivity

_KIND_VERB = {"email": ("sent", "email", "emails"), "message": ("sent", "message thread", "message threads"),
              "meeting": ("had", "meeting", "meetings"), "ai": ("used", "AI chat", "AI chats"),
              "code": ("did", "code task", "code tasks"), "document": ("worked on", "doc", "docs")}


def hour_span_label(h: int) -> str:
    a = dt.time(h).strftime("%-I%p").lower()
    b = dt.time((h + 1) % 24).strftime("%-I%p").lower()
    return f"{a}–{b}"


def _hour_start(ts: float) -> float:
    return dt.datetime.fromtimestamp(ts).replace(minute=0, second=0, microsecond=0).timestamp()


def _place(s: Segment) -> str:
    return place_name(s.domain, s.app, s.url)


def _clip(s: Segment, a: float, b: float) -> float:
    return max(0.0, min(s.end, b) - max(s.start, a))


def hourly(segments: list[Segment], actions: list[dict], unit_summaries: list[dict] | None = None,
           now: float | None = None) -> list[dict]:
    """One entry per clock hour that has any recorded time, oldest first."""
    if not segments:
        return []
    now = now or segments[-1].end
    starts = sorted({_hour_start(t) for s in segments for t in (s.start, max(s.start, s.end - 0.001))})
    # fill hours fully inside long segments too
    hours: list[float] = []
    h = starts[0]
    while h <= starts[-1]:
        hours.append(h)
        h = (dt.datetime.fromtimestamp(h) + dt.timedelta(hours=1)).timestamp()
    out = []
    for h0 in hours:
        h1 = (dt.datetime.fromtimestamp(h0) + dt.timedelta(hours=1)).timestamp()
        segs = [s for s in segments if s.end > h0 and s.start < h1]
        if not segs:
            continue
        cls: Counter = Counter()
        by_proj: Counter = Counter()
        by_place: Counter = Counter()
        proj_places: dict[str, Counter] = defaultdict(Counter)
        dist_place: Counter = Counter()
        typing = 0.0
        for s in segs:
            sec = _clip(s, h0, h1)
            if s.away:
                cls["away"] += sec
                continue
            c = productivity(s.category)
            cls[c] += sec
            place = "(private)" if s.private else _place(s)
            by_place[place] += sec
            if s.project:
                by_proj[s.project] += sec
                proj_places[s.project][place] += sec
            if c == "distracting":
                dist_place[place] += sec
            if sec > 0 and s.seconds > 0:
                typing += s.typing_s * sec / s.seconds
        active = cls["productive"] + cls["neutral"] + cls["distracting"]
        done = [a for a in actions if h0 <= a["ts"] < h1]
        notes = [u["text"] for u in (unit_summaries or []) if u["start"] < h1 and u["end"] > h0
                 and _clip_span(u, h0, h1) >= 120]
        entry = {
            "start": h0, "end": h1, "hour": dt.datetime.fromtimestamp(h0).hour,
            "label": hour_span_label(dt.datetime.fromtimestamp(h0).hour),
            "active_s": active, "away_s": cls["away"], "productive_s": cls["productive"],
            "neutral_s": cls["neutral"], "distracting_s": cls["distracting"], "typing_s": typing,
            "projects": [{"name": p, "seconds": x,
                          "where": [w for w, _ in proj_places[p].most_common(3)]} for p, x in by_proj.most_common(3)],
            "places": [{"name": p, "seconds": x} for p, x in by_place.most_common(4)],
            "distracted_on": [{"name": p, "seconds": x} for p, x in dist_place.most_common(2)],
            "done": [{"ts": a["ts"], "kind": a["kind"], "text": a["text"]} for a in done[:8]],
            "notes": notes[:6],
            "finished": h1 <= now,
        }
        entry["text"] = rule_text(entry)
        entry["ai"] = ""
        out.append(entry)
    return out


def _clip_span(u: dict, a: float, b: float) -> float:
    return max(0.0, min(u["end"], b) - max(u["start"], a))


def rule_text(e: dict) -> str:
    active, away = e["active_s"], e["away_s"]
    if active < 60:
        return "Away from the computer." if away else "Barely used the computer."
    parts = []
    if e["projects"] and e["projects"][0]["seconds"] >= max(300, active * 0.3):
        p = e["projects"][0]
        where = ", ".join(p["where"][:2])
        parts.append(f"{fmt_dur(p['seconds'])} on {p['name']}" + (f" ({where})" if where else ""))
        rest = [x for x in e["places"] if x["name"] not in p["where"]][:2]
    else:
        rest = e["places"][:3]
    rest = [x for x in rest if x["seconds"] >= 120]
    if rest:
        joined = ", ".join(f"{fmt_dur(x['seconds'])} in {x['name']}" for x in rest)
        parts.append(("also " if parts else "") + joined)
    text = "; ".join(parts) if parts else f"{fmt_dur(active)} across a few apps"
    text = text + "."
    picked = [a for a in e["done"] if a["kind"] != "video"][:2]
    if picked:
        text += " " + " ".join(a["text"].rstrip(".") + "." for a in picked)
        extra = len([a for a in e["done"] if a["kind"] != "video"]) - len(picked)
        if extra > 0:
            text += f" (+{extra} more)"
    if e["distracting_s"] >= 300 and e["distracted_on"]:
        text += f" {fmt_dur(e['distracting_s'])} distracted ({e['distracted_on'][0]['name']})."
    if away >= 900:
        text += f" Away {fmt_dur(away)}."
    return text


# --------------------------------------------------------------- AI layer --

HOURS_SYSTEM = """You write a short hour-by-hour log of someone's workday from their computer activity.
For each hour you get: time spent per project/app, things they did, and notes about each part of the hour.
Write 1-2 plain sentences per hour in second person ("You ..."): what they actually worked on and got done,
naming projects, people, files, topics. Mention distractions only if they were significant. No fluff.
Reply with JSON only: {"hours": [{"hour": "9am–10am", "summary": "..."}]}"""


def _hour_input(e: dict) -> dict:
    return {
        "hour": e["label"],
        "active_minutes": round(e["active_s"] / 60),
        "projects": [f"{p['name']} {round(p['seconds'] / 60)}m" for p in e["projects"]],
        "apps_and_sites": [f"{p['name']} {round(p['seconds'] / 60)}m" for p in e["places"]],
        "distracted_minutes": round(e["distracting_s"] / 60),
        "did": [f"{fmt_time(a['ts'])} {a['text']}" for a in e["done"]],
        "notes": e["notes"],
    }


def hour_key(day: str, e: dict) -> str:
    blob = json.dumps(_hour_input(e), sort_keys=True)
    return f"h:{day}:{e['hour']}:" + hashlib.sha1(blob.encode()).hexdigest()[:16]


def add_ai_text(hours: list[dict], store, day: str, llm=None) -> int:
    """Fill e["ai"] from cache, and ask the model about finished hours it hasn't summarized.
    Returns how many hours were newly summarized."""
    todo = []
    for e in hours:
        if e["active_s"] < 120:
            continue
        cached = store.get_note(hour_key(day, e))
        if cached:
            e["ai"] = cached.get("text", "")
        elif llm is not None and e["finished"]:
            todo.append(e)
    if not todo:
        return 0
    done = 0
    for i in range(0, len(todo), 8):
        batch = todo[i:i + 8]
        res = llm.chat_json(HOURS_SYSTEM, json.dumps([_hour_input(e) for e in batch], ensure_ascii=False))
        items = res.get("hours") if isinstance(res, dict) else res if isinstance(res, list) else None
        if not items:
            continue
        by_label = {}
        for it in items:
            if isinstance(it, dict) and it.get("summary"):
                by_label[_norm_label(str(it.get("hour", "")))] = str(it["summary"]).strip()[:400]
        for j, e in enumerate(batch):
            text = by_label.get(_norm_label(e["label"]))
            if text is None and len(items) == len(batch) and isinstance(items[j], dict):
                text = str(items[j].get("summary") or "").strip()[:400] or None
            if text:
                e["ai"] = text
                store.put_note(hour_key(day, e), day, {"text": text})
                done += 1
    return done


def _norm_label(s: str) -> str:
    return re.sub(r"[^0-9apm]", "", s.lower())


# ------------------------------------------------------- what you got done --

def rule_done(m: dict, actions: list[dict], limit: int = 8) -> list[str]:
    """Accomplishments from the recording alone."""
    out: list[str] = []
    for p in (m.get("by_project") or [])[:3]:
        if p["seconds"] >= 900:
            where = ", ".join(t["name"] for t in p["top"][:2])
            out.append(f"Worked {fmt_dur(p['seconds'])} on {p['name']}" + (f" ({where})" if where else ""))
    strong = [a for a in actions if a["kind"] in ("code", "document") or a.get("source") == "mail"
              or (a["kind"] == "email" and "sent" in a["text"].lower())]
    for a in strong:
        if len(out) >= limit - 2:
            break
        if a["text"] not in out:
            out.append(a["text"])
    counts = Counter(a["kind"] for a in actions)
    tail = []
    for kind in ("email", "meeting", "message", "ai"):
        n = counts.get(kind, 0)
        if n:
            verb, one, many = _KIND_VERB[kind]
            tail.append(f"{verb} {n} {one if n == 1 else many}")
    if tail:
        s = ", ".join(tail)
        out.append(s[0].upper() + s[1:])
    return out[:limit]
