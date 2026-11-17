# Inbox Cleanup

[![tests](https://github.com/OllieKnight31/inbox-cleanup-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/OllieKnight31/inbox-cleanup-skill/actions/workflows/tests.yml)

A Claude Code skill that clears junk out of Gmail, Outlook / Microsoft 365 and any IMAP mailbox.

It removes one kind of email only: mail you **never opened** that is **not from a real person**. Newsletters, promotions, automated notifications and bulk senders go. People, receipts, bookings, starred email and anything you have read all stay.

![Demo of the cleanup plan](docs/demo.gif)

## What it does

1. Reads the headers of every email in your inbox (never the body).
2. Builds a plan grouped by sender and shows it to you. Nothing changes yet.
3. Asks you about borderline senders, the ones you open now and then.
4. On your yes, moves the junk to Trash, where it stays recoverable.
5. Unsubscribes you from every sender you have never opened an email from, and blocks the ones that ignore it.

| Feature | |
|---|---|
| Gmail, Outlook / Microsoft 365, IMAP (Yahoo, iCloud, Fastmail, custom domains) | Several accounts in one run |
| Undo | One command puts the last cleanup back, still unread |
| Your call list | Borderline senders are asked about, never guessed |
| Unsubscribe and block | One click unsubscribe, then a filter for senders that ignore it |
| Spam and old mail sweeps | Clear the spam folder, archive bulk email you read months ago |
| Weekly autopilot | Only touches senders you approved before, reports the rest |
| Try it first | A demo mode with a made up inbox, no account needed |

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/OllieKnight31/inbox-cleanup-skill/main/install.sh | bash
```

Or by hand:

```bash
git clone https://github.com/OllieKnight31/inbox-cleanup-skill ~/.claude/skills/inbox-cleanup
pip install -r ~/.claude/skills/inbox-cleanup/requirements.txt
```

See it work straight away, with no account connected:

```bash
python3 ~/.claude/skills/inbox-cleanup/scripts/cleanup.py demo
```

Then follow [references/setup.md](references/setup.md) to connect Gmail, Outlook or an IMAP mailbox. You bring your own Google and Microsoft app, so no keys are shared.

## Use

In Claude Code, say "clean my inbox" or run `/inbox-cleanup`.

Or drive it by hand:

```bash
cd ~/.claude/skills/inbox-cleanup
python3 scripts/cleanup.py auth gmail --alias personal
python3 scripts/cleanup.py auth outlook --alias work
python3 scripts/cleanup.py scan                    # builds the plan, changes nothing
python3 scripts/cleanup.py decide --trash a@x.com  # settle the borderline senders
python3 scripts/cleanup.py apply --yes             # moves junk to trash
python3 scripts/cleanup.py undo                    # changed your mind
python3 scripts/cleanup.py unsub --yes             # unsubscribes from never opened senders
python3 scripts/cleanup.py block --yes             # filters senders that ignored it
python3 scripts/cleanup.py schedule install        # weekly autopilot
```

Extra sweeps: `scan --include-spam` clears the spam folder too, and `scan --archive-old-months 6` archives bulk email you read more than six months ago.

## Safety

- Dry run by default. `apply`, `unsub` and `block` do nothing without `--yes`.
- Nothing is permanently deleted, and `undo` reverses the last cleanup.
- Read email is never trashed.
- Senders you have written to, threads you replied in, starred email and receipts, bookings and security emails are kept.
- The weekly autopilot only trashes mail from senders you approved in an earlier run.
- Add your own always keep rules in `~/.inbox-cleanup/config.json` (see `config.example.json`).
- Tokens, config and scan results live in `~/.inbox-cleanup/`, outside this repo.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The suite runs offline. It checks the junk rules against the sample inbox in `sample/`, drives every command end to end against a fake Microsoft Graph server, and runs the IMAP code against a fake mail server.

## Licence

MIT. Changes are listed in [CHANGELOG.md](CHANGELOG.md).
