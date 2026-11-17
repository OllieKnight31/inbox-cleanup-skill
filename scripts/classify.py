"""Decide what is junk.

An inbox email is trashed only when ALL of these are true:
  1. it was never opened (unread)
  2. it is not from a real person (bulk or automated sender)
  3. it is older than min_age_days
  4. nothing protects it (starred, a sender you have written to, a thread you
     replied in, your protect lists, or a transactional subject)
  5. you have never opened anything from that sender, or you already approved
     trashing that sender

Unread junk from a bulk sender you open now and then goes to an "ask" list
instead, so a person decides. Bulk senders you usually open are kept.
"""
from __future__ import annotations

import collections
import re
import time

from common import parse_addr

DAY_MS = 86_400_000

ROBOT_LOCAL = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|noreply.*|newsletter.*|news|marketing|promo.*|offers?|"
    r"deals?|mailer.*|notifications?|notify|updates?|digest|alerts?|info|hello|hi|team|"
    r"bounce.*|postmaster|email|mail|announce.*|campaigns?|events?|community|members?|rewards?)$",
    re.I,
)
ROBOT_DOMAIN = re.compile(
    r"(^|\.)(e|em|email|mail|mailer|news|newsletter|send|sender|reply|mkt|marketing|"
    r"notifications?|campaign|bounce|engage|info|updates?|promo)\d*\.", re.I,
)
TRANSACTIONAL = re.compile(
    r"\b(receipt|invoice|order (confirm|#|number|no)|your order|booking|reservation|itinerary|"
    r"e-?ticket|boarding pass|payment|refund|statement|tax|payslip|contract|"
    r"password reset|reset your password|security alert|delivery|shipped|tracking|"
    r"appointment|renewal|subscription (confirm|renew)|verify your)\b",
    re.I,
)


def is_bulk(m: dict) -> tuple[bool, str]:
    """True when the email was sent by a machine to a list, not by a person."""
    if m.get("list_unsub"):
        return True, "has unsubscribe header"
    if m.get("list_id"):
        return True, "mailing list"
    if (m.get("precedence") or "").lower() in ("bulk", "list", "junk"):
        return True, "bulk precedence"
    if (m.get("auto_submitted") or "no").lower() not in ("no", ""):
        return True, "auto generated"
    addr = parse_addr(m.get("from"))
    local, _, domain = addr.partition("@")
    robot_addr = bool(ROBOT_LOCAL.match(local)) or bool(ROBOT_DOMAIN.search(domain + "."))
    if m.get("bulk_hint") and robot_addr:
        return True, "robot address in promotions/other"
    if m.get("bulk_hint") and m["provider"] == "gmail":
        return True, "promotions/social tab"
    return False, ""


