"""Built-in knowledge: app/site categories, project detection, YouTube parsing,
privacy checks and rule-based action detection. Works with no AI at all; the
local model (analyzer.py) adds detail on top."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from urllib.parse import parse_qs, urlparse

from .models import Segment

CATEGORIES = [
    "Coding", "Terminal", "AI Assistants", "Writing", "Design", "Research", "Learning", "Planning",
    "Email", "Communication", "Meetings", "Browsing", "Video", "Finance",
    "Social", "Entertainment", "News", "Shopping", "Games", "System", "Other",
]
PRODUCTIVE = {"Coding", "Terminal", "AI Assistants", "Writing", "Design", "Research", "Learning", "Planning"}
DISTRACTING = {"Social", "Entertainment", "News", "Shopping", "Games"}


def productivity(category: str) -> str:
    if category in PRODUCTIVE:
        return "productive"
    if category in DISTRACTING:
        return "distracting"
    return "neutral"


BROWSER_BUNDLES = {
    "com.apple.Safari": "safari", "com.apple.SafariTechnologyPreview": "safari",
    "com.google.Chrome": "chromium", "com.google.Chrome.beta": "chromium", "com.google.Chrome.canary": "chromium",
    "com.brave.Browser": "chromium", "com.microsoft.edgemac": "chromium", "com.vivaldi.Vivaldi": "chromium",
    "company.thebrowser.Browser": "chromium", "com.operasoftware.Opera": "chromium",
    "org.chromium.Chromium": "chromium", "com.kagi.kagimacOS": "safari",
    "org.mozilla.firefox": "firefox", "app.zen-browser.zen": "firefox", "org.mozilla.firefoxdeveloperedition": "firefox",
}
BROWSER_APPS = {"safari", "google chrome", "chrome", "arc", "brave browser", "microsoft edge", "firefox", "opera",
                "vivaldi", "orion", "zen", "zen browser", "chromium", "dia"}


def is_browser(app: str, bundle_id: str = "") -> bool:
    return bundle_id in BROWSER_BUNDLES or app.lower() in BROWSER_APPS


_APP_CATEGORY = {
    # coding
    "code": "Coding", "visual studio code": "Coding", "cursor": "Coding", "windsurf": "Coding", "zed": "Coding",
    "xcode": "Coding", "sublime text": "Coding", "nova": "Coding", "pycharm": "Coding", "intellij idea": "Coding",
    "webstorm": "Coding", "goland": "Coding", "rustrover": "Coding", "android studio": "Coding", "vscodium": "Coding",
    "github desktop": "Coding", "tower": "Coding", "fork": "Coding", "docker desktop": "Coding", "docker": "Coding",
    "postman": "Coding", "tableplus": "Coding", "simulator": "Coding", "trae": "Coding", "kiro": "Coding",
    # terminal
    "terminal": "Terminal", "iterm2": "Terminal", "iterm": "Terminal", "warp": "Terminal", "ghostty": "Terminal",
    "alacritty": "Terminal", "kitty": "Terminal", "hyper": "Terminal", "wezterm": "Terminal",
    # ai
    "claude": "AI Assistants", "chatgpt": "AI Assistants", "perplexity": "AI Assistants", "ollama": "AI Assistants",
    "lm studio": "AI Assistants",
    # writing / docs
    "pages": "Writing", "microsoft word": "Writing", "notes": "Writing", "obsidian": "Writing", "notion": "Writing",
    "bear": "Writing", "ulysses": "Writing", "ia writer": "Writing", "craft": "Writing", "textedit": "Writing",
    "microsoft excel": "Writing", "numbers": "Writing", "keynote": "Writing", "microsoft powerpoint": "Writing",
    "google docs": "Writing", "logseq": "Writing",
    # design
    "figma": "Design", "sketch": "Design", "adobe photoshop": "Design", "adobe illustrator": "Design",
    "canva": "Design", "affinity designer": "Design", "pixelmator pro": "Design", "final cut pro": "Design",
    "davinci resolve": "Design", "adobe premiere pro": "Design", "framer": "Design", "screen studio": "Design",
    # planning
    "calendar": "Planning", "reminders": "Planning", "things": "Planning", "todoist": "Planning",
    "omnifocus": "Planning", "linear": "Planning", "fantastical": "Planning", "notion calendar": "Planning",
    "jira": "Planning", "asana": "Planning", "trello": "Planning", "clickup": "Planning",
    # email
    "mail": "Email", "microsoft outlook": "Email", "outlook": "Email", "spark": "Email", "spark desktop": "Email",
    "superhuman": "Email", "airmail": "Email", "mimestream": "Email", "proton mail": "Email",
    # communication
    "slack": "Communication", "messages": "Communication", "discord": "Communication", "whatsapp": "Communication",
    "telegram": "Communication", "signal": "Communication", "messenger": "Communication",
    "microsoft teams": "Meetings", "skype": "Communication",
    # meetings
    "zoom.us": "Meetings", "zoom": "Meetings", "facetime": "Meetings", "webex": "Meetings", "around": "Meetings",
    "tuple": "Meetings", "google meet": "Meetings",
    # browsing
    "safari": "Browsing", "google chrome": "Browsing", "arc": "Browsing", "firefox": "Browsing",
    "brave browser": "Browsing", "microsoft edge": "Browsing", "opera": "Browsing", "vivaldi": "Browsing",
    "orion": "Browsing", "zen": "Browsing", "dia": "Browsing", "chromium": "Browsing",
    # media / fun
    "spotify": "Entertainment", "music": "Entertainment", "tv": "Entertainment", "netflix": "Entertainment",
    "vlc": "Video", "iina": "Video", "quicktime player": "Video", "photos": "Other", "podcasts": "Learning",
    "steam": "Games", "chess": "Games", "epic games launcher": "Games", "battle.net": "Games",
    # learning / reading
    "kindle": "Learning", "books": "Learning", "anki": "Learning", "preview": "Research", "skim": "Research",
    "readwise": "Learning", "reeder": "News", "news": "News", "stocks": "Finance", "tradingview": "Finance",
    # system
    "finder": "System", "system settings": "System", "system preferences": "System", "activity monitor": "System",
    "app store": "System", "loginwindow": "System", "1password": "System", "passwords": "System",
    "optiday": "System", "python": "System", "installer": "System", "archive utility": "System",
    "screenshot": "System", "raycast": "System", "alfred": "System", "spotlight": "System",
}

_BUNDLE_PREFIX_CATEGORY = [
    ("com.jetbrains.", "Coding"),
    ("com.microsoft.VSCode", "Coding"),
    ("com.todesktop.", "Coding"),  # Cursor
    ("com.exafunction.windsurf", "Coding"),
    ("dev.zed.", "Coding"),
    ("com.apple.dt.Xcode", "Coding"),
    ("com.googlecode.iterm2", "Terminal"),
    ("dev.warp.", "Terminal"),
    ("com.mitchellh.ghostty", "Terminal"),
    ("com.anthropic.claude", "AI Assistants"),
    ("com.openai.chat", "AI Assistants"),
    ("com.tinyspeck.slackmacgap", "Communication"),
    ("us.zoom.", "Meetings"),
]

_DOMAIN_CATEGORY = {
    # coding
    "github.com": "Coding", "gitlab.com": "Coding", "bitbucket.org": "Coding", "stackoverflow.com": "Coding",
    "stackexchange.com": "Coding", "developer.apple.com": "Coding", "docs.python.org": "Coding",
    "readthedocs.io": "Coding", "npmjs.com": "Coding", "pypi.org": "Coding", "vercel.com": "Coding",
    "netlify.com": "Coding", "render.com": "Coding", "railway.app": "Coding", "railway.com": "Coding",
    "supabase.com": "Coding", "firebase.google.com": "Coding", "console.cloud.google.com": "Coding",
    "console.aws.amazon.com": "Coding", "localhost": "Coding", "127.0.0.1": "Coding", "replit.com": "Coding",
    "codesandbox.io": "Coding", "huggingface.co": "Coding", "developer.mozilla.org": "Coding",
    "docs.anthropic.com": "Coding", "platform.openai.com": "Coding", "console.anthropic.com": "Coding",
    "claude.ai/code": "Coding", "cloudflare.com": "Coding", "dash.cloudflare.com": "Coding", "lovable.dev": "Coding",
    "bolt.new": "Coding", "v0.dev": "Coding", "v0.app": "Coding", "expo.dev": "Coding",
    # ai
    "chatgpt.com": "AI Assistants", "chat.openai.com": "AI Assistants", "claude.ai": "AI Assistants",
    "gemini.google.com": "AI Assistants", "perplexity.ai": "AI Assistants", "poe.com": "AI Assistants",
    "copilot.microsoft.com": "AI Assistants", "grok.com": "AI Assistants", "chat.deepseek.com": "AI Assistants",
    # email
    "mail.google.com": "Email", "outlook.live.com": "Email", "outlook.office.com": "Email",
    "outlook.office365.com": "Email", "mail.yahoo.com": "Email", "mail.superhuman.com": "Email",
    "mail.proton.me": "Email", "app.hey.com": "Email",
    # communication
    "app.slack.com": "Communication", "slack.com": "Communication", "discord.com": "Communication",
    "web.whatsapp.com": "Communication", "messenger.com": "Communication", "web.telegram.org": "Communication",
    "teams.microsoft.com": "Meetings", "teams.live.com": "Meetings",
    # meetings
    "meet.google.com": "Meetings", "zoom.us": "Meetings", "app.zoom.us": "Meetings", "whereby.com": "Meetings",
    "around.co": "Meetings", "webex.com": "Meetings",
    # writing
    "docs.google.com": "Writing", "notion.so": "Writing", "notion.site": "Writing", "coda.io": "Writing",
    "quip.com": "Writing", "office.com": "Writing", "onedrive.live.com": "Writing", "airtable.com": "Writing",
    "dropbox.com": "Writing", "drive.google.com": "Writing", "craft.do": "Writing", "gamma.app": "Writing",
    "pitch.com": "Writing",
    # planning
    "calendar.google.com": "Planning", "linear.app": "Planning", "trello.com": "Planning", "asana.com": "Planning",
    "atlassian.net": "Planning", "todoist.com": "Planning", "clickup.com": "Planning", "monday.com": "Planning",
    "cal.com": "Planning", "calendly.com": "Planning",
    # design
    "figma.com": "Design", "canva.com": "Design", "dribbble.com": "Design", "behance.net": "Design",
    "framer.com": "Design", "mobbin.com": "Design",
    # video
    "youtube.com": "Video", "youtu.be": "Video", "vimeo.com": "Video", "loom.com": "Video",
    # entertainment
    "netflix.com": "Entertainment", "hulu.com": "Entertainment", "disneyplus.com": "Entertainment",
    "max.com": "Entertainment", "primevideo.com": "Entertainment", "twitch.tv": "Entertainment",
    "open.spotify.com": "Entertainment", "soundcloud.com": "Entertainment", "tiktok.com": "Entertainment",
    "9gag.com": "Entertainment", "kick.com": "Entertainment", "crunchyroll.com": "Entertainment",
    # social
    "twitter.com": "Social", "x.com": "Social", "reddit.com": "Social", "facebook.com": "Social",
    "instagram.com": "Social", "threads.net": "Social", "threads.com": "Social", "linkedin.com": "Social",
    "bsky.app": "Social", "pinterest.com": "Social", "snapchat.com": "Social", "tumblr.com": "Social",
    # news
    "news.ycombinator.com": "News", "nytimes.com": "News", "wsj.com": "News", "bloomberg.com": "News",
    "cnn.com": "News", "bbc.com": "News", "bbc.co.uk": "News", "reuters.com": "News", "theverge.com": "News",
    "techcrunch.com": "News", "ft.com": "News", "axios.com": "News", "news.google.com": "News",
    "foxnews.com": "News", "cnbc.com": "News", "apnews.com": "News", "washingtonpost.com": "News",
    "theinformation.com": "News", "businessinsider.com": "News", "espn.com": "News",
    # shopping
    "amazon.com": "Shopping", "ebay.com": "Shopping", "etsy.com": "Shopping", "walmart.com": "Shopping",
    "target.com": "Shopping", "aliexpress.com": "Shopping", "bestbuy.com": "Shopping", "temu.com": "Shopping",
    "shein.com": "Shopping", "costco.com": "Shopping",
    # finance
    "finance.yahoo.com": "Finance", "tradingview.com": "Finance", "robinhood.com": "Finance",
    "seekingalpha.com": "Finance", "finviz.com": "Finance", "investing.com": "Finance", "coinbase.com": "Finance",
    "schwab.com": "Finance", "fidelity.com": "Finance", "stripe.com": "Finance", "dashboard.stripe.com": "Finance",
    "mercury.com": "Finance", "quickbooks.intuit.com": "Finance",
    # research / learning
    "wikipedia.org": "Research", "scholar.google.com": "Research", "arxiv.org": "Research",
    "medium.com": "Research", "substack.com": "Research", "google.com/search": "Research",
    "bing.com/search": "Research", "duckduckgo.com": "Research", "kagi.com": "Research",
    "producthunt.com": "Research", "ycombinator.com": "Research", "crunchbase.com": "Research",
    "coursera.org": "Learning", "udemy.com": "Learning", "khanacademy.org": "Learning", "edx.org": "Learning",
    "duolingo.com": "Learning", "brilliant.org": "Learning", "instructure.com": "Learning",
    "canvas.com": "Learning", "gradescope.com": "Learning", "piazza.com": "Learning", "edstem.org": "Learning",
    "quizlet.com": "Learning", "chegg.com": "Learning", "blackboard.com": "Learning", "brightspace.com": "Learning",
    "d2l.com": "Learning", "moodle.org": "Learning", "tophat.com": "Learning", "pearson.com": "Learning",
    "mheducation.com": "Learning", "wolframalpha.com": "Learning", "desmos.com": "Learning",
    # games
    "chess.com": "Games", "lichess.org": "Games", "poki.com": "Games", "crazygames.com": "Games",
}

PASSIVE_MEDIA_APPS = {"zoom.us", "zoom", "facetime", "vlc", "iina", "quicktime player", "tv", "netflix",
                      "microsoft teams", "webex", "around", "tuple", "spotify", "music", "podcasts"}
PASSIVE_MEDIA_DOMAINS = {"youtube.com", "youtu.be", "netflix.com", "twitch.tv", "vimeo.com", "meet.google.com",
                         "zoom.us", "app.zoom.us", "teams.microsoft.com", "teams.live.com", "hulu.com",
                         "disneyplus.com", "max.com", "primevideo.com", "loom.com", "whereby.com", "kick.com",
                         "crunchyroll.com", "tiktok.com"}


def domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _lookup_domain(url: str, table: dict[str, str]) -> str | None:
    host = domain_of(url)
    if not host:
        return None
    path = urlparse(url).path or ""
    first = path.strip("/").split("/")[0] if path.strip("/") else ""
    labels = host.split(".")
    for i in range(len(labels) - 1):
        h = ".".join(labels[i:])
        if first and f"{h}/{first}" in table:
            return table[f"{h}/{first}"]
        if h in table:
            return table[h]
    return None


def categorize(app: str, bundle_id: str, url: str, overrides: dict[str, str] | None = None) -> str:
    overrides = {k.lower(): v for k, v in (overrides or {}).items()}
    a = app.lower()
    if url:
        if overrides:
            hit = _lookup_domain(url, overrides)
            if hit:
                return hit
        hit = _lookup_domain(url, _DOMAIN_CATEGORY)
        if hit:
            if hit == "Video" and domain_of(url).endswith("youtube.com"):
                path = urlparse(url).path
                if path.startswith("/shorts") or path in ("", "/", "/feed/subscriptions", "/feed/trending"):
                    return "Entertainment"
            return hit
    if a in overrides:
        return overrides[a]
    if a in _APP_CATEGORY:
        cat = _APP_CATEGORY[a]
        if cat == "Browsing" and not url:
            return "Browsing"
        return cat
    for prefix, cat in _BUNDLE_PREFIX_CATEGORY:
        if bundle_id.startswith(prefix):
            return cat
    if is_browser(app, bundle_id):
        return "Browsing"
    return "Other"


def is_passive_media(app: str, url: str) -> bool:
    if app.lower() in PASSIVE_MEDIA_APPS:
        return True
    d = domain_of(url)
    if not d:
        return False
    if d.endswith("youtube.com"):
        path = urlparse(url).path
        return path.startswith("/watch") or path.startswith("/live") or path.startswith("/shorts")
    return any(d == m or d.endswith("." + m) for m in PASSIVE_MEDIA_DOMAINS)


def is_excluded(app: str, url: str, title: str, cfg) -> bool:
    excluded = {a.lower() for a in cfg.privacy.get("excluded_apps", [])}
    if app.lower() in excluded:
        return True
    u = (url or "").lower()
    if u and any(k.lower() in u for k in cfg.privacy.get("excluded_url_keywords", [])):
        return True
    return False


_PRIVATE_TITLE = re.compile(r"(private browsing|incognito|\bprivate\b\s*$|— private$)", re.I)


def looks_private(title: str) -> bool:
    return bool(_PRIVATE_TITLE.search(title or ""))


# ---------------------------------------------------------------- titles ----

_BROWSER_SUFFIX = re.compile(
    r"\s+[—–-]\s+(Google Chrome|Mozilla Firefox|Firefox|Safari|Brave|Microsoft Edge|Arc|Opera|Vivaldi|Chromium|Zen Browser|Orion)"
    r"(\s+[—–-]\s+.*)?$")
_UNREAD_PREFIX = re.compile(r"^\s*(\(\d+\+?\)|\[\d+\]|[•●*])\s*")


def clean_title(title: str) -> str:
    t = _BROWSER_SUFFIX.sub("", title or "")
    t = _UNREAD_PREFIX.sub("", t)
    return t.strip()


# -------------------------------------------------------------- projects ----

_EDITOR_APPS = {"code", "visual studio code", "cursor", "windsurf", "zed", "vscodium", "code - insiders", "trae",
                "kiro", "sublime text", "nova"}
_JETBRAINS_APPS = {"pycharm", "intellij idea", "webstorm", "goland", "rustrover", "android studio", "clion",
                   "phpstorm", "rider", "datagrip", "rubymine", "fleet"}
_TERMINAL_APPS = {"terminal", "iterm2", "iterm", "warp", "ghostty", "alacritty", "kitty", "hyper", "wezterm"}
_NOISE_PARTS = {"welcome", "settings", "get started", "release notes", "extensions", "untitled", "keyboard shortcuts",
                "zsh", "-zsh", "bash", "-bash", "fish", "login", "ssh", "python", "node", "claude", "default",
                "visual studio code", "cursor", "windsurf", "zed", "xcode", "terminal", "iterm2", "warp", "ghostty"}
_GENERIC_DIRS = {"desktop", "downloads", "documents", "code", "projects", "project", "src", "dev", "repos",
                 "github", "git", "work", "workspace", "sites", "home", "tmp", "users", "library", "applications",
                 "app", "apps", "lib", "bin", "test", "tests", "scripts", "~"}
_FILE_EXT = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_SPLIT = re.compile(r"\s+[—–-]\s+")
_GH_SKIP_OWNERS = {"settings", "orgs", "notifications", "marketplace", "explore", "topics", "features", "pulls",
                   "issues", "login", "sponsors", "new", "codespaces", "search", "trending", "collections",
                   "organizations", "apps", "about", "pricing", "enterprise", "team", "customer-stories", "readme",
                   "copilot", "dashboard"}


def _clean_part(p: str) -> str:
    p = re.sub(r"\[(SSH|WSL|Dev Container|Codespaces)[^\]]*\]", "", p, flags=re.I)
    p = re.sub(r"\((Workspace|Working Tree|Index)\)", "", p, flags=re.I)
    p = p.strip(" ●•*")
    return p.strip()


def _ok_name(p: str) -> bool:
    low = p.lower()
    return bool(p) and low not in _NOISE_PARTS and not _FILE_EXT.search(p) and len(p) <= 60 \
        and not low.startswith(("extension:", "untitled", "settings")) and "×" not in p


def _me() -> set[str]:
    """Your login and home folder name: a terminal titled "sam — -zsh" isn't a project called "sam"."""
    import getpass
    from pathlib import Path
    names = set()
    try:
        names.add(getpass.getuser().lower())
    except Exception:  # noqa: BLE001
        pass
    names.add(Path.home().name.lower())
    return {n for n in names if n}


