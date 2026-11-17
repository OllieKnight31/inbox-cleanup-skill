"""Generic IMAP provider: Yahoo, iCloud, Fastmail, custom domains and anything
else that speaks IMAP.

Sign in uses an app password, stored only in your private
~/.inbox-cleanup/tokens folder (file readable by you alone).
"""
from __future__ import annotations

import email
import email.utils
import getpass
import imaplib
import json
import os
import re
import smtplib
from email.header import decode_header, make_header
from email.mime.text import MIMEText

from common import parse_addr, save_json, token_path

FIELDS = ("FROM SUBJECT MESSAGE-ID LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST "
          "LIST-ID PRECEDENCE AUTO-SUBMITTED")
SPECIAL = {"trash": "\\Trash", "spam": "\\Junk", "archive": "\\Archive", "sent": "\\Sent"}
FALLBACK = {
    "trash": ("Trash", "Deleted Items", "Deleted Messages", "Deleted", "Bin"),
    "spam": ("Junk", "Spam", "Junk Email", "Bulk Mail", "Bulk"),
    "archive": ("Archive", "Archives", "All Mail"),
    "sent": ("Sent", "Sent Items", "Sent Messages", "Sent Mail"),
}


def authorize(cfg: dict, alias: str, host=None, user=None, smtp_host=None, port=993, **_) -> str:
    if not host or not user:
        raise SystemExit("IMAP needs --host and --user, e.g. --host imap.mail.yahoo.com --user you@yahoo.com")
    password = os.environ.get("INBOX_CLEANUP_IMAP_PASSWORD") or getpass.getpass(
        f"App password for {user}: ")
    acct = {"host": host, "port": int(port), "user": user, "password": password,
            "smtp_host": smtp_host or re.sub(r"^imap\.", "smtp.", host)}
    conn = _connect(acct)
    conn.logout()
    save_json(token_path("imap", alias), acct)
    return user


def _acct(alias: str) -> dict:
    tp = token_path("imap", alias)
    if not tp.exists():
        raise SystemExit(f"No IMAP account '{alias}'. Run: cleanup.py auth imap --alias {alias} --host HOST --user USER")
    return json.loads(tp.read_text())


def _connect(acct: dict):
    conn = imaplib.IMAP4_SSL(acct["host"], acct.get("port", 993))
    conn.login(acct["user"], acct["password"])
    return conn


def _folders(conn) -> dict:
    """Map inbox/trash/spam/archive/sent to this server's real folder names."""
    typ, data = conn.list()
    found, names = {"inbox": "INBOX"}, []
    for raw in data or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        names.append(name)
        for key, flag in SPECIAL.items():
            if flag.lower() in m.group("flags").lower():
                found.setdefault(key, name)
    for key, options in FALLBACK.items():
        if key in found:
            continue
        for opt in options:
            hit = [n for n in names if n.lower() == opt.lower() or n.lower().endswith("/" + opt.lower())
                   or n.lower().endswith("." + opt.lower())]
            if hit:
                found[key] = hit[0]
                break
    return found


