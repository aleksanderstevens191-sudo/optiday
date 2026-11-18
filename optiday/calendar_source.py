"""Calendar awareness from your calendar's private iCal (.ics) address.

Read-only: OptiDay downloads the feed straight from your calendar provider to this Mac and
never writes to it. Handles what real Google/iCloud/Outlook feeds contain: folded lines,
time zones, all-day events, recurring events (daily/weekly/monthly/yearly, with interval,
count, until, weekdays like "2nd Tuesday", month days), cancelled single occurrences and moved
occurrences.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo

log = logging.getLogger("optiday.calendar")

_WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_MEET_URL = re.compile(r"https://[^\s<>\"']*(zoom\.us/j|meet\.google\.com|teams\.microsoft\.com|webex\.com|"
                       r"around\.co|whereby\.com|cal\.com|facetime\.apple\.com)[^\s<>\"']*", re.I)
# Windows zone names some Outlook feeds use
_WIN_TZ = {"Pacific Standard Time": "America/Los_Angeles", "Mountain Standard Time": "America/Denver",
           "Central Standard Time": "America/Chicago", "Eastern Standard Time": "America/New_York",
           "GMT Standard Time": "Europe/London", "W. Europe Standard Time": "Europe/Berlin",
           "Romance Standard Time": "Europe/Paris", "India Standard Time": "Asia/Kolkata",
           "China Standard Time": "Asia/Shanghai", "Tokyo Standard Time": "Asia/Tokyo",
           "AUS Eastern Standard Time": "Australia/Sydney", "UTC": "UTC"}


@dataclass
class Event:
    uid: str
    title: str
    start: float
    end: float
    all_day: bool = False
    location: str = ""
    description: str = ""
    attendees: list[str] = field(default_factory=list)
    organizer: str = ""
    meet_url: str = ""
    calendar: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- parsing --

def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out


def _split_prop(line: str) -> tuple[str, dict[str, str], str]:
    # NAME;PARAM=a;PARAM2="b:c":value   (colons may appear inside quoted params)
    i, q = 0, False
    while i < len(line):
        c = line[i]
        if c == '"':
            q = not q
        elif c == ":" and not q:
            break
        i += 1
    head, value = line[:i], line[i + 1:]
    parts = re.findall(r'(?:[^;"]|"[^"]*")+', head)
    name = parts[0].upper() if parts else ""
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return name, params, value


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _zone(tzid: str | None, default: dt.tzinfo):
    if not tzid:
        return default
    tzid = _WIN_TZ.get(tzid, tzid)
    # Some feeds use "/mozilla.org/20070129_1/Europe/Berlin" style ids
    for cand in (tzid, "/".join(tzid.split("/")[-2:]), tzid.split("/")[-1]):
        try:
            return ZoneInfo(cand)
        except Exception:  # noqa: BLE001 - unknown/odd zone ids
            continue
    return default


def _parse_dt(value: str, params: dict, local: dt.tzinfo) -> tuple[dt.datetime, bool]:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = dt.datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=local), True
    if value.endswith("Z"):
        return dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc), False
    return dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=_zone(params.get("TZID"), local)), False


def _duration(v: str) -> dt.timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return dt.timedelta(0)
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return sign * dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def local_zone() -> dt.tzinfo:
    """This Mac's time zone, DST-aware (a fixed offset would shift events after a clock change)."""
    import os
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        try:
            target = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in target:
                name = target.split("zoneinfo/", 1)[1]
        except OSError:
            name = ""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            pass
    return dt.datetime.now().astimezone().tzinfo