_ME = _me()


def project_from_window(app: str, bundle_id: str, title: str, url: str) -> str:
    a = app.lower()
    title = title or ""
    if url:
        host = domain_of(url)
        if host in ("github.com", "gitlab.com", "bitbucket.org"):
            parts = [p for p in urlparse(url).path.split("/") if p]
            if len(parts) >= 2 and parts[0].lower() not in _GH_SKIP_OWNERS:
                return parts[1].removesuffix(".git")
            return ""
        if host == "vercel.com":
            parts = [p for p in urlparse(url).path.split("/") if p]
            if len(parts) >= 2 and parts[1] not in ("settings", "~"):
                return parts[1]
        return ""
    if a in _EDITOR_APPS or bundle_id.startswith(("com.microsoft.VSCode", "com.todesktop.", "dev.zed.",
                                                  "com.exafunction.windsurf")):
        parts = [_clean_part(p) for p in _SPLIT.split(title)]
        parts = [p for p in parts if p and p.lower() != a and not p.lower().startswith(a + " ")]
        candidates = [p for p in parts if _ok_name(p) and p.lower() not in _ME]
        return candidates[-1] if candidates else ""
    if a == "xcode" or a in _JETBRAINS_APPS or bundle_id.startswith(("com.jetbrains.", "com.apple.dt.Xcode")):
        parts = [_clean_part(p) for p in _SPLIT.split(title)]
        candidates = [p for p in parts if _ok_name(p)]
        return candidates[0] if candidates else ""
    if a in _TERMINAL_APPS:
        m = re.findall(r"(?:~|/Users/[^/\s]+|/home/[^/\s]+)((?:/[\w.\-]+)+)", title)
        if m:
            comps = [c for c in m[-1].split("/") if c]
            for c in reversed(comps):
                if c.lower() not in _GENERIC_DIRS and not c.startswith(".") and c.lower() not in _ME:
                    return c
        parts = [_clean_part(p) for p in _SPLIT.split(title)]
        if len(parts) >= 2 and _ok_name(parts[0]) and not re.search(r"\d+×\d+|\d+x\d+", parts[0]):
            if parts[0].lower() not in _GENERIC_DIRS and " " not in parts[0] and parts[0].lower() not in _ME \
                    and "@" not in parts[0]:
                return parts[0]
    return ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_GENERIC_PROJECT_NAMES = {"web", "app", "api", "site", "frontend", "backend", "server", "client", "main", "test",
                          "demo", "docs", "notes", "website", "mobile", "landing", "readme", "scripts", "utils"}


