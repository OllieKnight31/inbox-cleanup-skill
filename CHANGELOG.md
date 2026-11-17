# Changelog

## 1.1.0 (2026-11-17)

Added
- `undo`: puts every email from the last apply back where it came from.
- `decide` and a "your call" list for bulk senders you open now and then, so borderline mail is asked about instead of guessed.
- `block`: creates a Gmail filter or Outlook inbox rule for senders whose unsubscribe failed.
- `auto` and `schedule install|remove`: a weekly run that only trashes mail from senders you approved before, with an optional Slack or Discord summary.
- `scan --include-spam` to clear the spam / junk folder and `scan --archive-old-months N` to archive old bulk email you already read.
- IMAP support (Yahoo, iCloud, Fastmail, custom domains) through `auth imap`.
- `demo`: runs the planner on a made up inbox with no account connected.
- `apply --limit N` to try a cleanup on a few emails first.
- Test suite with a sample inbox, a fake Microsoft Graph server and a fake IMAP server, run on every push.
- One line installer.

Changed
- Bulk senders you open at least half the time are now kept.
- Outlook uses immutable message ids so email can be restored after a move.
- Gmail now also asks for the basic settings permission, used only for block filters. Accounts connected earlier keep working; reconnect once to use `block`.

## 1.0.0 (2026-11-17)

- First release: Gmail and Outlook scan, plan, trash and unsubscribe.
