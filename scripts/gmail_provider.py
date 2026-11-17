"""Gmail provider: OAuth, inbox scan, trash, send."""
from __future__ import annotations

import base64
import json
import time
from email.mime.text import MIMEText
from pathlib import Path

from common import parse_addr, save_json, token_path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
HEADERS = [
    "From", "To", "Subject", "List-Unsubscribe", "List-Unsubscribe-Post",
    "List-Id", "Precedence", "Auto-Submitted",
]


def _libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise SystemExit(
            "Missing Google libraries. Run: pip install -r requirements.txt"
        ) from e
    return Request, Credentials, InstalledAppFlow, build


def authorize(cfg: dict, alias: str, import_token: str | None = None) -> str:
    """Run browser consent (or import an existing authorized-user token)."""
    Request, Credentials, InstalledAppFlow, build = _libs()
    tp = token_path("gmail", alias)
    if import_token:
        data = json.loads(Path(import_token).expanduser().read_text())
        creds = Credentials.from_authorized_user_info(data)
    else:
        client = Path(cfg["gmail"]["oauth_client_file"]).expanduser()
        if not client.exists():
            raise SystemExit(
                f"Gmail OAuth client file not found at {client}. "
                "See references/setup.md, step 'Gmail'."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    save_json(tp, json.loads(creds.to_json()))
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return svc.users().getProfile(userId="me").execute()["emailAddress"]


def service(alias: str):
    Request, Credentials, _, build = _libs()
    tp = token_path("gmail", alias)
    if not tp.exists():
        raise SystemExit(f"No Gmail token for '{alias}'. Run: cleanup.py auth gmail --alias {alias}")
    creds = Credentials.from_authorized_user_info(json.loads(tp.read_text()))
    if not creds.valid:
        creds.refresh(Request())
        save_json(tp, json.loads(creds.to_json()))
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _list_ids(svc, query: str, cap: int | None = None) -> list[str]:
    ids, tok = [], None
    while True:
        r = svc.users().messages().list(
            userId="me", q=query, maxResults=500, pageToken=tok
        ).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        tok = r.get("nextPageToken")
        if not tok or (cap and len(ids) >= cap):
            break
    return ids[:cap] if cap else ids


def _fetch_metadata(svc, ids: list[str], headers: list[str], log) -> dict:
    res: dict[str, dict] = {}

    def cb(rid, resp, exc):
        if exc is None:
            res[rid] = resp

    todo, attempts = list(ids), 0
    while todo and attempts < 6:
        for i in range(0, len(todo), 50):
            b = svc.new_batch_http_request(callback=cb)
            for mid in todo[i:i + 50]:
                b.add(
                    svc.users().messages().get(
                        userId="me", id=mid, format="metadata", metadataHeaders=headers
                    ),
                    request_id=mid,
                )
            try:
                b.execute()
            except Exception:
                # Network hiccup or rate limit: anything missing is retried below.
                time.sleep(3)
            time.sleep(0.25)
        todo = [m for m in ids if m not in res]
        attempts += 1
        log(f"  fetched {len(res)}/{len(ids)}")
        if todo:
            time.sleep(5)
    return res


def scan(alias: str, log=print) -> dict:
    """Return {'me': address, 'contacts': [...], 'messages': [...]} for the inbox."""
    svc = service(alias)
    me = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()

    log(f"[gmail:{alias}] listing inbox")
    ids = _list_ids(svc, "in:inbox")
    log(f"[gmail:{alias}] {len(ids)} inbox emails, reading headers")
    raw = _fetch_metadata(svc, ids, HEADERS, log)

    log(f"[gmail:{alias}] reading who you have written to")
    sent_ids = _list_ids(svc, "in:sent", cap=2000)
    sent = _fetch_metadata(svc, sent_ids, ["To", "Cc"], lambda *_: None)
    contacts, sent_threads = set(), set()
    for m in sent.values():
        sent_threads.add(m.get("threadId"))
        for h in m["payload"].get("headers", []):
            for part in h["value"].split(","):
                a = parse_addr(part)
                if "@" in a:
                    contacts.add(a)

    msgs = []
    for mid, m in raw.items():
        h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
        labels = m.get("labelIds", [])
        msgs.append({
            "id": mid,
            "provider": "gmail",
            "account": alias,
            "from": h.get("from", ""),
            "subject": h.get("subject", ""),
            "ts": int(m.get("internalDate", 0)),
            "unread": "UNREAD" in labels,
            "starred": "STARRED" in labels,
            "replied_thread": m.get("threadId") in sent_threads,
            "bulk_hint": any(
                c in labels for c in ("CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS")
            ),
            "list_unsub": h.get("list-unsubscribe"),
            "list_unsub_post": h.get("list-unsubscribe-post"),
            "list_id": h.get("list-id"),
            "precedence": h.get("precedence"),
            "auto_submitted": h.get("auto-submitted"),
        })
    return {"me": me, "contacts": sorted(contacts), "messages": msgs}


def trash(alias: str, ids: list[str], log=print) -> int:
    """Move messages to Trash (recoverable for 30 days)."""
    svc = service(alias)
    done = 0
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        svc.users().messages().batchModify(
            userId="me", body={"ids": chunk, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}
        ).execute()
        done += len(chunk)
        log(f"[gmail:{alias}] trashed {done}/{len(ids)}")
    return done


def send(alias: str, to: str, subject: str, body: str) -> None:
    svc = service(alias)
    msg = MIMEText(body)
    msg["to"], msg["subject"] = to, subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