def match_known_project(text: str, projects: dict[str, list[str]], discovered: list[str]) -> str:
    """Match configured keywords first, then auto-discovered project names."""
    t = text.lower()
    nt = _norm(text)
    for name, keywords in projects.items():
        for kw in [name, *keywords]:
            k = _norm(kw)
            if len(k) >= 3 and (k in nt or kw.lower() in t):
                return name
    for name in discovered:
        k = _norm(name)
        if len(k) >= 5 and k not in _GENERIC_PROJECT_NAMES and k in nt:
            return name
    return ""


# --------------------------------------------------------------- youtube ----

def youtube_video(url: str, title: str, app: str = "") -> dict | None:
    t = clean_title(title)
    vid, kind = None, "video"
    if url:
        try:
            u = urlparse(url)
        except ValueError:
            return None
        host = domain_of(url)
        if host.endswith("youtube.com"):
            if u.path == "/watch":
                vid = (parse_qs(u.query).get("v") or [None])[0]
            elif u.path.startswith("/shorts/"):
                vid, kind = u.path.split("/")[2], "short"
            elif u.path.startswith("/live/"):
                vid, kind = u.path.split("/")[2], "live"
        elif host == "youtu.be":
            vid = u.path.strip("/") or None
        if not vid:
            return None
    else:
        if not re.search(r"\s[-–—]\s+YouTube\s*$", t):
            return None
        vid = "t-" + hashlib.sha1(t.encode()).hexdigest()[:11]
    name = re.sub(r"\s[-–—]\s+YouTube\s*$", "", t).strip() or "(untitled video)"
    if kind == "short" and name.lower() in ("youtube", "shorts"):
        name = "YouTube Shorts"
    return {"id": vid, "title": name, "kind": kind}


