---
name: inbox-cleanup
description: Clean junk out of Gmail, Outlook / Microsoft 365 and any IMAP mailbox (Yahoo, iCloud, custom domains). Finds every email that was never opened and is not from a real person (newsletters, promotions, automated notifications, bulk senders), shows a plan grouped by sender, asks about borderline senders, then moves the junk to Trash, unsubscribes from senders you never read and blocks the ones that ignore it. Has undo, a spam folder sweep, an old email archive sweep and a weekly scheduled mode. Works across several accounts at once. Dry run by default and nothing is permanently deleted. Use when the user wants to clean an inbox, clear spam or unread junk, mass unsubscribe, or reach inbox zero. Aliases /inbox-cleanup, /clean-inbox, /email-cleanup. Triggers on "clean my inbox", "clean up my emails", "clear the spam", "delete unread junk", "unsubscribe me from everything", "inbox zero", "tidy my Gmail", "tidy my Outlook", "undo the inbox cleanup".
---

# Inbox Cleanup

Clears email that was **never read** and is **not from a real person** out of Gmail, Outlook and IMAP mailboxes. Everything else stays.

All commands run from this skill's folder:

```bash
python3 scripts/cleanup.py <command>
```

Private data (OAuth keys, tokens, scan results, personal protect rules) lives in `~/.inbox-cleanup/`, never inside this skill folder.

## The rule

An inbox email is trashed only when all five are true:

1. It is unread.
2. It is bulk or automated: it has an unsubscribe header, a mailing list ID, bulk precedence, an auto generated flag, or it sits in Gmail Promotions / Social / Forums or Outlook "Other" with a robot style sender address.
3. It is older than `min_age_days` (default 2).
4. Nothing protects it.
5. The user has never opened anything from that sender, or has approved trashing that sender before.

Unread junk from a bulk sender the user opens now and then goes to a **your call** list. Bulk senders the user opens half the time or more are kept.

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
python3 scripts/cleanup.py auth imap --alias yahoo --host imap.mail.yahoo.com --user you@yahoo.com
```

Gmail opens a browser for consent. Outlook prints a short code and a link; show both to the user and wait for them to finish signing in. IMAP asks for an app password in the terminal, so have the user run that command themselves.

No account yet and the user just wants to see it work? Run `python3 scripts/cleanup.py demo`.

### 2. Scan (changes nothing)

```bash
python3 scripts/cleanup.py scan
```

| Flag | Effect |
|---|---|
| `--provider gmail\|outlook\|imap`, `--alias NAME` | limit to some accounts |
| `--include-spam` | also clear the spam / junk folder |
| `--archive-old-months N` | also archive bulk email already read that is older than N months |
| `--min-age-days N` | leave unread junk newer than N days alone |
| `--top N` | print only the top N senders per table |

This reads headers only, never email bodies, then prints the plan: what goes to trash per sender, what gets archived, which senders need the user's call, what was kept and why, and which senders are unsubscribe candidates.

### 3. Show the plan and settle the "your call" list

Show the user the full sender tables, not a sample. Then:

- For each sender on the **your call** list, ask trash or keep, and record it:

```bash
python3 scripts/cleanup.py decide --trash a@x.com b@y.com --keep c@z.com
```

  `--trash` moves that sender's unread email into the trash list. `--keep` adds the sender to the always keep list for every future run. `--trash-all` and `--keep-all` settle the rest in one go. Senders left undecided are simply not touched.
- For anything in the trash tables the user wants kept, add a regex to `protect_senders` or `protect_subjects` in `~/.inbox-cleanup/config.json`, then scan again.

Never run `apply --yes`, `unsub --yes` or `block --yes` without the user approving that specific plan in this conversation.

### 4. Apply

```bash
python3 scripts/cleanup.py apply --yes
```

Trash items move to Trash (Gmail, IMAP) or Deleted Items (Outlook). Archive items leave the inbox but stay in the mailbox. Both are recoverable. Nervous user? `apply --yes --limit 5` moves only the first five so they can look before committing.

Every sender trashed here is remembered as approved, which is what the scheduled mode relies on.

### 5. Undo (any time before the trash is emptied)

```bash
python3 scripts/cleanup.py undo
```

Puts every email from the last apply back where it came from, still unread.

### 6. Unsubscribe and block (optional, ask first)

```bash
python3 scripts/cleanup.py unsub --yes
python3 scripts/cleanup.py block --yes
```

`unsub` only targets bulk senders where the user has never opened a single email. It uses one click unsubscribe when the sender supports it, otherwise visits the unsubscribe link, otherwise sends the unsubscribe email.

`block` creates a mail filter (Gmail) or inbox rule (Outlook) for each sender whose unsubscribe failed, so their future mail goes straight to trash. `block --all --yes` does it for every unsubscribe candidate. IMAP servers have no rules API; tell the user to block those in their mail app.

### 7. Weekly autopilot (optional)

```bash
python3 scripts/cleanup.py auto                 # one scheduled style run, right now
python3 scripts/cleanup.py schedule install     # every Monday 09:00 (macOS launchd; prints a cron line elsewhere)
python3 scripts/cleanup.py schedule remove
```

`auto` trashes junk only from senders the user already approved in an earlier `apply`. Email from new senders and the your call list wait in the plan for a person. The summary is written to `~/.inbox-cleanup/last_auto.md` and posted to `notify_webhook` (Slack or Discord) when set. Only install the schedule when the user asks for it.

### 8. Report back

Tell the user: emails trashed and archived per account, senders unsubscribed and blocked, what failed, that `undo` exists, and where the plan is saved (`~/.inbox-cleanup/runs/<timestamp>/`).

## Config

`~/.inbox-cleanup/config.json` is created on first run. See `config.example.json` for every option.

| Key | Meaning |
|---|---|
| `min_age_days` | Unread junk newer than this is left alone |
| `unsub_min_emails` | Minimum emails from a never opened sender before unsubscribing |
| `ask_keep_read_rate` | Bulk senders opened at least this share of the time are kept (default 0.5) |
| `archive_read_bulk_months` | Archive read bulk email older than this, 0 is off |
| `block_action` | What a block rule does: `trash` or `archive` |
| `protect_senders` | Regex list matched against sender address, always kept |
| `protect_subjects` | Regex list matched against subject, always kept |
| `keep_transactional` | Keep unread receipts, bookings, security emails (default true) |
| `notify_webhook` | Slack or Discord webhook for the scheduled run summary |
| `gmail.oauth_client_file` | Path to the Google OAuth desktop client JSON |
| `outlook.client_id` | Azure app registration client ID |

## Troubleshooting

- **No Gmail token / No Outlook token / No IMAP account**: run the matching `auth` command.
- **Gmail says it has no permission to manage filters**: the account was connected before blocking existed. Run `auth gmail --alias NAME` once more.
- **Outlook token refresh failed**: sign in again with `auth outlook --alias NAME`.
- **Outlook says headers are not available**: the scan falls back to the Focused / Other split. It is more cautious and will find fewer emails.
- **Something was trashed by mistake**: run `undo`, or restore it from Trash, then add the sender to `protect_senders`.
- **Undo could not find some emails**: the trash was emptied in the meantime; those are gone.
- **Unsubscribe failed for a sender**: some senders require a login. Use `block --yes`.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

Runs offline against a fake inbox, a fake Microsoft server and a fake IMAP server. No account is touched.
