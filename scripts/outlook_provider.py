"""Outlook / Microsoft 365 provider via Microsoft Graph.

Uses the device code flow, so no client secret is needed or stored. Works for
personal accounts (outlook.com, hotmail.com, live.com) and work or school
accounts.
"""
from __future__ import annotations

import json
import time

import requests

from common import parse_addr, save_json, token_path

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Mail.ReadWrite Mail.Send"
SELECT = "id,subject,from,receivedDateTime,isRead,flag,inferenceClassification,conversationId"


def _authority(cfg: dict) -> str:
    return f"https://login.microsoftonline.com/{cfg['outlook'].get('tenant') or 'common'}/oauth2/v2.0"


def _client_id(cfg: dict) -> str:
    cid = (cfg["outlook"].get("client_id") or "").strip()
    if not cid:
        raise SystemExit(
            "Outlook client_id is not set in config.json. "
            "See references/setup.md, step 'Outlook'."
        )
    return cid


def authorize(cfg: dict, alias: str, log=print) -> str:
    cid, auth = _client_id(cfg), _authority(cfg)
    r = requests.post(f"{auth}/devicecode", data={"client_id": cid, "scope": SCOPES}, timeout=30)
    r.raise_for_status()
    dc = r.json()
    log(dc["message"])
    deadline = time.time() + dc.get("expires_in", 900)
    interval = dc.get("interval", 5)
    while time.time() < deadline:
        time.sleep(interval)
        t = requests.post(f"{auth}/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": cid,
            "device_code": dc["device_code"],
        }, timeout=30).json()
        if "access_token" in t:
            _store(alias, t)
            me = _get(cfg, alias, f"{GRAPH}/me?$select=mail,userPrincipalName")
            return (me.get("mail") or me.get("userPrincipalName") or "").lower()
        if t.get("error") == "slow_down":
            interval += 5
        elif t.get("error") != "authorization_pending":
            raise SystemExit(f"Outlook sign in failed: {t.get('error_description', t.get('error'))}")
    raise SystemExit("Outlook sign in timed out. Run the auth command again.")


def _store(alias: str, t: dict) -> None:
    save_json(token_path("outlook", alias), {
        "access_token": t["access_token"],
        "refresh_token": t.get("refresh_token"),
        "expires_at": time.time() + int(t.get("expires_in", 3600)) - 120,
    })


def _token(cfg: dict, alias: str) -> str:
    tp = token_path("outlook", alias)
    if not tp.exists():
        raise SystemExit(f"No Outlook token for '{alias}'. Run: cleanup.py auth outlook --alias {alias}")
    tok = json.loads(tp.read_text())
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    r = requests.post(f"{_authority(cfg)}/token", data={
        "grant_type": "refresh_token",
        "client_id": _client_id(cfg),
        "refresh_token": tok["refresh_token"],
        "scope": SCOPES,
    }, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"Outlook token refresh failed for '{alias}'. Run the auth command again.")
    t = r.json()
    t.setdefault("refresh_token", tok["refresh_token"])
    _store(alias, t)
    return t["access_token"]


def _request(cfg, alias, method, url, **kw):
    for attempt in range(6):
        r = requests.request(
            method, url, timeout=60,
            headers={"Authorization": f"Bearer {_token(cfg, alias)}", **kw.pop("headers", {})},
            **kw,
        )
        if r.status_code in (429, 503, 504):
            time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
            continue
        return r
    return r


def _get(cfg, alias, url) -> dict:
    r = _request(cfg, alias, "GET", url)
    r.raise_for_status()
    return r.json()


def _pages(cfg, alias, url, cap=None):
    n = 0
    while url:
        data = _get(cfg, alias, url)
        for item in data.get("value", []):
            yield item
            n += 1
            if cap and n >= cap:
                return
        url = data.get("@odata.nextLink")