def _q(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _hdr(msg, name: str) -> str | None:
    v = msg.get(name)
    if v is None:
        return None
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return str(v)


def _fetch(conn, folder: str, fields: str):
    """Yield (uid, flags, internaldate_ms, parsed headers) for every email in a folder."""
    typ, _ = conn.select(_q(folder), readonly=True)
    if typ != "OK":
        return
    typ, data = conn.uid("FETCH", "1:*", f"(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS ({fields})])")
    if typ != "OK":
        return
    for part in data or []:
        if not isinstance(part, tuple):
            continue
        meta = part[0].decode("utf-8", "replace")
        uid = re.search(r"UID (\d+)", meta)
        if not uid:
            continue
        flags = re.search(r"FLAGS \(([^)]*)\)", meta)
        date = re.search(r'INTERNALDATE "([^"]+)"', meta)
        ts = 0
        if date:
            try:
                ts = int(email.utils.parsedate_to_datetime(
                    date.group(1).replace("-", " ", 2)).timestamp() * 1000)
            except Exception:
                ts = 0
        yield uid.group(1), (flags.group(1) if flags else ""), ts, email.message_from_bytes(part[1])


def scan(cfg: dict, alias: str, include_spam: bool = False, log=print) -> dict:
    acct = _acct(alias)
    conn = _connect(acct)
    try:
        folders = _folders(conn)
        msgs = []
        todo = ["inbox"] + (["spam"] if include_spam and "spam" in folders else [])
        for key in todo:
            log(f"[imap:{alias}] reading {folders[key]}")
            for uid, flags, ts, h in _fetch(conn, folders[key], FIELDS):
                msgid = (h.get("Message-ID") or "").strip()
                msgs.append({
                    "id": f"{uid}|{msgid}",
                    "provider": "imap",
                    "account": alias,
                    "folder": key,
                    "from": _hdr(h, "From") or "",
                    "subject": _hdr(h, "Subject") or "",
                    "ts": ts,
                    "unread": "\\Seen" not in flags,
                    "starred": "\\Flagged" in flags,
                    "replied_thread": "\\Answered" in flags,
                    "bulk_hint": False,
                    "list_unsub": _hdr(h, "List-Unsubscribe"),
                    "list_unsub_post": _hdr(h, "List-Unsubscribe-Post"),
                    "list_id": _hdr(h, "List-Id"),
                    "precedence": _hdr(h, "Precedence"),
                    "auto_submitted": _hdr(h, "Auto-Submitted"),
                })
        log(f"[imap:{alias}] {len(msgs)} emails")
        contacts = set()
        if "sent" in folders:
            log(f"[imap:{alias}] reading who you have written to")
            for _uid, _f, _ts, h in _fetch(conn, folders["sent"], "TO CC"):
                for _name, a in email.utils.getaddresses(h.get_all("To", []) + h.get_all("Cc", [])):
                    if "@" in a:
                        contacts.add(a.lower())
        return {"me": parse_addr(acct["user"]), "contacts": sorted(contacts), "messages": msgs}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def move(cfg: dict, alias: str, ids: list[str], src: str, dest: str, log=print) -> list:
    """Move emails between folders. Ids stay the same so undo can find them again."""
    acct = _acct(alias)
    conn = _connect(acct)
    out: list = []
    try:
        folders = _folders(conn)
        if src not in folders or dest not in folders:
            missing = src if src not in folders else dest
            raise SystemExit(f"This mail server has no {missing} folder that inbox-cleanup can find.")
        conn.select(_q(folders[src]), readonly=False)
        typ, caps = conn.capability()
        has_move = b"MOVE" in b" ".join(caps or []).upper().split()
        for mid in ids:
            uid, _, msgid = mid.partition("|")
            # Outside the inbox the uid has changed, so look the email up by Message-ID.
            if src != "inbox" or not uid:
                uid = None
                if msgid:
                    typ, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", _q(msgid))
                    found = (data[0] or b"").split() if typ == "OK" and data else []
                    uid = found[-1].decode() if found else None
            if not uid:
                out.append(None)
                continue
            if has_move:
                typ, _ = conn.uid("MOVE", uid, _q(folders[dest]))
            else:
                typ, _ = conn.uid("COPY", uid, _q(folders[dest]))
                if typ == "OK":
                    conn.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
            out.append(mid if typ == "OK" else None)
        if not has_move:
            conn.expunge()
        log(f"[imap:{alias}] moved {len([x for x in out if x])}/{len(ids)} to {dest}")
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def send(cfg: dict, alias: str, to: str, subject: str, body: str) -> None:
    acct = _acct(alias)
    msg = MIMEText(body)
    msg["From"], msg["To"], msg["Subject"] = acct["user"], to, subject
    with smtplib.SMTP_SSL(acct["smtp_host"], 465, timeout=30) as s:
        s.login(acct["user"], acct["password"])
        s.send_message(msg)


def block(cfg: dict, alias: str, senders: list[str], action: str = "trash") -> tuple[list, list]:
    """IMAP has no standard way to create server rules."""
    return [], list(senders)
