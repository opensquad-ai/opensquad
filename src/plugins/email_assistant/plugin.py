# -*- coding: utf-8 -*-
"""
Email Assistant Plugin

Provides real-time email reception (IMAP IDLE) and sending (SMTP SSL).
Uses SQLite for local email storage with msg_id deduplication.

Design decisions:
- node_scope="single": Only one node should run the IMAP listener.  Enable on
  exactly one node via the admin Node Management panel.
- IMAP IDLE with auto-reconnect (exponential back-off, max 60 s).
- IDLE is restarted every 29 minutes to avoid server-side timeout.
- Sending uses smtplib.SMTP_SSL (standard library, no extra deps).
- imapclient is the only third-party dependency.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from email import message_from_bytes
from email.header import decode_header as _decode_header
from email.utils import parseaddr
from typing import Any, Dict, List, Optional

from opensquad.plugin_api import register, tool, Plugin, Context

logger = logging.getLogger("plugins.email_assistant")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_str(raw: Any) -> str:
    """Decode an email header value (bytes or str) to plain text."""
    if raw is None:
        return ""
    parts = _decode_header(str(raw))
    result = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            try:
                result.append(chunk.decode(enc or "utf-8", errors="replace"))
            except Exception:
                result.append(chunk.decode("utf-8", errors="replace"))
        else:
            result.append(str(chunk))
    return "".join(result)


def _extract_plain_text(msg) -> str:
    """Extract plain-text body from an email.Message object."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                try:
                    return part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    return ""
    else:
        if msg.get_content_type() == "text/plain":
            charset = msg.get_content_charset() or "utf-8"
            try:
                return msg.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                return ""
    return ""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class EmailStorage:
    """SQLite-backed email store."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS emails (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id      TEXT    UNIQUE,
                    subject     TEXT,
                    sender      TEXT,
                    recipients  TEXT,
                    date_str    TEXT,
                    body        TEXT,
                    received_at REAL
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_received ON emails(received_at DESC)")
            self._conn.commit()

    def insert(self, msg_id: str, subject: str, sender: str,
               recipients: str, date_str: str, body: str) -> bool:
        """Insert email; returns True if inserted, False if duplicate."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO emails(msg_id,subject,sender,recipients,date_str,body,received_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (msg_id, subject, sender, recipients, date_str, body, time.time())
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False  # duplicate

    def list_emails(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id,msg_id,subject,sender,date_str,received_at"
                " FROM emails ORDER BY received_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "msg_id": r[1], "subject": r[2],
             "sender": r[3], "date_str": r[4], "received_at": r[5]}
            for r in rows
        ]

    def get_email(self, email_id: int) -> Optional[Dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id,msg_id,subject,sender,recipients,date_str,body,received_at"
                " FROM emails WHERE id=?", (email_id,)
            )
            r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "msg_id": r[1], "subject": r[2], "sender": r[3],
            "recipients": r[4], "date_str": r[5], "body": r[6], "received_at": r[7]
        }

    def search_emails(self, query: str, limit: int = 20) -> List[Dict]:
        like = f"%{query}%"
        with self._lock:
            cur = self._conn.execute(
                "SELECT id,msg_id,subject,sender,date_str,received_at FROM emails"
                " WHERE subject LIKE ? OR sender LIKE ? OR body LIKE ?"
                " ORDER BY received_at DESC LIMIT ?",
                (like, like, like, limit)
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "msg_id": r[1], "subject": r[2],
             "sender": r[3], "date_str": r[4], "received_at": r[5]}
            for r in rows
        ]

    def count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM emails")
            return cur.fetchone()[0]

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# IMAP IDLE listener (background thread)
# ---------------------------------------------------------------------------