def scan(cfg: dict, alias: str, log=print) -> dict:
    me = _get(cfg, alias, f"{GRAPH}/me?$select=mail,userPrincipalName")
    me_addr = (me.get("mail") or me.get("userPrincipalName") or "").lower()

    log(f"[outlook:{alias}] reading inbox")
    base = f"{GRAPH}/me/mailFolders/inbox/messages?$top=50&$select={SELECT}"
    with_headers = True
    probe = _request(cfg, alias, "GET", base + ",internetMessageHeaders")
    if probe.status_code >= 400:
        # Some mailboxes refuse headers in list calls; fall back to Focused/Other.
        with_headers = False
        log(f"[outlook:{alias}] headers not available in list view, using Focused/Other signal")
    url = base + (",internetMessageHeaders" if with_headers else "")

    msgs = []
    for m in _pages(cfg, alias, url):
        h = {x["name"].lower(): x["value"] for x in (m.get("internetMessageHeaders") or [])}
        ea = (m.get("from") or {}).get("emailAddress") or {}
        frm = f"{ea.get('name', '')} <{ea.get('address', '')}>"
        msgs.append({
            "id": m["id"],
            "provider": "outlook",
            "account": alias,
            "from": frm,
            "subject": m.get("subject") or "",
            "ts": _utc_ms(m["receivedDateTime"]),
            "unread": not m.get("isRead", False),
            "starred": (m.get("flag") or {}).get("flagStatus") == "flagged",
            "conversation": m.get("conversationId"),
            "replied_thread": False,
            "bulk_hint": m.get("inferenceClassification") == "other",
            "list_unsub": h.get("list-unsubscribe"),
            "list_unsub_post": h.get("list-unsubscribe-post"),
            "list_id": h.get("list-id"),
            "precedence": h.get("precedence"),
            "auto_submitted": h.get("auto-submitted"),
        })
        if len(msgs) % 500 == 0:
            log(f"  read {len(msgs)}")
    log(f"[outlook:{alias}] {len(msgs)} inbox emails")

    log(f"[outlook:{alias}] reading who you have written to")
    contacts, convs = set(), set()
    sent_url = f"{GRAPH}/me/mailFolders/sentitems/messages?$top=100&$select=toRecipients,ccRecipients,conversationId"
    for m in _pages(cfg, alias, sent_url, cap=2000):
        convs.add(m.get("conversationId"))
        for r in (m.get("toRecipients") or []) + (m.get("ccRecipients") or []):
            a = parse_addr((r.get("emailAddress") or {}).get("address"))
            if "@" in a:
                contacts.add(a)
    for m in msgs:
        m["replied_thread"] = m.pop("conversation") in convs
    return {"me": me_addr, "contacts": sorted(contacts), "messages": msgs}


def _utc_ms(iso: str) -> int:
    import calendar
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) * 1000


def trash(cfg: dict, alias: str, ids: list[str], log=print) -> int:
    """Move messages to Deleted Items (recoverable)."""
    done = 0
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        body = {"requests": [{
            "id": str(n), "method": "POST", "url": f"/me/messages/{mid}/move",
            "headers": {"Content-Type": "application/json"},
            "body": {"destinationId": "deleteditems"},
        } for n, mid in enumerate(chunk)]}
        r = _request(cfg, alias, "POST", f"{GRAPH}/$batch", json=body)
        r.raise_for_status()
        retry = [chunk[int(x["id"])] for x in r.json().get("responses", []) if x.get("status") == 429]
        done += len(chunk) - len(retry)
        for mid in retry:
            time.sleep(1)
            rr = _request(cfg, alias, "POST", f"{GRAPH}/me/messages/{mid}/move",
                          json={"destinationId": "deleteditems"})
            done += rr.status_code < 400
        if done % 200 < 20:
            log(f"[outlook:{alias}] moved {done}/{len(ids)} to Deleted Items")
    return done


def send(cfg: dict, alias: str, to: str, subject: str, body: str) -> None:
    r = _request(cfg, alias, "POST", f"{GRAPH}/me/sendMail", json={
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": False,
    })
    r.raise_for_status()
