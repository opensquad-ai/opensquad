# Email Assistant Plugin

General-purpose IMAP/SMTP email plugin for OpenSquad.

Receives mail in real time via **IMAP IDLE** and sends mail via **SMTP SSL**.
Emails are stored locally in SQLite for instant search and retrieval.

---

## Features

- **Real-time inbox** — IMAP IDLE keeps a persistent connection; new mail appears within seconds.
- **Auto-reconnect** — Exponential back-off (max 60 s) on network failures; IDLE refreshed every 29 minutes to prevent server-side timeout.
- **Deduplication** — `Message-ID` used as a unique key; duplicate messages are silently ignored.
- **Agent tools** — Four tools exposed to the Agent: `list_emails`, `read_email`, `search_emails`, `send_email`.
- **Inbox UI view** — Registers a sidebar view (`Email Inbox`) via `contributes.views`.

---

## Installation

```bash
pip install imapclient
```

Or let the framework install dependencies automatically on first load.

---

## Configuration

| Field | Type | Default | Description |
|---|---|---|---|
| `imap_host` | string | `""` | IMAP server hostname, e.g. `imap.gmail.com` |
| `imap_port` | integer | `993` | IMAP port (993 for SSL) |
| `imap_ssl` | boolean | `true` | Use SSL for IMAP |
| `imap_mailbox` | string | `"INBOX"` | Mailbox to monitor |
| `smtp_host` | string | `""` | SMTP server hostname, e.g. `smtp.gmail.com` |
| `smtp_port` | integer | `465` | SMTP port (465 for SSL) |
| `username` | string | `""` | Email account address |
| `password` | string | `""` | Account password or app password (stored encrypted) |

Configure these fields in the admin **Plugin Manager** settings panel after installation.

---

## Multi-node Deployment

This plugin uses `node_scope="single"`, which means:

- The IMAP listener is **disabled by default** on all nodes.
- Enable it on **exactly one node** via the Node Management panel to avoid duplicate mail processing.
- The `send_email` tool works independently on any node regardless of whether the listener is enabled.

---

## Agent Tools

### `list_emails`
List recent emails from the inbox.

```
Parameters:
  limit   (int, default 20)  — Maximum number of emails to return
  offset  (int, default 0)   — Pagination offset

Returns:
  { emails: [...], total: int, limit: int, offset: int }
```

### `read_email`
Read the full content of an email by its integer ID.

```
Parameters:
  email_id  (int)  — ID from list_emails

Returns:
  { id, msg_id, subject, sender, recipients, date_str, body, received_at }
```

### `search_emails`
Search emails by keyword (matches subject, sender, or body).

```
Parameters:
  query  (str)             — Search keyword
  limit  (int, default 20) — Maximum results

Returns:
  { emails: [...], query: str, count: int }
```

### `send_email`
Send an email via SMTP SSL.

```
Parameters:
  to       (str)  — Recipient email address
  subject  (str)  — Subject line
  body     (str)  — Plain-text body

Returns:
  { ok: true, message: "Email sent to ..." }  or  { error: "..." }
```

---

## Data Storage

All emails are stored in:

```
data/plugins/email_assistant/emails.db
```

The framework guarantees this directory exists. No manual setup is required.

---

## Gmail / App Password Setup

Google requires an **App Password** (not your main password) when using third-party IMAP/SMTP clients:

1. Enable 2-Step Verification on your Google account.
2. Go to **Google Account > Security > App passwords**.
3. Generate a password for "Mail" / "Other".
4. Use that 16-character password in the `password` config field.

IMAP/SMTP settings for Gmail:

| | Host | Port | SSL |
|---|---|---|---|
| IMAP | `imap.gmail.com` | 993 | Yes |
| SMTP | `smtp.gmail.com` | 465 | Yes |