# Strong words decide on their own; weak words need company. Anything else stays "not sure"
# so you can make the call once and OptiDay remembers it.
_INFO_STRONG = ["tutorial", "how to", "lecture", "course", "lesson", "explained", "explainer", "crash course",
                "masterclass", "documentary", "podcast", "interview", "keynote", "webinar", "walkthrough", "guide",
                "deep dive", "case study", "system design", "full course", "for beginners", "step by step",
                "breakdown", "explains", "what is", "history of", "ted talk", "tedx", "study with me",
                "exam", "review session", "professor", "lab", "chapter", "homework", "class"]
_INFO_WEAK = ["learn", "learning", "build", "building", "coding", "programming", "python", "javascript",
              "typescript", "swift", "react", "next.js", "ai", "gpt", "claude", "llm", "agents", "machine learning",
              "startup", "founder", "y combinator", "investing", "stock", "stocks", "markets", "economics",
              "finance", "business", "marketing", "sales", "productivity", "strategy", "tips", "lessons",
              "framework", "career", "fundraising", "pitch", "saas", "revenue", "earnings", "analysis", "why",
              "how i", "science", "math", "physics", "chemistry", "biology", "engineering", "data"]
_WASTE_STRONG = ["funny", "prank", "reaction", "reacts", "compilation", "try not to laugh", "fails", "meme",
                 "memes", "tiktok", "#shorts", "asmr", "mukbang", "gameplay", "let's play", "lets play",
                 "fortnite", "minecraft", "roblox", "gta", "music video", "official video", "official audio",
                 "lyrics", "lyric video", "vlog", "unboxing", "celebrity", "gossip", "satisfying", "i survived",
                 "mrbeast", "speedrun", "stream highlights", "rizz", "skibidi", "goes wrong", "gone wrong",
                 "you won't believe", "trailer", "anime", "full episode", "highlights", "visualizer"]
