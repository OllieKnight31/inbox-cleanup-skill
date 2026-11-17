#!/usr/bin/env python3
"""inbox-cleanup: clear never-read, not-from-a-person email out of Gmail and Outlook.

    cleanup.py accounts                      list connected accounts
    cleanup.py auth gmail   --alias NAME     connect a Gmail account
    cleanup.py auth outlook --alias NAME     connect an Outlook / Microsoft 365 account
    cleanup.py scan                          read every connected inbox, write a plan (changes nothing)
    cleanup.py report                        print the latest plan
    cleanup.py apply --yes                   move the planned emails to Trash / Deleted Items
    cleanup.py unsub --yes                   unsubscribe from senders you never open

Nothing is deleted permanently. Gmail Trash and Outlook Deleted Items keep
email recoverable (30 days by default).
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import classify  # noqa: E402
import common  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; inbox-cleanup)"}


def _provider(name: str):
    if name == "gmail":
        import gmail_provider
        return gmail_provider
    import outlook_provider
    return outlook_provider


def _accounts(args) -> list[tuple[str, str]]:
    accts = common.list_accounts()
    if getattr(args, "provider", None):
        accts = [a for a in accts if a[0] == args.provider]
    if getattr(args, "alias", None):
        accts = [a for a in accts if a[1] == args.alias]
    if not accts:
        raise SystemExit("No connected accounts match. Run: cleanup.py auth gmail|outlook --alias NAME")
    return accts


def cmd_accounts(args, cfg):
    accts = common.list_accounts()
    if not accts:
        print("No accounts connected yet.")
    for prov, alias in accts:
        print(f"{prov}\t{alias}")


def cmd_auth(args, cfg):
    if args.provider == "gmail":
        email = _provider("gmail").authorize(cfg, args.alias, args.import_token)
    else:
        email = _provider("outlook").authorize(cfg, args.alias)
    print(f"Connected {args.provider} account '{args.alias}' ({email})")


def cmd_scan(args, cfg):
    run = common.new_run_dir()
    scans = []
    for prov, alias in _accounts(args):
        p = _provider(prov)
        data = p.scan(alias) if prov == "gmail" else p.scan(cfg, alias)
        common.save_json(run / f"scan-{prov}-{alias}.json", data)
        scans.append(data)
    if args.min_age_days is not None:
        cfg["min_age_days"] = args.min_age_days
    plan = classify.build_plan(scans, cfg)
    common.save_json(run / "plan.json", plan)
    text = classify.report(plan)
    (run / "report.md").write_text(text)
    print(classify.report(plan, top=args.top))
    print(f"Full plan saved to {run}")
    print("Nothing has been changed. Review, then run: cleanup.py apply --yes")


def _latest_plan() -> tuple[Path, dict]:
    run = common.latest_run_dir()
    if not run or not (run / "plan.json").exists():
        raise SystemExit("No plan found. Run: cleanup.py scan")
    return run, json.loads((run / "plan.json").read_text())


def cmd_report(args, cfg):
    run, plan = _latest_plan()
    print(classify.report(plan, top=args.top))
    print(f"Plan folder: {run}")


def cmd_apply(args, cfg):
    run, plan = _latest_plan()
    if (run / "applied.json").exists():
        raise SystemExit("This plan was already applied. Run a fresh scan first.")
    groups = collections.defaultdict(list)
    for t in plan["trash"]:
        groups[(t["provider"], t["account"])].append(t["id"])
    if not args.yes:
        for (prov, alias), ids in groups.items():
            print(f"Would trash {len(ids)} emails in {prov}: {alias}")
        print("Dry run only. Add --yes to apply.")
        return
    result = {}
    for (prov, alias), ids in groups.items():
        p = _provider(prov)
        n = p.trash(alias, ids) if prov == "gmail" else p.trash(cfg, alias, ids)
        result[f"{prov}:{alias}"] = n
        print(f"{prov}: {alias}: moved {n} emails to trash")
    common.save_json(run / "applied.json", result)


def _unsub_http(u: dict) -> str | None:
    import requests
    parts = re.findall(r"<([^>]+)>", u["header"]) or [u["header"].strip()]
    urls = [p for p in parts if p.lower().startswith("https://")]
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
    to = urllib.parse.unquote(url.path)
    subject = q.get("subject", ["unsubscribe"])[0]
    body = q.get("body", ["unsubscribe"])[0]
    try:
        p = _provider(u["provider"])
        if u["provider"] == "gmail":
            p.send(u["account"], to, subject, body)
        else:
            p.send(cfg, u["account"], to, subject, body)
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
        results.append({"from": u["from"], "account": u["account"], "result": method})
    common.save_json(run / "unsub_results.json", results)
    counts = collections.Counter(r["result"] for r in results)
    print("Unsubscribe results:", dict(counts))
    failed = [r["from"] for r in results if r["result"] == "failed"]
    if failed:
        print("Could not unsubscribe automatically (block or filter these by hand):")
        for f in failed:
            print(" ", f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("accounts")
    a = sub.add_parser("auth")
    a.add_argument("provider", choices=["gmail", "outlook"])
    a.add_argument("--alias", default="main", help="short name for this account, e.g. personal or work")
    a.add_argument("--import-token", help="gmail only: reuse an existing authorized-user token file")
    s = sub.add_parser("scan")
    s.add_argument("--provider", choices=["gmail", "outlook"])
    s.add_argument("--alias")
    s.add_argument("--min-age-days", type=float)
    s.add_argument("--top", type=int, default=0, help="show only the top N senders (0 = all)")
    r = sub.add_parser("report")
    r.add_argument("--top", type=int, default=0)
    for name in ("apply", "unsub"):
        x = sub.add_parser(name)
        x.add_argument("--yes", action="store_true", help="actually do it (default is a dry run)")

    args = ap.parse_args()
    cfg = common.load_config()
    {"accounts": cmd_accounts, "auth": cmd_auth, "scan": cmd_scan, "report": cmd_report,
     "apply": cmd_apply, "unsub": cmd_unsub}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
