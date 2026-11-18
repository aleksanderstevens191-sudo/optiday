"""Render a day report as one self-contained HTML page (no external files,
works offline, light and dark mode)."""
from __future__ import annotations

import datetime as dt
from html import escape

from .rules import fmt_dur, fmt_time

CLS_COLORS = {"productive": "var(--good)", "neutral": "var(--mid)", "distracting": "var(--bad)",
              "away": "var(--away)"}
KIND_LABEL = {"email": "Email", "message": "Message", "meeting": "Meeting", "code": "Code", "document": "Doc",
              "ai": "AI", "purchase": "Purchase", "video": "Video", "other": "Other"}


def _e(x) -> str:
    return escape(str(x if x is not None else ""))


def _pct(part: float, whole: float) -> float:
    return 0 if whole <= 0 else max(0.0, min(100.0, 100 * part / whole))


def _bars(rows: list[tuple[str, float, str]], total: float | None = None, color: str = "var(--accent)",
          limit: int = 12) -> str:
    rows = rows[:limit]
    if not rows:
        return '<p class="muted">Nothing recorded.</p>'
    top = max(r[1] for r in rows) or 1
    out = []
    for name, secs, sub in rows:
        share = f" · {round(_pct(secs, total))}%" if total else ""
        out.append(
            f'<div class="bar"><div class="bar-head"><span class="bar-name">{_e(name)}</span>'
            f'<span class="bar-val">{fmt_dur(secs)}{share}</span></div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{_pct(secs, top):.1f}%;'
            f'background:{color}"></div></div>'
            + (f'<div class="bar-sub">{_e(sub)}</div>' if sub else "") + "</div>")
    return "".join(out)


def _timeline(log: list[dict], m: dict) -> str:
    if not log:
        return ""
    start_h = dt.datetime.fromtimestamp(m["first_ts"]).replace(minute=0, second=0, microsecond=0)
    end_d = dt.datetime.fromtimestamp(m["last_ts"])
    end_h = end_d.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    t0, t1 = start_h.timestamp(), end_h.timestamp()
    span = max(1.0, t1 - t0)
    W, H = 1000, 46
    from .rules import productivity
    rects = []
    for r in log:
        cls = "away" if r["app"] == "Away" else productivity(r["category"])
        x = (r["start"] - t0) / span * W
        w = max(0.6, (r["end"] - r["start"]) / span * W)
        tip = f"{fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['app']}" + (f" · {r['title'][:80]}" if r["title"] else "")
        rects.append(f'<rect x="{x:.1f}" y="6" width="{w:.1f}" height="26" rx="1.5" '
                     f'fill="{CLS_COLORS[cls]}"><title>{_e(tip)}</title></rect>')
    ticks = []
    h = start_h
    while h <= end_h:
        x = (h.timestamp() - t0) / span * W
        ticks.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="2" y2="36" class="tick"/>'
                     f'<text x="{min(max(x, 14), W - 14):.1f}" y="45" class="tick-l" text-anchor="middle">{_e(h.strftime("%-I%p").lower())}</text>')
        h += dt.timedelta(hours=1)
    return (f'<svg viewBox="0 0 {W} {H}" class="timeline" role="img" aria-label="Your day, colored by type of work">'
            + "".join(ticks) + "".join(rects) + "</svg>")


