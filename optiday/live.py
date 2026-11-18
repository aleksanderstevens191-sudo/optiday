"""Live state for the side-monitor view.

The recorder hands every check to `LiveState.ingest`, which updates running totals in memory
(no database reads, no AI), so the live page can update the moment you switch apps at almost
no cost. The page long-polls `/api/live`: a request only returns when something changed (or
after 25 seconds), so nothing spins while you're away.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from collections import Counter, deque

from . import activity, rules, videos
from .models import Snapshot
from .rules import fmt_dur, place_name, productivity

log = logging.getLogger("optiday.live")

BUCKET = 300           # timeline resolution: 5 minutes
FLOW_GRACE = 90        # a quick check of chat/email shorter than this doesn't break your flow
FLOW_MIN = 600         # productive this long without a break = "in flow"
STALE = 150            # no check for this long = you're away / the Mac is asleep


def _live_cfg(cfg) -> dict:
    d = {"focus_goal_hours": 4.0, "distraction_budget_minutes": 30, "drift_alert_seconds": 60}
    d.update(cfg.get("live", {}) or {})
    return d


class LiveState:
    def __init__(self, cfg, store, clock=time.time, notify=None, paused=None):
        self.cfg = cfg
        self.store = store
        self.clock = clock
        self.notify = notify
        self.paused = paused or (lambda: False)
        self.cond = threading.Condition()
        self.version = 0
        self.attached = False          # True once a recorder feeds us
        self._seeded_at = 0.0
        self._extras: dict = {}
        self._extras_at = -1e9
        self.judge = videos.VideoJudge(store, cfg, clock=clock)
        self.focus: dict | None = None
        self.last_focus: dict | None = None
        self._reset(store.today())
        self._load_focus()

    # ------------------------------------------------------------- state --
    def _reset(self, day: str) -> None:
        self.day = day
        self.day_start, self.day_end = self.store.day_bounds(day)
        self.cls: Counter = Counter()
        self.by_project: Counter = Counter()
        self.by_place: Counter = Counter()
        self.place_split: dict[str, Counter] = {}   # place -> seconds per class (YouTube: learning/unsure/waste)
        self.videos: dict[str, dict] = {}           # video id -> title, verdict, seconds, time per bucket
        self.buckets: dict[int, Counter] = {}
        self.switches = 0
        self.recent_switches: deque = deque()
        self.recent: deque = deque(maxlen=8)   # (ts, place, project, cls) each time you moved on
        self.discovered: list[str] = []
        self.current: dict | None = None
        self.current_since = 0.0
        self.last_seen = 0.0
        self._prev: tuple[float, float, dict] | None = None   # (ts, cap, info)
        self.flow_start: float | None = None
        self.flow_last: float = 0.0
        self.focus_s = 0.0
        self.longest_flow = 0.0
        self.drift_since: float | None = None
        self.last_productive_project = ""
        self.first_ts: float | None = None

    def _bump(self) -> None:
        self.version += 1
        self.cond.notify_all()

    # -------------------------------------------------------- classifying --
    def _project(self, app: str, bundle: str, title: str, url: str, category: str) -> str:
        hint = rules.project_from_window(app, bundle, title, url)
        if hint and hint not in self.discovered:
            self.discovered.append(hint)
        text = f"{title} {url}" + (f" {app}" if category in ("Coding", "Terminal") else "")
        hit = rules.match_known_project(text, self.cfg.projects, [])
        if not hit:
            hit = hint or rules.match_known_project(text, {}, self.discovered)
        canon = {rules._norm(n): n for n in self.cfg.projects}
        return canon.get(rules._norm(hit), hit) if hit else ""

    def _info(self, app: str, bundle: str, title: str, url: str, away: bool, private: bool) -> dict:
        title = rules.clean_title(title)
        info = {"app": app, "title": "", "domain": "", "place": app, "category": "Away", "cls": "away",
                "project": "", "video": None, "private": private, "away": away}
        if away:
            info["place"] = "Away"
            return info
        info["domain"] = rules.domain_of(url)
        info["category"] = rules.categorize(app, bundle, url, self.cfg.categories)
        if private:
            info.update(place="(private)", cls=productivity(info["category"]))
            return info
        info["title"] = title
        if rules.is_optiday_page(url, title):   # looking at OptiDay itself
            info.update(place="OptiDay", category="Other", cls="neutral")
            return info
        info["place"] = place_name(info["domain"], app, url) if info["domain"] else app
        v = rules.youtube_video(url, title, app)
        if v:
            rec = self.videos.get(v["id"])
            if rec is not None:   # already judged today; keep using the latest verdict
                j = {k: rec[k] for k in ("label", "reason", "source", "channel")}
            else:
                self.judge.remember(v, self.day)
                j = self.judge.judge(v)
            info["video"] = {"id": v["id"], "title": v["title"], "kind": v["kind"], **j}
            info["category"] = videos.category_for(j["label"])
        info["project"] = self._project(app, bundle, title, url, info["category"])
        info["cls"] = productivity(info["category"])
        return info

    # --------------------------------------------------------- accounting --
    def _account(self, info: dict, start: float, secs: float) -> None:
        if secs <= 0:
            return
        if self.first_ts is None and not info["away"]:
            self.first_ts = start
        c = info["cls"]
        v = info.get("video")
        rec = None
        bkey = c
        if v and not info["away"]:
            rec = self.videos.get(v["id"])
            if rec is None:
                rec = self.videos[v["id"]] = {
                    "id": v["id"], "title": v["title"], "kind": v.get("kind", "video"), "place": info["place"],
                    "label": v["label"], "reason": v["reason"], "source": v["source"], "channel": v["channel"],
                    "s": 0.0, "first": start, "last": start, "buckets": Counter()}
            c = productivity(videos.category_for(rec["label"]))   # your latest call wins
            bkey = "unsure" if rec["label"] == videos.UNSURE else c
            rec["s"] += secs
            rec["last"] = start + secs
        self.cls[c] += secs
        if not info["away"]:
            self.by_place[info["place"]] += secs
            self.place_split.setdefault(info["place"], Counter())[c] += secs
            if info["project"]:
                self.by_project[info["project"]] += secs
        t, end = start, start + secs
        while t < end:
            b = int((t - self.day_start) // BUCKET)
            nxt = min(end, self.day_start + (b + 1) * BUCKET)
            self.buckets.setdefault(b, Counter())[bkey] += nxt - t
            if rec is not None:
                rec["buckets"][b] += nxt - t
            t = nxt
        # flow: productive time where breaks are shorter than FLOW_GRACE
        if c == "productive":
            if self.flow_start is None or start - self.flow_last > FLOW_GRACE:
                self._close_flow()
                self.flow_start = start
            self.flow_last = end
            if info["project"]:
                self.last_productive_project = info["project"]
        if self.focus and self.focus["start"] <= start < self.focus["end"]:
            if c == "distracting":
                self.focus["drift_s"] += secs
            elif c == "productive":
                self.focus["focused_s"] += secs

    def _close_flow(self) -> None:
        if self.flow_start is None:
            return
        length = self.flow_last - self.flow_start
        if length >= float(self.cfg.report.get("focus_block_minutes", 25)) * 60:
            self.focus_s += length
        self.longest_flow = max(self.longest_flow, length)
        self.flow_start = None

    def _observe(self, info: dict, ts: float) -> None:
        """Track switches, how long you've been on the current thing, and drift."""
        cur = self.current
        if cur is None or info["place"] != cur["place"] or info["project"] != cur["project"] \
                or info["away"] != cur["away"]:
            if cur is not None and not cur["away"] and not info["away"] and info["place"] != cur["place"]:
                self.switches += 1
                self.recent_switches.append(ts)
            if cur is not None and not cur["away"] and ts - self.current_since >= 20:
                self.recent.append((self.current_since, ts, cur["place"], cur["project"], cur["cls"]))
            self.current_since = ts
        while self.recent_switches and ts - self.recent_switches[0] > 1800:
            self.recent_switches.popleft()
        if info["cls"] == "distracting":
            if self.drift_since is None:
                self.drift_since = ts
        else:
            self.drift_since = None
        self.current = info
        self.last_seen = ts

    # ------------------------------------------------------------ inputs --
    def seed(self) -> None:
        """Rebuild today's totals from the database (once at startup, or when no recorder runs)."""
        with self.cond:
            day = self.store.today()
            self._reset(day)
            self.judge.refresh(force=True)
            snaps = self.store.snapshots(day)
            for sn in snaps:   # channel names seen on screen earlier today
                if sn.ocr and not sn.away and "youtube" in (sn.url or sn.title).lower():
                    v = rules.youtube_video(sn.url, sn.title, sn.app)
                    if v and not self.judge.channel_of(v["id"]):
                        channel = rules.channel_from_screen(sn.ocr)
                        if channel:
                            self.judge.remember(v, day, channel)
            segs = activity.build_segments(snaps, float(self.cfg.rec["interval_seconds"]))
            for s in segs:
                info = self._info(s.app, s.bundle_id, s.title, s.url, s.away, s.private)
                self._observe(info, s.start)
                self._account(info, s.start, s.seconds)
            if segs:
                self.last_seen = segs[-1].end
            self._seeded_at = self.clock()
            self._bump()

    def ingest(self, snap: Snapshot) -> None:
        """Called by the recorder for every check. Cheap: a few dictionary updates."""
        with self.cond:
            self.attached = True
            if self.store.day_of(snap.ts) != self.day:
                self._close_flow()
                self._reset(self.store.day_of(snap.ts))
            if self._prev is not None:
                pts, cap, pinfo = self._prev
                self._account(pinfo, pts, max(0.0, min(snap.ts - pts, cap)))
            info = self._info(snap.app, snap.bundle_id, snap.title, snap.url, snap.away, snap.private)
            v = info.get("video")
            if v and not v["channel"] and snap.ocr:
                channel = rules.channel_from_screen(snap.ocr)
                if channel and self.judge.remember(v, self.day, channel):
                    j = self.judge.judge(v, channel)
                    v.update(j)
                    info["category"] = videos.category_for(j["label"])
                    info["cls"] = productivity(info["category"])
                    self._relabel(v["id"], j)
            self._observe(info, snap.ts)
            cap = snap.span * 1.5 + 2 if snap.span else float(self.cfg.rec["interval_seconds"]) * 3
            self._prev = (snap.ts, cap, info)
            self._check_focus(snap.ts)
            self._bump()

    # ------------------------------------------------------------- videos --
    def _relabel(self, vid: str, j: dict) -> None:
        """A video's verdict changed (you marked it, its channel, or the AI): move its time today."""
        rec = self.videos.get(vid)
        if rec is None:
            return
        old_c = productivity(videos.category_for(rec["label"]))
        new_c = productivity(videos.category_for(j["label"]))
        old_k = "unsure" if rec["label"] == videos.UNSURE else old_c
        new_k = "unsure" if j["label"] == videos.UNSURE else new_c
        if old_c != new_c and rec["s"] > 0:
            self.cls[old_c] = max(0.0, self.cls[old_c] - rec["s"])
            self.cls[new_c] += rec["s"]
            split = self.place_split.setdefault(rec["place"], Counter())
            split[old_c] = max(0.0, split[old_c] - rec["s"])
            split[new_c] += rec["s"]
        if old_k != new_k:
            for b, x in rec["buckets"].items():
                c = self.buckets.setdefault(b, Counter())
                c[old_k] = max(0.0, c[old_k] - x)
                c[new_k] += x
        rec.update({k: j[k] for k in ("label", "reason", "source", "channel") if k in j})
        for info in (self.current, self._prev[2] if self._prev else None):
            if info and info.get("video") and info["video"]["id"] == vid:
                info["video"].update({k: rec[k] for k in ("label", "reason", "source", "channel")})
                info["category"] = videos.category_for(rec["label"])
                info["cls"] = new_c
        cur = self.current
        if cur and cur.get("video") and cur["video"]["id"] == vid:
            if new_c == "distracting":
                self.drift_since = self.drift_since or self.current_since
            else:
                self.drift_since = None

    def _rejudge(self) -> bool:
        """Look at today's videos again (new labels from you, the report page or the local AI)."""
        changed = False
        for vid, rec in list(self.videos.items()):
            j = self.judge.judge({"id": vid, "title": rec["title"], "kind": rec["kind"]}, rec["channel"])
            if (j["label"], j["source"], j["channel"]) != (rec["label"], rec["source"], rec["channel"]):
                self._relabel(vid, j)
                changed = True
        return changed

    def label_video(self, vid: str, label: str) -> dict:
        """You decided: learning, waste (or not sure). Remembered for this video and its channel."""
        with self.cond:
            self.judge.label(vid, label)
            self._rejudge()
            self._bump()
            rec = self.videos.get(vid)
            return {"id": vid, "label": label, "channel": rec["channel"] if rec else self.judge.channel_of(vid)}

    def rejudge(self) -> None:
        with self.cond:
            self.judge.refresh(force=True)
            if self._rejudge():
                self._bump()

    # ----------------------------------------------------- focus sessions --
    def _load_focus(self) -> None:
        raw = self.store.get_meta("focus-session")
        if raw:
            try:
                f = json.loads(raw)
                if f and f.get("end", 0) > self.clock():
                    self.focus = f
            except ValueError:
                pass

    def _save_focus(self) -> None:
        self.store.set_meta("focus-session", json.dumps(self.focus) if self.focus else "")

    def start_focus(self, minutes: int, label: str = "") -> dict:
        minutes = max(5, min(240, int(minutes)))
        with self.cond:
            now = self.clock()
            label = (label or (self.current or {}).get("project") or self.last_productive_project
                     or "your work").strip()[:60]
            self.focus = {"start": now, "end": now + minutes * 60, "minutes": minutes, "label": label,
                          "drift_s": 0.0, "focused_s": 0.0, "alerted_at": 0.0}
            self._save_focus()
            self._bump()
            return dict(self.focus)

    def stop_focus(self) -> dict | None:
        with self.cond:
            done = self._finish_focus(self.clock(), early=True)
            self._bump()
            return done

    def _finish_focus(self, now: float, early: bool) -> dict | None:
        f = self.focus
        if not f:
            return None
        res = dict(f, ended=now, early=early)
        self.last_focus = res
        self.focus = None
        self._save_focus()
        if self.notify:
            mins = fmt_dur(min(now, f["end"]) - f["start"])
            self.notify("Focus session " + ("stopped" if early else "done"),
                        f"{mins} on {f['label']}: {fmt_dur(f['focused_s'])} focused, "
                        f"{fmt_dur(f['drift_s'])} drifting")
        return res

    def _check_focus(self, now: float) -> None:
        f = self.focus
        if not f:
            return
        if now >= f["end"]:
            self._finish_focus(now, early=False)
            return
        alert_after = float(_live_cfg(self.cfg)["drift_alert_seconds"])
        if self.drift_since is not None and now - self.drift_since >= alert_after \
                and now - f.get("alerted_at", 0) > 300 and self.notify:
            f["alerted_at"] = now
            left = fmt_dur(f["end"] - now)
            self.notify("You drifted from your focus session",
                        f"{(self.current or {}).get('place', 'Something')} for {fmt_dur(now - self.drift_since)}. "
                        f"{left} left on {f['label']}.")
            self._save_focus()

    # ------------------------------------------------------------ output --
    def _extras_cached(self) -> dict:
        """Calendar, email, agents, last hour: read from the database at most every 2 minutes."""
        now = self.clock()
        if now - self._extras_at < 120:
            return self._extras
        ex: dict = {"next_meeting": None, "meetings_left": 0, "emails": 0, "top_email": None, "agents": 0,
                    "last_hour": None}
        try:
            cal = self.store.get_note(f"calendar:{dt.date.fromtimestamp(now).isoformat()}") or {}
            upcoming = [e for e in cal.get("events", []) if not e.get("all_day") and e["end"] > now]
            upcoming.sort(key=lambda e: e["start"])
            ex["meetings_left"] = len(upcoming)
            if upcoming:
                e = upcoming[0]
                ex["next_meeting"] = {"title": e["title"], "start": e["start"], "end": e["end"],
                                      "url": e.get("meet_url", "") if str(e.get("meet_url", "")).startswith(
                                          "https://") else ""}
            mail = self.store.get_note(f"email:{self.day}")
            if mail is None:
                prev = (dt.date.fromisoformat(self.day) - dt.timedelta(days=1)).isoformat()
                mail = self.store.get_note(f"email:{prev}")
            replies = (mail or {}).get("needs_reply", [])
            ex["emails"] = len(replies)
            if replies:
                r = replies[0]
                ex["top_email"] = {"from": r.get("from_name", ""), "subject": r.get("subject", "")[:80],
                                   "ts": r.get("ts", now)}
            from .automations import queue
            ex["agents"] = sum(1 for a in queue(self.store) if a["status"] == "suggested")
            lh = self.store.get_meta("last-hour")
            ex["last_hour"] = json.loads(lh) if lh else None
        except Exception:  # noqa: BLE001 - extras are nice-to-have
            log.exception("live extras failed")
        self._extras, self._extras_at = ex, now
        self.judge.refresh(force=True)
        self._rejudge()
        return ex

    def _state(self, now: float) -> tuple[str, float]:
        if self.paused():
            return "paused", now
        cur = self.current
        if cur is None or cur["away"] or now - self.last_seen > STALE:
            return "away", self.current_since if cur and cur["away"] else self.last_seen or now
        if cur["cls"] == "distracting":
            return "drift", self.drift_since or self.current_since
        if self.flow_start is not None and now - self.flow_last <= FLOW_GRACE + 60:
            if self.flow_last - self.flow_start >= FLOW_MIN or (cur["cls"] == "productive" and
                                                               now - self.flow_start >= FLOW_MIN):
                return "flow", self.flow_start
            if cur["cls"] == "productive":
                return "focus", self.flow_start
        if cur.get("video") and cur["video"]["label"] == videos.UNSURE:
            return "watch", self.current_since
        if cur["cls"] == "neutral":
            return "comms", self.current_since
        return "focus", self.current_since

    def _youtube(self, cur: dict) -> dict:
        """Today's YouTube: learning vs waste vs not sure, and the videos that need your call."""
        cur_vid = (cur.get("video") or {}).get("id")
        totals = Counter()
        shorts = [0, 0.0]
        for r in self.videos.values():
            totals[r["label"]] += r["s"]
            if r["kind"] == "short":
                shorts[0] += 1
                shorts[1] += r["s"]
        places = {r["place"] for r in self.videos.values()} or {"YouTube"}
        browse = sum(self.by_place.get(p, 0.0) for p in places) - sum(totals.values())

        def needs_call(r):
            return r["label"] == videos.UNSURE and r["source"] != "you"
        listed = [r for r in self.videos.values() if r["kind"] != "short" and (r["s"] >= 20 or r["id"] == cur_vid)]
        listed.sort(key=lambda r: (not needs_call(r) or r["s"] < 30, -r["last"]))
        return {
            "learn_s": round(totals[videos.LEARN]), "waste_s": round(totals[videos.WASTE]),
            "unsure_s": round(totals[videos.UNSURE]), "browse_s": round(max(0.0, browse)),
            "shorts": {"count": shorts[0], "s": round(shorts[1])},
            "unsure": sum(1 for r in self.videos.values() if needs_call(r) and r["s"] >= 30 and r["kind"] != "short"),
            "videos": [{k: r[k] for k in ("id", "title", "label", "reason", "source", "channel", "last")}
                       | {"s": round(r["s"]), "now": r["id"] == cur_vid} for r in listed[:8]],
        }

    def _nudges(self, now: float, state: str, since: float, ex: dict, lc: dict, yt: dict | None = None) -> list[dict]:
        out = []
        cur = self.current or {}
        recent = sum(1 for t in self.recent_switches if now - t <= 600)
        nm = ex.get("next_meeting")
        if nm and 0 <= nm["start"] - now <= 15 * 60:
            out.append({"tone": "info", "text": f"{nm['title']} starts in {fmt_dur(nm['start'] - now)}. "
                                                "Wrap up what you're doing."})
        if state == "drift" and now - since >= 180:
            back = self.last_productive_project
            out.append({"tone": "bad", "text": f"{fmt_dur(now - since)} on {cur.get('place', 'this')}."
                        + (f" Back to {back}?" if back else " Time to get back to work?")})
        if recent >= 15:
            out.append({"tone": "warn", "text": f"{recent} app switches in the last 10 minutes. Pick one thing "
                                                "and close the rest."})
        if state == "flow" and now - since >= 50 * 60:
            out.append({"tone": "good", "text": f"{fmt_dur(now - since)} in flow. A 5-minute break soon keeps "
                                                "it going."})
        budget = float(lc["distraction_budget_minutes"]) * 60
        if budget and self.cls["distracting"] >= budget:
            out.append({"tone": "warn", "text": f"{fmt_dur(self.cls['distracting'])} on distractions today, over "
                                                f"your {fmt_dur(budget)} budget."})
        goal = float(lc["focus_goal_hours"]) * 3600
        focus_now = self.focus_s + self._open_flow(now)
        if goal and focus_now >= goal:
            out.append({"tone": "good", "text": f"Focus goal reached: {fmt_dur(focus_now)} of deep work."})
        if yt and yt["unsure"] and len(out) < 4:
            n = yt["unsure"]
            out.append({"tone": "info", "text": f"{n} YouTube video{'s' if n != 1 else ''} need{'' if n != 1 else 's'} "
                                                "your call: learning or waste? Tap one below and OptiDay remembers."})
        te = ex.get("top_email")
        if te and now - te["ts"] > 4 * 3600 and len(out) < 4:
            out.append({"tone": "info", "text": f"{te['from']} has been waiting {fmt_dur(now - te['ts'])} for a "
                                                f"reply: “{te['subject']}”."})
        if ex.get("agents") and len(out) < 4:
            n = ex["agents"]
            out.append({"tone": "info", "text": f"{n} chore{'s' if n != 1 else ''} an agent could do for you. "
                                                "Review them on the Today page."})
        if not out:
            if state in ("flow", "focus"):
                out.append({"tone": "good", "text": "You're on track. Keep going."})
            elif state == "comms":
                out.append({"tone": "info", "text": "Batch email and chat, then close them and get back to "
                                                    "your main project."})
        return out[:5]

    def _open_flow(self, now: float) -> float:
        if self.flow_start is None or now - self.flow_last > FLOW_GRACE + 60:
            return 0.0
        length = self.flow_last - self.flow_start
        return length if length >= float(self.cfg.report.get("focus_block_minutes", 25)) * 60 else 0.0

    def snapshot(self) -> dict:
        with self.cond:
            now = self.clock()
            lc = _live_cfg(self.cfg)
            ex = self._extras_cached()
            state, since = self._state(now)
            active = self.cls["productive"] + self.cls["neutral"] + self.cls["distracting"]
            # include the time since the last check so totals tick up smoothly
            if self._prev is not None and state not in ("away", "paused"):
                pts, cap, pinfo = self._prev
                pending = max(0.0, min(now - pts, cap))
            else:
                pending, pinfo = 0.0, None
            cls = Counter(self.cls)
            if pinfo is not None:
                cls[pinfo["cls"]] += pending
                active += pending if not pinfo["away"] else 0
            first_b = int(((self.first_ts or now) - self.day_start) // BUCKET)
            now_b = int((now - self.day_start) // BUCKET)
            timeline = []
            for b in range(min(first_b, now_b), now_b + 1):
                c = self.buckets.get(b)
                if c:
                    timeline.append([b, round(c["productive"]), round(c["neutral"]), round(c["distracting"]),
                                     round(c["away"]), round(c["unsure"])])
            hours: dict[int, Counter] = {}
            for b, c in self.buckets.items():
                h = dt.datetime.fromtimestamp(self.day_start + b * BUCKET).hour
                hours.setdefault(h, Counter()).update(c)
            this_hour = Counter(hours.get(dt.datetime.fromtimestamp(now).hour, Counter()))
            this_hour["neutral"] += this_hour.pop("unsure", 0.0)
            cur = self.current or {}
            video_places = {r["place"] for r in self.videos.values()}
            places = []
            for p, secs in self.by_place.most_common(7):
                split = self.place_split.get(p, Counter())
                places.append({"name": p, "s": round(secs), "cls": max(split, key=split.get) if split else "neutral",
                               "split": {k: round(x) for k, x in split.items() if x >= 1},
                               "video": p in video_places})
            yt = self._youtube(cur)
            projects = [{"name": p, "s": round(s)} for p, s in self.by_project.most_common(6)]
            open_flow = self._open_flow(now)
            focus = None
            if self.focus:
                f = self.focus
                focus = {k: f[k] for k in ("start", "end", "minutes", "label")} | {
                    "drift_s": round(f["drift_s"]), "focused_s": round(f["focused_s"])}
            return {
                "version": self.version,
                "now": now,
                "day": self.day,
                "day_start": self.day_start,
                "bucket": BUCKET,
                "state": state,
                "since": since,
                "paused": self.paused(),
                "live": self.attached,
                "current": None if not cur else {
                    "place": cur["place"], "app": cur["app"], "title": cur["title"][:160], "project": cur["project"],
                    "category": cur["category"], "cls": cur["cls"], "since": self.current_since,
                    "video": {k: cur["video"][k] for k in ("id", "title", "kind", "label", "reason", "source",
                                                           "channel")} if cur.get("video") else None,
                    "private": cur["private"], "away": cur["away"]},
                "totals": {"active_s": round(active), "productive_s": round(cls["productive"]),
                           "neutral_s": round(cls["neutral"]), "distracting_s": round(cls["distracting"]),
                           "away_s": round(cls["away"]),
                           "score": round(100 * cls["productive"] / active) if active else 0,
                           "focus_s": round(self.focus_s + open_flow),
                           "longest_flow_s": round(max(self.longest_flow, (self.flow_last - self.flow_start)
                                                       if self.flow_start is not None else 0)),
                           "switches": self.switches,
                           "switches_10m": sum(1 for t in self.recent_switches if now - t <= 600),
                           "first_ts": self.first_ts},
                "this_hour": {k: round(this_hour[k]) for k in ("productive", "neutral", "distracting", "away")},
                "recent": [{"start": a, "end": b, "place": p, "project": pr, "cls": c}
                           for a, b, p, pr, c in reversed(self.recent)][:6],
                "projects": projects,
                "places": places,
                "timeline": timeline,
                "goals": {"focus_s": round(float(lc["focus_goal_hours"]) * 3600),
                          "distraction_s": round(float(lc["distraction_budget_minutes"]) * 60)},
                "focus_session": focus,
                "last_focus": self.last_focus if self.last_focus and now - self.last_focus["ended"] < 900 else None,
                "youtube": yt,
                "nudges": self._nudges(now, state, since, ex, lc, yt),
                "next_meeting": ex.get("next_meeting"),
                "meetings_left": ex.get("meetings_left", 0),
                "emails_waiting": ex.get("emails", 0),
                "agents_waiting": ex.get("agents", 0),
                "last_hour": ex.get("last_hour"),
            }

    def wait(self, version: int, timeout: float = 25.0) -> dict:
        """Long poll: return as soon as something changes (or after `timeout`)."""
        if not self.attached and self.clock() - self._seeded_at > 30:
            self.seed()  # no recorder in this process: re-read the database now and then
        with self.cond:
            if self.version == version:
                self.cond.wait(timeout)
        return self.snapshot()