def parse_ics(text: str, local: dt.tzinfo | None = None, calendar: str = "") -> list[dict]:
    """Returns raw VEVENT dicts with python datetimes (recurrences not yet expanded)."""
    local = local or local_zone()
    events, cur, depth = [], None, 0
    cal_name = calendar
    for line in _unfold(text):
        if not line.strip():
            continue
        name, params, value = _split_prop(line)
        if name == "BEGIN":
            if value.upper() == "VEVENT":
                cur = {"exdates": [], "attendees": [], "calendar": cal_name}
                depth = 0
            elif cur is not None:
                depth += 1  # VALARM etc. inside an event
            continue
        if name == "END":
            if value.upper() == "VEVENT" and cur is not None:
                if "start" in cur:
                    events.append(cur)
                cur = None
            elif cur is not None:
                depth -= 1
            continue
        if cur is None:
            if name == "X-WR-CALNAME" and not calendar:
                cal_name = _unescape(value)
            continue
        if depth > 0:
            continue
        try:
            if name == "DTSTART":
                cur["start"], cur["all_day"] = _parse_dt(value, params, local)
            elif name == "DTEND":
                cur["end"], _ = _parse_dt(value, params, local)
            elif name == "DURATION":
                cur["duration"] = _duration(value)
            elif name == "SUMMARY":
                cur["title"] = _unescape(value).strip()
            elif name == "LOCATION":
                cur["location"] = _unescape(value).strip()
            elif name == "DESCRIPTION":
                cur["description"] = _unescape(value).strip()
            elif name == "UID":
                cur["uid"] = value.strip()
            elif name == "STATUS":
                cur["status"] = value.strip().upper()
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.strip().split(";") if "=" in p)
            elif name == "EXDATE":
                for v in value.split(","):
                    if v.strip():
                        cur["exdates"].append(_parse_dt(v, params, local)[0])
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"], _ = _parse_dt(value, params, local)
            elif name == "ATTENDEE":
                who = params.get("CN") or re.sub(r"(?i)^mailto:", "", value)
                if who and params.get("PARTSTAT", "").upper() != "DECLINED":
                    cur["attendees"].append(who.strip())
            elif name == "ORGANIZER":
                cur["organizer"] = params.get("CN") or re.sub(r"(?i)^mailto:", "", value)
            elif name == "URL":
                cur["url"] = value.strip()
        except (ValueError, KeyError) as e:
            log.debug("skipping bad calendar line %r: %s", line[:80], e)
    return events


# ------------------------------------------------------------- recurrence --

def _add_months(d: dt.datetime, n: int) -> dt.datetime | None:
    y, m = divmod(d.month - 1 + n, 12)
    try:
        return d.replace(year=d.year + y, month=m + 1)
    except ValueError:
        return None  # e.g. Jan 31 + 1 month: RFC 5545 skips invalid dates


def _nth_weekday(year: int, month: int, wd: int, n: int, like: dt.datetime) -> dt.datetime | None:
    if n > 0:
        first = dt.date(year, month, 1)
        day = 1 + (wd - first.weekday()) % 7 + 7 * (n - 1)
    else:
        nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
        last = nxt - dt.timedelta(days=1)
        day = last.day - (last.weekday() - wd) % 7 + 7 * (n + 1)
    try:
        return like.replace(year=year, month=month, day=day)
    except ValueError:
        return None