def build_plan(scans: list[dict], cfg: dict, approved=()) -> dict:
    now = time.time() * 1000
    min_age = float(cfg.get("min_age_days", 2))
    keep_rate = float(cfg.get("ask_keep_read_rate", 0.5))
    archive_days = float(cfg.get("archive_read_bulk_months", 0) or 0) * 30.4
    prot_s = [re.compile(p, re.I) for p in cfg.get("protect_senders", [])]
    prot_j = [re.compile(p, re.I) for p in cfg.get("protect_subjects", [])]
    approved = {a.lower() for a in approved}

    # Pass 1: how often does the user open each bulk sender?
    stats: dict[tuple, dict] = {}
    for scan in scans:
        for m in scan["messages"]:
            m["_addr"] = parse_addr(m["from"])
            m["_bulk"], m["_why"] = is_bulk(m)
            if m["_bulk"] and m.get("folder", "inbox") == "inbox":
                key = (m["provider"], m["account"], m["_addr"])
                s = stats.setdefault(key, {"total": 0, "read": 0, "unsub": None, "ts": 0})
                s["total"] += 1
                s["read"] += 0 if m["unread"] else 1
                if m.get("list_unsub") and m["ts"] >= s["ts"]:
                    s["ts"] = m["ts"]
                    s["unsub"] = {"header": m["list_unsub"], "post": m.get("list_unsub_post")}

    trash, archive, ask, kept = [], [], [], collections.Counter()

    def item(m, why):
        return {
            "id": m["id"], "provider": m["provider"], "account": m["account"],
            "folder": m.get("folder", "inbox"), "from": m["_addr"],
            "subject": (m["subject"] or "")[:120], "why": why,
        }

    # Pass 2: decide each email.
    for scan in scans:
        me, contacts = scan["me"], set(scan["contacts"])
        for m in scan["messages"]:
            addr, subj = m["_addr"], m["subject"] or ""
            age = (now - m["ts"]) / DAY_MS
            known = addr == me or addr in contacts
            prot = None
            if m["starred"]:
                prot = "starred or flagged"
            elif known:
                prot = "sender you have written to"
            elif m.get("replied_thread"):
                prot = "thread you replied in"
            elif any(p.search(addr) for p in prot_s):
                prot = "protected sender"
            elif any(p.search(subj) for p in prot_j):
                prot = "protected subject"
            elif cfg.get("keep_transactional", True) and TRANSACTIONAL.search(subj):
                prot = "receipt, booking or security email"

            if m.get("folder") == "spam":
                if prot in ("sender you have written to", "protected sender", "starred or flagged"):
                    kept[f"in spam, but {prot}"] += 1
                else:
                    trash.append(item(m, "spam folder"))
                continue

            if not m["unread"]:
                if archive_days and m["_bulk"] and age > archive_days and not prot:
                    archive.append(item(m, "read bulk email, old"))
                else:
                    kept["already read"] += 1
                continue
            if not m["_bulk"]:
                kept["from a real person"] += 1
                continue
            if prot:
                kept[prot] += 1
                continue
            if age < min_age:
                kept[f"newer than {min_age:g} days"] += 1
                continue

            s = stats[(m["provider"], m["account"], addr)]
            rate = s["read"] / s["total"]
            if rate == 0 or addr in approved:
                trash.append(item(m, m["_why"]))
            elif rate < keep_rate:
                it = item(m, m["_why"])
                it["opened"], it["total"] = s["read"], s["total"]
                ask.append(it)
            else:
                kept["bulk sender you usually open"] += 1

    trash_senders = {(t["provider"], t["account"], t["from"]) for t in trash if t["folder"] == "inbox"}
    unsub = []
    for key, s in stats.items():
        if (key in trash_senders and s["read"] == 0 and s["unsub"]
                and s["total"] >= int(cfg.get("unsub_min_emails", 2))):
            unsub.append({
                "provider": key[0], "account": key[1], "from": key[2],
                "emails": s["total"], **s["unsub"],
            })
    unsub.sort(key=lambda u: -u["emails"])
    for scan in scans:
        for m in scan["messages"]:
            for k in ("_addr", "_bulk", "_why"):
                m.pop(k, None)
    return {"trash": trash, "archive": archive, "ask": ask, "unsub": unsub, "kept": dict(kept)}


def _sender_table(items: list[dict], top: int, extra=None) -> list[str]:
    head = "| Emails | Sender | Example subject | Signal |"
    lines = [head, "|---:|---|---|---|"]
    count = collections.Counter(t["from"] for t in items)
    first = {}
    for t in items:
        first.setdefault(t["from"], t)
    for addr, n in count.most_common(top or None):
        f = first[addr]
        subj = f["subject"].replace("|", "/")[:60]
        sig = extra(f) if extra else f["why"]
        lines.append(f"| {n} | {addr} | {subj} | {sig} |")
    return lines


def report(plan: dict, top: int = 0) -> str:
    trash, archive, ask = plan["trash"], plan.get("archive", []), plan.get("ask", [])
    lines = ["# Inbox cleanup plan", ""]
    lines.append(f"**To trash: {len(trash)} emails** (never opened, not from a real person)")
    if archive:
        lines.append(f"**To archive: {len(archive)} emails** (old bulk email you already read)")
    if ask:
        n = len({(a['account'], a['from']) for a in ask})
        lines.append(f"**Needs your call: {len(ask)} emails from {n} senders** (you open these now and then)")
    lines.append(f"**To unsubscribe: {len(plan['unsub'])} senders** (never opened a single email from them)")
    lines.append("")

    def by_account(items):
        g = collections.defaultdict(list)
        for t in items:
            g[(t["provider"], t["account"])].append(t)
        return g

    for (prov, acct), items in by_account(trash).items():
        lines += [f"## Trash: {prov}: {acct} ({len(items)} emails)", ""]
        lines += _sender_table(items, top) + [""]
    for (prov, acct), items in by_account(archive).items():
        lines += [f"## Archive: {prov}: {acct} ({len(items)} emails)", ""]
        lines += _sender_table(items, top) + [""]
    for (prov, acct), items in by_account(ask).items():
        lines += [f"## Your call: {prov}: {acct} ({len(items)} emails)", "",
                  "Trash these, or keep them? Decide per sender.", ""]
        lines += _sender_table(
            items, top, extra=lambda f: f"you opened {f['opened']} of {f['total']}") + [""]
    lines += ["## Kept", ""]
    for k, v in sorted(plan["kept"].items(), key=lambda x: -x[1]):
        lines.append(f"- {v} kept: {k}")
    if plan["unsub"]:
        lines += ["", "## Unsubscribe candidates", ""]
        for u in plan["unsub"]:
            lines.append(f"- {u['emails']} emails, none opened: {u['from']} ({u['provider']}: {u['account']})")
    return "\n".join(lines) + "\n"
