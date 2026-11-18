import pytest

from optiday import rules


@pytest.mark.parametrize("app,bundle,title,url,expected", [
    ("Code", "com.microsoft.VSCode", "recorder.py — optiday — Visual Studio Code", "", "optiday"),
    ("Code", "com.microsoft.VSCode", "● main.py - backend-api - Visual Studio Code", "", "backend-api"),
    ("Code", "com.microsoft.VSCode", "Welcome — Visual Studio Code", "", ""),
    ("Cursor", "com.todesktop.230313mzl4w4u92", "strategy.py — AutoTrader — Cursor", "", "AutoTrader"),
    ("Cursor", "com.todesktop.230313mzl4w4u92", "app.tsx — web [SSH: box] — Cursor", "", "web"),
    ("Xcode", "com.apple.dt.Xcode", "OptiDayMac — ContentView.swift", "", "OptiDayMac"),
    ("PyCharm", "com.jetbrains.pycharm", "stocks – analysis.py", "", "stocks"),
    ("Terminal", "com.apple.Terminal", "sam@mac: ~/code/side-project", "", "side-project"),
    ("iTerm2", "com.googlecode.iterm2", "~/Desktop", "", ""),
    ("Terminal", "com.apple.Terminal", "optiday — -zsh — 120×40", "", "optiday"),
    ("Google Chrome", "com.google.Chrome", "PR", "https://github.com/sam/optiday/pull/12", "optiday"),
    ("Google Chrome", "com.google.Chrome", "Notifications", "https://github.com/notifications", ""),
    ("Google Chrome", "com.google.Chrome", "x", "https://vercel.com/sam/acme-web", "acme-web"),
])
def test_project_from_window(app, bundle, title, url, expected):
    assert rules.project_from_window(app, bundle, title, url) == expected


def test_match_known_project_uses_config_then_discovered():
    projects = {"Client X": ["acme corp"]}
    assert rules.match_known_project("Acme Corp invoice - Google Docs", projects, []) == "Client X"
    assert rules.match_known_project("OptiDay pricing plan - Google Docs", {}, ["optiday"]) == "optiday"
    assert rules.match_known_project("my web notes", {}, ["web"]) == ""  # too generic


@pytest.mark.parametrize("url,title,vid,kind", [
    ("https://www.youtube.com/watch?v=abc123&t=30", "(3) Learn Swift in 1 hour - YouTube", "abc123", "video"),
    ("https://youtu.be/xyz", "Thing - YouTube", "xyz", "video"),
    ("https://www.youtube.com/shorts/s9H", "Shorts - YouTube", "s9H", "short"),
    ("https://www.youtube.com/", "YouTube", None, None),
    ("", "Funny cats - YouTube — Mozilla Firefox", "t-", "video"),
])
def test_youtube_video(url, title, vid, kind):
    v = rules.youtube_video(url, title)
    if vid is None:
        assert v is None
    else:
        assert v["id"].startswith(vid) and v["kind"] == kind
        assert "YouTube" not in v["title"] or kind == "short"


def test_youtube_title_cleanup():
    v = rules.youtube_video("https://www.youtube.com/watch?v=a", "(3) Learn Swift in 1 hour - YouTube")
    assert v["title"] == "Learn Swift in 1 hour"


@pytest.mark.parametrize("title,label", [
    ("Building a SaaS with Claude Code - Full Tutorial", "informational"),
    ("Try Not To Laugh Challenge #47", "waste"),
    ("MrBeast: I Survived 24 Hours in a Desert", "waste"),
    ("Untitled video", "unclear"),
])
def test_video_heuristic(title, label):
    assert rules.classify_video_heuristic({"title": title, "kind": "video"})[0] == label


def test_shorts_are_waste_by_default():
    assert rules.classify_video_heuristic({"title": "YouTube Shorts", "kind": "short"})[0] == "waste"


@pytest.mark.parametrize("app,bundle,url,cat", [
    ("Google Chrome", "com.google.Chrome", "https://mail.google.com/mail/u/0/#inbox", "Email"),
    ("Google Chrome", "com.google.Chrome", "https://www.youtube.com/", "Entertainment"),
    ("Google Chrome", "com.google.Chrome", "https://www.youtube.com/watch?v=1", "Video"),
    ("Google Chrome", "com.google.Chrome", "https://docs.google.com/spreadsheets/d/1", "Writing"),
    ("Google Chrome", "com.google.Chrome", "https://www.google.com/search?q=x", "Research"),
    ("Safari", "com.apple.Safari", "https://x.com/home", "Social"),
    ("Safari", "com.apple.Safari", "https://some-unknown-site.dev", "Browsing"),
    ("Cursor", "com.todesktop.230313mzl4w4u92", "", "Coding"),
    ("Slack", "com.tinyspeck.slackmacgap", "", "Communication"),
    ("Mystery", "com.example.mystery", "", "Other"),
])
def test_categorize(app, bundle, url, cat):
    assert rules.categorize(app, bundle, url) == cat


def test_category_override():
    assert rules.categorize("Safari", "com.apple.Safari", "https://www.linkedin.com/feed",
                            {"linkedin.com": "Communication"}) == "Communication"


def test_passive_media():
    assert rules.is_passive_media("Google Chrome", "https://www.youtube.com/watch?v=1")
    assert rules.is_passive_media("zoom.us", "")
    assert not rules.is_passive_media("Google Chrome", "https://www.youtube.com/")
    assert not rules.is_passive_media("Code", "")


def test_excluded(cfg):
    assert rules.is_excluded("1Password", "", "", cfg)
    assert rules.is_excluded("Safari", "https://secure.chase.com/x", "", cfg)
    assert not rules.is_excluded("Safari", "https://github.com", "", cfg)


def test_clean_title():
    assert rules.clean_title("(12) Inbox - Gmail - Google Chrome - Sam") == "Inbox - Gmail"
    assert rules.clean_title("Funny - YouTube — Mozilla Firefox") == "Funny - YouTube"


def test_slack_and_ai_titles():
    assert rules._slack_conversation("general (Channel) - Builders - Slack") == "#general"
    assert rules._slack_conversation("Jordan Kim (DM) - Builders - Slack") == "Jordan Kim"
    assert rules._ai_conversation("Python async help - ChatGPT") == "Python async help"
    assert rules._ai_conversation("Claude") == ""
