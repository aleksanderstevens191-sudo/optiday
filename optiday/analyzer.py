"""Build a day's report: segments -> rules -> local AI annotations -> metrics ->
narrative. AI results are cached per chunk, so hourly background runs make the
evening report fast and re-running never repeats work."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field

from . import activity, automations, email_source, metrics, prep, rules, summaries
from .llm import LLM
from .models import Segment
from .rules import fmt_dur, fmt_time
from .storage import Store
from .videos import VideoJudge

log = logging.getLogger("optiday.analyzer")
REPORT_VERSION = 2


@dataclass
class Unit:
    """A chunk of related segments sent to the AI together."""
    segs: list[Segment] = field(default_factory=list)
    uid: str = ""

    @property
    def start(self) -> float:
        return self.segs[0].start

    @property
    def end(self) -> float:
        return self.segs[-1].end

    @property
    def key(self) -> str:
        h = hashlib.sha1("|".join(f"{s.key}:{len(s.ocr)}:{s.end:.0f}" for s in self.segs).encode())
        return "u:" + h.hexdigest()[:24]


def build_units(segments: list[Segment], max_span: float = 900, gap: float = 30) -> list[Unit]:
    units: list[Unit] = []
    cur: Unit | None = None
    for s in segments:
        if s.away or s.private:
            cur = None
            continue
        ident = (s.app, s.domain, s.project, (s.video or {}).get("id"))
        if cur and ident == (cur.segs[-1].app, cur.segs[-1].domain, cur.segs[-1].project,
                             (cur.segs[-1].video or {}).get("id")) \
                and s.start - cur.end <= gap and s.end - cur.start <= max_span:
            cur.segs.append(s)
        else:
            cur = Unit(segs=[s])
            units.append(cur)
    for i, u in enumerate(units):
        u.uid = f"u{i}"
    return units


def _screen_excerpt(u: Unit, seen: set[str], limit: int = 900) -> str:
    """Only text that newly appeared on screen in this chunk (what you read/typed)."""
    out, size = [], 0
    for s in u.segs:
        for _ts, text in s.ocr:
            for ln in (text or "").splitlines():
                ln = ln.strip()
                if len(ln) < 3 or ln in seen:
                    continue
                seen.add(ln)
                if size + len(ln) > limit:
                    return "\n".join(out)
                out.append(ln)
                size += len(ln) + 1
    return "\n".join(out)


def _describe(u: Unit, seen: set[str]) -> str:
    secs = sum(s.seconds for s in u.segs)
    titles = []
    for s in u.segs:
        if s.title and s.title not in titles:
            titles.append(s.title)
    first = u.segs[0]
    lines = [f"[{u.uid}] {fmt_time(u.start)}–{fmt_time(u.end)} ({fmt_dur(secs)}) app={first.app}"]
    if first.domain:
        lines.append(f"site={first.domain}")
    if titles:
        lines.append("window titles: " + " | ".join(t[:120] for t in titles[:4]))
    typing = sum(s.typing_s for s in u.segs)
    if typing:
        lines.append(f"typing for ~{fmt_dur(typing)}")
    if first.project:
        lines.append(f"detected project: {first.project}")
    if first.video:
        lines.append(f"YouTube video: “{first.video['title']}”")
    ex = _screen_excerpt(u, seen)
    if ex:
        lines.append("new text on screen:\n" + ex)
    return "\n".join(lines)


UNIT_SYSTEM = """You turn one person's computer activity log into an accurate, specific diary.
Each chunk lists the time, app, website, window titles, whether they were typing, and text that appeared on screen (from OCR, so it can be noisy).

About the person: {about}
Their goals: {goals}
Known projects: {projects}

For EVERY chunk return an object with:
- "id": the chunk id
- "project": which project this work belongs to. Reuse a known project name when it fits; else a short new name if the work clearly belongs to a specific project; else "".
- "summary": one short sentence of what they actually did, concrete and specific (who, what file, what topic). No guessing beyond the evidence.
- "actions": notable things they DID (not just viewed), each {{"time": "h:mmam/pm", "kind": one of email|message|meeting|code|document|ai|purchase|other, "text": short past-tense description}}. Examples: "Sent email to Sarah Lee about the Q3 invoice", "Asked Claude how to fix the login bug", "Merged PR #42 in optiday". Only include an action when the screen text or titles show it happened. Use [] if none.
- "video_label" (only for YouTube chunks): "informational" if it helps their work, goals or learning; "waste" if it is entertainment with no real value to them; "unclear" if you can't tell.
- "video_reason" (only for YouTube chunks): a few words why.

