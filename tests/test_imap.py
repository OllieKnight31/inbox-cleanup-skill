"""IMAP provider against an in-memory fake mail server."""
import imaplib
import json
import time
import unittest
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import classify
import common
import imap_provider


def imap_date(age_days):
    d = datetime.now(timezone.utc) - timedelta(days=age_days)
    return d.strftime("%d-%b-%Y %H:%M:%S +0000")


class FakeServer:
    """Folders of emails. Uids change when an email moves, like a real server."""

    def __init__(self, supports_move=True):
        self.supports_move = supports_move
        self.folders = {"INBOX": [], "Trash": [], "Junk": [], "Sent": [], "Archive": []}
        self.flags = {"Trash": "\\Trash", "Junk": "\\Junk", "Sent": "\\Sent", "Archive": "\\Archive"}
        self.next_uid = 1

    def add(self, folder, frm, subject, seen=False, age=10, unsub=None, to=None, flagged=False):
        uid = self.next_uid
        self.next_uid += 1
        h = f"From: {frm}\r\nSubject: {subject}\r\nMessage-ID: <m{uid}@fake.example>\r\n"
        if unsub:
            h += f"List-Unsubscribe: {unsub}\r\n"
        if to:
            h += f"To: {to}\r\n"
        fl = " ".join(f for f, on in (("\\Seen", seen), ("\\Flagged", flagged)) if on)
        self.folders[folder].append({"uid": uid, "flags": fl, "date": imap_date(age), "headers": h})


class FakeIMAP:
    server: FakeServer

    def __init__(self, host, port=993):
        self.sel = None

    def login(self, user, password):
        if password != "app-password":
            raise imaplib.IMAP4.error("bad password")
        return "OK", [b"ok"]

    def logout(self):
        return "BYE", [b""]

    def capability(self):
        return "OK", [b"IMAP4rev1 UIDPLUS MOVE" if self.server.supports_move else b"IMAP4rev1"]

    def list(self):
        out = [b'(\\HasNoChildren) "/" "INBOX"']
        for name, flag in self.server.flags.items():
            out.append(f'(\\HasNoChildren {flag}) "/" "{name}"'.encode())
        return "OK", out

    def select(self, folder, readonly=False):
        name = folder.strip('"')
        if name not in self.server.folders:
            return "NO", [b"no such folder"]
        self.sel = name
        return "OK", [str(len(self.server.folders[name])).encode()]

    def expunge(self):
        f = self.server.folders[self.sel]
        f[:] = [m for m in f if not m.get("deleted")]
        return "OK", [b""]

    def uid(self, cmd, *args):
        box = self.server.folders[self.sel]
        if cmd == "FETCH":
            data = []
            for m in box:
                head = m["headers"].encode() + b"\r\n"
                meta = (f'{m["uid"]} (UID {m["uid"]} FLAGS ({m["flags"]}) INTERNALDATE "{m["date"]}" '
                        f'BODY[HEADER.FIELDS (X)] {{{len(head)}}}').encode()
                data += [(meta, head), b")"]
            return "OK", data
        if cmd == "SEARCH":
            msgid = args[-1].strip('"')
            hits = [str(m["uid"]) for m in box if f"Message-ID: {msgid}" in m["headers"]]
            return "OK", [" ".join(hits).encode()]
        if cmd in ("MOVE", "COPY"):
            uid, dest = int(args[0]), args[1].strip('"')
            m = next((x for x in box if x["uid"] == uid), None)
            if not m:
                return "NO", [b"no such message"]
            copy = dict(m, uid=self.server.next_uid)
            self.server.next_uid += 1
            self.server.folders[dest].append(copy)
            if cmd == "MOVE":
                box.remove(m)
            return "OK", [b""]
        if cmd == "STORE":
            next(x for x in box if x["uid"] == int(args[0]))["deleted"] = True
            return "OK", [b""]
        raise AssertionError(cmd)


