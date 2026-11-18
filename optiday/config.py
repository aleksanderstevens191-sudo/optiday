"""Configuration and data locations.

The default config below is the single source of truth: it is written to disk
on first run (so the user can edit it) and also parsed to provide defaults for
any key the user's file leaves out.
"""
from __future__ import annotations

import copy
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_TOML = """\
# OptiDay settings. Edit this file, then choose "Restart OptiDay" from the menu bar
# (or run: optiday restart). Everything stays on this Mac unless you point [ai] at
# your own private server.

[recording]
# Fastest check rate while you're actively switching things (seconds). App switches are
# picked up instantly anyway; when nothing changes OptiDay backs off to save battery.
interval_seconds = 5
# While plugged in, how often to re-read the screen of an email/chat/AI/doc window you're
# actively using (seconds). Every new window or tab is read once when you open it.
ocr_interval_seconds = 20
# Read on-screen text (Apple's built-in OCR). Needed for "sent email to X at 2:14pm".
ocr_enabled = true
# No mouse/keyboard input for this long = away (videos and calls still count as active).
idle_threshold_seconds = 120
# Your day starts at this hour, so late nights count toward the previous day.
day_starts_at_hour = 4
# Keep raw on-screen text this many days. Reports and stats are kept forever.
retention_days = 30
# Screenshots are deleted right after OCR. Set true only if you want to keep them.
keep_screenshots = false

[power]
# "auto"  = full detail when plugged in, battery saver when unplugged (recommended)
# "saver" = battery saver all the time
# "max"   = full detail all the time
mode = "auto"
# Run the local AI on battery? Off by default: reports built on battery get their AI
# summary automatically the next time you plug in.
ai_on_battery = false
# Below this battery level (or in Low Power Mode) OptiDay stops reading the screen and only
# keeps app/window/site names.
screen_reading_off_below_percent = 30

[privacy]
# Nothing is captured from these apps except "you used it for N minutes".
excluded_apps = ["1Password", "1Password 7", "Bitwarden", "LastPass", "Dashlane", "Keychain Access", "Passwords"]
# Same for any browser tab whose address contains one of these.
excluded_url_keywords = ["bank", "chase.com", "wellsfargo", "paypal.com", "venmo.com", "robinhood.com", "schwab.com", "fidelity.com", "coinbase.com", "accounts.google.com", "login", "signin", "password"]
# Skip incognito / private browsing windows entirely.
skip_private_windows = true

[ai]
# "ollama"  = a local model on this Mac (default) or on your private VM (via `optiday connect-vm`)
# "openai"  = any OpenAI-compatible server you run yourself (vLLM, llama.cpp, LM Studio...)
# "none"    = no AI; reports use built-in rules only
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen2.5:7b"
api_key = ""
timeout_seconds = 240
# Analyze finished chunks of your day every hour so the evening report is instant.
background_analysis = true

[report]
# When the end-of-day report is generated (24h, local time).
time = "18:00"
notify = true
open_automatically = true
# Local dashboard (only reachable from this Mac).
dashboard_port = 8765
# A stretch of productive work at least this long counts as a focus block.
focus_block_minutes = 25
# Pop up a one-line summary of the previous hour, every hour.
hourly_notification = false
# Morning: the first time you use your Mac after this time, OptiDay gets your day ready
# (meetings, emails that need you, drafts, where you left off, agent tasks).
# "open" = open the Today page, "notify" = just a notification, "off" = nothing.
morning_brief = "open"
morning_brief_after = "06:00"

[live]
# The live view (menu bar → Live view) for a side monitor.
# Daily goal for deep focus (stretches of 25+ minutes of productive work).
focus_goal_hours = 4
# How much distracting time per day before the live view warns you.
distraction_budget_minutes = 30
# During a focus session, notify you after this long on something distracting.
drift_alert_seconds = 60

[email]
# Optional. Lets OptiDay see the emails you sent (exact times) and the ones waiting for your
# reply, so it can prepare drafts for tomorrow. Set up with:  optiday connect-email
# Mail is read over an encrypted connection straight to this Mac, kept encrypted, and only
# summarized by your own AI. OptiDay never sends, deletes or marks anything.
enabled = false
address = ""
imap_host = ""
days_back = 3

[calendar]
# Optional. Paste your calendar's private "iCal" address(es) so tomorrow's meetings show up in
# your plan. Google Calendar: Settings → your calendar → "Secret address in iCal format".
# iCloud: Calendar app → share calendar → Public. Or run: optiday connect-calendar
ics_urls = []

[agents]
# OptiDay spots tedious things you repeat and suggests handing them to an agent.
# NOTHING runs until you click Approve on the suggestion. Approved tasks run at this time the
# next morning (or immediately with "Run now").
default = "claude"
run_at = "09:00"

[agents.claude]
# Claude Code in headless mode. Add "--permission-mode", "acceptEdits" to let it edit files.
type = "command"
command = ["claude", "-p", "{task}"]
workdir = "~"
timeout_minutes = 30

[agents.webhook]
# Your own agent / n8n / Zapier endpoint. Receives JSON: {"task": ..., "title": ..., "why": ...}
type = "webhook"
url = ""

[profile]
# Helps the AI judge what is useful vs. a waste of time for YOU. Edit freely.
about_me = "Founder and builder. I ship software products with AI coding tools (I don't hand-write code), run several side projects and startup ideas at once, and follow AI, startups, investing and markets."
goals = [
  "Ship my products faster",
  "Spend more time building and less time on low-value browsing",
  "Learn things that directly help my projects",
]
# Video topics that count as useful for you even if they look like entertainment.
useful_video_topics = ["AI", "coding", "startups", "investing", "markets", "productivity", "business"]

[projects]
# Map project names to keywords seen in window titles, repo names, URLs or doc titles.
# Projects are also discovered automatically (git repos, editor folders, GitHub URLs).
# Example:  "Client X" = ["clientx", "acme corp"]
OptiDay = ["optiday"]

[categories]
# Override the category of an app or website. Categories:
# Coding, Terminal, AI Assistants, Writing, Design, Research, Learning, Planning,
# Email, Communication, Meetings, Browsing, Video, Finance, Social, Entertainment,
# News, Shopping, Games, System, Other
# Example:  "linkedin.com" = "Communication"
"""