class ImapIdleListener(threading.Thread):
    """
    Background daemon thread that maintains an IMAP IDLE connection.
    New messages are fetched and stored via EmailStorage.
    Auto-reconnects with exponential back-off on failure.
    IDLE is refreshed every 29 minutes to prevent server timeouts.
    """

    IDLE_REFRESH_SECS = 29 * 60   # 29 minutes
    MAX_BACKOFF_SECS  = 60

    def __init__(self, host: str, port: int, username: str, password: str,
                 mailbox: str, storage: EmailStorage, use_ssl: bool = True):
        super().__init__(daemon=True, name="email_idle_listener")
        self._host     = host
        self._port     = port
        self._username = username
        self._password = password
        self._mailbox  = mailbox
        self._storage  = storage
        self._use_ssl  = use_ssl
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()

    # ---- main loop ----

    def run(self):
        backoff = 2
        while not self._stop_evt.is_set():
            try:
                self._run_session()
                backoff = 2  # reset on clean exit
            except Exception as e:
                logger.warning(f"[EmailAssistant] IMAP session error: {e}; retrying in {backoff}s")
                self._stop_evt.wait(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF_SECS)

    def _run_session(self):
        """Open one IMAP connection, do initial fetch, then loop IDLE."""
        try:
            import imapclient  # noqa: F401 — checked at import time
        except ImportError:
            logger.error("[EmailAssistant] imapclient not installed. "
                         "Run: pip install imapclient")
            self._stop_evt.wait(30)
            return

        import imapclient

        client = imapclient.IMAPClient(
            self._host, port=self._port, ssl=self._use_ssl, use_uid=True
        )
        try:
            client.login(self._username, self._password)
            client.select_folder(self._mailbox, readonly=False)
            logger.info(f"[EmailAssistant] IMAP connected to {self._host}")

            # Fetch existing unseen messages on first connect
            self._fetch_unseen(client)

            # IDLE loop — refresh every 29 min
            idle_start = time.time()
            client.idle()
            while not self._stop_evt.is_set():
                elapsed = time.time() - idle_start
                timeout = max(1, self.IDLE_REFRESH_SECS - elapsed)
                responses = client.idle_check(timeout=min(timeout, 30))

                if self._stop_evt.is_set():
                    break

                if time.time() - idle_start >= self.IDLE_REFRESH_SECS:
                    # Refresh IDLE to avoid server timeout
                    client.idle_done()
                    client.idle()
                    idle_start = time.time()
                    continue

                if responses:
                    # New mail arrived
                    client.idle_done()
                    self._fetch_unseen(client)
                    client.idle()
                    idle_start = time.time()
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _fetch_unseen(self, client):
        """Fetch all UNSEEN messages and store them."""
        try:
            uids = client.search(["UNSEEN"])
            if not uids:
                return
            messages = client.fetch(uids, ["RFC822", "ENVELOPE"])
            for uid, data in messages.items():
                raw = data.get(b"RFC822", b"")
                if not raw:
                    continue
                try:
                    msg = message_from_bytes(raw)
                    msg_id   = _decode_str(msg.get("Message-ID", f"<uid-{uid}>")).strip()
                    subject  = _decode_str(msg.get("Subject", "(no subject)"))
                    from_raw = msg.get("From", "")
                    # Use only the email address part
                    _, sender_addr = parseaddr(from_raw)
                    sender   = sender_addr or _decode_str(from_raw)
                    to_raw   = msg.get("To", "")
                    recipients = to_raw
                    date_str = _decode_str(msg.get("Date", ""))
                    body     = _extract_plain_text(msg)
                    inserted = self._storage.insert(
                        msg_id=msg_id, subject=subject, sender=sender,
                        recipients=recipients, date_str=date_str, body=body
                    )
                    if inserted:
                        logger.info(f"[EmailAssistant] New email from {sender}: {subject}")
                except Exception as e:
                    logger.warning(f"[EmailAssistant] Failed to parse uid {uid}: {e}")
        except Exception as e:
            logger.warning(f"[EmailAssistant] _fetch_unseen error: {e}")


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

