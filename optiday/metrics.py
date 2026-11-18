"""Numbers for the report: time per app/project/category, focus blocks,
context switching, distractions, videos, and rule-based coaching tips."""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from .models import Segment
from .rules import DISTRACTING, fmt_dur, fmt_time, productivity


def _hour(ts: float) -> int:
    return dt.datetime.fromtimestamp(ts).hour


def _split_by_hour(s: Segment):
    """Yield (hour, seconds) for a segment that may cross hour boundaries."""
    t = s.start
    while t < s.end:
        d = dt.datetime.fromtimestamp(t)
        nxt = (d.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)).timestamp()
        e = min(s.end, nxt)
        yield d.hour, e - t
        t = e


def hour_label(h: int) -> str:
    return dt.time(h).strftime("%-I%p").lower()


def compute(segments: list[Segment], cfg) -> dict:
    active = [s for s in segments if not s.away]
    total = sum(s.seconds for s in active)
    away = sum(s.seconds for s in segments if s.away)
    out: dict = {"active_s": total, "away_s": away}
    if not active:
        out.update(empty=True)
        return out
    out["first_ts"], out["last_ts"] = active[0].start, active[-1].end

    by_app, by_cat, by_proj, by_domain = Counter(), Counter(), Counter(), Counter()
    proj_apps: dict[str, Counter] = defaultdict(Counter)
    proj_typing: Counter = Counter()
    cls_s: Counter = Counter()
    hourly: dict[int, Counter] = defaultdict(Counter)
    for s in segments:
        cls = "away" if s.away else productivity(s.category)
        for h, sec in _split_by_hour(s):
            hourly[h][cls] += sec
        if s.away:
            continue
        cls_s[cls] += s.seconds
        by_app[s.app] += s.seconds
        by_cat[s.category] += s.seconds
        if s.domain:
            by_domain[s.domain] += s.seconds
        p = s.project or ""
        if p:
            by_proj[p] += s.seconds
            proj_apps[p][s.domain or s.app] += s.seconds
            proj_typing[p] += s.typing_s

    out["by_app"] = by_app.most_common()
    out["by_category"] = by_cat.most_common()
    out["by_domain"] = by_domain.most_common(25)
    out["by_project"] = [
        {"name": p, "seconds": sec, "typing_s": proj_typing[p],
         "top": [{"name": a, "seconds": x} for a, x in proj_apps[p].most_common(4)]}
        for p, sec in by_proj.most_common()
    ]
    out["unassigned_s"] = total - sum(by_proj.values())
    out["productive_s"] = cls_s["productive"]
    out["neutral_s"] = cls_s["neutral"]
    out["distracting_s"] = cls_s["distracting"]
    out["score"] = round(100 * cls_s["productive"] / total) if total else 0
    out["typing_s"] = sum(s.typing_s for s in active)
    hours = sorted(hourly)
    out["hourly"] = [{"hour": h, "label": hour_label(h), **{k: hourly[h][k] for k in
                                                            ("productive", "neutral", "distracting", "away")}}
                     for h in range(hours[0], hours[-1] + 1)] if hours else []

    # --- context switching (ignore sub-5s flicks) ---
    meaningful = [s for s in active if s.seconds >= 5 and not s.private]
    switches_by_hour: Counter = Counter()
    switches = 0
    prev = None
    for s in meaningful:
        k = (s.app, s.domain)
        if prev is not None and k != prev:
            switches += 1
            switches_by_hour[_hour(s.start)] += 1
        prev = k
    out["switches"] = switches
    active_hours = max(1.0, total / 3600)
    out["switches_per_hour"] = round(switches / active_hours, 1)
    if switches_by_hour:
        h, n = switches_by_hour.most_common(1)[0]
        out["worst_switch_hour"] = {"hour": h, "label": f"{hour_label(h)}–{hour_label((h + 1) % 24)}", "count": n}
    out["switches_by_hour"] = {hour_label(h): n for h, n in sorted(switches_by_hour.items())}

    # --- how often you checked email / chat ---
    checks = Counter()
    last_comm_end: dict[str, float] = {}
    for s in meaningful:
        if s.category in ("Email", "Communication"):
            k = s.category
            if s.start - last_comm_end.get(k, -1e9) > 60:
                checks[k] += 1
            last_comm_end[k] = s.end
    out["checks"] = dict(checks)

    # --- focus blocks ---
    out["focus_blocks"] = focus_blocks(segments, int(cfg.report.get("focus_block_minutes", 25)))
    out["focus_s"] = sum(b["seconds"] for b in out["focus_blocks"])

    # --- distraction stretches ---
    out["distractions"] = distraction_stretches(active)

    # --- best hour ---
    if out["hourly"]:
        best = max(out["hourly"], key=lambda r: r["productive"])
        if best["productive"] > 0:
            out["best_hour"] = {"hour": best["hour"], "label": best["label"], "productive_s": best["productive"]}

    # --- videos ---
    vids: dict[str, dict] = {}
    for s in active:
        if not s.video:
            continue
        v = vids.setdefault(s.video["id"], {**s.video, "seconds": 0.0, "first_ts": s.start, "url": s.url})
        v["seconds"] += s.seconds
        v.update({k: s.video[k] for k in ("label", "reason", "source")})
    out["videos"] = sorted(vids.values(), key=lambda v: v["first_ts"])
    out["video_s"] = {lab: sum(v["seconds"] for v in vids.values() if v["label"] == lab)
                      for lab in ("informational", "waste", "unclear")}
    return out