_WASTE_WEAK = ["challenge", "drama", "exposed", "freestyle", "24 hours", "twitch", "insane", "craziest", "dunk",
               "nba", "nfl", "ufc", "season", "episode", "edit", "song", "remix", "live performance", "ft.",
               "feat.", "prod.", "cover", "audio", "official"]


def _kw_re(words: list[str]) -> re.Pattern:
    alts = sorted({w.strip().lower() for w in words if w.strip()}, key=len, reverse=True)
    return re.compile(r"(?<![\w#])(" + "|".join(re.escape(w) for w in alts) + r")(?!\w)")


_INFO_STRONG_RE, _INFO_WEAK_RE = _kw_re(_INFO_STRONG), _kw_re(_INFO_WEAK)
_WASTE_STRONG_RE, _WASTE_WEAK_RE = _kw_re(_WASTE_STRONG), _kw_re(_WASTE_WEAK)


def classify_video_heuristic(video: dict, useful_topics: list[str] | None = None) -> tuple[str, str]:
    """A cautious first guess from the title: "informational", "waste" or "unclear" (not sure)."""
    title = video.get("title", "").lower()
    topics = [t for t in (useful_topics or []) if str(t).strip()]
    info_s = set(_INFO_STRONG_RE.findall(title)) | (set(_kw_re(topics).findall(title)) if topics else set())
    info_w = set(_INFO_WEAK_RE.findall(title)) - info_s
    waste_s = set(_WASTE_STRONG_RE.findall(title))
    waste_w = set(_WASTE_WEAK_RE.findall(title)) - waste_s
    if video.get("kind") == "short":
        if info_s and not waste_s:
            return "informational", "Short, but the title looks educational"
        return "waste", "YouTube Shorts are built for endless scrolling"

    def why(strong, weak=()):
        return ", ".join((sorted(strong) + sorted(weak))[:3])
    if info_s and not waste_s:
        return "informational", f"Title suggests learning ({why(info_s, info_w)})"
    if waste_s and not info_s:
        return "waste", f"Title suggests entertainment ({why(waste_s, waste_w)})"
    if not info_s and not waste_s:
        if len(info_w) >= 2 and not waste_w:
            return "informational", f"Title suggests learning ({why(info_w)})"
        if len(waste_w) >= 2 and not info_w:
            return "waste", f"Title suggests entertainment ({why(waste_w)})"
    return "unclear", "Not sure from the title alone"


