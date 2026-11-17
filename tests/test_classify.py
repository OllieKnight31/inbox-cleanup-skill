"""The rules that decide what is junk, checked against the sample inbox."""
import collections
import contextlib
import io
import plistlib
import platform
import unittest

import classify
import cleanup
import common


def plan(**over):
    cfg = common.default_config()
    approved = over.pop("approved", ())
    cfg.update(over)
    return classify.build_plan(cleanup.load_sample(), cfg, approved=approved)


def senders(items):
    return collections.Counter(t["from"] for t in items)


class Rules(unittest.TestCase):
    def test_never_opened_bulk_is_trashed(self):
        t = senders(plan()["trash"])
        self.assertEqual(t["deals@mail.shoply.example"], 42)
        self.assertEqual(t["newsletter@news.tripnest.example"], 27)
        self.assertEqual(t["marketing@em.saasly.example"], 31)

    def test_real_people_and_contacts_are_kept(self):
        p = plan()
        everyone = senders(p["trash"] + p["ask"] + p["archive"])
        for person in ("maya@okafor-studio.example", "dad@family.example", "sam@client-co.example",
                       "priya.shah@partnerfirm.example", "helpdesk@corp.example",
                       "updates@client-co.example"):
            self.assertNotIn(person, everyone)

    def test_starred_and_transactional_are_kept(self):
        t = senders(plan()["trash"])
        self.assertNotIn("events@eventsco.example", t)        # starred
        self.assertNotIn("statements@northbank.example", t)   # statements
        self.assertNotIn("no-reply@parcelgo.example", t)      # deliveries
        self.assertIn("statements@northbank.example", senders(plan(keep_transactional=False)["trash"]))

    def test_read_email_is_never_trashed(self):
        ids = {m["id"] for s in cleanup.load_sample() for m in s["messages"] if not m["unread"]}
        self.assertFalse(ids & {t["id"] for t in plan()["trash"]})

    def test_new_email_waits(self):
        p = plan()
        self.assertEqual(p["kept"]["newer than 2 days"], 1)
        self.assertEqual(senders(plan(min_age_days=0)["trash"])["deals@mail.shoply.example"], 43)

    def test_sometimes_opened_sender_goes_to_ask(self):
        p = plan()
        a = senders(p["ask"])
        self.assertEqual(a["digest@devweekly.example"], 11)   # opened 3 of 14
        self.assertEqual(a["team@indieletters.example"], 5)   # opened 4 of 9
        self.assertNotIn("digest@devweekly.example", senders(p["trash"]))

    def test_usually_opened_sender_is_kept(self):
        p = plan(ask_keep_read_rate=0.2)
        self.assertNotIn("team@indieletters.example", senders(p["ask"]))
        self.assertEqual(p["kept"]["bulk sender you usually open"], 16)

    def test_approved_sender_skips_the_ask_list(self):
        p = plan(approved=["digest@devweekly.example"])
        self.assertEqual(senders(p["trash"])["digest@devweekly.example"], 11)
        self.assertNotIn("digest@devweekly.example", senders(p["ask"]))

    def test_protect_lists(self):
        p = plan(protect_senders=[r"@mail\.shoply\.example$"], protect_subjects=["escape"])
        t = p["trash"]
        self.assertNotIn("deals@mail.shoply.example", senders(t))
        self.assertFalse([x for x in t if "escape" in x["subject"]])

    def test_spam_folder_is_cleared(self):
        t = [x for x in plan()["trash"] if x["folder"] == "spam"]
        self.assertEqual(len(t), 13)
        self.assertTrue(all(x["why"] == "spam folder" for x in t))

    def test_archive_is_opt_in(self):
        self.assertEqual(plan()["archive"], [])
        a = plan(archive_read_bulk_months=6)["archive"]
        self.assertEqual(senders(a), {"news@oldcourses.example": 16})

    def test_unsubscribe_only_never_opened_senders(self):
        u = {x["from"] for x in plan()["unsub"]}
        self.assertIn("deals@mail.shoply.example", u)
        self.assertNotIn("digest@devweekly.example", u)        # opened sometimes
        self.assertNotIn("winner@prize-centre.example", u)     # spam, no header
        self.assertNotIn("no-reply@vendorhub.example", u)      # no unsubscribe header

    def test_bulk_signals(self):
        base = {"provider": "outlook", "from": "A <a@b.example>"}
        self.assertFalse(classify.is_bulk(base)[0])
        self.assertTrue(classify.is_bulk({**base, "list_unsub": "<https://x.example/u>"})[0])
        self.assertTrue(classify.is_bulk({**base, "list_id": "<l.x.example>"})[0])
        self.assertTrue(classify.is_bulk({**base, "precedence": "Bulk"})[0])
        self.assertTrue(classify.is_bulk({**base, "auto_submitted": "auto-generated"})[0])
        self.assertFalse(classify.is_bulk({**base, "bulk_hint": True})[0])
        self.assertTrue(classify.is_bulk({**base, "bulk_hint": True, "from": "noreply@b.example"})[0])
        self.assertTrue(classify.is_bulk({**base, "bulk_hint": True, "provider": "gmail"})[0])

    def test_report_lists_every_sender(self):
        p = plan(archive_read_bulk_months=6)
        text = classify.report(p)
        for addr in senders(p["trash"] + p["ask"] + p["archive"]):
            self.assertIn(addr, text)


class Commands(unittest.TestCase):
    def run_cmd(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cleanup.main(list(argv))
        return buf.getvalue()

    def test_demo_needs_no_account(self):
        out = self.run_cmd("demo")
        self.assertIn("To trash: 173 emails", out)
        self.assertIn("made up inbox", out)

    def test_accounts_empty(self):
        self.assertIn("No accounts connected", self.run_cmd("accounts"))

    @unittest.skipUnless(platform.system() == "Darwin", "launchd is macOS only")
    def test_schedule_file_is_valid(self):
        out = self.run_cmd("schedule", "install", "--print", "--day", "3", "--hour", "7")
        d = plistlib.loads(out.encode())
        self.assertEqual(d["StartCalendarInterval"], {"Weekday": 3, "Hour": 7, "Minute": 0})
        self.assertEqual(d["ProgramArguments"][-1], "auto")
        self.assertTrue(d["ProgramArguments"][1].endswith("cleanup.py"))


if __name__ == "__main__":
    unittest.main()