class ImapProvider(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        FakeIMAP.server = self.server
        self._real = imaplib.IMAP4_SSL
        imaplib.IMAP4_SSL = FakeIMAP
        s = self.server
        for _ in range(5):
            s.add("INBOX", "Promo <promo@shop.example>", "Sale", unsub="<https://shop.example/u>")
        s.add("INBOX", "Promo <promo@shop.example>", "=?utf-8?q?Caf=C3=A9_deals?=", unsub="<https://shop.example/u>")
        s.add("INBOX", "Bob <bob@people.example>", "Hello")
        s.add("INBOX", "News <news@read.example>", "Read me", seen=True, unsub="<https://read.example/u>")
        s.add("INBOX", "Flag <flag@list.example>", "Keep", flagged=True, unsub="<https://list.example/u>")
        s.add("Junk", "Scam <scam@x.example>", "You won")
        s.add("Sent", "me@fake.example", "Hi", to="Bob <bob@people.example>")
        common.save_json(common.token_path("imap", "home"), {
            "host": "imap.fake.example", "port": 993, "user": "me@fake.example",
            "password": "app-password", "smtp_host": "smtp.fake.example"})
        self.cfg = common.default_config()

    def tearDown(self):
        imaplib.IMAP4_SSL = self._real
        common.token_path("imap", "home").unlink()

    def quiet(self, *a):
        pass

    def test_scan_and_plan(self):
        data = imap_provider.scan(self.cfg, "home", include_spam=True, log=self.quiet)
        self.assertEqual(data["me"], "me@fake.example")
        self.assertEqual(data["contacts"], ["bob@people.example"])
        self.assertEqual(len(data["messages"]), 10)
        self.assertIn("Café deals", [m["subject"] for m in data["messages"]])
        ages = [(time.time() * 1000 - m["ts"]) / 86400000 for m in data["messages"]]
        self.assertTrue(all(9.9 < a < 10.1 for a in ages))
        p = classify.build_plan([data], self.cfg)
        self.assertEqual(len(p["trash"]), 7)          # 6 promo + 1 junk
        self.assertEqual({t["from"] for t in p["trash"]}, {"promo@shop.example", "scam@x.example"})

    def check_round_trip(self):
        data = imap_provider.scan(self.cfg, "home", log=self.quiet)
        ids = [m["id"] for m in data["messages"] if "promo@" in m["from"]]
        moved = imap_provider.move(self.cfg, "home", ids, "inbox", "trash", log=self.quiet)
        self.assertEqual(moved, ids)
        self.assertEqual(len(self.server.folders["Trash"]), 6)
        self.assertEqual(len(self.server.folders["INBOX"]), 3)
        # Undo: uids changed in Trash, so emails are found again by Message-ID.
        back = imap_provider.move(self.cfg, "home", moved, "trash", "inbox", log=self.quiet)
        self.assertEqual(back, ids)
        self.assertEqual(len(self.server.folders["Trash"]), 0)
        self.assertEqual(len(self.server.folders["INBOX"]), 9)
        gone = imap_provider.move(self.cfg, "home", ["999|<nope@fake.example>"], "trash", "inbox", log=self.quiet)
        self.assertEqual(gone, [None])

    def test_move_and_undo(self):
        self.check_round_trip()

    def test_move_and_undo_without_move_support(self):
        self.server.supports_move = False
        self.check_round_trip()

    def test_wrong_password_is_rejected(self):
        tp = common.token_path("imap", "home")
        acct = json.loads(tp.read_text())
        common.save_json(tp, {**acct, "password": "wrong"})
        with self.assertRaises(imaplib.IMAP4.error):
            imap_provider.scan(self.cfg, "home", log=self.quiet)

    def test_block_reports_unsupported(self):
        self.assertEqual(imap_provider.block(self.cfg, "home", ["a@b.example"]), ([], ["a@b.example"]))


if __name__ == "__main__":
    unittest.main()