def _parse_defaults() -> dict[str, Any]:
    return tomllib.loads(DEFAULT_CONFIG_TOML)


DEFAULTS = _parse_defaults()


def data_dir() -> Path:
    env = os.environ.get("OPTIDAY_HOME")
    if env:
        p = Path(env).expanduser()
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "OptiDay"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        p = Path(base) / "optiday"
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def config_path() -> Path:
    return data_dir() / "config.toml"


def reports_dir() -> Path:
    p = data_dir() / "reports"
    p.mkdir(exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(exist_ok=True)
    return p


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("projects", "categories"):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """A dict of sections with defaults filled in."""

    @property
    def rec(self) -> dict:
        return self["recording"]

    @property
    def ai(self) -> dict:
        return self["ai"]

    @property
    def privacy(self) -> dict:
        return self["privacy"]

    @property
    def report(self) -> dict:
        return self["report"]

    @property
    def profile(self) -> dict:
        return self["profile"]

    @property
    def projects(self) -> dict[str, list[str]]:
        return self.get("projects") or {}

    @property
    def categories(self) -> dict[str, str]:
        return self.get("categories") or {}


def load_config(path: Path | None = None, write_default: bool = True) -> Config:
    path = path or config_path()
    if not path.exists():
        if write_default:
            path.write_text(DEFAULT_CONFIG_TOML)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return Config(copy.deepcopy(DEFAULTS))
    try:
        user = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:  # keep running with defaults, but say why
        print(f"[optiday] config.toml has an error ({e}); using defaults", file=sys.stderr)
        user = {}
    return Config(_deep_merge(DEFAULTS, user))


def set_config_value(section: str, key: str, value: Any, path: Path | None = None) -> None:
    """Change one `key = value` line inside `[section]`, keeping comments intact."""
    path = path or config_path()
    if not path.exists():
        load_config(path)
    text = path.read_text()
    rendered = _toml_value(value)
    lines = text.splitlines()
    out, in_section, done = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not done:
                out.append(f"{key} = {rendered}")
                done = True
            in_section = stripped.strip("[]").strip() == section
        if in_section and not done and re.match(rf"^\s*{re.escape(key)}\s*=", line):
            out.append(f"{key} = {rendered}")
            done = True
            continue
        out.append(line)
    if not done:
        if not in_section:
            out.append(f"\n[{section}]")
        out.append(f"{key} = {rendered}")
    path.write_text("\n".join(out) + "\n")


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'
