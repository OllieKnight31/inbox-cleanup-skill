---
name: inbox-cleanup
description: Clean junk out of Gmail and Outlook / Microsoft 365 inboxes. Finds every email that was never opened and is not from a real person (newsletters, promotions, automated notifications, bulk senders), shows a plan grouped by sender, then moves them to Trash and unsubscribes from senders you never read. Works across several accounts at once. Dry run by default and nothing is permanently deleted. Use when the user wants to clean an inbox, clear spam or unread junk, mass unsubscribe, or reach inbox zero. Aliases /inbox-cleanup, /clean-inbox, /email-cleanup. Triggers on "clean my inbox", "clean up my emails", "clear the spam", "delete unread junk", "unsubscribe me from everything", "inbox zero", "tidy my Gmail", "tidy my Outlook".
---

# Inbox Cleanup

Clears email that was **never read** and is **not from a real person** out of Gmail and Outlook. Everything else stays.

All commands run from this skill's folder:

```bash
python3 scripts/cleanup.py <command>
```

Private data (OAuth keys, tokens, scan results, personal protect rules) lives in `~/.inbox-cleanup/`, never inside this skill folder.

## The rule

An email is trashed only when all four are true:

1. It is unread.
2. It is bulk or automated: it has an unsubscribe header, a mailing list ID, bulk precedence, an auto generated flag, or it sits in Gmail Promotions / Social / Forums or Outlook "Other" with a robot style sender address.
3. It is older than `min_age_days` (default 2).
4. Nothing protects it.

Always kept: anything read, anything from a real person, starred or flagged email, senders the user has ever written to, threads the user replied in, senders or subjects on the user's protect lists, and receipts, invoices, bookings, tickets, deliveries and security emails.

## Workflow

### 1. Check accounts

```bash
python3 scripts/cleanup.py accounts
```

If the account the user wants is missing, connect it. First time setup of the Google or Microsoft app is in `references/setup.md`; read it and walk the user through only the part they need.

```bash
python3 scripts/cleanup.py auth gmail --alias personal
python3 scripts/cleanup.py auth outlook --alias work
```

Gmail opens a browser for consent. Outlook prints a short code and a link; show both to the user and wait for them to finish signing in.

### 2. Scan (changes nothing)

```bash
python3 scripts/cleanup.py scan
```

Optional flags: `--provider gmail|outlook`, `--alias NAME`, `--min-age-days N`, `--top N`.

This reads headers only, never email bodies, then prints the plan: how many emails go to trash per sender, what was kept and why, and which senders are unsubscribe candidates.

### 3. Show the plan and get a yes

Show the user the full sender table, not a sample. Ask them to name anything that should stay. For each sender or topic they want kept, add a regex to `protect_senders` or `protect_subjects` in `~/.inbox-cleanup/config.json`, then scan again.

Never run `apply --yes` or `unsub --yes` without the user approving that specific plan in this conversation.

### 4. Apply

```bash
python3 scripts/cleanup.py apply --yes
```

Gmail email moves to Trash, Outlook email moves to Deleted Items. Both are recoverable (30 days by default). A plan can be applied once; scan again for a fresh one.

### 5. Unsubscribe (optional, ask first)

```bash
python3 scripts/cleanup.py unsub --yes
```

Only targets bulk senders where the user has never opened a single email. Uses one click unsubscribe when the sender supports it, otherwise visits the unsubscribe link, otherwise sends the unsubscribe email. Report the senders that failed so the user can block them by hand.

### 6. Report back

Tell the user: emails trashed per account, senders unsubscribed, what failed, and where the plan is saved (`~/.inbox-cleanup/runs/<timestamp>/`).

## Config

`~/.inbox-cleanup/config.json` is created on first run. See `config.example.json` for every option.

| Key | Meaning |
|---|---|
| `min_age_days` | Unread junk newer than this is left alone |
| `unsub_min_emails` | Minimum emails from a never opened sender before unsubscribing |
| `protect_senders` | Regex list matched against sender address, always kept |
| `protect_subjects` | Regex list matched against subject, always kept |
| `keep_transactional` | Keep unread receipts, bookings, security emails (default true) |
| `gmail.oauth_client_file` | Path to the Google OAuth desktop client JSON |
| `outlook.client_id` | Azure app registration client ID |

## Troubleshooting

- **No Gmail token / No Outlook token**: run the matching `auth` command.
- **Outlook token refresh failed**: sign in again with `auth outlook --alias NAME`.
- **Outlook says headers are not available**: the scan falls back to the Focused / Other split. It is more cautious and will find fewer emails.
- **Something was trashed by mistake**: restore it from Trash / Deleted Items, then add the sender to `protect_senders`.
- **Unsubscribe failed for a sender**: some senders require a login. Block the sender or create a filter in the mail client.
