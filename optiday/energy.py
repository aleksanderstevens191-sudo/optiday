"""How much energy OptiDay really uses, measured on your Mac.

`optiday energy` samples macOS's own per-process energy numbers (the same "Energy Impact" that
Activity Monitor shows) for 10 seconds and puts OptiDay next to everything else running. It also
prints the hour-by-hour log OptiDay keeps about itself (CPU time, checks, screen reads).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass

_OLLAMA = re.compile(r"^(ollama|ollama_llama_server|llama-server|Ollama)", re.I)


@dataclass
class Proc:
    pid: int
    command: str
    cpu: float
    idlew: float
    power: float


def _num(s: str) -> float:
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else 0.0


def parse_top(text: str) -> list[Proc]:
    """Parse the LAST sample of `top -l N -stats pid,command,cpu,idlew,power` (the first sample's
    CPU numbers are meaningless: they cover no time)."""
    blocks = re.split(r"(?m)^\s*PID\s+COMMAND\s+%CPU", text)
    if len(blocks) < 2:
        return []
    procs = []
    for ln in blocks[-1].splitlines()[1:]:
        tok = ln.split()
        if len(tok) < 5 or not tok[0].isdigit():
            continue
        procs.append(Proc(pid=int(tok[0]), command=" ".join(tok[1:-3]), cpu=_num(tok[-3]), idlew=_num(tok[-2]),
                          power=_num(tok[-1])))
    return procs


def optiday_pids(ps_text: str | None = None, me: int | None = None) -> list[int]:
    """PIDs of the running OptiDay app/recorder (not this `optiday energy` command)."""
    if ps_text is None:
        ps_text = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    me = me if me is not None else os.getpid()
    out = []
    for ln in ps_text.splitlines():
        ln = ln.strip()
        pid, _, cmd = ln.partition(" ")
        if not pid.isdigit() or int(pid) in (me, os.getppid()):
            continue
        if re.search(r"(-m optiday|/optiday|optiday)\s+(app|record)\b", cmd) or re.search(r"-m optiday\s*$", cmd):
            out.append(int(pid))
    return out


def sample(seconds: int = 10, run=subprocess.run) -> list[Proc]:
    r = run(["top", "-l", "2", "-s", str(seconds), "-stats", "pid,command,cpu,idlew,power"],
            capture_output=True, text=True, timeout=seconds + 30)
    return parse_top(r.stdout)


# ---------------------------------------------------------- hourly self-log --

class HourLog:
    """Kept by the running app: OptiDay's own CPU time, checks and screen reads per hour."""

    def __init__(self, store, cpu_fn, stats_fn, clock=time.time):
        self.store, self.cpu_fn, self.stats_fn, self.clock = store, cpu_fn, stats_fn, clock
        self._key = None
        self._base = None

    @staticmethod
    def key_for(ts: float) -> str:
        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H")

    def tick(self, mode: str = "", on_battery: bool | None = None) -> None:
        now = self.clock()
        key = self.key_for(now)
        stats = self.stats_fn()
        cur = (self.cpu_fn(), stats.get("ticks", 0), stats.get("ocr", 0), now)
        if self._key != key or self._base is None:
            if self._key is not None and self._base is not None:
                self._write(self._key, self._base, cur, mode, on_battery)  # close out the previous hour
            self._key, self._base = key, cur
            return
        self._write(key, self._base, cur, mode, on_battery)

    def _write(self, key, base, cur, mode, on_battery) -> None:
        rec = {"cpu_s": round(cur[0] - base[0], 2), "ticks": cur[1] - base[1], "ocr": cur[2] - base[2],
               "secs": round(cur[3] - base[3]), "mode": mode, "battery": on_battery}
        self.store.set_meta(f"energy:{key}", json.dumps(rec))


