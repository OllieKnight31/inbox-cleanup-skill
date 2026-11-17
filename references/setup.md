# First time setup

You only do this once per provider. Each person uses their own Google and Microsoft app, so no shared keys ship with this skill.

Install the Python packages:

```bash
pip install -r requirements.txt
```

Want to see it work before connecting anything? Run `python3 scripts/cleanup.py demo`.

## Gmail

1. Open https://console.cloud.google.com and create a project (any name).
2. APIs and Services, Library: enable **Gmail API**.
3. APIs and Services, OAuth consent screen: choose External, fill in the app name and your email, and add your own Gmail address as a test user.
4. APIs and Services, Credentials, Create credentials, **OAuth client ID**, application type **Desktop app**.
5. Download the JSON and save it as `~/.inbox-cleanup/gmail_oauth_client.json` (or point `gmail.oauth_client_file` in the config at wherever you keep it).
6. Connect the account:

```bash
python3 scripts/cleanup.py auth gmail --alias personal
```

A browser window opens. Sign in and approve. Repeat with a different `--alias` for each extra Gmail account.

Permissions requested: read and modify mail (to move email to Trash), send mail (only used for unsubscribe emails) and manage basic settings (only used to create block filters).

Already have an authorized user token from another Gmail tool? Reuse it:

```bash
python3 scripts/cleanup.py auth gmail --alias personal --import-token /path/to/token.json
```

An imported token keeps whatever permissions it was given. Everything works except `block`, which needs the settings permission; connect the normal way if you want blocking.

## Outlook / Microsoft 365

1. Open https://portal.azure.com, go to **App registrations**, New registration.
2. Name it anything. Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**. No redirect URI needed.
3. After it is created, copy the **Application (client) ID**.
4. Authentication, Advanced settings: set **Allow public client flows** to Yes and save.
5. API permissions, Add a permission, Microsoft Graph, Delegated: add `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.ReadWrite`, `User.Read`, `offline_access`.
6. Put the client ID into `~/.inbox-cleanup/config.json`:

```json
{ "outlook": { "client_id": "YOUR-CLIENT-ID", "tenant": "common" } }
```

7. Connect the account:

```bash
python3 scripts/cleanup.py auth outlook --alias work
```

The command prints a link and a short code. Open the link, enter the code, sign in and approve. No client secret is used or stored.

Work accounts may need an admin to approve the app. If sign in is blocked, send your IT admin the client ID and the permissions above.

## Any other mailbox (IMAP)

Works with Yahoo, iCloud, Fastmail, Zoho and most custom domains.

1. In your mail provider's security settings, create an **app password** (do not use your normal password).
2. Connect, and paste the app password when asked:

```bash
python3 scripts/cleanup.py auth imap --alias yahoo --host imap.mail.yahoo.com --user you@yahoo.com
```

| Provider | `--host` |
|---|---|
| Yahoo | `imap.mail.yahoo.com` |
| iCloud | `imap.mail.me.com` |
| Fastmail | `imap.fastmail.com` |
| Zoho | `imap.zoho.com` |

The outgoing server (used only for unsubscribe emails) defaults to the same host with `imap.` swapped for `smtp.`. Override it with `--smtp-host`.

Limits on IMAP: there is no Promotions tab signal, so detection relies on unsubscribe and mailing list headers, and `block` is not available because IMAP has no rules.

## Where things are stored

| What | Where |
|---|---|
| Config and protect lists | `~/.inbox-cleanup/config.json` |
| Tokens and app passwords (one file per account, readable only by you) | `~/.inbox-cleanup/tokens/` |
| Scan results, plans and undo records | `~/.inbox-cleanup/runs/` |
| Senders you have approved trashing | `~/.inbox-cleanup/approved.json` |
| Last scheduled run summary and log | `~/.inbox-cleanup/last_auto.md`, `auto.log` |

Set `INBOX_CLEANUP_HOME` to move this folder. To disconnect an account, delete its token file.