@register(
    name="email_assistant",
    author="OpenSquad",
    description="General-purpose IMAP/SMTP email plugin. Receives mail via IMAP IDLE, sends via SMTP SSL. Use node_scope=single to avoid duplicate listeners.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Email Assistant",
    node_scope="single",
    config_schema={
        "imap_host": {
            "type": "string",
            "default": "",
            "description": "IMAP server hostname (e.g. imap.gmail.com)",
        },
        "imap_port": {
            "type": "integer",
            "default": 993,
            "description": "IMAP server port (993 for SSL)",
        },
        "imap_ssl": {
            "type": "boolean",
            "default": True,
            "description": "Use SSL for IMAP connection",
        },
        "imap_mailbox": {
            "type": "string",
            "default": "INBOX",
            "description": "Mailbox to monitor (default: INBOX)",
        },
        "smtp_host": {
            "type": "string",
            "default": "",
            "description": "SMTP server hostname (e.g. smtp.gmail.com)",
        },
        "smtp_port": {
            "type": "integer",
            "default": 465,
            "description": "SMTP server port (465 for SSL)",
        },
        "username": {
            "type": "string",
            "default": "",
            "description": "Email account username / address",
        },
        "password": {
            "type": "string",
            "default": "",
            "secret": True,
            "description": "Email account password or app password",
        },
    },
    contributes={
        "views": [
            {
                "name": "inbox",
                "title": "Email Inbox",
                "icon": "Mail",
                "data_endpoint": "/api/plugins/email_assistant/data",
            }
        ]
    },
    dependencies={"pip": ["imapclient"]},
    tags=["email", "communication"],
)
class EmailAssistantPlugin(Plugin):

    def __init__(self, context: Context):
        super().__init__(context)
        self._storage: Optional[EmailStorage] = None
        self._listener: Optional[ImapIdleListener] = None

    # ---- lifecycle ----

    def on_load(self) -> None:
        cfg = self.context.config
        db_path = os.path.join(self.context.data_dir, "emails.db")
        self._storage = EmailStorage(db_path)

        imap_host = cfg.get("imap_host", "")
        username  = cfg.get("username", "")
        password  = cfg.get("password", "")

        if imap_host and username and password:
            self._listener = ImapIdleListener(
                host     = imap_host,
                port     = int(cfg.get("imap_port", 993)),
                username = username,
                password = password,
                mailbox  = cfg.get("imap_mailbox", "INBOX"),
                storage  = self._storage,
                use_ssl  = bool(cfg.get("imap_ssl", True)),
            )
            self._listener.start()
            logger.info(f"[EmailAssistant] IMAP listener started for {username}@{imap_host}")
        else:
            logger.info("[EmailAssistant] IMAP not configured — listener not started")

    def on_unload(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._storage:
            self._storage.close()
            self._storage = None

    # ---- agent tools ----

    @tool(
        name="list_emails",
        description="List recent emails from the inbox. Returns a list with id, subject, sender, date.",
    )
    def list_emails(self, limit: int = 20, offset: int = 0) -> Dict:
        """List recent emails."""
        if not self._storage:
            return {"error": "Email storage not initialized"}
        try:
            emails = self._storage.list_emails(limit=limit, offset=offset)
            total  = self._storage.count()
            return {"emails": emails, "total": total, "limit": limit, "offset": offset}
        except Exception as e:
            logger.error(f"[EmailAssistant] list_emails error: {e}")
            return {"error": str(e)}

    @tool(
        name="read_email",
        description="Read the full content of an email by its id (integer). Returns subject, sender, date, and body.",
    )
    def read_email(self, email_id: int) -> Dict:
        """Read full email content."""
        if not self._storage:
            return {"error": "Email storage not initialized"}
        try:
            email = self._storage.get_email(int(email_id))
            if not email:
                return {"error": f"Email id={email_id} not found"}
            return email
        except Exception as e:
            logger.error(f"[EmailAssistant] read_email error: {e}")
            return {"error": str(e)}

    @tool(
        name="search_emails",
        description="Search emails by keyword (matches subject, sender, or body). Returns a list of matching emails.",
    )
    def search_emails(self, query: str, limit: int = 20) -> Dict:
        """Search emails by keyword."""
        if not self._storage:
            return {"error": "Email storage not initialized"}
        try:
            results = self._storage.search_emails(query=query, limit=limit)
            return {"emails": results, "query": query, "count": len(results)}
        except Exception as e:
            logger.error(f"[EmailAssistant] search_emails error: {e}")
            return {"error": str(e)}

    @tool(
        name="send_email",
        description="Send an email via SMTP. Parameters: to (recipient address), subject, body (plain text).",
    )
    def send_email(self, to: str, subject: str, body: str) -> Dict:
        """Send an email via SMTP SSL."""
        cfg = self.context.config
        smtp_host = cfg.get("smtp_host", "")
        smtp_port = int(cfg.get("smtp_port", 465))
        username  = cfg.get("username", "")
        password  = cfg.get("password", "")

        if not smtp_host:
            return {"error": "SMTP host not configured"}
        if not username or not password:
            return {"error": "Email credentials not configured"}

        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = username
        msg["To"]      = to

        try:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                server.login(username, password)
                server.sendmail(username, [to], msg.as_string())
            logger.info(f"[EmailAssistant] Sent email to {to}: {subject}")
            return {"ok": True, "message": f"Email sent to {to}"}
        except Exception as e:
            logger.error(f"[EmailAssistant] send_email error: {e}")
            return {"error": str(e)}
