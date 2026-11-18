"""Read-only email awareness over IMAP (Gmail, iCloud, Fastmail… with an app password).

* Sent mail gives exact "sent email to X at 2:14pm" entries.
* Inbox mail from real people that you haven't answered becomes "needs you" items for tomorrow,
  with a reply draft prepared by your local AI. OptiDay never sends, deletes or flags anything:
  mailboxes are opened read-only and messages are fetched with BODY.PEEK (stays unread).
"""
from __future__ import annotations

import datetime as dt
import email
import email.policy
import email.utils
import html
import imaplib
import logging
import re
from dataclasses import asdict, dataclass, field

log = logging.getLogger("optiday.email")

KNOWN_HOSTS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com", "mac.com": "imap.mail.me.com",
    "fastmail.com": "imap.fastmail.com", "yahoo.com": "imap.mail.yahoo.com", "aol.com": "imap.aol.com",
    "proton.me": "127.0.0.1", "protonmail.com": "127.0.0.1",  # via Proton Mail Bridge
}
SENT_NAMES = ["[Gmail]/Sent Mail", "[Google Mail]/Sent Mail", "Sent Messages", "Sent Items", "Sent", "INBOX.Sent"]
_BULK_FROM = re.compile(r"(no-?reply|do-?not-?reply|notifications?|newsletter|mailer-daemon|postmaster|bounce|"
                        r"updates?@|news@|marketing|digest|alerts?@|billing@|receipts?@|support@|hello@|team@|"
                        r"info@|noreply|automated|calendar-notification)", re.I)


def guess_host(address: str) -> str:
    domain = address.rsplit("@", 1)[-1].lower()
    return KNOWN_HOSTS.get(domain, f"imap.{domain}")


@dataclass
class Mail:
    folder: str
    msg_id: str
    subject: str
    from_name: str
    from_addr: str
    to: list[str]
    cc: list[str]
    ts: float
    snippet: str
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    bulk: bool = False
    to_names: list[str] = field(default_factory=list)


def _addr_list(v) -> list[str]:
    return [a.lower() for _, a in email.utils.getaddresses([str(v or "")]) if a]


def _name_list(v) -> list[str]:
    return [(n or a).strip() for n, a in email.utils.getaddresses([str(v or "")]) if a]


