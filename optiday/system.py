"""macOS integration: start at login (launchd), SSH tunnel to a private VM,
and the permission/health check."""
from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from .config import config_path, data_dir, load_config, logs_dir, set_config_value

AGENT_LABEL = "com.optiday.agent"
TUNNEL_LABEL = "com.optiday.vm-tunnel"
TUNNEL_LOCAL_PORT = 11435


def _agents_dir() -> Path:
    p = Path.home() / "Library" / "LaunchAgents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _plist_path(label: str) -> Path:
    return _agents_dir() / f"{label}.plist"


def agent_plist(python: str | None = None) -> dict:
    python = python or sys.executable
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": [python, "-m", "optiday", "app"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "LowPriorityIO": True,
        "LimitLoadToSessionType": "Aqua",
        "ThrottleInterval": 20,
        "StandardOutPath": str(logs_dir() / "optiday.out.log"),
        "StandardErrorPath": str(logs_dir() / "optiday.err.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install_agent(label: str = AGENT_LABEL, plist: dict | None = None) -> Path:
    plist = plist or agent_plist()
    path = _plist_path(label)
    if path.exists():
        _launchctl("bootout", _domain(), str(path))
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    r = _launchctl("bootstrap", _domain(), str(path))
    if r.returncode != 0:  # older macOS fallback
        _launchctl("load", "-w", str(path))
    return path


def uninstall_agent(label: str = AGENT_LABEL) -> None:
    path = _plist_path(label)
    if path.exists():
        _launchctl("bootout", _domain(), str(path))
        path.unlink()


def restart_agent() -> bool:
    r = _launchctl("kickstart", "-k", f"{_domain()}/{AGENT_LABEL}")
    return r.returncode == 0


def agent_running() -> bool:
    r = _launchctl("print", f"{_domain()}/{AGENT_LABEL}")
    return r.returncode == 0 and "state = running" in r.stdout


# --------------------------------------------------------------- private VM --

def connect_vm(target: str, ssh_port: int = 22, remote_port: int = 11434, identity: str = "",
               model: str = "") -> str:
    """Keep an encrypted SSH tunnel open to Ollama on your own server and point OptiDay at it.
    Ollama on the server stays bound to 127.0.0.1, so it is never exposed to the internet."""
    args = ["/usr/bin/ssh", "-N", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
            "-L", f"127.0.0.1:{TUNNEL_LOCAL_PORT}:127.0.0.1:{remote_port}", "-p", str(ssh_port)]
    if identity:
        args += ["-i", os.path.expanduser(identity)]
    args.append(target)
    install_agent(TUNNEL_LABEL, {
        "Label": TUNNEL_LABEL, "ProgramArguments": args, "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 15,
        "StandardErrorPath": str(logs_dir() / "vm-tunnel.err.log"),
    })
    set_config_value("ai", "provider", "ollama")
    set_config_value("ai", "base_url", f"http://127.0.0.1:{TUNNEL_LOCAL_PORT}")
    if model:
        set_config_value("ai", "model", model)
    return " ".join(shlex.quote(a) for a in args)


def disconnect_vm() -> None:
    uninstall_agent(TUNNEL_LABEL)
    set_config_value("ai", "base_url", "http://127.0.0.1:11434")


# ------------------------------------------------------------------ doctor --

def doctor(request: bool = False) -> list[tuple[bool, str, str]]:
    """Returns (ok, check, how-to-fix) rows."""
    from .llm import make_llm
    cfg = load_config()
    rows: list[tuple[bool, str, str]] = []
    rows.append((sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", "Re-run ./install.sh"))
    rows.append((True, f"Data folder: {data_dir()}", ""))
    rows.append((True, f"Settings: {config_path()}", ""))
    if sys.platform == "darwin":
        from .capture import macos
        sr = macos.screen_recording_granted()
        if not sr and request:
            macos.request_screen_recording()
            macos.open_privacy_pane("ScreenCapture")
        rows.append((sr, "Screen Recording permission (window titles + on-screen text)",
                     "System Settings → Privacy & Security → Screen & System Audio Recording → turn on "
                     f"'{Path(sys.executable).resolve().name}' (or the app shown), then choose Restart OptiDay"))
        ax = macos.accessibility_granted(prompt=False)
        rows.append((ax, "Accessibility permission (optional: better titles for a few apps)",
                     "System Settings → Privacy & Security → Accessibility → turn on Python"))
        fv = subprocess.run(["fdesetup", "status"], capture_output=True, text=True).stdout
        rows.append(("On" in fv, "FileVault disk encryption",
                     "Recommended: System Settings → Privacy & Security → FileVault → Turn On"))
        rows.append((agent_running(), "OptiDay starts automatically at login",
                     "Run ./install.sh again, or: optiday install-agent"))
    from .power import PowerMonitor
    st = PowerMonitor(cfg).state()
    pol = PowerMonitor(cfg, probe=lambda: st).policy()
    where = "on battery" + (f" ({st.percent}%)" if st.percent is not None else "") if st.on_battery else "plugged in"
    rows.append((True, f"Power: {where} → {pol.name} mode"
                       f" ({'screen reading on' if pol.ocr else 'screen reading off'},"
                       f" {'AI allowed now' if pol.ai else 'AI waits until you plug in'})", ""))
    ec = cfg.get("email", {}) or {}
    if ec.get("enabled"):
        from . import vault
        has_pw = bool(vault.get_secret(f"email:{ec.get('address', '')}"))
        rows.append((has_pw, f"Email: reading {ec.get('address')} (read-only)",
                     "Run: optiday connect-email"))
    else:
        rows.append((True, "Email: not connected (optional: optiday connect-email)", ""))
    urls = (cfg.get("calendar", {}) or {}).get("ics_urls", [])
    rows.append((True, f"Calendar: {len(urls)} connected" if urls
                 else "Calendar: not connected (optional: optiday connect-calendar)", ""))
    agent = (cfg.get("agents", {}) or {}).get("default", "claude")
    conf = (cfg.get("agents", {}) or {}).get(agent) or {}
    if conf.get("type") == "command" and conf.get("command"):
        import shutil
        from .automations import _env
        found = shutil.which(conf["command"][0], path=_env()["PATH"])
        rows.append((bool(found), f"Agent '{agent}': {'found ' + found if found else conf['command'][0] + ' not found'}",
                     "Install Claude Code (npm install -g @anthropic-ai/claude-code) or change [agents] in Settings"))
    llm = make_llm(cfg)
    if llm is None:
        rows.append((True, "AI analysis is off (provider = none); using built-in rules", ""))
    else:
        ok, msg = llm.status()
        rows.append((ok, msg, f"Start Ollama (open the Ollama app) and run: ollama pull {cfg.ai['model']}"))
    return rows