def _occurrences(ev: dict, win_start: dt.datetime, win_end: dt.datetime, limit: int = 3000):
    """Yield start datetimes of an event within [win_start, win_end)."""
    start: dt.datetime = ev["start"]
    rule = ev.get("rrule")
    if not rule:
        yield start
        return
    freq = rule.get("FREQ", "").upper()
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        u, is_date = _parse_dt(rule["UNTIL"], {}, start.tzinfo)
        until = u + dt.timedelta(days=1) - dt.timedelta(seconds=1) if is_date else u
    byday = [d.strip() for d in rule.get("BYDAY", "").split(",") if d.strip()]
    bymonthday = [int(x) for x in rule.get("BYMONTHDAY", "").split(",") if x.strip().lstrip("-").isdigit()]
    bymonth = [int(x) for x in rule.get("BYMONTH", "").split(",") if x.strip().isdigit()]
    n = 0
    # Without COUNT we can jump straight to the window instead of walking from (say) 2017.
    ff = count is None and win_start > start
    if count is not None:
        limit = 200_000  # COUNT ends the loop

    def emit(c: dt.datetime):
        nonlocal n
        if c < start:
            return None
        if until and c > until:
            return False
        n += 1
        if count is not None and n > count:
            return False
        return True

    i = 0
    if freq == "DAILY":
        if ff:
            i = max(0, (win_start - start).days // interval - 1)
        limit += i
        while i < limit:
            c = start + dt.timedelta(days=i * interval)
            i += 1
            if bymonth and c.month not in bymonth:
                continue
            if byday and not any(_WD.get(d[-2:]) == c.weekday() for d in byday):
                continue
            ok = emit(c)
            if ok is False or c >= win_end:
                return
            if ok and c + ev["length"] > win_start:
                yield c
    elif freq == "WEEKLY":
        days = sorted({_WD[d[-2:]] for d in byday if d[-2:] in _WD}) or [start.weekday()]
        week0 = start - dt.timedelta(days=start.weekday())
        if ff:
            i = max(0, (win_start - week0).days // 7 // interval - 1)
        limit += i
        while i < limit:
            wk = week0 + dt.timedelta(weeks=i * interval)
            i += 1
            for wd in days:
                c = wk + dt.timedelta(days=wd)
                ok = emit(c)
                if ok is None:
                    continue
                if ok is False or c >= win_end:
                    return
                if c + ev["length"] > win_start:
                    yield c
    elif freq in ("MONTHLY", "YEARLY"):
        step = interval if freq == "MONTHLY" else 12 * interval
        months = bymonth if freq == "YEARLY" and bymonth else None
        if ff:
            diff = (win_start.year - start.year) * 12 + win_start.month - start.month
            i = max(0, diff // step - 1)
        limit += i
        while i < limit:
            base = start.replace(day=1) if (byday or bymonthday) else start
            anchor = _add_months(base, i * step)
            i += 1
            if anchor is None:
                continue
            month_list = [anchor.replace(day=1, month=m) for m in months] if months else [anchor]
            for mbase in month_list:
                cands: list[dt.datetime] = []
                if byday:
                    for d in byday:
                        mm = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", d)
                        if not mm:
                            continue
                        wd = _WD[mm.group(2)]
                        if mm.group(1):
                            c = _nth_weekday(mbase.year, mbase.month, wd, int(mm.group(1)), start)
                            if c:
                                cands.append(c)
                        else:
                            for k in range(1, 6):
                                c = _nth_weekday(mbase.year, mbase.month, wd, k, start)
                                if c and c.month == mbase.month:
                                    cands.append(c)
                elif bymonthday:
                    for md in bymonthday:
                        try:
                            if md > 0:
                                cands.append(start.replace(year=mbase.year, month=mbase.month, day=md))
                            else:
                                nxt = dt.date(mbase.year + (mbase.month == 12), mbase.month % 12 + 1, 1)
                                last = (nxt - dt.timedelta(days=1)).day
                                cands.append(start.replace(year=mbase.year, month=mbase.month, day=last + md + 1))
                        except ValueError:
                            continue
                else:
                    cands.append(mbase)
                for c in sorted(set(cands)):
                    ok = emit(c)
                    if ok is None:
                        continue
                    if ok is False or c >= win_end:
                        return
                    if c + ev["length"] > win_start:
                        yield c
    else:
        yield start


def events_between(raw: list[dict], win_start: dt.datetime, win_end: dt.datetime) -> list[Event]:
    """Expand recurring events and return the concrete events overlapping the window."""
    overrides: dict[tuple[str, float], dict] = {}
    for ev in raw:
        if ev.get("recurrence_id") is not None and ev.get("uid"):
            overrides[(ev["uid"], ev["recurrence_id"].timestamp())] = ev
    out: list[Event] = []
    for ev in raw:
        if ev.get("recurrence_id") is not None:
            continue
        start = ev["start"]
        if "end" in ev:
            length = ev["end"] - start
        elif "duration" in ev:
            length = ev["duration"]
        else:
            length = dt.timedelta(days=1) if ev.get("all_day") else dt.timedelta(0)
        ev["length"] = max(length, dt.timedelta(0))
        ex = {e.timestamp() for e in ev.get("exdates", [])}
        for c in _occurrences(ev, win_start, win_end):
            if c.timestamp() in ex:
                continue
            src, c_start, c_len = ev, c, ev["length"]
            ov = overrides.get((ev.get("uid", ""), c.timestamp()))
            if ov is not None:
                src, c_start = ov, ov["start"]
                c_len = (ov["end"] - ov["start"]) if "end" in ov else ov.get("duration", ev["length"])
            if src.get("status") == "CANCELLED":
                continue
            e_end = c_start + c_len
            if e_end <= win_start or c_start >= win_end:
                continue
            out.append(_event(src, c_start, e_end))
    # moved occurrences that now fall inside the window but whose original slot didn't
    seen = {(e.uid, e.start) for e in out}
    for (uid, _), ov in overrides.items():
        if ov.get("status") == "CANCELLED":
            continue
        end = ov.get("end") or ov["start"] + ov.get("duration", dt.timedelta(0))
        if ov["start"] < win_end and end > win_start and (uid, ov["start"].timestamp()) not in seen:
            out.append(_event(ov, ov["start"], end))
    out.sort(key=lambda e: (not e.all_day, e.start))
    return out


def _event(src: dict, start: dt.datetime, end: dt.datetime) -> Event:
    desc = src.get("description", "")
    meet = ""
    for blob in (src.get("location", ""), src.get("url", ""), desc):
        m = _MEET_URL.search(blob or "")
        if m:
            meet = m.group(0)
            break
    return Event(uid=src.get("uid", ""), title=src.get("title") or "(no title)", start=start.timestamp(),
                 end=end.timestamp(), all_day=bool(src.get("all_day")), location=src.get("location", "")[:200],
                 description=re.sub(r"\s+", " ", desc)[:400], attendees=src.get("attendees", [])[:30],
                 organizer=src.get("organizer", ""), meet_url=meet, calendar=src.get("calendar", ""))


# ---------------------------------------------------------------- fetching --

_FETCHED: dict[str, tuple[float, str]] = {}


def fetch_ics(url: str, timeout: float = 20) -> str:
    """Download a feed (reused for 5 minutes, since today's and tomorrow's lookups come together)."""
    import time
    hit = _FETCHED.get(url)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    text = _download(url, timeout)
    for k in [k for k, (t, _) in _FETCHED.items() if time.time() - t >= 300]:
        del _FETCHED[k]
    _FETCHED[url] = (time.time(), text)
    return text


def _download(url: str, timeout: float) -> str:
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.lower().startswith("https://"):
        raise ValueError("calendar address must start with https:// or webcal://")
    req = urllib.request.Request(url, headers={"User-Agent": "OptiDay calendar reader"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(15_000_000)
    return data.decode("utf-8", "replace")


def day_window(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Calendar days run midnight to midnight local time."""
    local = local_zone()
    s = dt.datetime(day.year, day.month, day.day, tzinfo=local)
    nxt = day + dt.timedelta(days=1)
    return s, dt.datetime(nxt.year, nxt.month, nxt.day, tzinfo=local)


def events_for_day(urls: list[str], day: dt.date, fetch=fetch_ics) -> tuple[list[dict], list[str]]:
    """Returns (events, errors) for one calendar day across all configured feeds."""
    s, e = day_window(day)
    events: list[Event] = []
    errors: list[str] = []
    for i, url in enumerate(urls):
        try:
            raw = parse_ics(fetch(url), calendar=f"calendar {i + 1}")
            events.extend(events_between(raw, s, e))
        except Exception as ex:  # noqa: BLE001 - one bad feed shouldn't hide the others
            errors.append(f"calendar {i + 1}: {ex}")
            log.warning("calendar %d failed: %s", i + 1, ex)
    # same meeting on two calendars (e.g. work + personal) -> keep one
    uniq, seen = [], set()
    for ev in sorted(events, key=lambda x: (not x.all_day, x.start)):
        k = (ev.title.lower(), round(ev.start / 60))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(ev.to_dict())
    return uniq, errors