def _text_of(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001 - truncated/odd MIME
        part = None
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:  # noqa: BLE001
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    # drop quoted history and signatures so the snippet is what this person actually wrote
    lines = []
    for ln in text.splitlines():
        if ln.startswith(">") or re.match(r"^On .{5,120} wrote:\s*$", ln.strip()) or ln.strip() == "--":
            break
        lines.append(ln)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def parse_message(raw: bytes, folder: str) -> Mail | None:
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:  # noqa: BLE001
        return None
    try:
        date = email.utils.parsedate_to_datetime(str(msg.get("Date")))
        ts = date.timestamp()
    except (TypeError, ValueError):
        return None
    name, addr = email.utils.parseaddr(str(msg.get("From", "")))
    bulk = bool(msg.get("List-Unsubscribe") or msg.get("List-Id")
                or str(msg.get("Precedence", "")).lower() in ("bulk", "list", "junk")
                or str(msg.get("Auto-Submitted", "no")).lower() not in ("no", "")
                or _BULK_FROM.search(addr or ""))
    refs = str(msg.get("References", "") or "").split()
    return Mail(folder=folder, msg_id=str(msg.get("Message-ID", "") or "").strip(),
                subject=re.sub(r"\s+", " ", str(msg.get("Subject", "") or "(no subject)")).strip(),
                from_name=name or addr, from_addr=(addr or "").lower(), to=_addr_list(msg.get("To")),
                cc=_addr_list(msg.get("Cc")), ts=ts, snippet=_text_of(msg)[:600],
                in_reply_to=str(msg.get("In-Reply-To", "") or "").strip(), references=refs, bulk=bulk,
                to_names=_name_list(msg.get("To")))


_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$')


def find_sent_folder(conn) -> str | None:
    typ, data = conn.list()
    names = []
    for line in data or []:
        if not isinstance(line, bytes):
            continue
        m = _LIST_RE.match(line)
        if not m:
            continue
        name = m.group("name").decode("utf-8", "replace").strip().strip('"')
        if b"\\Sent" in m.group("flags"):
            return name
        names.append(name)
    for cand in SENT_NAMES:
        if cand in names:
            return cand
    return None


def _fetch_folder(conn, folder: str, since: dt.date, limit: int) -> list[Mail]:
    typ, _ = conn.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        return []
    typ, data = conn.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
    if typ != "OK" or not data or not data[0]:
        return []
    uids = data[0].split()[-limit:]
    out: list[Mail] = []
    for i in range(0, len(uids), 25):
        chunk = b",".join(uids[i:i + 25]).decode()
        typ, resp = conn.uid("FETCH", chunk, "(BODY.PEEK[]<0.24000>)")
        if typ != "OK":
            continue
        for item in resp or []:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                m = parse_message(item[1], folder)
                if m:
                    out.append(m)
    return out


def fetch(address: str, password: str, host: str = "", days_back: int = 3, connect=None) -> dict:
    """Returns {"inbox": [Mail], "sent": [Mail]} for the last few days."""
    host = host or guess_host(address)
    connect = connect or (lambda h: imaplib.IMAP4_SSL(h, 993, timeout=30))
    conn = connect(host)
    try:
        conn.login(address, password)
        since = dt.date.today() - dt.timedelta(days=days_back)
        inbox = _fetch_folder(conn, "INBOX", since, 150)
        sent_folder = find_sent_folder(conn)
        sent = _fetch_folder(conn, sent_folder, since, 100) if sent_folder else []
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    return {"inbox": inbox, "sent": sent}


def _norm_subject(s: str) -> str:
    return re.sub(r"^((re|fwd?|aw|sv)\s*:\s*)+", "", s.strip(), flags=re.I).lower()


def digest(mail: dict, me: str, day_start: float, day_end: float, now: float | None = None) -> dict:
    """What the report needs: emails sent during the day, and emails still waiting for you."""
    me = me.lower()
    now = now or day_end
    sent = [m for m in mail["sent"] if day_start <= m.ts < day_end]
    replied_ids, replied_threads = set(), []
    for m in mail["sent"]:
        if m.in_reply_to:
            replied_ids.add(m.in_reply_to)
        replied_ids.update(m.references)
        replied_threads.append((_norm_subject(m.subject), set(m.to + m.cc), m.ts))
    needs = []
    for m in mail["inbox"]:
        if m.bulk or m.from_addr == me or m.ts > now:
            continue
        if me and me not in m.to and me not in m.cc and m.to:
            continue  # e.g. a list you're BCC'd on
        if m.msg_id and m.msg_id in replied_ids:
            continue
        ns = _norm_subject(m.subject)
        if any(ns == s and m.from_addr in rcpts and ts > m.ts for s, rcpts, ts in replied_threads):
            continue
        # a later message in the same thread from you also counts as handled
        age_h = (now - m.ts) / 3600
        asks = "?" in m.snippet or re.search(r"\b(can you|could you|please|let me know|thoughts|when are you)\b",
                                              m.snippet, re.I)
        needs.append({**asdict(m), "age_hours": round(age_h, 1), "asks": bool(asks),
                      "priority": (2 if asks else 1) + (1 if me in m.to else 0) + (1 if age_h > 24 else 0)})
    # keep only the newest message per thread
    seen, uniq = set(), []
    for n in sorted(needs, key=lambda x: -x["ts"]):
        k = (_norm_subject(n["subject"]), n["from_addr"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    uniq.sort(key=lambda x: (-x["priority"], -x["ts"]))
    return {"sent": [asdict(m) for m in sorted(sent, key=lambda x: x.ts)], "needs_reply": uniq[:12],
            "inbox_count": sum(1 for m in mail["inbox"] if day_start <= m.ts < day_end),
            "human_inbox_count": sum(1 for m in mail["inbox"] if day_start <= m.ts < day_end and not m.bulk)}


def sent_actions(dig: dict) -> list[dict]:
    acts = []
    for m in dig.get("sent", []):
        names = m.get("to_names") or m["to"]
        who = ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")
        acts.append({"ts": m["ts"], "kind": "email", "source": "mail",
                     "text": f"Sent email to {who or 'someone'} — “{m['subject'][:80]}”"})
    return acts