_SUBS_RE = re.compile(r"^(.*?)\s*\b\d[\d.,]*\s*[KMB]?\s+subscribers?\b", re.I)
_YT_BUTTONS = {"subscribe", "subscribed", "join", "share", "download", "clip", "save", "thanks", "remix",
               "like", "dislike", "more", "...", "…", "notifications", "analytics", "edit video"}


def channel_from_screen(text: str | None) -> str:
    """The channel name on a YouTube watch page, from the screen text (the line above "1.2M subscribers")."""
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        m = _SUBS_RE.match(ln)
        if not m:
            continue
        cand = m.group(1).strip()
        j = i - 1
        while not cand and j >= 0 and j >= i - 3:
            c = lines[j].strip()
            if c and c.lower() not in _YT_BUTTONS and not re.fullmatch(r"[\d.,KMB\s]+", c):
                cand = c
            j -= 1
        cand = re.sub(r"[\s✓✔☑︎•·|]+$", "", cand).strip()
        if 2 <= len(cand) <= 60 and not re.search(r"\bviews?\b|\bago\b|subscribers?", cand, re.I):
            return cand
    return ""


def is_optiday_page(url: str, title: str) -> bool:
    """OptiDay's own pages (the live view, reports) on this Mac."""
    return domain_of(url) in ("127.0.0.1", "localhost") and (title or "").lower().startswith("optiday")


# ------------------------------------------------------------- actions ------

def fmt_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%-I:%M %p").lower().replace(" ", "")


def fmt_dur(seconds: float) -> str:
    m = int(round(seconds / 60))
    if m < 1:
        return f"{int(seconds)}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