def _hourly(m: dict) -> str:
    rows = m.get("hourly") or []
    if not rows:
        return ""
    W, H, pad = 1000, 170, 22
    n = len(rows)
    bw = W / n
    out = []
    for i, r in enumerate(rows):
        y = H - pad
        for cls in ("productive", "neutral", "distracting", "away"):
            v = r.get(cls, 0)
            if v <= 0:
                continue
            hgt = (v / 3600) * (H - pad - 8)
            y -= hgt
            out.append(f'<rect x="{i * bw + bw * 0.14:.1f}" y="{y:.1f}" width="{bw * 0.72:.1f}" height="{hgt:.1f}" '
                       f'fill="{CLS_COLORS[cls]}"><title>{_e(r["label"])} · {cls}: {fmt_dur(v)}</title></rect>')
        out.append(f'<text x="{i * bw + bw / 2:.1f}" y="{H - 6}" class="tick-l" text-anchor="middle">'
                   f'{_e(r["label"])}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="hourly" role="img" aria-label="Minutes per hour">{"".join(out)}</svg>'


def _legend() -> str:
    items = [("productive", "Productive"), ("neutral", "Neutral (email, chat, meetings)"),
             ("distracting", "Distracting"), ("away", "Away")]
    return '<div class="legend">' + "".join(
        f'<span><i style="background:{CLS_COLORS[k]}"></i>{_e(v)}</span>' for k, v in items) + "</div>"


def _kpi(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return (f'<div class="kpi {tone}"><div class="kpi-label">{_e(label)}</div><div class="kpi-val">{_e(value)}</div>'
            + (f'<div class="kpi-sub">{_e(sub)}</div>' if sub else "") + "</div>")


def _video_rows(videos: list[dict], interactive: bool) -> str:
    if not videos:
        return '<p class="muted">No YouTube today.</p>'
    rows = []
    for v in videos:
        label = v["label"]
        pill = {"informational": ("Learning", "pill-good"), "waste": ("Waste of time", "pill-bad")}.get(
            label, ("Not sure", "pill-mid"))
        src = {"you": "you marked it", "channel": "you marked this channel", "ai": "local AI",
               "learned": "like videos you marked", "rules": "from the title"}.get(v.get("source", ""), "")
        if v.get("channel"):
            src = f'{v["channel"]}{" · " + src if src else ""}'
        buttons = ""
        if interactive:
            buttons = (f'<div class="relabel" data-id="{_e(v["id"])}">'
                       f'<button data-l="informational">Useful</button><button data-l="waste">Waste</button></div>')
        link = v.get("url") or ""
        title = f'<a href="{_e(link)}" target="_blank" rel="noopener">{_e(v["title"])}</a>' if link.startswith(
            "https://") else _e(v["title"])
        rows.append(
            f'<tr><td class="t">{_e(fmt_time(v["first_ts"]))}</td><td>{title}'
            f'<div class="muted small">{_e(v.get("reason", ""))}{" · " + src if src else ""}</div></td>'
            f'<td class="t">{_e(fmt_dur(v["seconds"]))}</td><td><span class="pill {pill[1]}">{pill[0]}</span>'
            f'{buttons}</td></tr>')
    return ('<table class="tbl"><thead><tr><th>Time</th><th>Video</th><th>Watched</th><th>Verdict</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


# ------------------------------------------------------------ v0.3 sections --

def _hour_bar(h: dict) -> str:
    total = 3600.0
    parts = []
    for cls, key in (("productive", "productive_s"), ("neutral", "neutral_s"), ("distracting", "distracting_s"),
                     ("away", "away_s")):
        w = _pct(h.get(key, 0), total)
        if w > 0.5:
            parts.append(f'<i style="width:{w:.1f}%;background:{CLS_COLORS[cls]}" title="{cls} {fmt_dur(h.get(key, 0))}"></i>')
    return f'<div class="hbar">{"".join(parts)}</div>'


def hours_html(hours: list[dict], empty: str = "Nothing recorded yet.") -> str:
    rows = []
    for h in hours or []:
        if h["active_s"] < 60 and h["away_s"] < 600:
            continue
        text = h.get("ai") or h.get("text") or ""
        badge = '<span class="ai-dot" title="Written by your local AI">AI</span>' if h.get("ai") else ""
        rows.append(f'<li><div class="hr-l"><b>{_e(h["label"])}</b><span class="muted small">'
                    f'{_e(fmt_dur(h["active_s"]))} active</span>{_hour_bar(h)}</div>'
                    f'<div class="hr-t">{_e(text)}{badge}</div></li>')
    return f'<ul class="hours">{"".join(rows)}</ul>' if rows else f'<p class="muted">{_e(empty)}</p>'


def _copy_block(text: str, label: str = "Copy") -> str:
    return (f'<div class="draft"><textarea readonly rows="{min(10, max(3, text.count(chr(10)) + 2))}">{_e(text)}'
            f'</textarea><button class="copy" type="button">{_e(label)}</button></div>')


def _meeting_li(ev: dict) -> str:
    when = "All day" if ev.get("all_day") else f'{fmt_time(ev["start"])}–{fmt_time(ev["end"])}'
    who = ev.get("attendees") or []
    extra = []
    if who:
        extra.append(", ".join(who[:4]) + (f" +{len(who) - 4}" if len(who) > 4 else ""))
    if ev.get("location") and not ev.get("meet_url"):
        extra.append(ev["location"][:80])
    link = f' · <a href="{_e(ev["meet_url"])}" target="_blank" rel="noopener">join</a>' \
        if str(ev.get("meet_url", "")).startswith("https://") else ""
    ctx = "".join(f"<li>{_e(c)}</li>" for c in ev.get("context", []))
    return (f'<li><span class="t">{_e(when)}</span><div><b>{_e(ev["title"])}</b>{link}'
            + (f'<div class="muted small">{_e(" · ".join(extra))}</div>' if extra else "")
            + (f'<ul class="ctx">{ctx}</ul>' if ctx else "") + "</div></li>")


def _reply_li(r: dict, interactive: bool) -> str:
    age = r.get("age_hours", 0)
    age_s = f"{round(age)}h ago" if age < 48 else f"{round(age / 24)}d ago"
    head = (f'<b>{_e(r["from_name"])}</b> · {_e(r["subject"][:90])} <span class="muted small">{_e(age_s)}'
            f'{" · asks you something" if r.get("asks") else ""}</span>')
    body = f'<div class="muted small quote">{_e(r.get("snippet", "")[:280])}</div>'
    if r.get("draft"):
        draft = (f'<details><summary>Reply draft{" · " + _e(r["next_step"]) if r.get("next_step") else ""}</summary>'
                 f'{_copy_block(r["draft"], "Copy draft")}</details>')
    else:
        draft = '<div class="muted small">A draft is written by your local AI next time it runs (plugged in).</div>'
    return f"<li><div>{head}{body}{draft}</div></li>"


def plan_html(plan: dict, interactive: bool, today: bool = False) -> str:
    """Meetings, replies, where you left off and next steps (for tomorrow, or for today)."""
    if not plan:
        return '<p class="muted">Nothing prepared yet.</p>'
    out = []
    if plan.get("priority"):
        out.append(f'<p class="tomorrow"><b>{"Most important today" if today else "Most important tomorrow"}:'
                   f'</b> {_e(plan["priority"])}</p>')
    meetings = plan.get("meetings") or []
    if meetings:
        out.append(f'<h3>Meetings</h3><ul class="plain mt">{"".join(_meeting_li(e) for e in meetings)}</ul>')
    elif plan.get("calendar_connected"):
        out.append('<h3>Meetings</h3><p class="muted">No meetings. A good day for deep work.</p>')
    else:
        out.append('<p class="muted small">Connect your calendar to see meetings here: run '
                   '<code>optiday connect-calendar</code>.</p>')
    if plan.get("calendar_error"):
        out.append(f'<p class="warn small">{_e(plan["calendar_error"])}</p>')
    replies = plan.get("replies") or []
    if replies:
        out.append(f'<h3>Emails waiting for you ({len(replies)})</h3>'
                   f'<ul class="plain">{"".join(_reply_li(r, interactive) for r in replies[:8])}</ul>'
                   '<p class="muted small">Drafts are only shown here. OptiDay never sends, deletes or marks email.</p>')
    elif plan.get("email_connected"):
        out.append('<h3>Email</h3><p class="muted">No one is waiting on a reply.</p>')
    else:
        out.append('<p class="muted small">Connect your email to see who is waiting on you, with reply drafts: run '
                   '<code>optiday connect-email</code>.</p>')
    if plan.get("email_error"):
        out.append(f'<p class="warn small">{_e(plan["email_error"])}</p>')
    lo = plan.get("left_off") or []
    if lo:
        items = "".join(
            f'<li><span class="t">{_e(fmt_time(x["at"]))}</span><div><b>{_e(x["project"])}</b> · {_e(x["app"])}: '
            f'{_e(x["where"])}' + (f'<div class="muted small">{_e(x["note"])}</div>' if x.get("note") else "")
            + "</div></li>" for x in lo)
        out.append(f'<h3>Where you left off</h3><ul class="plain">{items}</ul>')
    steps = plan.get("next_steps") or []
    if steps:
        out.append("<h3>Next steps</h3><ol class=\"steps\">" + "".join(f"<li>{_e(x)}</li>" for x in steps) + "</ol>")
    return "".join(out)


_STATUS = {"suggested": ("Waiting for your OK", "pill-mid"), "approved": ("Approved", "pill-good"),
           "running": ("Running…", "pill-good"), "done": ("Done", "pill-good"), "failed": ("Failed", "pill-bad"),
           "dismissed": ("Dismissed", "pill-mid")}


def agents_html(items: list[dict], interactive: bool, agent_names: dict | None = None,
                default_agent: str = "claude") -> str:
    if not items:
        return ('<p class="muted">Nothing repetitive enough to hand off yet. OptiDay watches for things you do '
                'over and over (copying between apps, similar emails, daily checks) and suggests them here.</p>')
    rows = []
    for it in items:
        label, pill = _STATUS.get(it["status"], (it["status"], "pill-mid"))
        when = ""
        if it["status"] == "approved" and it.get("run_after"):
            when = f' · runs {dt.datetime.fromtimestamp(it["run_after"]).strftime("%a %-I:%M %p")}'
        agent = it.get("agent") or default_agent
        agent_line = (agent_names or {}).get(agent, agent)
        task = it.get("task", "")
        if interactive and it["status"] in ("suggested", "approved", "failed", "done"):
            box = (f'<textarea class="task" rows="7" aria-label="Instructions for the agent">{_e(task)}</textarea>')
            btns = []
            if it["status"] == "suggested":
                btns.append('<button data-a="approve" class="primary">Approve (runs at the next scheduled time)</button>')
            if it["status"] in ("suggested", "approved", "failed", "done"):
                btns.append('<button data-a="run_now">Run now</button>')
            if it["status"] == "approved":
                btns.append('<button data-a="unapprove">Cancel</button>')
            if it["status"] in ("suggested", "failed", "done"):
                btns.append('<button data-a="dismiss">Dismiss</button>')
            ctl = f'{box}<div class="agent-btns" data-id="{_e(it["id"])}">{"".join(btns)}</div>'
        else:
            ctl = f'<pre class="task">{_e(task)}</pre>'
        output = ""
        if it.get("output"):
            output = f'<details><summary>Agent output</summary><pre class="task">{_e(it["output"][-4000:])}</pre></details>'
        rows.append(
            f'<li class="agent"><div class="agent-h"><b>{_e(it["title"])}</b><span class="pill {pill}">{_e(label)}'
            f'{_e(when)}</span></div><div class="muted small">{_e(it.get("why", ""))}'
            f'{" · saves " + _e(it["saves"]) if it.get("saves") else ""}</div>'
            f'<details{" open" if it["status"] == "suggested" and interactive else ""}><summary>What the agent will be told'
            f' · {_e(agent_line)}</summary>{ctl}</details>{output}</li>')
    note = ("" if interactive else
            '<p class="muted small">Open this report from the menu bar (Open today\'s report) to approve tasks.</p>')
    return (f'<ul class="plain agents">{"".join(rows)}</ul>{note}'
            '<p class="muted small">Nothing runs until you approve it. Suggestions come from what was on your screen, '
            'so read the instructions before approving.</p>')


def render(report: dict, interactive: bool = False, nav: str = "", agents: list[dict] | None = None,
           agent_names: dict | None = None, default_agent: str = "claude") -> str:
    m = report["metrics"]
    day = dt.date.fromisoformat(report["day"])
    nice_day = day.strftime("%A, %B %-d, %Y")
    ins = report.get("insights", {})
    ai = report.get("ai", {})
    if m.get("empty"):
        body = f'<section class="card"><h2>No activity recorded for {_e(nice_day)}</h2>' \
               '<p class="muted">OptiDay records while the menu bar icon shows ◉. Check that it is running and has ' \
               'Screen Recording permission (menu bar → Run setup check).</p></section>'
        return _page(nice_day, body, nav, interactive, report["day"])

    total = m["active_s"]
    headline = ins.get("headline") or f"{fmt_dur(total)} active, {m['score']}% of it productive"
    longest = max((b["seconds"] for b in m["focus_blocks"]), default=0)
    checks = sum(m.get("checks", {}).values())
    if ai.get("pending"):
        ai_badge = "Built on battery with built-in rules · the AI summary is added when you plug in"
    elif ai.get("used"):
        ai_badge = f'Analyzed on your device by {_e(ai.get("model"))}'
    else:
        ai_badge = "Built-in rules only (local AI was not available)"

    kpis = "".join([
        _kpi("Active", fmt_dur(total), f"{fmt_time(m['first_ts'])} – {fmt_time(m['last_ts'])}"),
        _kpi("Productive", f"{m['score']}%", fmt_dur(m["productive_s"]), "good" if m["score"] >= 60 else ""),
        _kpi("Deep focus", fmt_dur(m["focus_s"]), f"longest {fmt_dur(longest)}" if longest else "no 25m+ blocks"),
        _kpi("Distracted", fmt_dur(m["distracting_s"]), f"{round(_pct(m['distracting_s'], total))}% of day",
             "bad" if m["distracting_s"] >= 1800 else ""),
        _kpi("App switches", str(m["switches"]), f"{m['switches_per_hour']}/hour · {checks} inbox/chat checks"),
    ])

    summary = ""
    if ins.get("summary"):
        summary = f'<p class="lede">{_e(ins["summary"])}</p>'
    tomorrow = f'<p class="tomorrow"><b>Tomorrow:</b> {_e(ins["tomorrow"])}</p>' if ins.get("tomorrow") else ""
    went = "".join(f"<li>{_e(x)}</li>" for x in ins.get("went_well", [])) or "<li class='muted'>—</li>"
    sinks = "".join(f"<li>{_e(x)}</li>" for x in ins.get("time_sinks", [])) or "<li class='muted'>Nothing big.</li>"
    sugg = "".join(f'<li><b>{_e(s["title"])}</b><span>{_e(s.get("detail", ""))}</span></li>'
                   for s in ins.get("suggestions", []))

    projects = _bars([(p["name"], p["seconds"], " · ".join(f"{t['name']} {fmt_dur(t['seconds'])}" for t in p["top"]))
                      for p in m["by_project"]], total)
    if m.get("unassigned_s", 0) > 60:
        projects += f'<p class="muted small">{fmt_dur(m["unassigned_s"])} wasn\'t tied to a project ' \
                    f'(general browsing, email, chat…). Add keywords under [projects] in Settings to sort more.</p>'
    apps = _bars([(a, s, "") for a, s in m["by_app"]], total)
    cats = _bars([(c, s, "") for c, s in m["by_category"]], total, "var(--accent2)")
    sites = _bars([(d, s, "") for d, s in m["by_domain"]], total, "var(--accent2)", limit=10)

    acts = []
    for a in report.get("actions", []):
        k = a.get("kind", "other")
        acts.append(f'<li><span class="t">{_e(fmt_time(a["ts"]))}</span><span class="tag tag-{_e(k)}">'
                    f'{_e(KIND_LABEL.get(k, k))}</span><span>{_e(a["text"])}</span></li>')
    actions = f'<ul class="actions">{"".join(acts)}</ul>' if acts else '<p class="muted">No key actions detected.</p>'

    blocks = "".join(f'<li><span class="t">{_e(fmt_time(b["start"]))}–{_e(fmt_time(b["end"]))}</span>'
                     f'<b>{_e(fmt_dur(b["seconds"]))}</b> on {_e(b["main"])}</li>' for b in m["focus_blocks"]) \
        or "<li class='muted'>No uninterrupted 25-minute stretches today.</li>"
    dis = "".join(f'<li><span class="t">{_e(fmt_time(d["start"]))}</span><b>{_e(fmt_dur(d["seconds"]))}</b> on '
                  f'{_e(", ".join(d["sites"]))}</li>' for d in m["distractions"]) \
        or "<li class='muted'>No distraction stretches over 2 minutes.</li>"

    log_rows = "".join(
        f'<tr><td class="t">{_e(fmt_time(r["start"]))}</td><td class="t">{_e(fmt_dur(r["seconds"]))}</td>'
        f'<td>{_e(r["app"])}</td><td>{_e(r["title"][:140])}{" ✎" if r.get("typing") else ""}</td>'
        f'<td>{_e(r.get("project", ""))}</td></tr>' for r in report.get("log", []))
    summaries = "".join(f'<li><span class="t">{_e(fmt_time(u["start"]))}</span>{_e(u["text"])}</li>'
                        for u in report.get("unit_summaries", []))
    done = "".join(f"<li>{_e(x)}</li>" for x in report.get("done", []))
    plan = report.get("prep") or {}
    nxt = ""
    if plan.get("for_day"):
        nxt = dt.date.fromisoformat(plan["for_day"]).strftime("%A")
    agent_items = agents if agents is not None else [
        dict(a, why="", task="") for a in plan.get("agent_tasks", [])]
    mail = report.get("email") or {}
    mail_line = ""
    if mail.get("connected"):
        mail_line = (f'<p class="muted small">Email: sent {mail.get("sent_count", 0)} · received '
                     f'{mail.get("human_inbox_count") or 0} from people ({mail.get("inbox_count") or 0} total)</p>')

    body = f"""
<header class="hero">
  <div class="brand">OptiDay</div>
  <h1>{_e(nice_day)}</h1>
  <p class="headline">{_e(headline)}</p>
  <p class="muted small">{ai_badge} · nothing left this Mac</p>
</header>
<section class="kpis">{kpis}</section>
<section class="card">
  <h2>Your day in one line</h2>
  {_timeline(report.get("log", []), m)}
  {_legend()}
  {summary}{tomorrow}
</section>
<section class="card"><h2>Hour by hour</h2>{hours_html(report.get("hours", []))}</section>
<section class="grid3">
  <div class="card"><h2>What you got done</h2><ul class="dots">{done or "<li class='muted'>—</li>"}</ul>{mail_line}</div>
  <div class="card"><h2>What went well</h2><ul class="dots">{went}</ul></div>
  <div class="card"><h2>Where time leaked</h2><ul class="dots">{sinks}</ul></div>
</section>
<section class="card"><h2>Ready for {_e(nxt or "tomorrow")}</h2>{plan_html(plan, interactive)}</section>
<section class="card" id="agents"><h2>Hand off to an agent</h2>{agents_html(agent_items, interactive, agent_names, default_agent)}</section>
<section class="card"><h2>How to optimize tomorrow</h2><ol class="tips">{sugg}</ol></section>
<section class="card"><h2>What you did, with times</h2>{actions}</section>
<section class="grid2">
  <div class="card"><h2>Time per project</h2>{projects}</div>
  <div class="card"><h2>Time per app</h2>{apps}</div>
</section>
<section class="card"><h2>YouTube</h2>
  <p class="muted small">Learning {fmt_dur(m["video_s"]["informational"])} · Waste {fmt_dur(m["video_s"]["waste"])}
  · Not sure {fmt_dur(m["video_s"]["unclear"])}{" · click Useful / Waste to make the call, OptiDay remembers it for the video and its channel" if interactive else ""}</p>
  {_video_rows(m["videos"], interactive)}
</section>
<section class="card"><h2>Minutes per hour</h2>{_hourly(m)}{_legend()}</section>
<section class="grid2">
  <div class="card"><h2>Focus blocks</h2><ul class="plain">{blocks}</ul></div>
  <div class="card"><h2>Distraction stretches</h2><ul class="plain">{dis}</ul></div>
</section>
<section class="grid2">
  <div class="card"><h2>Categories</h2>{cats}</div>
  <div class="card"><h2>Top websites</h2>{sites}</div>
</section>
{f'<section class="card"><details><summary>AI notes for each part of the day</summary><ul class="plain">{summaries}</ul></details></section>' if summaries else ""}
<section class="card"><details><summary>Full activity log ({len(report.get("log", []))} entries)</summary>
<table class="tbl log"><thead><tr><th>Start</th><th>For</th><th>App</th><th>Window / tab (✎ = typing)</th><th>Project</th></tr></thead>
<tbody>{log_rows}</tbody></table></details></section>
<footer class="muted small">Generated {_e(dt.datetime.fromtimestamp(report["generated_at"]).strftime("%b %-d, %-I:%M %p"))}
in {report.get("build_seconds", 0)}s. Your data is encrypted on this Mac and is never uploaded.</footer>
"""
    return _page(nice_day, body, nav, interactive, report["day"])


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171c;--muted:#667085;--line:#e6e8ec;--accent:#4f46e5;--accent2:#0ea5e9;
--good:#16a34a;--mid:#94a3b8;--bad:#e5484d;--away:#e2e5ea;--pill-good:#dcfce7;--pill-bad:#fee2e2;--pill-mid:#eef0f3}
@media (prefers-color-scheme:dark){:root{--bg:#0d0f13;--card:#161a20;--fg:#e8eaee;--muted:#98a2b3;--line:#262b33;
--accent:#818cf8;--accent2:#38bdf8;--good:#22c55e;--mid:#64748b;--bad:#f87171;--away:#2a2f37;--pill-good:#14361f;
--pill-bad:#3f1a1c;--pill-mid:#262b33}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,sans-serif}
main{max-width:1060px;margin:0 auto;padding:28px 16px 60px}
a{color:var(--accent)}.muted{color:var(--muted)}.small{font-size:13px}
.nav{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px;font-size:14px}
.nav a{text-decoration:none}.nav .sp{flex:1}
.hero{padding:8px 0 18px}.brand{font-weight:700;letter-spacing:.02em;color:var(--accent);font-size:13px;text-transform:uppercase}
h1{font-size:30px;margin:4px 0 2px;letter-spacing:-.02em}.headline{font-size:19px;margin:2px 0 4px}
h2{font-size:15px;margin:0 0 12px;letter-spacing:.01em}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 18px;margin:0 0 14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid2 .card{margin:0 0 14px}
.grid3{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:14px}.grid3 .card{margin:0 0 14px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.kpi-label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.kpi-val{font-size:24px;font-weight:650;letter-spacing:-.02em}.kpi-sub{font-size:12px;color:var(--muted)}
.kpi.good .kpi-val{color:var(--good)}.kpi.bad .kpi-val{color:var(--bad)}
.lede{font-size:16px;margin:14px 0 4px}.tomorrow{margin:8px 0 0;padding:10px 12px;border-radius:10px;
background:var(--pill-mid)}
.timeline,.hourly{width:100%;height:auto;display:block}.tick{stroke:var(--line)}.tick-l{fill:var(--muted);font-size:11px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.bar{margin:0 0 10px}.bar-head{display:flex;justify-content:space-between;gap:10px;font-size:14px}
.bar-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-val{color:var(--muted);white-space:nowrap;
font-variant-numeric:tabular-nums}.bar-track{height:7px;background:var(--pill-mid);border-radius:4px;overflow:hidden;
margin-top:3px}.bar-fill{height:100%;border-radius:4px}.bar-sub{font-size:12px;color:var(--muted);margin-top:2px}
ul.dots{margin:0;padding-left:18px}ul.dots li{margin:0 0 6px}
ol.tips{margin:0;padding-left:20px}ol.tips li{margin:0 0 10px}ol.tips b{display:block}ol.tips span{color:var(--muted)}
ul.actions,ul.plain{list-style:none;margin:0;padding:0}
ul.actions li,ul.plain li{display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line)}
ul.actions li:last-child,ul.plain li:last-child{border-bottom:0}
.t{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:13px;min-width:62px}
.tag{font-size:11px;padding:1px 7px;border-radius:999px;background:var(--pill-mid);white-space:nowrap}
.tag-email{background:#dbeafe;color:#1e40af}.tag-meeting{background:#ede9fe;color:#5b21b6}
.tag-code{background:#dcfce7;color:#166534}.tag-message{background:#e0f2fe;color:#075985}
.tag-ai{background:#fae8ff;color:#86198f}.tag-video{background:#fee2e2;color:#991b1b}.tag-document{background:#fef9c3;color:#854d0e}
@media (prefers-color-scheme:dark){.tag{filter:brightness(.85) saturate(1.1)}}
.tbl{width:100%;border-collapse:collapse;font-size:14px}.tbl th{text-align:left;font-size:12px;color:var(--muted);
font-weight:500;border-bottom:1px solid var(--line);padding:6px 8px}.tbl td{border-bottom:1px solid var(--line);
padding:8px;vertical-align:top}.tbl.log td{font-size:13px;padding:5px 8px}
.pill{font-size:12px;padding:2px 9px;border-radius:999px;white-space:nowrap}.pill-good{background:var(--pill-good)}
.pill-bad{background:var(--pill-bad)}.pill-mid{background:var(--pill-mid)}
.relabel{display:flex;gap:4px;margin-top:6px}.relabel button{font:inherit;font-size:12px;border:1px solid var(--line);
background:var(--card);color:var(--fg);border-radius:8px;padding:2px 8px;cursor:pointer}
.relabel button:hover{border-color:var(--accent)}
details summary{cursor:pointer;font-weight:600}footer{margin-top:24px;text-align:center}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:16px 0 6px}
ul.hours{list-style:none;margin:0;padding:0}ul.hours li{display:grid;grid-template-columns:120px 1fr;gap:12px;
padding:9px 0;border-bottom:1px solid var(--line)}ul.hours li:last-child{border-bottom:0}
.hr-l{display:flex;flex-direction:column;gap:2px}.hr-t{font-size:14px}
.hbar{display:flex;height:5px;border-radius:3px;overflow:hidden;background:var(--pill-mid);margin-top:4px}
.hbar i{display:block;height:100%}
.ai-dot{font-size:10px;margin-left:6px;padding:0 5px;border-radius:5px;background:#fae8ff;color:#86198f;vertical-align:1px}
ul.ctx{margin:4px 0 0;padding-left:16px;font-size:13px;color:var(--muted)}
ul.plain ul.ctx li{display:list-item;border:0;padding:1px 0}
.quote{margin:3px 0 4px;border-left:2px solid var(--line);padding-left:8px}
.draft{display:flex;flex-direction:column;gap:6px;margin-top:6px}
textarea{width:100%;font:inherit;font-size:14px;color:var(--fg);background:var(--bg);border:1px solid var(--line);
border-radius:10px;padding:8px 10px;resize:vertical}
pre.task{white-space:pre-wrap;font:13px/1.45 ui-monospace,Menlo,monospace;background:var(--bg);border:1px solid var(--line);
border-radius:10px;padding:8px 10px;margin:6px 0;max-height:320px;overflow:auto}
button.copy,.agent-btns button{align-self:flex-start;font:inherit;font-size:13px;border:1px solid var(--line);
background:var(--card);color:var(--fg);border-radius:8px;padding:4px 10px;cursor:pointer}
.agent-btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.agent-btns button.primary{background:var(--accent);
color:#fff;border-color:var(--accent)}button:disabled{opacity:.6;cursor:default}
li.agent{display:block!important}.agent-h{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
ol.steps{margin:0;padding-left:20px}ol.steps li{margin:0 0 6px}
ul.plain.mt li{align-items:flex-start}.warn{color:var(--bad)}code{font-size:12.5px;background:var(--pill-mid);
padding:1px 5px;border-radius:5px}
.brief{font-size:18px;margin:4px 0 0}
@media (max-width:760px){.grid2,.grid3{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}
ul.hours li{grid-template-columns:1fr;gap:4px}
h1{font-size:24px}.tbl.log th:nth-child(5),.tbl.log td:nth-child(5){display:none}}
"""

JS = """
document.querySelectorAll('button.copy').forEach(b=>b.addEventListener('click',async()=>{
  const t=b.parentElement.querySelector('textarea');
  try{await navigator.clipboard.writeText(t.value)}catch(_){t.select();document.execCommand('copy')}
  const o=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=o,1500);
}));
document.querySelectorAll('.agent-btns button').forEach(b=>b.addEventListener('click',async()=>{
  const box=b.closest('.agent-btns');const ta=box.parentElement.querySelector('textarea.task');
  box.querySelectorAll('button').forEach(x=>x.disabled=true);
  const r=await fetch('/api/automation',{method:'POST',headers:{'Content-Type':'application/json','X-OptiDay':'1'},
    body:JSON.stringify({id:box.dataset.id,action:b.dataset.a,task:ta?ta.value:null})});
  if(r.ok){location.reload()}else{box.querySelectorAll('button').forEach(x=>x.disabled=false);alert('Could not save')}
}));
document.querySelectorAll('.relabel button').forEach(b=>b.addEventListener('click',async e=>{
  const box=e.target.closest('.relabel');b.disabled=true;
  const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json','X-OptiDay':'1'},
    body:JSON.stringify({video_id:box.dataset.id,label:b.dataset.l,day:document.body.dataset.day})});
  if(r.ok){location.reload()}else{b.disabled=false;alert('Could not save')}
}));
"""


def _page(title: str, body: str, nav: str, interactive: bool, day: str = "") -> str:
    day_attr = f' data-day="{_e(day)}"' if day else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>OptiDay · {_e(title)}</title><style>{CSS}</style></head>'
            f'<body{day_attr}><main>{nav}{body}</main>'
            + (f"<script>{JS}</script>" if interactive else "") + "</body></html>")


def render_index(days: list[dict]) -> str:
    rows = []
    for d in days:
        rows.append(f'<tr><td><a href="/day/{_e(d["day"])}">{_e(d["label"])}</a></td><td>{_e(d.get("active", ""))}</td>'
                    f'<td>{_e(d.get("score", ""))}</td><td class="muted">{_e(d.get("headline", ""))}</td></tr>')
    body = ('<header class="hero"><div class="brand">OptiDay</div><h1>Your days</h1>'
            '<p class="muted">Everything here is stored encrypted on this Mac.</p></header>'
            '<section class="card"><table class="tbl"><thead><tr><th>Day</th><th>Active</th><th>Productive</th>'
            f'<th>Headline</th></tr></thead><tbody>{"".join(rows)}</tbody></table></section>')
    return _page("Your days", body, "", False)


def render_plan(plan: dict, nav: str = "", interactive: bool = True) -> str:
    """The Today page: your day, ready."""
    from .prep import brief_line
    day = dt.date.fromisoformat(plan["for_day"])
    hours = plan.get("hours") or []
    so_far = sum(h["active_s"] for h in hours)
    body = f"""
<header class="hero">
  <div class="brand">OptiDay · Today</div>
  <h1>{_e(day.strftime("%A, %B %-d"))}</h1>
  <p class="brief">{_e(brief_line(plan))}</p>
</header>
<section class="card"><h2>Your day, ready</h2>{plan_html(plan, interactive, today=True)}</section>
<section class="card" id="agents"><h2>Hand off to an agent</h2>{agents_html(plan.get("agents", []), interactive,
                                                                      plan.get("agent_names"), plan.get("default_agent", "claude"))}</section>
<section class="card"><h2>Hour by hour so far{f" · {fmt_dur(so_far)} active" if so_far else ""}</h2>
{hours_html(hours, "Nothing recorded yet today.")}
<p class="small"><a href="/day/{_e(plan["for_day"])}">Open today's full report →</a></p></section>
<footer class="muted small">Everything on this page was prepared on this Mac. Drafts are never sent for you.</footer>
"""
    return _page("Today", body, nav, interactive, plan["for_day"])
