"""Is this YouTube video learning, a waste of time, or not sure?

One place decides, so the live view and the reports always agree. In order:

1. you     you marked this video                      (on the Live view or in a report)
2. channel you marked another video from this channel  (the channel is read from the screen)
3. ai      the local model judged it                   (only when plugged in and the model is installed)
4. learned it looks like videos you already marked    (shared words in the title)
5. rules   the title clearly says so                   ("tutorial", "lecture", "prank", "official video"…)
6. otherwise "not sure", and the Live view asks you once.

Everything stays in the encrypted database on this Mac.
"""
from __future__ import annotations

import logging
import re
import time

from . import rules

log = logging.getLogger("optiday.videos")

LEARN, WASTE, UNSURE = "informational", "waste", "unclear"
LABELS = (LEARN, WASTE, UNSURE)
WORDS = {LEARN: "Learning", WASTE: "Waste", UNSURE: "Not sure"}
META = "video:"        # note per video: title, channel, first day seen
AI = "video-ai:"       # note per video: the local model's verdict

_STOP = {"the", "and", "for", "with", "you", "your", "this", "that", "from", "how", "what", "why", "are", "was",
         "official", "video", "part", "full", "new", "live", "2023", "2024", "2025", "2026", "2027", "into", "about",
         "best", "most", "ever", "have", "has", "will", "can", "not", "all", "but", "out", "get", "its", "it's"}


def category_for(label: str) -> str:
    return {LEARN: "Learning", WASTE: "Entertainment"}.get(label, "Video")


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'+.#-]*[a-z0-9+#]|[a-z0-9]", title.lower())
            if len(w) >= 3 and w not in _STOP and not w.isdigit()}