Reply with JSON only: {{"chunks": [ ... ]}}"""


NARRATIVE_SYSTEM = """You are a sharp, friendly productivity coach writing someone's end-of-day review from their real computer activity.
About them: {about}
Their goals: {goals}

Use ONLY the data given. Be specific: name projects, sites, videos, times and minutes. Write in second person, plain English, no fluff, no moralizing.
Reply with JSON only:
{{"headline": "max 12 words capturing the day",
  "summary": "3-4 sentences: what you got done, where the time went, how focused you were",
  "went_well": ["up to 3 short bullets"],
  "time_sinks": ["up to 3 short bullets with minutes"],
  "suggestions": [{{"title": "short imperative", "detail": "1-2 sentences, concrete, tied to a time or habit seen today"}}],
  "tomorrow": "one sentence: the single most important thing to do differently tomorrow",
  "done": ["up to 8 concrete things they got done today, past tense, most important first"],
  "next_steps": ["up to 5 concrete next steps for tomorrow, based on unfinished work seen today"]}}
Give 3 to 5 suggestions."""


def _parse_clock(s: str, near: float) -> float:
    m = re.search(r"(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?", s or "", re.I)
    if not m:
        return near
    h, mi = int(m.group(1)), int(m.group(2))
    ampm = (m.group(3) or "").lower().replace(".", "")
    base = dt.datetime.fromtimestamp(near)
    cands = []
    hours = [h % 12 + (12 if ampm == "pm" else 0)] if ampm else [h % 24, (h % 12) + 12]
    for hh in hours:
        for day_shift in (-1, 0, 1):
            try:
                c = (base.replace(hour=hh, minute=mi, second=0, microsecond=0)
                     + dt.timedelta(days=day_shift)).timestamp()
            except ValueError:
                continue
            cands.append(c)
    best = min(cands, key=lambda c: abs(c - near)) if cands else near
    return best if abs(best - near) < 3 * 3600 else near


def annotate_units(units: list[Unit], store: Store, day: str, llm: LLM, cfg, known_projects: list[str],
                   only_finished_before: float | None = None, progress=None, batch_chars: int = 5000,
                   batch_max: int = 6) -> dict[str, dict]:
    """Ask the local model about chunks it hasn't seen yet. Returns {unit.key: note}."""
    notes: dict[str, dict] = {}
    todo: list[Unit] = []
    for u in units:
        n = store.get_note(u.key)
        if n is not None:
            notes[u.key] = n
            continue
        if only_finished_before and u.end > only_finished_before:
            continue
        secs = sum(s.seconds for s in u.segs)
        has_text = any(s.ocr for s in u.segs)
        if secs < 30 and not (has_text and any(s.typing_s for s in u.segs)):
            continue
        todo.append(u)
    if not todo:
        return notes
    system = UNIT_SYSTEM.format(
        about=cfg.profile.get("about_me", ""), goals="; ".join(cfg.profile.get("goals", [])),
        projects=", ".join(known_projects) or "(none yet)")
    seen: set[str] = set()
    batches, cur, size = [], [], 0
    for u in todo:
        d = _describe(u, seen)
        if cur and (size + len(d) > batch_chars or len(cur) >= batch_max):
            batches.append(cur)
            cur, size = [], 0
        cur.append((u, d))
        size += len(d)
    if cur:
        batches.append(cur)
    for bi, batch in enumerate(batches):
        if progress:
            progress(f"AI is reading your day: part {bi + 1} of {len(batches)}")
        prompt = "Activity chunks:\n\n" + "\n\n".join(d for _, d in batch)
        res = llm.chat_json(system, prompt)
        items = []
        if isinstance(res, dict):
            items = res.get("chunks") or res.get("items") or res.get("units") or []
        elif isinstance(res, list):
            items = res
        by_id = {str(i.get("id", "")).strip("[]"): i for i in items if isinstance(i, dict)}
        if res is None:
            continue  # AI unavailable/failed: retry next time
        for u, _ in batch:
            item = by_id.get(u.uid) or {}
            note = _clean_note(item, u)
            store.put_note(u.key, day, note)
            notes[u.key] = note
    return notes


_KINDS = {"email", "message", "meeting", "code", "document", "ai", "purchase", "other", "video"}