def focus_blocks(segments: list[Segment], min_minutes: int) -> list[dict]:
    blocks, cur = [], None

    def close():
        nonlocal cur
        if cur and cur["seconds"] >= min_minutes * 60:
            top = cur["what"].most_common(1)[0][0]
            blocks.append({"start": cur["start"], "end": cur["end"], "seconds": cur["seconds"], "main": top,
                           "interruptions_s": cur["interrupt"]})
        cur = None

    for s in segments:
        cls = "away" if s.away else productivity(s.category)
        if cls == "productive":
            if cur is None:
                cur = {"start": s.start, "end": s.end, "seconds": 0.0, "interrupt": 0.0, "what": Counter()}
            cur["end"] = s.end
            cur["seconds"] += s.seconds
            cur["what"][s.project or s.category] += s.seconds
        elif cur is not None:
            cur["interrupt"] += s.seconds
            budget = max(180.0, 0.1 * cur["seconds"])
            if cur["interrupt"] > budget or (cls == "distracting" and s.seconds > 120) or s.seconds > 300:
                close()
    close()
    return blocks


def distraction_stretches(active: list[Segment], gap: float = 60) -> list[dict]:
    out, cur = [], None
    for s in active:
        if s.category not in DISTRACTING:
            continue
        what = s.domain or s.app
        if cur and s.start - cur["end"] <= gap:
            cur["end"] = s.end
            cur["seconds"] += s.seconds
            cur["what"][what] += s.seconds
        else:
            if cur:
                out.append(cur)
            cur = {"start": s.start, "end": s.end, "seconds": s.seconds, "what": Counter({what: s.seconds})}
    if cur:
        out.append(cur)
    res = []
    for d in out:
        if d["seconds"] >= 120:
            res.append({"start": d["start"], "end": d["end"], "seconds": d["seconds"],
                        "main": d["what"].most_common(1)[0][0],
                        "sites": [w for w, _ in d["what"].most_common(3)]})
    return res