def hour_rows(store, hours: int = 24, now: float | None = None) -> list[tuple[str, dict]]:
    now = now or time.time()
    rows = []
    for i in range(hours - 1, -1, -1):
        ts = now - i * 3600
        k = HourLog.key_for(ts)
        v = store.get_meta(f"energy:{k}")
        if v:
            try:
                rows.append((dt.datetime.fromtimestamp(ts).strftime("%-I%p").lower(), json.loads(v)))
            except ValueError:
                continue
    return rows


# ----------------------------------------------------------------- report --

def report(store=None, seconds: int = 10, run=subprocess.run, ps_text: str | None = None,
           power_line: str = "") -> str:
    when = dt.datetime.now().strftime("%a %b %-d, %-I:%M %p")
    lines = [f"OptiDay energy check — {when} ({seconds}-second sample)"]
    if power_line:
        lines.append(power_line)
    pids = set(optiday_pids(ps_text))
    procs = sample(seconds, run=run)
    if not procs:
        lines.append("Couldn't read macOS energy numbers (top gave no output).")
    else:
        mine = [p for p in procs if p.pid in pids]
        ai = [p for p in procs if _OLLAMA.match(p.command)]
        others = [p for p in procs if p not in mine and p not in ai]
        total_power = sum(p.power for p in procs) or 1.0

        def row(label, ps):
            return (f"  {label:<26}{sum(p.cpu for p in ps):>6.1f}%{sum(p.power for p in ps):>10.1f}"
                    f"{sum(p.idlew for p in ps):>10.0f}")
        lines.append("")
        lines.append(f"  {'':<26}{'CPU':>7}{'Energy':>10}{'Wakeups':>10}")
        if mine:
            lines.append(row(f"OptiDay (pid {', '.join(str(p.pid) for p in mine)})", mine))
        else:
            lines.append("  OptiDay                   not running (start it from Launchpad or: optiday restart)")
        lines.append(row("Local AI (Ollama)", ai) + ("" if ai else "   (not loaded)"))
        lines.append(row("Everything else", others))
        share = 100 * sum(p.power for p in mine) / total_power
        lines.append("")
        if mine:
            lines.append(f"OptiDay = {share:.1f}% of this Mac's total energy use right now.")
        top = sorted(others, key=lambda p: -p.power)[:5]
        lines.append("Biggest users right now: " + ", ".join(f"{p.command} {p.power:.1f}" for p in top))
    if store is not None:
        rows = hour_rows(store)
        if rows:
            lines.append("")
            lines.append("OptiDay's own log, last 24 hours:")
            lines.append(f"  {'hour':<6}{'mode':<14}{'CPU time':>9}{'checks':>8}{'screen reads':>14}")
            cpu = secs = 0.0
            for label, r in rows:
                lines.append(f"  {label:<6}{(r.get('mode') or '?'):<14}{r['cpu_s']:>8.1f}s{r['ticks']:>8}"
                             f"{r['ocr']:>14}")
                cpu += r["cpu_s"]
                secs += r.get("secs") or 3600
            if secs:
                lines.append(f"  Average: {100 * cpu / secs:.2f}% of one CPU core "
                             f"({cpu / max(1, secs / 3600):.1f} CPU-seconds per hour).")
    lines.append("")
    lines.append("Energy = macOS 'Energy Impact' (same as Activity Monitor). Paste this whole block to Claude.")
    return "\n".join(lines)


def powermetrics(pids: list[int], seconds: int = 10) -> str:
    """Optional, needs your password: Apple's detailed per-process energy sampler."""
    r = subprocess.run(["sudo", "powermetrics", "--samplers", "tasks", "--show-process-energy",
                        "-i", str(seconds * 1000), "-n", "1"], capture_output=True, text=True,
                       timeout=seconds + 60)
    keep = []
    for ln in r.stdout.splitlines():
        low = ln.lower()
        if ln.strip().startswith("Name") or "ollama" in low or "python" in low or \
                any(re.search(rf"\b{p}\b", ln) for p in pids) or low.startswith("all_tasks"):
            keep.append(ln)
    return "\n".join(keep) or (r.stderr.strip() or "powermetrics gave no output")
