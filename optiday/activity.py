"""Turn raw recorder ticks into continuous segments, then enrich them with
category, project and video info."""
from __future__ import annotations

import datetime as dt
from urllib.parse import parse_qs, urlparse

from . import rules
from .models import Segment, Snapshot


def _norm_url(url: str) -> str:
    if not url:
        return ""
    try:
        u = urlparse(url)
    except ValueError:
        return url
    base = f"{u.scheme}://{u.netloc}{u.path}"
    if (u.hostname or "").endswith("youtube.com") and u.path == "/watch":
        v = (parse_qs(u.query).get("v") or [""])[0]
        return f"{base}?v={v}"
    return base


def build_segments(snaps: list[Snapshot], interval: float, max_gap: float | None = None) -> list[Segment]:
    """Each tick accounts for the time until the next tick (capped, so sleep/shutdown
    gaps don't count). Consecutive ticks in the same window merge into one segment."""
    if not snaps:
        return []
    max_gap = max_gap or interval * 3
    # Checks are spaced adaptively (5-90s), so each tick carries how long it was meant to cover.
    segs: list[Segment] = []
    cur: Segment | None = None
    cur_key = None
    for i, s in enumerate(snaps):
        nxt = snaps[i + 1].ts if i + 1 < len(snaps) else s.ts + interval
        cap = s.span * 1.5 + 2 if s.span else max_gap
        end = min(nxt, s.ts + cap)
        title = rules.clean_title(s.title)
        key = (s.app, title, _norm_url(s.url), s.away, s.private)
        contiguous = cur is not None and abs(s.ts - cur.end) < 1.0
        if cur is not None and key == cur_key and contiguous:
            cur.end = end
        else:
            cur = Segment(start=s.ts, end=end, app=s.app, bundle_id=s.bundle_id, title=title, url=s.url,
                          away=s.away, private=s.private)
            segs.append(cur)
            cur_key = key
        if s.typing:
            cur.typing_s += end - s.ts
        if s.ocr:
            cur.ocr.append((s.ts, s.ocr))
    return [x for x in segs if x.seconds > 0]


def enrich(segments: list[Segment], cfg, video_overrides: dict[str, str] | None = None,
           judge=None, day: str = "") -> list[str]:
    """Fill in domain/category/project/video. Returns auto-discovered project names.
    `judge` (a videos.VideoJudge) decides learning / waste / not sure for YouTube videos."""
    discovered: list[str] = []
    for s in segments:
        s.domain = rules.domain_of(s.url)
        if s.away:
            s.category = "Away"
            continue
        s.category = rules.categorize(s.app, s.bundle_id, s.url, cfg.categories)
        if s.private or rules.is_optiday_page(s.url, s.title):
            if not s.private:
                s.category = "Other"  # looking at OptiDay itself isn't coding on OptiDay
            continue
        hint = rules.project_from_window(s.app, s.bundle_id, s.title, s.url)
        if hint:
            s.project, s.project_source = hint, "rule"
            if hint not in discovered:
                discovered.append(hint)
    # Configured keywords beat auto-detection; discovered names spread to docs, tabs, etc.
    for s in segments:
        if s.away or s.private or rules.is_optiday_page(s.url, s.title):
            continue
        text = f"{s.title} {s.url}"
        if s.category in ("Coding", "Terminal"):
            text += f" {s.app}"
        cfg_hit = rules.match_known_project(text, cfg.projects, [])
        if cfg_hit:
            s.project, s.project_source = cfg_hit, "config"
        elif not s.project:
            hit = rules.match_known_project(text, {}, discovered)
            if hit:
                s.project, s.project_source = hit, "rule"
    # unify casing of discovered names with configured names
    canon = {rules._norm(n): n for n in cfg.projects}
    for s in segments:
        if s.project and rules._norm(s.project) in canon:
            s.project = canon[rules._norm(s.project)]
    overrides = video_overrides or {}
    for s in segments:
        if s.away or s.private:
            continue
        v = rules.youtube_video(s.url, s.title, s.app)
        if v:
            if judge is not None:
                judge.remember(v, day or dt.date.fromtimestamp(s.start).isoformat())
                j = judge.judge(v)
                v.update(label=j["label"], reason=j["reason"], source=j["source"])
                if j["channel"]:
                    v["channel"] = j["channel"]
            else:
                label, reason = rules.classify_video_heuristic(v, cfg.profile.get("useful_video_topics"))
                v.update(label=label, reason=reason, source="rules")
                if v["id"] in overrides:
                    v.update(label=overrides[v["id"]], reason="You marked this", source="you")
            s.video = v
            s.category = _video_category(v)
    return discovered


def _video_category(v: dict) -> str:
    return {"informational": "Learning", "waste": "Entertainment"}.get(v.get("label", ""), "Video")


def apply_video_label(s: Segment, label: str, reason: str, source: str) -> None:
    if s.video is None:
        return
    s.video.update(label=label, reason=reason, source=source)
    s.category = _video_category(s.video)
