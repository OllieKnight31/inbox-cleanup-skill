"""A small fake of Microsoft Graph + Microsoft login + a few unsubscribe pages.

Lets the whole Outlook path run end to end with no Microsoft account.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = 10


def iso(age_days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_days * 86400))


class State:
    def __init__(self, base: str, refuse_headers: bool = False):
        self.base = base
        self.refuse_headers = refuse_headers
        self.msgs: dict[str, dict] = {}
        self.rules, self.sent, self.unsub_hits = [], [], []
        self.throttle_once = True
        self.missing_prefer = 0
        self.n = 0
        self.seed()

    def add(self, frm, subject, folder="inbox", read=False, age=10, unsub=None, post=False,
            other=True, n=1):
        for _ in range(n):
            self.n += 1
            headers = []
            if unsub:
                headers.append({"name": "List-Unsubscribe", "value": unsub})
            if post:
                headers.append({"name": "List-Unsubscribe-Post", "value": "List-Unsubscribe=One-Click"})
            self.msgs[f"MSG{self.n}"] = {
                "id": f"MSG{self.n}", "folder": folder, "subject": subject,
                "from": {"emailAddress": {"name": frm.split("@")[0], "address": frm}},
                "receivedDateTime": iso(age), "isRead": read,
                "flag": {"flagStatus": "notFlagged"},
                "inferenceClassification": "other" if other else "focused",
                "conversationId": f"CONV{self.n}", "internetMessageHeaders": headers,
            }

    def seed(self):
        b = self.base
        self.add("promo@shop.example", "Sale now on", unsub=f"<{b}/unsub/shop>", post=True, n=6)
        self.add("news@letters.example", "Weekly letter", unsub=f"<{b}/unsub/letters>", n=4)
        self.add("list@mailonly.example", "List update", unsub="<mailto:leave@mailonly.example?subject=stop>", n=3)
        self.add("bad@stubborn.example", "Buy now", unsub=f"<{b}/unsub/fail>", n=3)
        self.add("digest@mixed.example", "Digest", unsub=f"<{b}/unsub/mixed>", n=4)
        self.add("digest@mixed.example", "Digest", unsub=f"<{b}/unsub/mixed>", read=True)
        self.add("alice@people.example", "Lunch on Friday?", other=False, n=2)
        self.add("old@read.example", "Old news", unsub=f"<{b}/unsub/old>", read=True, age=300, n=3)
        self.add("scam@x.example", "You won", folder="junkemail", n=2)

    def count(self, folder):
        return sum(1 for m in self.msgs.values() if m["folder"] == folder)


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = json.dumps(obj if obj is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if code == 429:
            self.send_header("Retry-After", "0")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if "json" in (self.headers.get("Content-Type") or ""):
            return json.loads(raw or b"{}")
        return {k: v[0] for k, v in parse_qs(raw.decode()).items()}

    def _move(self, mid, dest):
        m = self.state.msgs.get(mid)
        if not m:
            return 404, {"error": "not found"}
        m["folder"] = dest
        return 201, {"id": mid}

    def do_GET(self):
        s, u = self.state, urlparse(self.path)
        q = parse_qs(u.query)
        if u.path.startswith("/unsub/"):
            s.unsub_hits.append(("GET", u.path))
            return self._send(500 if u.path.endswith("fail") else 200)
        if u.path.startswith("/v1.0/") and "ImmutableId" not in (self.headers.get("Prefer") or ""):
            s.missing_prefer += 1
        if u.path == "/v1.0/me":
            return self._send(200, {"mail": "You@Corp.example"})
        m = re.match(r"/v1.0/me/mailFolders/(\w+)/messages$", u.path)
        if m:
            folder = m.group(1)
            select = q.get("$select", [""])[0]
            if s.refuse_headers and "internetMessageHeaders" in select:
                return self._send(400, {"error": "headers not supported"})
            if folder == "sentitems":
                rows = [{"toRecipients": [{"emailAddress": {"address": "Alice@People.example"}}],
                         "ccRecipients": [], "conversationId": "CONVX"}]
            else:
                rows = [dict(x) for x in s.msgs.values() if x["folder"] == folder]
                for r in rows:
                    r.pop("folder")
                    if "internetMessageHeaders" not in select:
                        r.pop("internetMessageHeaders")
            skip = int(q.get("skip", ["0"])[0])
            out = {"value": rows[skip:skip + PAGE]}
            if skip + PAGE < len(rows):
                sel = f"&$select={select}" if select else ""
                out["@odata.nextLink"] = f"{s.base}{u.path}?skip={skip + PAGE}{sel}"
            return self._send(200, out)
        m = re.match(r"/v1.0/me/mailFolders/(\w+)$", u.path)
        if m:
            return self._send(200, {"id": f"FOLDERID-{m.group(1)}"})
        self._send(404, {"error": u.path})

    def do_POST(self):
        s, u = self.state, urlparse(self.path)
        body = self._body()
        if u.path.startswith("/unsub/"):
            s.unsub_hits.append(("POST", u.path))
            return self._send(500 if u.path.endswith("fail") else 200)
        if u.path.endswith("/devicecode"):
            return self._send(200, {"device_code": "DC", "interval": 0, "expires_in": 60,
                                    "message": "Open the link and enter code TEST-CODE"})
        if u.path.endswith("/token"):
            return self._send(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
        if "ImmutableId" not in (self.headers.get("Prefer") or ""):
            s.missing_prefer += 1
        if u.path == "/v1.0/$batch":
            res = []
            for r in body["requests"]:
                mid = re.match(r"/me/messages/([^/]+)/move", r["url"]).group(1)
                if s.throttle_once:
                    s.throttle_once = False
                    res.append({"id": r["id"], "status": 429, "body": {}})
                    continue
                code, b = self._move(mid, r["body"]["destinationId"])
                res.append({"id": r["id"], "status": code, "body": b})
            return self._send(200, {"responses": res})
        m = re.match(r"/v1.0/me/messages/([^/]+)/move$", u.path)
        if m:
            return self._send(*self._move(m.group(1), body["destinationId"]))
        if u.path == "/v1.0/me/sendMail":
            s.sent.append(body["message"])
            return self._send(202)
        if u.path == "/v1.0/me/mailFolders/inbox/messageRules":
            s.rules.append(body)
            return self._send(201, {"id": f"RULE{len(s.rules)}"})
        self._send(404, {"error": u.path})


def start(refuse_headers: bool = False):
    """Start the fake on a free port. Returns (server, state, base_url)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (Handler,), {}))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    state = State(base, refuse_headers)
    server.RequestHandlerClass.state = state
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state, base