def _clean_note(item: dict, u: Unit) -> dict:
    acts = []
    for a in item.get("actions") or []:
        if not isinstance(a, dict) or not str(a.get("text", "")).strip():
            continue
        ts = _parse_clock(str(a.get("time", "")), u.start)
        ts = min(max(ts, u.start - 60), u.end + 60)
        kind = str(a.get("kind", "other")).lower()
        acts.append({"ts": ts, "kind": kind if kind in _KINDS else "other",
                     "text": str(a["text"]).strip()[:200], "source": "ai"})
    label = str(item.get("video_label", "")).lower()
    return {
        "project": str(item.get("project") or "").strip()[:60],
        "summary": str(item.get("summary") or "").strip()[:300],
        "actions": acts,
        "video_label": label if label in ("informational", "waste", "unclear") else "",
        "video_reason": str(item.get("video_reason") or "").strip()[:160],
    }


def _merge_actions(rule_acts: list[dict], ai_acts: list[dict]) -> list[dict]:
    out = list(rule_acts)
    for a in ai_acts:
        dup = None
        for b in out:
            if b["kind"] == a["kind"] and abs(b["ts"] - a["ts"]) <= 300 and a["kind"] in ("email", "meeting",
                                                                                           "message", "ai"):
                dup = b
                break
            if b["text"].lower() == a["text"].lower():
                dup = b
                break
        if dup is None:
            out.append(a)
        elif dup["source"] == "rule" and a["kind"] in ("email", "message"):
            # AI usually knows who/what; keep its wording but the rule's exact time
            dup.update(text=a["text"], source="ai+rule")
    out.sort(key=lambda x: x["ts"])
    return out


def _prepare(store: Store, cfg, day: str, llm: LLM | None, progress=None) -> dict:
    """Segments with projects/videos resolved, AI chunk notes (new ones only if llm is given),
    and the merged list of things you did."""
    snaps = store.snapshots(day)
    interval = float(cfg.rec["interval_seconds"])
    segments = activity.build_segments(snaps, interval)
    overrides = store.overrides("video")
    judge = VideoJudge(store, cfg)
    discovered = activity.enrich(segments, cfg, overrides, judge=judge, day=day)
    known_projects = list(cfg.projects)
    for name in discovered:  # configured spelling wins over auto-detected spelling
        if rules._norm(name) not in {rules._norm(k) for k in known_projects}:
            known_projects.append(name)

    rule_acts = rules.rule_actions(segments)
    units = build_units(segments)
    ai_ok = False
    notes: dict[str, dict] = {}
    if llm is not None:
        ok, msg = llm.status()
        if ok:
            ai_ok = True
            finished = time.time() - 300 if day == store.today() else None
            notes = annotate_units(units, store, day, llm, cfg, known_projects, finished, progress)
        else:
            log.info("AI unavailable: %s", msg)
    if not ai_ok:
        # No model call now (e.g. on battery), but reuse anything it already worked out earlier.
        for u in units:
            n = store.get_note(u.key)
            if n is not None:
                notes[u.key] = n

    ai_acts: list[dict] = []
    unit_summaries = []
    for u in units:
        n = notes.get(u.key)
        if not n:
            continue
        ai_acts.extend(n.get("actions", []))
        if n.get("summary"):
            unit_summaries.append({"start": u.start, "end": u.end, "text": n["summary"],
                                   "app": u.segs[0].domain or u.segs[0].app})
        for s in u.segs:
            if n.get("project") and s.project_source not in ("config", "rule"):
                s.project, s.project_source = n["project"], "ai"
            if s.video and s.video.get("source") in ("rules", "unsure", "learned") \
                    and n.get("video_label") in ("informational", "waste"):
                activity.apply_video_label(s, n["video_label"], n.get("video_reason") or "AI judgment", "ai")
    # canonicalize AI project names against known names (case/punctuation)
    canon = {rules._norm(p): p for p in reversed(known_projects)}
    for s in segments:
        if s.project:
            s.project = canon.get(rules._norm(s.project), s.project)

    actions = _merge_actions(rule_acts, ai_acts)
    mail = store.get_note(f"email:{day}")
    if mail:
        actions = merge_mail_actions(actions, email_source.sent_actions(mail))
    return {"segments": segments, "notes": notes, "ai_ok": ai_ok, "actions": actions,
            "unit_summaries": unit_summaries, "mail": mail}


