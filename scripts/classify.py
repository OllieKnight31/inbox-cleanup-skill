"""Decide what is junk.

An email is trashed only when ALL of these are true:
  1. it was never opened (unread)
  2. it is not from a real person (bulk or automated sender)
  3. it is older than min_age_days
  4. nothing protects it (starred, a sender you have written to, a thread you
     replied in, your protect lists, or a transactional subject)
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


def build_plan(scans: list[dict], cfg: dict) -> dict:
    now = time.time() * 1000
    min_age = float(cfg.get("min_age_days", 2))
    prot_s = [re.compile(p, re.I) for p in cfg.get("protect_senders", [])]
    prot_j = [re.compile(p, re.I) for p in cfg.get("protect_subjects", [])]

    trash, kept = [], collections.Counter()
    stats: dict[tuple, dict] = {}

    for scan in scans:
        me = scan["me"]
        contacts = set(scan["contacts"])
        for m in scan["messages"]:
            addr = parse_addr(m["from"])
            key = (m["provider"], m["account"], addr)
            bulk, why = is_bulk(m)
            if bulk:
                s = stats.setdefault(key, {"total": 0, "read": 0, "unsub": None, "ts": 0})
                s["total"] += 1
                s["read"] += 0 if m["unread"] else 1
                if m.get("list_unsub") and m["ts"] >= s["ts"]:
                    s["ts"] = m["ts"]
                    s["unsub"] = {"header": m["list_unsub"], "post": m.get("list_unsub_post")}

            age = (now - m["ts"]) / DAY_MS
            if not m["unread"]:
                kept["already read"] += 1
            elif not bulk:
                kept["from a real person"] += 1
            elif m["starred"]:
                kept["starred or flagged"] += 1
            elif addr == me or addr in contacts:
                kept["sender you have written to"] += 1
            elif m.get("replied_thread"):
                kept["thread you replied in"] += 1
            elif any(p.search(addr) for p in prot_s):
                kept["protected sender"] += 1
            elif any(p.search(m["subject"]) for p in prot_j):
                kept["protected subject"] += 1
            elif cfg.get("keep_transactional", True) and TRANSACTIONAL.search(m["subject"]):
                kept["receipt, booking or security email"] += 1
            elif age < min_age:
                kept[f"newer than {min_age:g} days"] += 1
            else:
                trash.append({
                    "id": m["id"], "provider": m["provider"], "account": m["account"],
                    "from": addr, "subject": m["subject"][:120], "why": why,
                })

    trash_senders = {(t["provider"], t["account"], t["from"]) for t in trash}
    unsub = []
    for key, s in stats.items():
        if (key in trash_senders and s["read"] == 0 and s["unsub"]
                and s["total"] >= int(cfg.get("unsub_min_emails", 2))):
            unsub.append({
                "provider": key[0], "account": key[1], "from": key[2],
                "emails": s["total"], **s["unsub"],
            })
    unsub.sort(key=lambda u: -u["emails"])
    return {"trash": trash, "unsub": unsub, "kept": dict(kept)}


def report(plan: dict, top: int = 0) -> str:
    lines = ["# Inbox cleanup plan", ""]
    by_acct = collections.defaultdict(list)
    for t in plan["trash"]:
        by_acct[(t["provider"], t["account"])].append(t)
    lines.append(f"**To trash: {len(plan['trash'])} emails** (unread, not from a real person)")
    lines.append(f"**To unsubscribe: {len(plan['unsub'])} senders** (never opened a single email from them)")
    lines.append("")
    for (prov, acct), items in by_acct.items():
        lines += [f"## {prov}: {acct} ({len(items)} to trash)", "",
                  "| Emails | Sender | Example subject | Signal |", "|---:|---|---|---|"]
        c = collections.Counter(t["from"] for t in items)
        first = {}
        for t in items:
            first.setdefault(t["from"], t)
        rows = c.most_common(top or None)
        for addr, n in rows:
            subj = first[addr]["subject"].replace("|", "/")[:60]
            lines.append(f"| {n} | {addr} | {subj} | {first[addr]['why']} |")
        lines.append("")
    lines += ["## Kept", ""]
    for k, v in sorted(plan["kept"].items(), key=lambda x: -x[1]):
        lines.append(f"- {v} kept: {k}")
    if plan["unsub"]:
        lines += ["", "## Unsubscribe candidates", ""]
        for u in plan["unsub"]:
            lines.append(f"- {u['emails']} emails, none opened: {u['from']} ({u['provider']}: {u['account']})")
    return "\n".join(lines) + "\n"