class VideoJudge:
    def __init__(self, store, cfg, ttl: float = 120.0, clock=time.time):
        self.store = store
        self.cfg = cfg
        self.ttl = ttl
        self.clock = clock
        self._at = -1e9
        self.mine: dict[str, str] = {}
        self.channels: dict[str, str] = {}
        self.meta: dict[str, dict] = {}
        self.ai: dict[str, dict] = {}
        self._examples: list[tuple[set[str], str, str]] = []

    # ------------------------------------------------------------- data --
    def refresh(self, force: bool = False) -> None:
        if not force and self.clock() - self._at < self.ttl:
            return
        self._at = self.clock()
        try:
            self.mine = self.store.overrides("video")
            self.channels = {k.lower(): v for k, v in self.store.overrides("channel").items()}
            self.meta = {k[len(META):]: v for k, v in self.store.notes_with_prefix(META, limit=5000).items()
                         if isinstance(v, dict)}
            self.ai = {k[len(AI):]: v for k, v in self.store.notes_with_prefix(AI, limit=5000).items()
                       if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            log.exception("couldn't load video labels")
        self._examples = [(_tokens(self.meta[v].get("title", "")), lab, self.meta[v].get("title", ""))
                          for v, lab in self.mine.items() if lab in (LEARN, WASTE) and v in self.meta]

    def channel_of(self, vid: str) -> str:
        return (self.meta.get(vid) or {}).get("channel", "")

    def remember(self, video: dict, day: str, channel: str = "") -> bool:
        """Note a video's title (and channel once known). Returns True if something new was saved."""
        vid = video["id"]
        old = self.meta.get(vid)
        if old and (not channel or old.get("channel") == channel):
            return False
        rec = dict(old or {"title": video.get("title", ""), "kind": video.get("kind", "video"), "day": day})
        if channel:
            rec["channel"] = channel
        self.meta[vid] = rec
        try:
            self.store.put_note(META + vid, rec.get("day", day), rec)
        except Exception:  # noqa: BLE001
            log.exception("couldn't save video info")
        return True

    # ---------------------------------------------------------- verdict --
    def judge(self, video: dict, channel: str = "") -> dict:
        self.refresh()
        vid = video["id"]
        channel = channel or self.channel_of(vid)
        out = {"label": UNSURE, "reason": "Not sure from the title alone", "source": "unsure", "channel": channel}
        if vid in self.mine:
            return out | {"label": self.mine[vid], "reason": "You marked this", "source": "you"}
        if channel and channel.lower() in self.channels:
            lab = self.channels[channel.lower()]
            return out | {"label": lab, "reason": f"You marked {channel} as {WORDS[lab].lower()}",
                          "source": "channel"}
        ai = self.ai.get(vid)
        if ai and ai.get("label") in (LEARN, WASTE):
            return out | {"label": ai["label"], "reason": ai.get("reason") or "Local AI judgment", "source": "ai"}
        if video.get("kind") != "short":
            learned = self._learned(video.get("title", ""))
            if learned:
                return out | learned
        label, reason = rules.classify_video_heuristic(video, self.cfg.profile.get("useful_video_topics"))
        if label != UNSURE:
            return out | {"label": label, "reason": reason, "source": "rules"}
        if ai and ai.get("label") == UNSURE:
            out["reason"] = "Even the local AI couldn't tell"
        return out

    def _learned(self, title: str) -> dict | None:
        toks = _tokens(title)
        if len(toks) < 2 or not self._examples:
            return None
        best, best_sim = None, 0.0
        for ex, lab, ex_title in self._examples:
            shared = toks & ex
            if len(shared) < 2:
                continue
            sim = len(shared) / len(toks | ex)
            if sim > best_sim:
                best, best_sim = (lab, ex_title), sim
        if best and best_sim >= 0.34:
            lab, ex_title = best
            return {"label": lab, "source": "learned",
                    "reason": f"Like “{ex_title[:60]}”, which you marked as {WORDS[lab].lower()}"}
        return None

    # ----------------------------------------------------------- labels --
    def label(self, vid: str, label: str, channel_too: bool = True) -> str:
        """Save your call for a video (and, by default, its channel). Returns the channel, if known."""
        if label not in LABELS:
            raise ValueError(label)
        self.store.set_override("video", vid, label)
        channel = self.channel_of(vid)
        if channel and channel_too and label in (LEARN, WASTE):
            self.store.set_override("channel", channel.lower(), label)
        self.refresh(force=True)
        return channel


AI_SYSTEM = """You sort YouTube videos for one person. For each video decide:
- "informational": it helps their work, studies, goals or learning (tutorials, lectures, talks, explainers, news in their field).
- "waste": entertainment with no real value to them (music, gaming, pranks, reactions, sports clips, drama, memes).
- "unclear": you genuinely can't tell from the title and channel.
Be honest: say "unclear" rather than guess. Return JSON: {"videos": [{"n": <number>, "label": "...", "reason": "<a few words>"}]}"""


def ai_judge_unsure(store, cfg, llm, day: str, limit: int = 8) -> int:
    """Ask the local model about today's videos that are still "not sure". Each video is asked about once.
    Returns how many got a verdict. Callers only run this while plugged in."""
    if llm is None:
        return 0
    judge = VideoJudge(store, cfg)
    judge.refresh(force=True)
    todo = []
    for vid, m in judge.meta.items():
        if m.get("day") != day or vid in judge.ai:
            continue
        v = {"id": vid, "title": m.get("title", ""), "kind": m.get("kind", "video")}
        if judge.judge(v)["source"] in ("unsure", "rules", "learned"):
            todo.append((vid, m))
    try:
        ok, _msg = llm.status()   # e.g. the model isn't downloaded yet: stay "not sure", ask nothing
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        return 0
    todo.sort(key=lambda t: judge.judge({"id": t[0], "title": t[1].get("title", "")})["source"] != "unsure")
    todo = todo[:limit]
    if not todo:
        return 0
    profile = cfg.profile
    lines = [f"{i + 1}. “{m.get('title', '')}”" + (f" by {m['channel']}" if m.get("channel") else "")
             for i, (_, m) in enumerate(todo)]
    user = (f"About them: {profile.get('about_me', '') or 'not given'}\n"
            f"Their goals: {'; '.join(profile.get('goals', [])) or 'not given'}\n"
            f"Topics they find useful: {', '.join(profile.get('useful_video_topics', [])) or 'not given'}\n\n"
            "Videos:\n" + "\n".join(lines))
    out = llm.chat_json(AI_SYSTEM, user)
    items = (out or {}).get("videos") if isinstance(out, dict) else None
    if not isinstance(items, list):
        return 0
    done = 0
    for it in items:
        try:
            n = int(it.get("n")) - 1
        except (TypeError, ValueError, AttributeError):
            continue
        lab = str(it.get("label", "")).lower()
        if 0 <= n < len(todo) and lab in LABELS:
            vid = todo[n][0]
            store.put_note(AI + vid, day, {"label": lab, "reason": str(it.get("reason") or "")[:120]})
            done += 1
    return done
