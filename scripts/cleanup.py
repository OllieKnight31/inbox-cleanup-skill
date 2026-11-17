#!/usr/bin/env python3
"""inbox-cleanup: clear never-read, not-from-a-person email out of Gmail,
Outlook and any IMAP mailbox.

    cleanup.py demo                          try it on a fake inbox, no account needed
    cleanup.py accounts                      list connected accounts
    cleanup.py auth gmail|outlook|imap       connect an account (--alias NAME)
    cleanup.py scan                          read every inbox, write a plan (changes nothing)
    cleanup.py report                        print the latest plan
    cleanup.py decide --trash A --keep B     settle the "your call" senders
    cleanup.py apply --yes                   move the planned emails to trash / archive
    cleanup.py undo                          put everything from the last apply back
    cleanup.py unsub --yes                   unsubscribe from senders you never open
    cleanup.py block --yes                   filter out senders that ignored the unsubscribe
    cleanup.py auto                          scheduled mode: only senders you approved before
    cleanup.py schedule install|remove       run auto every week

Nothing is deleted permanently. Trash / Deleted Items keep email recoverable.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import classify  # noqa: E402
import common  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; inbox-cleanup)"}
SKILL_DIR = Path(__file__).resolve().parent.parent
PLIST_LABEL = "com.inbox-cleanup.auto"


def _provider(name: str):
    if name == "gmail":
        import gmail_provider as p
    elif name == "outlook":
        import outlook_provider as p
    else:
        import imap_provider as p
    return p


def _accounts(args) -> list[tuple[str, str]]:
    accts = common.list_accounts()
    if getattr(args, "provider", None):
        accts = [a for a in accts if a[0] == args.provider]
    if getattr(args, "alias", None):
        accts = [a for a in accts if a[1] == args.alias]
    if not accts:
        raise SystemExit("No connected accounts match. Run: cleanup.py auth gmail|outlook|imap --alias NAME")
    return accts


# ---------------------------------------------------------------- accounts

def cmd_accounts(args, cfg):
    accts = common.list_accounts()
    if not accts:
        print("No accounts connected yet.")
    for prov, alias in accts:
        print(f"{prov}\t{alias}")


def cmd_auth(args, cfg):
    email = _provider(args.provider).authorize(
        cfg, args.alias, import_token=args.import_token, host=args.host,
        user=args.user, smtp_host=args.smtp_host, port=args.port)
    print(f"Connected {args.provider} account '{args.alias}' ({email})")


# -------------------------------------------------------------------- plan

def _scan_and_plan(args, cfg) -> tuple[Path, dict]:
    run = common.new_run_dir()
    scans = []
    for prov, alias in _accounts(args):
        data = _provider(prov).scan(cfg, alias, include_spam=getattr(args, "include_spam", False))
        common.save_json(run / f"scan-{prov}-{alias}.json", data)
        scans.append(data)
    if getattr(args, "min_age_days", None) is not None:
        cfg["min_age_days"] = args.min_age_days
    if getattr(args, "archive_old_months", None) is not None:
        cfg["archive_read_bulk_months"] = args.archive_old_months
    plan = classify.build_plan(scans, cfg, approved=common.load_approved())
    common.save_json(run / "plan.json", plan)
    (run / "report.md").write_text(classify.report(plan))
    return run, plan


def cmd_scan(args, cfg):
    run, plan = _scan_and_plan(args, cfg)
    print(classify.report(plan, top=args.top))
    print(f"Full plan saved to {run}")
    print("Nothing has been changed. Review, then run: cleanup.py apply --yes")


def _latest_plan() -> tuple[Path, dict]:
    run = common.latest_run_dir(having="plan.json")
    if not run:
        raise SystemExit("No plan found. Run: cleanup.py scan")
    plan = json.loads((run / "plan.json").read_text())
    for k in ("archive", "ask"):
        plan.setdefault(k, [])
    return run, plan


def cmd_report(args, cfg):
    run, plan = _latest_plan()
    print(classify.report(plan, top=args.top))
    print(f"Plan folder: {run}")


def cmd_decide(args, cfg):
    run, plan = _latest_plan()
    senders = {a["from"] for a in plan["ask"]}
    to_trash = senders if args.trash_all else {s.lower() for s in args.trash or []}
    to_keep = (senders - to_trash) if args.keep_all else {s.lower() for s in args.keep or []}
    unknown = (to_trash | to_keep) - senders
    if unknown:
        print("Not on the ask list, ignored:", ", ".join(sorted(unknown)))
    moved = [a for a in plan["ask"] if a["from"] in to_trash]
    dropped = [a for a in plan["ask"] if a["from"] in to_keep and a["from"] not in to_trash]
    plan["trash"] += moved
    plan["ask"] = [a for a in plan["ask"] if a["from"] not in to_trash | to_keep]
    common.save_json(run / "plan.json", plan)
    (run / "report.md").write_text(classify.report(plan))
    keep_now = sorted({a["from"] for a in dropped})
    if keep_now:
        common.update_user_config(lambda c: c.setdefault("protect_senders", []).extend(
            p for p in ("^" + re.escape(s) + "$" for s in keep_now)
            if p not in c.get("protect_senders", [])))
    print(f"Moved {len(moved)} emails to the trash list ({len({a['from'] for a in moved})} senders).")
    print(f"Kept {len(dropped)} emails; {len(keep_now)} senders added to your always keep list.")
    print(f"Still waiting on your call: {len({a['from'] for a in plan['ask']})} senders.")


# ----------------------------------------------------------- apply and undo

def _execute(cfg, items: list[dict], dest: str) -> list[dict]:
    """Move plan items to dest. Returns one undo entry per email actually moved."""
    groups = collections.defaultdict(list)
    for t in items:
        groups[(t["provider"], t["account"], t.get("folder", "inbox"))].append(t)
    entries = []
    for (prov, alias, src), its in groups.items():
        new_ids = _provider(prov).move(cfg, alias, [t["id"] for t in its], src, dest)
        for t, nid in zip(its, new_ids):
            if nid:
                entries.append({"provider": prov, "account": alias, "id": nid,
                                "src": src, "dest": dest, "from": t["from"]})
    return entries


def _record(run: Path, entries: list[dict]) -> None:
    prev = common.load_json(run / "applied.json", [])
    common.save_json(run / "applied.json", prev + entries)


def _summarise(entries: list[dict]) -> str:
    c = collections.Counter((e["provider"], e["account"], e["dest"]) for e in entries)
    return "\n".join(f"{p}: {a}: moved {n} emails to {d}" for (p, a, d), n in sorted(c.items()))


def cmd_apply(args, cfg):
    run, plan = _latest_plan()
    trash, archive = plan["trash"], plan["archive"]
    if args.limit:
        trash, archive = trash[:args.limit], archive[:max(0, args.limit - len(trash))]
    if not trash and not archive:
        raise SystemExit("Nothing left to apply in the latest plan. Run a fresh scan.")
    if not args.yes:
        c = collections.Counter((t["provider"], t["account"]) for t in trash)
        for (prov, alias), n in c.items():
            print(f"Would trash {n} emails in {prov}: {alias}")
        if archive:
            print(f"Would archive {len(archive)} old read bulk emails")
        if plan["ask"]:
            print(f"{len(plan['ask'])} emails still need your call (cleanup.py decide); they are left alone.")
        print("Dry run only. Add --yes to apply.")
        return
    entries = _execute(cfg, trash, "trash") + _execute(cfg, archive, "archive")
    _record(run, entries)
    done = {id(t) for t in trash + archive}
    plan["trash"] = [t for t in plan["trash"] if id(t) not in done]
    plan["archive"] = [t for t in plan["archive"] if id(t) not in done]
    common.save_json(run / "plan.json", plan)
    common.add_approved(t["from"] for t in trash if t.get("folder", "inbox") == "inbox")
    print(_summarise(entries) or "Nothing was moved.")
    failed = len(trash) + len(archive) - len(entries)
    if failed:
        print(f"{failed} emails could not be moved and were left where they are.")
    print("Changed your mind? Run: cleanup.py undo")


def cmd_undo(args, cfg):
    run = common.latest_run_dir(having="applied.json")
    if not run:
        raise SystemExit("Nothing to undo.")
    entries = common.load_json(run / "applied.json", [])
    groups = collections.defaultdict(list)
    for e in entries:
        groups[(e["provider"], e["account"], e["dest"], e["src"])].append(e["id"])
    restored = 0
    for (prov, alias, was_dest, was_src), ids in groups.items():
        back = _provider(prov).move(cfg, alias, ids, was_dest, was_src)
        n = len([x for x in back if x])
        restored += n
        print(f"{prov}: {alias}: put {n} emails back in {was_src}")
    (run / "applied.json").rename(run / f"undone-{int(time.time())}.json")
    if restored < len(entries):
        print(f"{len(entries) - restored} emails could not be found to restore (already emptied from trash?).")


# ------------------------------------------------------- unsubscribe, block

def _unsub_http(u: dict) -> str | None:
    import requests
    schemes = ("https://", "http://") if os.environ.get("INBOX_CLEANUP_ALLOW_HTTP") else ("https://",)
    parts = re.findall(r"<([^>]+)>", u["header"]) or [u["header"].strip()]
    urls = [p for p in parts if p.lower().startswith(schemes)]
    one_click = "one-click" in (u.get("post") or "").lower()
    for url in urls:
        try:
            if one_click:
                r = requests.post(url, data={"List-Unsubscribe": "One-Click"}, headers=UA, timeout=20)
                if r.status_code < 400:
                    return "one-click"
            r = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
            if r.status_code < 400:
                return "link-visited"
        except Exception:
            continue
    return None


def _unsub_mailto(u: dict, cfg) -> str | None:
    parts = re.findall(r"<([^>]+)>", u["header"]) or [u["header"].strip()]
    mails = [p for p in parts if p.lower().startswith("mailto:")]
    if not mails:
        return None
    url = urllib.parse.urlparse(mails[0])
    q = urllib.parse.parse_qs(url.query)
    try:
        _provider(u["provider"]).send(
            cfg, u["account"], urllib.parse.unquote(url.path),
            q.get("subject", ["unsubscribe"])[0], q.get("body", ["unsubscribe"])[0])
        return "email-sent"
    except Exception:
        return None


def cmd_unsub(args, cfg):
    run, plan = _latest_plan()
    todo = plan["unsub"]
    if not args.yes:
        for u in todo:
            print(f"Would unsubscribe: {u['from']} ({u['emails']} emails, none opened)")
        print(f"{len(todo)} senders. Dry run only. Add --yes to unsubscribe.")
        return
    with cf.ThreadPoolExecutor(8) as ex:
        http = list(ex.map(_unsub_http, todo))
    results = []
    for u, method in zip(todo, http):
        method = method or _unsub_mailto(u, cfg) or "failed"
        results.append({"provider": u["provider"], "account": u["account"],
                        "from": u["from"], "result": method})
    common.save_json(run / "unsub_results.json", results)
    print("Unsubscribe results:", dict(collections.Counter(r["result"] for r in results)))
    failed = [r["from"] for r in results if r["result"] == "failed"]
    if failed:
        print("Could not unsubscribe automatically:")
        for f in failed:
            print(" ", f)
        print("Stop their mail reaching your inbox with: cleanup.py block --yes")


def cmd_block(args, cfg):
    if args.all:
        _, plan = _latest_plan()
        targets = plan["unsub"]
    else:
        run = common.latest_run_dir(having="unsub_results.json")
        if not run:
            raise SystemExit("No unsubscribe results yet. Run: cleanup.py unsub --yes (or use block --all)")
        targets = [r for r in common.load_json(run / "unsub_results.json", []) if r["result"] == "failed"]
    groups = collections.defaultdict(list)
    for t in targets:
        groups[(t["provider"], t["account"])].append(t["from"])
    if not groups:
        print("No senders to block.")
        return
    action = cfg.get("block_action", "trash")
    for (prov, alias), senders in groups.items():
        if not args.yes:
            print(f"Would create {len(senders)} rules in {prov}: {alias} (future mail goes to {action}):")
            for s in senders:
                print(" ", s)
            continue
        ok, failed = _provider(prov).block(cfg, alias, senders, action)
        print(f"{prov}: {alias}: blocked {len(ok)} senders")
        if failed:
            why = " (IMAP servers do not support rules; block these in your mail app)" if prov == "imap" else ""
            print(f"{prov}: {alias}: could not block {len(failed)}{why}: {', '.join(failed)}")
    if not args.yes:
        print("Dry run only. Add --yes to create the rules.")


# ------------------------------------------------------- scheduled running

def _notify(cfg, text: str) -> None:
    (common.HOME / "last_auto.md").write_text(text)
    hook = (cfg.get("notify_webhook") or "").strip()
    if hook:
        try:
            import requests
            requests.post(hook, json={"content": text[:1900], "text": text[:1900]}, timeout=20)
        except Exception as e:
            print(f"Could not send the summary to the webhook: {str(e)[:80]}")


def cmd_auto(args, cfg):
    """Scheduled mode. Trashes junk only from senders approved in an earlier run.
    Anything from a new sender waits in the plan for a person to look at."""
    run, plan = _scan_and_plan(args, cfg)
    approved = common.load_approved()
    go = [t for t in plan["trash"] if t["from"] in approved and t.get("folder", "inbox") == "inbox"]
    entries = _execute(cfg, go, "trash") if go else []
    if entries:
        _record(run, entries)
    done = {id(t) for t in go}
    plan["trash"] = [t for t in plan["trash"] if id(t) not in done]
    common.save_json(run / "plan.json", plan)
    (run / "report.md").write_text(classify.report(plan))
    new_senders = len({(t["account"], t["from"]) for t in plan["trash"]})
    ask_senders = len({(t["account"], t["from"]) for t in plan["ask"]})
    text = "\n".join([
        f"Inbox cleanup, {time.strftime('%Y-%m-%d %H:%M')}",
        f"Trashed automatically: {len(entries)} emails from senders you approved before.",
        f"Waiting for you: {len(plan['trash'])} emails from {new_senders} new senders, "
        f"and {len(plan['ask'])} emails from {ask_senders} senders that need your call.",
        "Review with: cleanup.py report   Undo with: cleanup.py undo",
    ])
    _notify(cfg, text)
    print(text)


def _plist(day: int, hour: int) -> str:
    env = ""
    if os.environ.get("INBOX_CLEANUP_HOME"):
        env = ("  <key>EnvironmentVariables</key>\n  <dict>\n    <key>INBOX_CLEANUP_HOME</key>\n"
               f"    <string>{common.HOME}</string>\n  </dict>\n")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{Path(__file__).resolve()}</string>
    <string>auto</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>{day}</integer>
    <key>Hour</key>
    <integer>{hour}</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
{env}  <key>StandardOutPath</key>
  <string>{common.HOME / 'auto.log'}</string>
  <key>StandardErrorPath</key>
  <string>{common.HOME / 'auto.log'}</string>
</dict>
</plist>
"""


