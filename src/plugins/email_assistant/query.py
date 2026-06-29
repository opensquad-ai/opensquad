# -*- coding: utf-8 -*-
"""
email_assistant — query.py

Called by the Launcher for:
  GET  /api/plugins/email_assistant/data   -> query_data(project_root, params)
  POST /api/plugins/email_assistant/action -> handle_action(project_root, action, data)
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_path(project_root: str) -> str:
    return os.path.join(project_root, "data", "plugins", "email_assistant", "emails.db")


def _open_db(project_root: str) -> Optional[sqlite3.Connection]:
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
    conn   = _open_db(project_root)

    if conn is None:
        if action == "list":
            return {"emails": [], "total": 0}
        return {"emails": [], "count": 0}

    try:
        if action == "read":
            email_id = int(params.get("id", 0))
            row = conn.execute(
                "SELECT id,msg_id,subject,sender,recipients,date_str,body,received_at"
                " FROM emails WHERE id=?", (email_id,)
            ).fetchone()
            if not row:
                return {"error": f"Email id={email_id} not found"}
            return dict(row)

        if action == "search":
            q = params.get("q", "")
            limit = int(params.get("limit", 20))
            like  = f"%{q}%"
            rows = conn.execute(
                "SELECT id,msg_id,subject,sender,date_str,received_at FROM emails"
                " WHERE subject LIKE ? OR sender LIKE ? OR body LIKE ?"
                " ORDER BY received_at DESC LIMIT ?",
                (like, like, like, limit)
            ).fetchall()
            return {"emails": [dict(r) for r in rows], "query": q, "count": len(rows)}

        # default: list
        limit  = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        rows = conn.execute(
            "SELECT id,msg_id,subject,sender,date_str,received_at"
            " FROM emails ORDER BY received_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
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
# handle_action — POST /api/plugins/email_assistant/action
# ---------------------------------------------------------------------------

def handle_action(project_root: str, action: str, data: dict) -> dict:
    """
    Supported actions:
        send_email  — data: {to, subject, body}
        read_email  — data: {id}
        search      — data: {query, limit?}
    """
    if action == "read_email":
        conn = _open_db(project_root)
        if conn is None:
            return {"error": "No email database found"}
        try:
            email_id = int(data.get("id", 0))
            row = conn.execute(
                "SELECT id,msg_id,subject,sender,recipients,date_str,body,received_at"
                " FROM emails WHERE id=?", (email_id,)
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
            q     = data.get("query", "")
            limit = int(data.get("limit", 20))
            like  = f"%{q}%"
            rows  = conn.execute(
                "SELECT id,msg_id,subject,sender,date_str,received_at FROM emails"
                " WHERE subject LIKE ? OR sender LIKE ? OR body LIKE ?"
                " ORDER BY received_at DESC LIMIT ?",
                (like, like, like, limit)
            ).fetchall()
            return {"emails": [dict(r) for r in rows], "query": q, "count": len(rows)}
        finally:
            conn.close()

    if action == "send_email":
        to      = data.get("to", "")
        subject = data.get("subject", "")
        body    = data.get("body", "")

        if not to:
            return {"error": "Missing 'to' field"}

        # Read credentials from config.json
        cfg_path = os.path.join(project_root, "data", "plugins", "email_assistant", "config.json")
        cfg: Dict[str, Any] = {}
        if os.path.isfile(cfg_path):
            try:
                import json
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass

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
            return {"ok": True, "message": f"Email sent to {to}"}
        except Exception as e:
            return {"error": str(e)}

    return {"error": f"Unknown action: {action!r}"}
