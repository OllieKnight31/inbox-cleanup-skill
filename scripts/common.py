"""Shared paths, config loading and small helpers for inbox-cleanup.

All user data (OAuth keys, tokens, scan results, personal protect rules) lives
in a private folder outside the skill, by default ~/.inbox-cleanup. Nothing
personal is ever written inside the skill folder.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path

HOME = Path(os.environ.get("INBOX_CLEANUP_HOME", Path.home() / ".inbox-cleanup"))
CONFIG_FILE = HOME / "config.json"
APPROVED_FILE = HOME / "approved.json"
TOKENS_DIR = HOME / "tokens"
RUNS_DIR = HOME / "runs"
PROVIDERS = ("gmail", "outlook", "imap")

DEFAULT_CONFIG = {
    # Only unread mail older than this many days is ever trashed.
    "min_age_days": 2,
    # A bulk sender with at least this many emails and none ever opened
    # becomes an unsubscribe candidate.
    "unsub_min_emails": 2,
    # Bulk senders you open at least this share of the time are kept.
    # Senders below it (but above zero) go to the "ask" list.
    "ask_keep_read_rate": 0.5,
    # Archive bulk email you already read once it is older than this many
    # months. 0 turns it off.
    "archive_read_bulk_months": 0,
    # What a block rule does with future mail: "trash" or "archive".
    "block_action": "trash",
    # Regexes (case insensitive) matched against the sender address.
    # Anything matching is always kept. Put banks, clients, family here.
    "protect_senders": [],
    # Regexes matched against the subject. Anything matching is always kept.
    "protect_subjects": [],
    # Keep unread receipts, invoices, bookings, tickets and similar.
    "keep_transactional": True,
    # Optional Slack or Discord webhook that receives the scheduled run summary.
    "notify_webhook": "",
    "gmail": {
        # OAuth desktop client JSON downloaded from Google Cloud Console.
        "oauth_client_file": str(HOME / "gmail_oauth_client.json"),
    },
    "outlook": {
        # Application (client) ID of your Azure app registration.
        "client_id": "",
        # "common" covers personal and work accounts.
        "tenant": "common",
    },
}


def ensure_home() -> None:
    for d in (HOME, TOKENS_DIR, RUNS_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(stat.S_IRWXU)
        except OSError:
            pass


def default_config() -> dict:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def load_config() -> dict:
    ensure_home()
    cfg = default_config()
    if CONFIG_FILE.exists():
        user = json.loads(CONFIG_FILE.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    else:
        save_json(CONFIG_FILE, cfg)
    return cfg


def update_user_config(mutate) -> None:
    """Apply a change to the user's config.json without writing defaults into it."""
    ensure_home()
    user = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else default_config()
    mutate(user)
    save_json(CONFIG_FILE, user)


def save_json(path: Path, data, private: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    if private:
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def load_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def load_approved() -> set[str]:
    """Senders whose junk the user has already approved trashing."""
    return set((load_json(APPROVED_FILE, {}) or {}).get("trash_senders", []))


def add_approved(senders) -> None:
    cur = load_approved() | {s.lower() for s in senders}
    save_json(APPROVED_FILE, {"trash_senders": sorted(cur)})


def token_path(provider: str, alias: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", alias)
    return TOKENS_DIR / f"{provider}-{safe}.json"


def list_accounts() -> list[tuple[str, str]]:
    ensure_home()
    out = []
    for p in sorted(TOKENS_DIR.glob("*.json")):
        provider, _, alias = p.stem.partition("-")
        if provider in PROVIDERS and alias:
            out.append((provider, alias))
    return out


def new_run_dir() -> Path:
    base = RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
    d, n = base, 1
    while d.exists():
        n += 1
        d = Path(f"{base}-{n}")
    d.mkdir(parents=True)
    return d


def run_dirs() -> list[Path]:
    ensure_home()
    return sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())


def latest_run_dir(having: str | None = None) -> Path | None:
    for d in reversed(run_dirs()):
        if having is None or (d / having).exists():
            return d
    return None


def parse_addr(value: str | None) -> str:
    m = re.search(r"<([^>]+)>", value or "")
    return (m.group(1) if m else (value or "")).strip().lower()