def cmd_schedule(args, cfg):
    mac = platform.system() == "Darwin"
    target = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
    cron = f"0 {args.hour} * * {args.day % 7} {sys.executable} {Path(__file__).resolve()} auto"
    if args.action == "remove":
        if mac and target.exists():
            subprocess.run(["launchctl", "unload", str(target)], capture_output=True)
            target.unlink()
            print("Weekly run removed.")
        else:
            print("No weekly run installed here." if mac else "Remove the inbox-cleanup line from your crontab.")
        return
    if not mac:
        print("Add this line to your crontab (crontab -e):")
        print(cron)
        return
    if args.print:
        print(_plist(args.day, args.hour))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(target)], capture_output=True)
    target.write_text(_plist(args.day, args.hour))
    r = subprocess.run(["launchctl", "load", str(target)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"Could not load the weekly run: {r.stderr.strip()}")
    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    print(f"Weekly run installed: every {days[args.day % 7]} at {args.hour:02d}:00.")
    print("It only trashes junk from senders you approved in an earlier run.")


# -------------------------------------------------------------------- demo

def load_sample() -> list[dict]:
    data = json.loads((SKILL_DIR / "sample" / "inbox.json").read_text())
    now = time.time() * 1000
    for scan in data:
        for m in scan["messages"]:
            m["ts"] = int(now - m.pop("age_days") * classify.DAY_MS)
    return data


def cmd_demo(args, cfg):
    demo_cfg = common.default_config()
    demo_cfg["archive_read_bulk_months"] = 6
    plan = classify.build_plan(load_sample(), demo_cfg)
    print(classify.report(plan, top=args.top))
    print("This was a made up inbox. Nothing was read or changed.")
    print("Connect a real account with: cleanup.py auth gmail|outlook|imap --alias NAME")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("accounts")
    d = sub.add_parser("demo")
    d.add_argument("--top", type=int, default=0)
    a = sub.add_parser("auth")
    a.add_argument("provider", choices=common.PROVIDERS)
    a.add_argument("--alias", default="main", help="short name for this account, e.g. personal or work")
    a.add_argument("--import-token", help="gmail only: reuse an existing authorized-user token file")
    a.add_argument("--host", help="imap only: server, e.g. imap.mail.yahoo.com")
    a.add_argument("--user", help="imap only: your email address / login")
    a.add_argument("--smtp-host", help="imap only: outgoing server (default: imap. swapped for smtp.)")
    a.add_argument("--port", type=int, default=993, help="imap only")
    for name in ("scan", "auto"):
        s = sub.add_parser(name)
        s.add_argument("--provider", choices=common.PROVIDERS)
        s.add_argument("--alias")
        s.add_argument("--min-age-days", type=float)
        s.add_argument("--include-spam", action="store_true", help="also clear the spam / junk folder")
        s.add_argument("--archive-old-months", type=float,
                       help="also archive bulk email you already read that is older than N months")
        s.add_argument("--top", type=int, default=0, help="show only the top N senders (0 = all)")
    r = sub.add_parser("report")
    r.add_argument("--top", type=int, default=0)
    dc = sub.add_parser("decide")
    dc.add_argument("--trash", nargs="*", help="sender addresses on the ask list to trash")
    dc.add_argument("--keep", nargs="*", help="sender addresses on the ask list to always keep")
    dc.add_argument("--trash-all", action="store_true")
    dc.add_argument("--keep-all", action="store_true", help="keep every sender not named in --trash")
    x = sub.add_parser("apply")
    x.add_argument("--yes", action="store_true", help="actually do it (default is a dry run)")
    x.add_argument("--limit", type=int, default=0, help="only move the first N emails, to try it out")
    sub.add_parser("undo")
    u = sub.add_parser("unsub")
    u.add_argument("--yes", action="store_true")
    b = sub.add_parser("block")
    b.add_argument("--yes", action="store_true")
    b.add_argument("--all", action="store_true", help="block every unsubscribe candidate, not only failures")
    sc = sub.add_parser("schedule")
    sc.add_argument("action", choices=["install", "remove"])
    sc.add_argument("--day", type=int, default=1, help="0 Sunday ... 6 Saturday (default 1, Monday)")
    sc.add_argument("--hour", type=int, default=9)
    sc.add_argument("--print", action="store_true", help="show the schedule file without installing it")

    args = ap.parse_args(argv)
    cfg = common.load_config()
    {"accounts": cmd_accounts, "auth": cmd_auth, "scan": cmd_scan, "report": cmd_report,
     "decide": cmd_decide, "apply": cmd_apply, "undo": cmd_undo, "unsub": cmd_unsub,
     "block": cmd_block, "auto": cmd_auto, "schedule": cmd_schedule, "demo": cmd_demo}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
