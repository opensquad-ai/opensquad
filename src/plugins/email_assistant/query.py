"""
email_assistant — query.py

Called by the Launcher for:
  GET  /api/plugins/email_assistant/data   -> query_data(project_root, params)
  POST /api/plugins/email_assistant/action -> handle_action(project_root, action, data)
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import suppress
from typing import Any

try:  # loaded by path (launcher) or as a package (tests)
    from plugins import setup_check as sc
except ImportError:  # pragma: no cover - path-loaded fallback
    import setup_check as sc  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path(project_root: str) -> str:
    return os.path.join(project_root, "data", "plugins", "email_assistant", "emails.db")


def _open_db(project_root: str) -> sqlite3.Connection | None:
    path = _db_path(project_root)
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


# ---------------------------------------------------------------------------
# query_data — GET /api/plugins/email_assistant/data
# ---------------------------------------------------------------------------


def query_data(project_root: str, params: dict) -> dict:
    """
    params:
        action  : "list" (default) | "read" | "search"
        limit   : int (default 50)
        offset  : int (default 0)
        id      : int  (for action=read)
        q       : str  (for action=search)
    """
    action = params.get("action", "list")
    conn = _open_db(project_root)

    if conn is None:
        if action == "list":
            return {"emails": [], "total": 0}
        return {"emails": [], "count": 0}

    try:
        if action == "read":
            email_id = int(params.get("id", 0))
            row = conn.execute(
                "SELECT id,msg_id,subject,sender,recipients,date_str,body,received_at FROM emails WHERE id=?",
                (email_id,),
            ).fetchone()
            if not row:
                return {"error": f"Email id={email_id} not found"}
            return dict(row)

        if action == "search":
            q = params.get("q", "")
            limit = int(params.get("limit", 20))
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id,msg_id,subject,sender,date_str,received_at FROM emails"
                " WHERE subject LIKE ? OR sender LIKE ? OR body LIKE ?"
                " ORDER BY received_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
            return {"emails": [dict(r) for r in rows], "query": q, "count": len(rows)}

        # default: list
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        rows = conn.execute(
            "SELECT id,msg_id,subject,sender,date_str,received_at"
            " FROM emails ORDER BY received_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        return {
            "emails": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_connection — the wizard's probe: log in for real, send nothing
# ---------------------------------------------------------------------------

AUTH_HINT = (
    "认证被拒：多数邮箱不能用登录密码，要用「应用专用密码 / 授权码」"
    "（Gmail 先开两步验证再生成 App Password；QQ/163 在邮箱设置里生成授权码）。"
)
NETWORK_HINT = "连不上邮件服务器：确认主机名、端口、SSL 开关与网络（企业网络常拦 993/465）。"


def _port(value: Any, default: int) -> int:
    try:
        return int(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


def _check_imap(cfg: dict[str, Any]) -> dict:
    import imaplib

    host = str(cfg.get("imap_host") or "").strip()
    port = _port(cfg.get("imap_port"), 993)
    use_ssl = bool(cfg.get("imap_ssl", True))
    mailbox = str(cfg.get("imap_mailbox") or "INBOX").strip() or "INBOX"
    user = str(cfg.get("username") or "")
    password = str(cfg.get("password") or "")
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        conn.login(user, password)
        typ, _data = conn.select(mailbox, readonly=True)
        if typ != "OK":
            return sc.check(
                "IMAP 登录",
                False,
                f"登录成功，但打不开文件夹 {mailbox}",
                "把「邮箱文件夹」改成 INBOX（或你在邮箱里实际的文件夹名）。",
            )
        return sc.check("IMAP 登录", True, f"已连接：{user} @ {host}:{port}")
    except imaplib.IMAP4.error as exc:
        detail = str(exc)
        hint = AUTH_HINT if any(k in detail.upper() for k in ("AUTH", "LOGIN", "CREDENTIAL")) else NETWORK_HINT
        return sc.check("IMAP 登录", False, detail, hint)
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
        return sc.failed("IMAP 登录", exc, NETWORK_HINT)
    finally:
        if conn is not None:
            with suppress(Exception):
                conn.logout()


def _check_smtp(cfg: dict[str, Any]) -> dict:
    import smtplib

    host = str(cfg.get("smtp_host") or "").strip()
    port = _port(cfg.get("smtp_port"), 465)
    user = str(cfg.get("username") or "")
    password = str(cfg.get("password") or "")
    conn = None
    try:
        # 465 is implicit TLS; anything else (587/25) speaks plaintext first and upgrades.
        if port == 465:
            conn = smtplib.SMTP_SSL(host, port, timeout=sc.CHECK_TIMEOUT_S)
        else:
            conn = smtplib.SMTP(host, port, timeout=sc.CHECK_TIMEOUT_S)
            with suppress(Exception):
                conn.starttls()
        conn.login(user, password)
        return sc.check("SMTP 登录", True, f"已连接：{user} @ {host}:{port}")
    except smtplib.SMTPAuthenticationError as exc:
        return sc.check("SMTP 登录", False, str(exc), AUTH_HINT)
    except smtplib.SMTPException as exc:
        return sc.check("SMTP 登录", False, str(exc), NETWORK_HINT)
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
        return sc.failed("SMTP 登录", exc, NETWORK_HINT)
    finally:
        if conn is not None:
            with suppress(Exception):
                conn.quit()


def test_connection(project_root: str, data: dict) -> dict:
    """Log in to IMAP (and SMTP when configured). Nothing is sent or stored."""
    cfg = sc.read_config(project_root, "email_assistant", overrides=(data or {}).get("config"))
    imap_host = str(cfg.get("imap_host") or "").strip()
    smtp_host = str(cfg.get("smtp_host") or "").strip()
    user = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "")

    if not (imap_host or smtp_host) or not user or not password:
        lacking = [
            label
            for label, ok in (
                ("imap_host（或 smtp_host）", bool(imap_host or smtp_host)),
                ("username（完整邮箱地址）", bool(user)),
                ("password（应用专用密码/授权码）", bool(password)),
            )
            if not ok
        ]
        return sc.missing(
            names=lacking,
            hint="收件要知道 IMAP 主机，发件要知道 SMTP 主机；密码通常是「授权码」而不是登录密码。",
        )

    checks = [_check_imap(cfg)] if imap_host else []
    if smtp_host:
        checks.append(_check_smtp(cfg))
    return sc.result(checks=checks)


# ---------------------------------------------------------------------------
# handle_action — POST /api/plugins/email_assistant/action
# ---------------------------------------------------------------------------


def handle_action(project_root: str, action: str, data: dict) -> dict:
    """
    Supported actions:
        test_connection — data: {config?} — log in to IMAP/SMTP without sending anything
        send_email  — data: {to, subject, body}
        read_email  — data: {id}
        search      — data: {query, limit?}
    """
    if action == "test_connection":
        return test_connection(project_root, data or {})

    if action == "read_email":
        conn = _open_db(project_root)
        if conn is None:
            return {"error": "No email database found"}
        try:
            email_id = int(data.get("id", 0))
            row = conn.execute(
                "SELECT id,msg_id,subject,sender,recipients,date_str,body,received_at FROM emails WHERE id=?",
                (email_id,),
            ).fetchone()
            if not row:
                return {"error": f"Email id={email_id} not found"}
            return dict(row)
        finally:
            conn.close()

    if action == "search":
        conn = _open_db(project_root)
        if conn is None:
            return {"emails": [], "count": 0}
        try:
            q = data.get("query", "")
            limit = int(data.get("limit", 20))
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id,msg_id,subject,sender,date_str,received_at FROM emails"
                " WHERE subject LIKE ? OR sender LIKE ? OR body LIKE ?"
                " ORDER BY received_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
            return {"emails": [dict(r) for r in rows], "query": q, "count": len(rows)}
        finally:
            conn.close()

    if action == "send_email":
        to = data.get("to", "")
        subject = data.get("subject", "")
        body = data.get("body", "")

        if not to:
            return {"error": "Missing 'to' field"}

        # Read credentials from config.json
        cfg_path = os.path.join(project_root, "data", "plugins", "email_assistant", "config.json")
        cfg: dict[str, Any] = {}
        if os.path.isfile(cfg_path):
            try:
                import json

                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass

        smtp_host = cfg.get("smtp_host", "")
        smtp_port = int(cfg.get("smtp_port", 465))
        username = cfg.get("username", "")
        password = cfg.get("password", "")

        if not smtp_host:
            return {"error": "SMTP host not configured"}
        if not username or not password:
            return {"error": "Email credentials not configured"}

        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = to

        try:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                server.login(username, password)
                server.sendmail(username, [to], msg.as_string())
            return {"ok": True, "message": f"Email sent to {to}"}
        except Exception as e:
            return {"error": str(e)}

    return {"error": f"Unknown action: {action!r}"}