def merge_mail_actions(actions: list[dict], mail_acts: list[dict], window: float = 600) -> list[dict]:
    """Emails seen in your Sent folder are exact; drop screen-guessed email entries near them."""
    if not mail_acts:
        return actions
    kept = [a for a in actions if not (a["kind"] == "email" and a.get("source") != "mail"
                                       and any(abs(a["ts"] - b["ts"]) <= window for b in mail_acts))]
    return sorted(kept + mail_acts, key=lambda a: a["ts"])


def hours_for(store: Store, cfg, day: str, llm: LLM | None = None) -> list[dict]:
    """Hour-by-hour summaries for a day so far (AI text for finished hours when llm is given)."""
    prep_ = _prepare(store, cfg, day, llm)
    hours = summaries.hourly(prep_["segments"], prep_["actions"], prep_["unit_summaries"], now=time.time())
    summaries.add_ai_text(hours, store, day, llm if prep_["ai_ok"] else None)
    return hours


def analyze_day(store: Store, cfg, day: str, llm: LLM | None = None, narrative: bool = True,
                progress=None, background: bool = False) -> dict:
    t0 = time.time()
    p = _prepare(store, cfg, day, llm, progress)
    segments, ai_ok, actions = p["segments"], p["ai_ok"], p["actions"]
    unit_summaries, notes, mail = p["unit_summaries"], p["notes"], p["mail"]
    now = time.time()
    hours = summaries.hourly(segments, actions, unit_summaries, now=now)
    if background:
        summaries.add_ai_text(hours, store, day, llm if ai_ok else None)
        return {"hours": hours}

    m = metrics.compute(segments, cfg)
    for v in m.get("videos", []):
        if v["seconds"] >= 30:
            label = {"informational": "informational", "waste": "waste of time"}.get(v["label"], "unclear")
            actions.append({"ts": v["first_ts"], "kind": "video", "source": v["source"],
                            "text": f"Watched “{v['title'][:90]}” on YouTube ({fmt_dur(v['seconds'])}) — {label}",
                            "video_id": v["id"]})
    actions.sort(key=lambda a: a["ts"])
    for h in hours:  # videos now have their final verdicts
        h["done"] = [{"ts": a["ts"], "kind": a["kind"], "text": a["text"]} for a in actions
                     if h["start"] <= a["ts"] < h["end"]][:8]
        h["text"] = summaries.rule_text(h)
    if progress and ai_ok:
        progress("AI is summarizing each hour")
    summaries.add_ai_text(hours, store, day, llm if ai_ok else None)

    insights = metrics.rule_insights(m)
    story = None
    if narrative and ai_ok and not m.get("empty"):
        if progress:
            progress("AI is writing your summary")
        story = _narrative(llm, cfg, day, m, actions, insights, hours)
    merged = _merge_story(insights, story)
    done = merged.pop("done", []) or summaries.rule_done(m, actions)
    next_steps = merged.pop("next_steps", [])
    log_rows = _detail_log(segments)

    agent_items: list[dict] = []
    if not m.get("empty"):
        try:
            agent_items = automations.suggest(store, cfg, day, segments, actions, llm if ai_ok else None)
        except Exception:  # noqa: BLE001 - suggestions must never break the report
            log.exception("agent suggestions failed")
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    if mail is not None:
        mail = dict(mail)
        mail["needs_reply"] = [dict(r) for r in mail.get("needs_reply", [])]
        if ai_ok and narrative:
            if progress:
                progress("AI is drafting replies")
            prep.add_drafts(mail["needs_reply"], store, day, cfg, llm)
        else:
            prep.add_drafts(mail["needs_reply"], store, day, cfg, None)
    plan = prep.build(cfg, day, m, actions, log_rows, unit_summaries, merged, mail,
                      store.get_note(f"calendar:{nxt}"), [a for a in agent_items if a["status"] == "suggested"],
                      next_steps)
    report = {
        "version": REPORT_VERSION,
        "day": day,
        "generated_at": time.time(),
        "ai": {"used": bool(notes), "narrative": story is not None, "provider": getattr(llm, "name", "none"),
               "model": getattr(llm, "model", "") or cfg.ai.get("model", ""), "pending": False},
        "metrics": m,
        "actions": actions,
        "insights": merged,
        "done": done,
        "hours": hours,
        "prep": plan,
        "email": {k: (mail or {}).get(k) for k in ("inbox_count", "human_inbox_count", "error", "fetched_at")}
                 | {"connected": mail is not None, "sent_count": len((mail or {}).get("sent", []))},
        "log": log_rows,
        "unit_summaries": unit_summaries,
        "build_seconds": round(time.time() - t0, 1),
    }
    store.put_note(f"report:{day}", day, report)
    store.put_note(f"prep:{nxt}", day, plan)
    return report