_SENT_RE = re.compile(r"\b(message sent|your message has been sent|email sent|message was sent|sent!)\b", re.I)
_TO_RE = re.compile(r"^\s*(?:to|recipients)\s*[:：]?\s+(.{3,100})$", re.I)
_SUBJ_RE = re.compile(r"^\s*subject\s*[:：]?\s*(.{2,120})$", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MERGED_RE = re.compile(r"(pull request successfully merged|merged commit [0-9a-f]{7}|successfully merged and closed)",
                        re.I)
_PR_TITLE_RE = re.compile(r"^(.*?)\s+by\s+[\w-]+\s+·\s+Pull Request #(\d+)\s+·\s+([\w.-]+)/([\w.-]+)", re.I)


def _lines(text: str | None) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _short_recipient(s: str) -> str:
    emails = _EMAIL_RE.findall(s)
    if emails:
        return ", ".join(emails[:2]) + (" +" + str(len(emails) - 2) if len(emails) > 2 else "")
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;")
    return s[:60]


def _slack_conversation(title: str) -> str:
    t = clean_title(title)
    m = re.match(r"^(.+?)\s+\((Channel|DM|Private channel|Group DM)\)", t, re.I)
    if m:
        name = m.group(1).strip()
        return f"#{name}" if m.group(2).lower().endswith("channel") and not name.startswith("#") else name
    parts = [p.strip() for p in _SPLIT.split(t) if p.strip()]
    parts = [p for p in parts if p.lower() not in ("slack", "discord", "whatsapp", "messages", "telegram")]
    return parts[0] if parts else ""


def _ai_conversation(title: str) -> str:
    t = clean_title(title)
    t = re.sub(r"\s*[-–—|]\s*(ChatGPT|Claude|Gemini|Perplexity|Grok|Copilot)\s*$", "", t, flags=re.I).strip()
    if t.lower() in ("chatgpt", "claude", "new chat", "gemini", "perplexity", "grok", "copilot", ""):
        return ""
    return t


def ai_tool_name(seg: Segment) -> str:
    d = seg.domain
    for key, name in (("chatgpt", "ChatGPT"), ("openai", "ChatGPT"), ("claude", "Claude"), ("gemini", "Gemini"),
                      ("perplexity", "Perplexity"), ("grok", "Grok"), ("copilot", "Copilot"), ("deepseek", "DeepSeek")):
        if key in d or key in seg.app.lower():
            return name
    return seg.app


SITE_NAMES = {"docs.google.com": "Google Docs", "notion.so": "Notion", "mail.google.com": "Gmail",
              "app.slack.com": "Slack", "meet.google.com": "Google Meet", "figma.com": "Figma",
              "github.com": "GitHub", "linear.app": "Linear", "youtube.com": "YouTube", "x.com": "X",
              "twitter.com": "X", "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT", "claude.ai": "Claude",
              "calendar.google.com": "Google Calendar", "drive.google.com": "Google Drive",
              "sheets.google.com": "Google Sheets", "dashboard.stripe.com": "Stripe", "reddit.com": "Reddit",
              "news.ycombinator.com": "Hacker News", "linkedin.com": "LinkedIn", "outlook.office.com": "Outlook",
              "outlook.live.com": "Outlook", "perplexity.ai": "Perplexity", "gemini.google.com": "Gemini",
              "analytics.google.com": "Google Analytics", "instagram.com": "Instagram", "facebook.com": "Facebook",
              "tiktok.com": "TikTok", "amazon.com": "Amazon", "web.whatsapp.com": "WhatsApp",
              "airtable.com": "Airtable", "hubspot.com": "HubSpot", "app.hubspot.com": "HubSpot",
              "salesforce.com": "Salesforce", "vercel.com": "Vercel", "netflix.com": "Netflix", "twitch.tv": "Twitch"}


def place_name(domain: str, app: str = "", url: str = "") -> str:
    """A friendly name for where time went: "Google Sheets", "YouTube", "Cursor"."""
    if domain:
        if domain == "docs.google.com" and "/spreadsheets/" in (url or ""):
            return "Google Sheets"
        if domain == "docs.google.com" and "/presentation/" in (url or ""):
            return "Google Slides"
        return SITE_NAMES.get(domain, domain)
    return app


def _site_name(seg: Segment) -> str:
    return place_name(seg.domain, seg.app, seg.url) if seg.domain else seg.app


def rule_actions(segments: list[Segment]) -> list[dict]:
    """Detect notable things you did, with exact times, from titles + screen text."""
    actions: list[dict] = []
    active = [s for s in segments if not s.away and not s.private]

    # 1) emails sent (Gmail/Outlook web toasts + Mail.app compose windows closing)
    compose: dict = {}
    prev_lines: set[str] = set()
    prev_key = None
    for i, s in enumerate(active):
        if s.category != "Email":
            continue
        if (s.app, s.title) != prev_key:
            prev_lines = set()
            prev_key = (s.app, s.title)
        for ts, text in s.ocr:
            lines = _lines(text)
            for j, ln in enumerate(lines):
                m = _TO_RE.match(ln)
                if m and len(m.group(1)) > 2:
                    compose = {**compose, "to": _short_recipient(m.group(1)), "ts": ts}
                elif ln.lower() in ("to", "recipients") and j + 1 < len(lines):
                    compose = {**compose, "to": _short_recipient(lines[j + 1]), "ts": ts}
                m = _SUBJ_RE.match(ln)
                if m:
                    compose = {**compose, "subject": m.group(1).strip(), "ts": ts}
            new = [ln for ln in lines if ln not in prev_lines]
            if any(_SENT_RE.search(ln) for ln in new):
                fresh = compose if compose and ts - compose.get("ts", 0) < 900 else {}
                actions.append(_email_action(ts, fresh, _site_name(s)))
                compose = {}
            prev_lines = set(lines)
        # Mail.app: a compose window (title = subject) that you typed in, then closed
        if s.app.lower() in ("mail", "microsoft outlook", "outlook") and s.typing_s >= 5:
            t = clean_title(s.title)
            nxt = active[i + 1] if i + 1 < len(active) else None
            looks_compose = bool(re.match(r"^(re|fwd?|aw|new message)\b", t, re.I)) or "compose" in t.lower()
            if looks_compose and nxt is not None and nxt.app == s.app and nxt.title != s.title \
                    and nxt.start - s.end < 10:
                subj = "" if t.lower().startswith("new message") else t
                info = {**compose, "subject": compose.get("subject") or subj}
                if not any(abs(a["ts"] - s.end) < 60 and a["kind"] == "email" for a in actions):
                    actions.append(_email_action(s.end, info, s.app))
                compose = {}

    # 2) meetings
    groups = _group(active, lambda s: s.category == "Meetings", lambda s: s.domain or s.app, gap=120)
    for g in groups:
        secs = sum(x.seconds for x in g)
        if secs < 120:
            continue
        first, last = g[0], g[-1]
        label = _meeting_label(first)
        actions.append({"ts": first.start, "end": last.end, "kind": "meeting", "source": "rule",
                        "text": f"{label} ({fmt_time(first.start)}–{fmt_time(last.end)}, {fmt_dur(secs)})"})

    # 3) chats where you typed
    chat = _group(active, lambda s: s.category == "Communication" and s.typing_s >= 3,
                  lambda s: (s.app, _slack_conversation(s.title) or s.app), gap=1800)
    for g in chat:
        who = _slack_conversation(g[0].title)
        typed = sum(x.typing_s for x in g)
        where = _site_name(g[0])
        text = f"Messaged {who} on {where}" if who and who.lower() != where.lower() else f"Sent messages on {where}"
        actions.append({"ts": g[0].start, "kind": "message", "source": "rule",
                        "text": f"{text} (typing ~{fmt_dur(typed)})"})

    # 4) AI assistants you prompted
    ai = _group(active, lambda s: s.category == "AI Assistants" and s.typing_s >= 3,
                lambda s: (ai_tool_name(s), _ai_conversation(s.title)), gap=900)
    for g in ai:
        conv = _ai_conversation(g[0].title)
        tool = ai_tool_name(g[0])
        secs = sum(x.seconds for x in g)
        text = f"Worked with {tool}: “{conv}”" if conv else f"Worked with {tool}"
        actions.append({"ts": g[0].start, "kind": "ai", "source": "rule", "text": f"{text} ({fmt_dur(secs)})"})

    # 5) coding sessions per project
    code = _group(active, lambda s: s.category in ("Coding", "Terminal") and bool(s.project),
                  lambda s: s.project, gap=900)
    for g in code:
        secs = sum(x.seconds for x in g)
        if secs < 300:
            continue
        files = []
        for x in g:
            if x.category == "Coding" and not x.domain:
                f = _clean_part(_SPLIT.split(x.title)[0]) if x.title else ""
                if f and _FILE_EXT.search(f) and f not in files:
                    files.append(f)
        extra = f" — {', '.join(files[:4])}{'…' if len(files) > 4 else ''}" if files else ""
        actions.append({"ts": g[0].start, "end": g[-1].end, "kind": "code", "source": "rule",
                        "text": f"Worked on {g[0].project} code for {fmt_dur(secs)}{extra}"})

    # 6) documents you wrote in
    docs = _group(active, lambda s: s.category == "Writing" and s.typing_s >= 20,
                  lambda s: clean_title(s.title), gap=900)
    for g in docs:
        name = re.sub(r"\s[-–—]\s+(Google Docs|Google Sheets|Google Slides|Notion|Microsoft Word)\s*$", "",
                      clean_title(g[0].title))
        secs = sum(x.seconds for x in g)
        if secs < 120 or not name:
            continue
        actions.append({"ts": g[0].start, "kind": "document", "source": "rule",
                        "text": f"Wrote in “{name[:80]}” on {_site_name(g[0])} ({fmt_dur(secs)})"})

    # 7) GitHub merges
    for s in active:
        if s.domain != "github.com":
            continue
        for ts, text in s.ocr:
            if _MERGED_RE.search(text or ""):
                m = _PR_TITLE_RE.match(clean_title(s.title))
                what = f"PR #{m.group(2)} “{m.group(1)}” in {m.group(4)}" if m else "a pull request"
                actions.append({"ts": ts, "kind": "code", "source": "rule", "text": f"Merged {what}"})
                break

    actions.sort(key=lambda a: a["ts"])
    return actions


def _email_action(ts: float, info: dict, where: str) -> dict:
    to = info.get("to")
    subj = info.get("subject")
    text = "Sent an email"
    if to:
        text += f" to {to}"
    if subj:
        text += f" — “{subj[:80]}”"
    text += f" ({where})"
    return {"ts": ts, "kind": "email", "source": "rule", "text": text}


def _meeting_label(s: Segment) -> str:
    t = clean_title(s.title)
    if s.domain == "meet.google.com":
        name = re.sub(r"^Meet\s*[-–—:]\s*", "", t)
        return f"Google Meet: {name}" if name and not re.match(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", name) \
            else "Google Meet call"
    if "zoom" in s.app.lower() or "zoom" in s.domain:
        return "Zoom call" if t.lower() in ("zoom meeting", "zoom", "") else f"Zoom: {t}"
    if s.app.lower() == "facetime":
        return f"FaceTime{': ' + t if t and t.lower() != 'facetime' else ' call'}"
    return f"Meeting on {_site_name(s)}"


def _group(segments, pred, keyfn, gap: float):
    groups: list[list[Segment]] = []
    open_by_key: dict = {}
    for s in segments:
        if not pred(s):
            continue
        k = keyfn(s)
        g = open_by_key.get(k)
        if g and s.start - g[-1].end <= gap:
            g.append(s)
        else:
            g = [s]
            groups.append(g)
            open_by_key[k] = g
    return groups
