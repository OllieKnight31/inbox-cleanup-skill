# Inbox Cleanup

A Claude Code skill that clears junk out of Gmail and Outlook / Microsoft 365.

It removes one kind of email only: mail you **never opened** that is **not from a real person**. Newsletters, promotions, automated notifications and bulk senders go. People, receipts, bookings, starred email and anything you have read all stay.

## What it does

1. Reads the headers of every email in your inbox (never the body).
2. Builds a plan grouped by sender and shows it to you. Nothing changes yet.
3. On your yes, moves the junk to Trash (Gmail) or Deleted Items (Outlook), where it stays recoverable.
4. Optionally unsubscribes you from every sender you have never opened an email from.

It handles several accounts in one run, Gmail and Outlook together.

## Install

```bash
git clone https://github.com/OllieKnight31/inbox-cleanup-skill ~/.claude/skills/inbox-cleanup
pip install -r ~/.claude/skills/inbox-cleanup/requirements.txt
```

Then follow [references/setup.md](references/setup.md) to connect Gmail, Outlook or both. You bring your own Google and Microsoft app, so no keys are shared.

## Use

In Claude Code, say "clean my inbox" or run `/inbox-cleanup`.

Or drive it by hand:

```bash
cd ~/.claude/skills/inbox-cleanup
python3 scripts/cleanup.py auth gmail --alias personal
python3 scripts/cleanup.py auth outlook --alias work
python3 scripts/cleanup.py scan           # builds the plan, changes nothing
python3 scripts/cleanup.py apply --yes    # moves junk to trash
python3 scripts/cleanup.py unsub --yes    # unsubscribes from never opened senders
```

## Safety

- Dry run by default. `apply` and `unsub` do nothing without `--yes`.
- Nothing is permanently deleted.
- Read email is never touched.
- Senders you have written to, threads you replied in, starred email and receipts, bookings and security emails are kept.
- Add your own always keep rules in `~/.inbox-cleanup/config.json` (see `config.example.json`).
- Tokens, config and scan results live in `~/.inbox-cleanup/`, outside this repo.

## Licence

MIT