def _merge_story(rule: dict, story: dict | None) -> dict:
    out = {"headline": "", "summary": "", "tomorrow": "", **rule, "source": "rules"}
    if not isinstance(story, dict):
        return out
    def strs(x, n):
        return [str(i).strip() for i in (x or []) if str(i).strip()][:n] if isinstance(x, list) else []
    sugg = []
    for s in story.get("suggestions") or []:
        if isinstance(s, dict) and s.get("title"):
            sugg.append({"title": str(s["title"])[:80], "detail": str(s.get("detail", ""))[:400]})
        elif isinstance(s, str) and s.strip():
            sugg.append({"title": s.strip()[:80], "detail": ""})
    out.update(
        done=strs(story.get("done"), 8),
        next_steps=strs(story.get("next_steps"), 5),
        headline=str(story.get("headline", ""))[:120],
        summary=str(story.get("summary", ""))[:1200],
        tomorrow=str(story.get("tomorrow", ""))[:300],
        went_well=strs(story.get("went_well"), 3) or rule["went_well"],
        time_sinks=strs(story.get("time_sinks"), 3) or rule["time_sinks"],
        suggestions=sugg[:5] or rule["suggestions"],
        source="ai",
    )
    return out


def _narrative(llm: LLM, cfg, day: str, m: dict, actions: list[dict], insights: dict,
               hours: list[dict] | None = None) -> dict | None:
    def mins(s):
        return round(s / 60)
    data = {
        "date": day,
        "active_minutes": mins(m["active_s"]),
        "productive_pct": m["score"],
        "start": fmt_time(m["first_ts"]), "end": fmt_time(m["last_ts"]),
        "projects": [{"name": p["name"], "minutes": mins(p["seconds"])} for p in m["by_project"][:8]],
        "apps": [{"name": a, "minutes": mins(s)} for a, s in m["by_app"][:8]],
        "categories": [{"name": c, "minutes": mins(s)} for c, s in m["by_category"][:10]],
        "focus_blocks": [{"start": fmt_time(b["start"]), "minutes": mins(b["seconds"]), "on": b["main"]}
                         for b in m["focus_blocks"]],
        "distractions": [{"start": fmt_time(d["start"]), "minutes": mins(d["seconds"]), "on": d["main"]}
                         for d in m["distractions"][:8]],
        "app_switches": m["switches"], "busiest_switching_hour": m.get("worst_switch_hour"),
        "email_chat_checks": m.get("checks", {}),
        "videos": [{"title": v["title"][:90], "minutes": mins(v["seconds"]), "label": v["label"]}
                   for v in m["videos"]],
        "things_done": [f"{fmt_time(a['ts'])} {a['text']}" for a in actions[:45]],
        "rule_based_observations": [s["title"] + ": " + s["detail"] for s in insights["suggestions"]],
        "hour_by_hour": [f"{h['label']}: {h['ai'] or h['text']}" for h in (hours or []) if h["active_s"] >= 120],
    }
    system = NARRATIVE_SYSTEM.format(about=cfg.profile.get("about_me", ""),
                                     goals="; ".join(cfg.profile.get("goals", [])))
    return llm.chat_json(system, json.dumps(data, ensure_ascii=False))


def _detail_log(segments: list[Segment]) -> list[dict]:
    """Merged, human-readable timeline (stretches of 20s+)."""
    rows: list[dict] = []
    for s in segments:
        if s.seconds < 20 and not (rows and rows[-1]["app"] == s.app and rows[-1]["title"] == s.title):
            continue
        title = "(private)" if s.private else ("Away" if s.away else s.title)
        if rows and rows[-1]["app"] == s.app and rows[-1]["title"] == title and s.start - rows[-1]["end"] < 60:
            rows[-1]["end"] = s.end
            rows[-1]["seconds"] += s.seconds
            continue
        rows.append({"start": s.start, "end": s.end, "seconds": s.seconds, "app": s.app if not s.away else "Away",
                     "title": title, "domain": s.domain, "category": s.category, "project": s.project,
                     "typing": s.typing_s >= 5})
    return rows


def load_or_build(store: Store, cfg, day: str, llm: LLM | None, rebuild: bool = False, progress=None) -> dict:
    if not rebuild:
        cached = store.get_note(f"report:{day}")
        if cached and cached.get("version") == REPORT_VERSION:
            return cached
    return analyze_day(store, cfg, day, llm, progress=progress)
