"""End to end run of every command against a fake Microsoft Graph server."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import mock_graph

CLI = str(Path(__file__).resolve().parent.parent / "scripts" / "cleanup.py")


class OutlookFlow(unittest.TestCase):
    def setUp(self):
        self.server, self.state, self.base = mock_graph.start()
        self.home = Path(tempfile.mkdtemp(prefix="inbox-cleanup-flow-"))
        (self.home / "config.json").write_text(json.dumps({"outlook": {"client_id": "TEST"}}))
        self.env = {**os.environ, "INBOX_CLEANUP_HOME": str(self.home),
                    "INBOX_CLEANUP_GRAPH_URL": self.base + "/v1.0",
                    "INBOX_CLEANUP_MS_LOGIN_URL": self.base,
                    "INBOX_CLEANUP_ALLOW_HTTP": "1"}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, CLI, *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def plan(self):
        runs = sorted(p for p in (self.home / "runs").iterdir() if (p / "plan.json").exists())
        return json.loads((runs[-1] / "plan.json").read_text())

    def test_full_flow(self):
        s = self.state
        out = self.run_cli("auth", "outlook", "--alias", "work")
        self.assertIn("TEST-CODE", out)
        self.assertIn("you@corp.example", out)
        self.assertIn("outlook\twork", self.run_cli("accounts"))

        # Scan: reads paged inbox + junk, changes nothing.
        self.run_cli("scan", "--include-spam", "--archive-old-months", "6")
        p = self.plan()
        self.assertEqual(len(p["trash"]), 18)      # 6 + 4 + 3 + 3 unread bulk, 2 junk
        self.assertEqual(len(p["ask"]), 4)         # digest@mixed: opened 1 of 5
        self.assertEqual(len(p["archive"]), 3)     # old read bulk
        self.assertEqual(len(p["unsub"]), 4)
        self.assertEqual(p["kept"].get("sender you have written to", 0)
                         + p["kept"].get("from a real person", 0), 2)
        self.assertEqual(s.count("inbox"), 26)

        # Dry run changes nothing.
        self.assertIn("Dry run", self.run_cli("apply"))
        self.assertEqual(s.count("deleteditems"), 0)

        # Try 3, then undo.
        self.run_cli("apply", "--yes", "--limit", "3")
        self.assertEqual(s.count("deleteditems"), 3)
        self.run_cli("undo")
        self.assertEqual(s.count("deleteditems"), 0)
        self.assertEqual(s.count("inbox"), 26)

        # Settle the ask list, apply everything (one move is throttled and retried).
        self.run_cli("scan", "--include-spam", "--archive-old-months", "6")
        self.run_cli("decide", "--trash", "digest@mixed.example")
        self.run_cli("apply", "--yes")
        self.assertEqual(s.count("deleteditems"), 22)
        self.assertEqual(s.count("archive"), 3)
        self.assertEqual(s.count("junkemail"), 0)
        self.assertEqual(s.count("inbox"), 3)      # 2 from Alice, 1 read digest
        self.assertFalse(s.throttle_once)

        # Full undo puts every email back where it was.
        self.run_cli("undo")
        self.assertEqual((s.count("inbox"), s.count("junkemail"), s.count("deleteditems"),
                          s.count("archive")), (26, 2, 0, 0))

        # Apply again for real, then unsubscribe.
        self.run_cli("scan", "--include-spam")
        self.run_cli("apply", "--yes")
        self.assertEqual(s.count("deleteditems"), 22)   # digest is now an approved sender
        self.assertIn("Dry run", self.run_cli("unsub"))
        self.assertEqual(s.unsub_hits, [])
        out = self.run_cli("unsub", "--yes")
        self.assertIn(("POST", "/unsub/shop"), s.unsub_hits)      # one click
        self.assertIn(("GET", "/unsub/letters"), s.unsub_hits)    # link visit
        self.assertEqual(s.sent[0]["toRecipients"][0]["emailAddress"]["address"], "leave@mailonly.example")
        self.assertEqual(s.sent[0]["subject"], "stop")
        self.assertIn("bad@stubborn.example", out)                # failed

        # Block the sender that ignored the unsubscribe.
        self.assertIn("Dry run", self.run_cli("block"))
        self.assertEqual(s.rules, [])
        self.run_cli("block", "--yes")
        self.assertEqual(len(s.rules), 1)
        self.assertEqual(s.rules[0]["conditions"]["senderContains"], ["bad@stubborn.example"])
        self.assertEqual(s.rules[0]["actions"]["moveToFolder"], "FOLDERID-deleteditems")

        # Scheduled mode: approved sender is trashed, a brand new sender waits.
        s.add("promo@shop.example", "Another sale", unsub=f"<{self.base}/unsub/shop>", post=True, n=2)
        s.add("new@fresh.example", "Hello from a new list", unsub=f"<{self.base}/unsub/fresh>", n=2)
        out = self.run_cli("auto")
        self.assertIn("Trashed automatically: 2 emails", out)
        self.assertIn("2 emails from 1 new senders", out)
        self.assertEqual(s.count("deleteditems"), 24)
        self.assertEqual(s.count("inbox"), 8)      # 3 kept, 3 old read, 2 new sender, 0 promo
        self.assertTrue((self.home / "last_auto.md").exists())

        # Every Graph call asked for ids that survive a move.
        self.assertEqual(s.missing_prefer, 0)

    def test_keep_decision_is_remembered(self):
        self.run_cli("auth", "outlook", "--alias", "work")
        self.run_cli("scan")
        self.run_cli("decide", "--keep", "digest@mixed.example")
        self.assertEqual(len(self.plan()["ask"]), 0)
        self.run_cli("scan")
        p = self.plan()
        self.assertEqual(len(p["ask"]), 0)
        self.assertEqual(p["kept"].get("protected sender"), 4)

    def test_fallback_when_headers_are_refused(self):
        self.server.shutdown()
        self.server.server_close()
        self.server, self.state, self.base = mock_graph.start(refuse_headers=True)
        self.env.update(INBOX_CLEANUP_GRAPH_URL=self.base + "/v1.0", INBOX_CLEANUP_MS_LOGIN_URL=self.base)
        self.run_cli("auth", "outlook", "--alias", "work")
        out = self.run_cli("scan")
        self.assertIn("Focused/Other", out)
        senders = {t["from"] for t in self.plan()["trash"]}
        self.assertIn("promo@shop.example", senders)       # robot address in Other
        self.assertNotIn("alice@people.example", senders)


if __name__ == "__main__":
    unittest.main()