def rule_insights(m: dict) -> dict:
    """Plain, specific coaching that works without any AI."""
    tips, wins, sinks = [], [], []
    if m.get("empty"):
        return {"suggestions": [], "went_well": [], "time_sinks": []}
    total = m["active_s"]

    blocks = m.get("focus_blocks", [])
    longest = max(blocks, key=lambda b: b["seconds"]) if blocks else None
    if longest:
        wins.append(f"Longest focus block: {fmt_dur(longest['seconds'])} on {longest['main']} starting "
                    f"{fmt_time(longest['start'])}.")
    best = m.get("best_hour")
    if best and best["productive_s"] >= 1800:
        wins.append(f"Your most productive hour was {best['label']} ({fmt_dur(best['productive_s'])} of real work).")
    if m.get("by_project"):
        p = m["by_project"][0]
        wins.append(f"Most time went to {p['name']} ({fmt_dur(p['seconds'])}).")

    w = m.get("worst_switch_hour")
    if w and w["count"] >= 25:
        tips.append({"title": "Cut the app-hopping",
                     "detail": f"Between {w['label']} you switched apps {w['count']} times. Next time, pick one task, "
                               f"close chat and email, and set a 45-minute timer before switching."})
    checks = m.get("checks", {})
    n_checks = sum(checks.values())
    if n_checks >= 15 and total > 0:
        every = int(total / 60 / max(1, n_checks))
        tips.append({"title": "Batch email and messages",
                     "detail": f"You opened email/chat {n_checks} times today (about every {every} min). "
                               f"Try checking only at set times, e.g. 9am, 12pm, 3pm and 5pm."})
    dis = m.get("distracting_s", 0)
    if dis >= 20 * 60:
        stretches = sorted(m.get("distractions", []), key=lambda d: -d["seconds"])
        top = stretches[0] if stretches else None
        detail = f"Distracting sites/apps took {fmt_dur(dis)}"
        if top:
            detail += (f", the biggest stretch being {fmt_dur(top['seconds'])} on {top['main']} at "
                       f"{fmt_time(top['start'])}")
        detail += ". Block those sites during your work hours (Screen Time or a Focus mode works)."
        tips.append({"title": "Plug the biggest time leak", "detail": detail})
        sinks.append(f"Distractions: {fmt_dur(dis)} ({', '.join(sorted({d['main'] for d in stretches})[:3])}).")
    waste = [v for v in m.get("videos", []) if v["label"] == "waste"]
    waste_s = sum(v["seconds"] for v in waste)
    if waste_s >= 10 * 60:
        big = max(waste, key=lambda v: v["seconds"])
        sinks.append(f"{fmt_dur(waste_s)} of low-value YouTube across {len(waste)} video(s); biggest was "
                     f"“{big['title'][:70]}” ({fmt_dur(big['seconds'])}).")
        tips.append({"title": "Save videos for later",
                     "detail": "When a non-work video tempts you, add it to Watch Later and watch it after your "
                               "report comes in."})
    if total >= 3600 and (not longest or longest["seconds"] < 45 * 60):
        when = best["label"] if best else "the morning"
        tips.append({"title": "Book one deep-work block",
                     "detail": f"Your longest uninterrupted stretch was "
                               f"{fmt_dur(longest['seconds']) if longest else 'under 25 min'}. Block 90 minutes "
                               f"around {when} tomorrow for your top project, with notifications off."})
    meetings = dict(m.get("by_category", [])).get("Meetings", 0)
    if meetings >= 2 * 3600:
        sinks.append(f"Meetings took {fmt_dur(meetings)}.")
        tips.append({"title": "Guard your maker time",
                     "detail": f"{fmt_dur(meetings)} of meetings. Group them into one part of the day."})
    small = [p for p in m.get("by_project", []) if p["seconds"] < 20 * 60]
    if len(m.get("by_project", [])) >= 5 and len(small) >= 3:
        tips.append({"title": "Fewer projects per day",
                     "detail": f"You touched {len(m['by_project'])} projects, {len(small)} of them for under 20 "
                               f"minutes. Pick one or two for tomorrow and leave the rest."})
    if not tips:
        tips.append({"title": "Keep the streak",
                     "detail": "Solid day. Repeat what worked: same start time and the same focus blocks."})
    return {"suggestions": tips[:5], "went_well": wins[:3], "time_sinks": sinks[:3]}
