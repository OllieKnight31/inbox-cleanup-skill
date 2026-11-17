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
TOKENS_DIR = HOME / "tokens"
RUNS_DIR = HOME / "runs"

DEFAULT_CONFIG = {
    # Only unread mail older than this many days is ever trashed.
    "min_age_days": 2,
    # A bulk sender with at least this many emails and none ever opened
    # becomes an unsubscribe candidate.
    "unsub_min_emails": 2,
    # Regexes (case insensitive) matched against the sender address.
    # Anything matching is always kept. Put banks, clients, family here.
    "protect_senders": [],
    # Regexes matched against the subject. Anything matching is always kept.
    "protect_subjects": [],
    # Keep unread receipts, invoices, bookings, tickets and similar.
    "keep_transactional": True,
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


def load_config() -> dict:
    ensure_home()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
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


def save_json(path: Path, data, private: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    if private:
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def token_path(provider: str, alias: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", alias)
    return TOKENS_DIR / f"{provider}-{safe}.json"


def list_accounts() -> list[tuple[str, str]]:
    ensure_home()
    out = []
    for p in sorted(TOKENS_DIR.glob("*.json")):
        provider, _, alias = p.stem.partition("-")
        if provider in ("gmail", "outlook") and alias:
            out.append((provider, alias))
    return out


def new_run_dir() -> Path:
    d = RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_run_dir() -> Path | None:
    ensure_home()
    runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())
    return runs[-1] if runs else None


def parse_addr(value: str | None) -> str:
    m = re.search(r"<([^>]+)>", value or "")
    return (m.group(1) if m else (value or "")).strip().lower()
