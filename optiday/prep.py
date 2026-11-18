"""Getting tomorrow ready: meetings with context, emails that need you (with reply drafts),
where you left off on each project, next steps, and agent tasks waiting for your OK.

Drafts are only ever shown to you. OptiDay never sends anything.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

from .rules import fmt_dur, fmt_time

_STOP = {"with", "and", "the", "for", "from", "about", "meeting", "call", "sync", "chat", "weekly", "daily",
         "monthly", "review", "intro", "catch", "catchup", "update", "team", "standup", "stand", "check",
         "your", "our", "this", "that", "zoom", "google", "meet", "invite", "event", "calendar", "1:1"}


def _words(title: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z0-9&'.-]{3,}", title or "") if w.lower() not in _STOP]


def _people_keys(ev: dict, me: str) -> list[str]:
    keys = []
    for a in ev.get("attendees", []) + ([ev["organizer"]] if ev.get("organizer") else []):
        a = a.strip()
        if not a or (me and a.lower() == me.lower()):
            continue
        if "@" in a:
            local = a.split("@")[0]
            first = re.split(r"[._+-]", local)[0]
            if len(first) >= 3:
                keys.append(first)
        else:
            first = a.split()[0]
            if len(first) >= 3:
                keys.append(first)
    return list(dict.fromkeys(keys))[:6]


def meeting_context(ev: dict, actions: list[dict], unit_summaries: list[dict], needs_reply: list[dict],
                    me: str = "") -> list[str]:
    """Lines from today that mention the people or topic of a meeting."""
    keys = _people_keys(ev, me) + _words(ev.get("title", ""))[:4]
    if not keys:
        return []
    pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b", re.I)
    out: list[str] = []
    for r in needs_reply:
        if pat.search(r.get("from_name", "") + " " + r.get("from_addr", "") + " " + r.get("subject", "")):
            out.append(f"Unanswered email from {r['from_name']}: “{r['subject'][:80]}”")
    for a in actions:
        if a["kind"] != "video" and pat.search(a["text"]):
            out.append(f"{fmt_time(a['ts'])} {a['text']}")
    for u in unit_summaries:
        if pat.search(u["text"]):
            out.append(f"{fmt_time(u['start'])} {u['text']}")
    seen, uniq = set(), []
    for line in out:
        k = line.lower()[:60]
        if k not in seen:
            seen.add(k)
            uniq.append(line)
    return uniq[:4]


def left_off(log: list[dict], m: dict, unit_summaries: list[dict], limit: int = 3) -> list[dict]:
    """For your main projects: the last thing you had open, so you can jump straight back in."""
    out = []
    for p in (m.get("by_project") or [])[:6]:
        if p["seconds"] < 600:
            continue
        rows = [r for r in log if r.get("project") == p["name"] and r["app"] != "Away"]
        if not rows:
            continue
        last = rows[-1]
        note = ""
        for u in unit_summaries:
            if u["start"] <= last["end"] and u["end"] >= last["start"] - 60:
                note = u["text"]
        out.append({"project": p["name"], "at": last["end"], "app": last["app"],
                    "where": last["title"][:140], "note": note, "today_s": p["seconds"]})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ drafts --

DRAFT_SYSTEM = """You draft email replies for a busy person to review and send themselves.
About them: {about}
Write each reply in their voice: short, warm, direct, ready to send, no subject line, no signature
placeholder like [Name]. If the email asks for something you can't know (a date, a number, a
decision), write the reply with a clear blank like "___" for them to fill in. Never invent facts.
Reply with JSON only: {{"drafts": [{{"id": "...", "draft": "...", "next_step": "one short line: what they need to do"}}]}}"""


def draft_key(item: dict) -> str:
    base = item.get("msg_id") or f"{item.get('from_addr')}|{item.get('subject')}|{item.get('ts')}"
    return "draft:" + hashlib.sha1(base.encode()).hexdigest()[:20]


def add_drafts(replies: list[dict], store, day: str, cfg, llm=None, limit: int = 5) -> None:
    """Attach cached drafts; ask the model for the missing ones (top `limit` only)."""
    todo = []
    for i, r in enumerate(replies):
        cached = store.get_note(draft_key(r))
        if cached:
            r["draft"], r["next_step"] = cached.get("draft", ""), cached.get("next_step", "")
        elif llm is not None and i < limit:
            todo.append(r)
    if not todo:
        return
    payload = [{"id": f"m{i}", "from": f"{r['from_name']} <{r['from_addr']}>", "subject": r["subject"],
                "received": fmt_time(r["ts"]), "their_message": r["snippet"][:900]} for i, r in enumerate(todo)]
    res = llm.chat_json(DRAFT_SYSTEM.format(about=cfg.profile.get("about_me", "")),
                        json.dumps(payload, ensure_ascii=False))
    items = res.get("drafts") if isinstance(res, dict) else res if isinstance(res, list) else None
    if not items:
        return
    by_id = {str(it.get("id")): it for it in items if isinstance(it, dict)}
    for i, r in enumerate(todo):
        it = by_id.get(f"m{i}")
        if it is None and len(items) == len(todo) and isinstance(items[i], dict):
            it = items[i]
        if not it or not str(it.get("draft", "")).strip():
            continue
        r["draft"] = str(it["draft"]).strip()[:2000]
        r["next_step"] = str(it.get("next_step", "")).strip()[:200]
        store.put_note(draft_key(r), day, {"draft": r["draft"], "next_step": r["next_step"]})


# -------------------------------------------------------------------- prep --

def build(cfg, day: str, m: dict, actions: list[dict], log: list[dict], unit_summaries: list[dict],
          insights: dict, mail: dict | None, cal_next: dict | None, agent_items: list[dict],
          next_steps: list[str] | None = None) -> dict:
    """Plan for the next day. `cal_next` = calendar note for that day; `mail` = email digest note."""
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    me = (cfg.get("email", {}) or {}).get("address", "")
    replies = [dict(r) for r in (mail or {}).get("needs_reply", [])]
    meetings = []
    for ev in (cal_next or {}).get("events", []):
        e = dict(ev)
        e["context"] = meeting_context(ev, actions, unit_summaries, replies, me)
        meetings.append(e)
    timed = [e for e in meetings if not e.get("all_day")]
    busy = sum(max(0.0, e["end"] - e["start"]) for e in timed)
    priority = insights.get("tomorrow") or ""
    if not priority:
        if replies and any(r.get("asks") for r in replies):
            r = next(r for r in replies if r.get("asks"))
            priority = f"Reply to {r['from_name']} about “{r['subject'][:60]}” first."
        elif m.get("by_project"):
            priority = f"Start the day on {m['by_project'][0]['name']} before opening email or chat."
    steps = [s for s in (next_steps or []) if s][:5]
    return {
        "for_day": nxt,
        "from_day": day,
        "priority": priority,
        "meetings": meetings,
        "meeting_time_s": busy,
        "first_meeting": timed[0]["start"] if timed else None,
        "replies": replies,
        "left_off": left_off(log, m, unit_summaries),
        "next_steps": steps,
        "agent_tasks": [{"id": a["id"], "title": a["title"], "status": a["status"]} for a in agent_items],
        "email_connected": mail is not None,
        "email_error": (mail or {}).get("error", ""),
        "calendar_connected": cal_next is not None,
        "calendar_error": "; ".join((cal_next or {}).get("errors", [])),
    }


def brief_line(prep: dict) -> str:
    """One-line morning notification."""
    bits = []
    timed = [e for e in prep.get("meetings", []) if not e.get("all_day")]
    if timed:
        bits.append(f"{len(timed)} meeting{'s' if len(timed) != 1 else ''}"
                    f" (first at {fmt_time(timed[0]['start'])})")
    if prep.get("replies"):
        n = len(prep["replies"])
        bits.append(f"{n} email{'s' if n != 1 else ''} need you")
    waiting = [a for a in prep.get("agent_tasks", []) if a["status"] == "suggested"]
    if waiting:
        bits.append(f"{len(waiting)} task{'s' if len(waiting) != 1 else ''} an agent could do")
    if prep.get("left_off"):
        bits.append(f"pick up {prep['left_off'][0]['project']}")
    head = ", ".join(bits) if bits else "Nothing scheduled"
    return head[0].upper() + head[1:] + "."


def meeting_line(ev: dict) -> str:
    if ev.get("all_day"):
        return f"All day · {ev['title']}"
    return f"{fmt_time(ev['start'])}–{fmt_time(ev['end'])} · {ev['title']} ({fmt_dur(ev['end'] - ev['start'])})"
