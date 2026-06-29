# -*- coding: utf-8 -*-
"""Shared utility functions for route modules.

Extracted from routes.py to avoid duplication.
"""
from __future__ import annotations

import re
from typing import Optional


def normalize_session_message(msg: dict) -> dict:
    """Normalize legacy/variant session message schema to a unified chat payload."""
    if not isinstance(msg, dict):
        return msg
    out = dict(msg)
    extra = out.get("extra") if isinstance(out.get("extra"), dict) else {}
    mid = out.get("message_id") or out.get("id") or extra.get("message_id") or extra.get("id")
    if mid:
        out["message_id"] = mid
    if not isinstance(out.get("content"), str):
        out["content"] = ""
    if not out.get("content") and isinstance(extra.get("message"), str) and extra.get("message"):
        out["content"] = extra.get("message")
    if not isinstance(out.get("images"), list):
        out["images"] = []
    else:
        out["images"] = [
            (i if isinstance(i, str) else (i.get("url") or i.get("path") or i.get("src") or ""))
            for i in out["images"] if isinstance(i, str) or isinstance(i, dict)
        ]
        out["images"] = [u for u in out["images"] if isinstance(u, str) and u.strip()]
    if not isinstance(out.get("attachments"), list):
        out["attachments"] = []
    if not isinstance(out.get("files"), list):
        out["files"] = []
    if not out["images"] and isinstance(extra.get("images"), list):
        out["images"] = [
            (i if isinstance(i, str) else (i.get("url") or i.get("path") or i.get("src") or ""))
            for i in extra.get("images") if isinstance(i, str) or isinstance(i, dict)
        ]
        out["images"] = [u for u in out["images"] if isinstance(u, str) and u.strip()]
    if not out["attachments"] and isinstance(extra.get("attachments"), list):
        out["attachments"] = extra.get("attachments")
    if not out["files"] and isinstance(extra.get("files"), list):
        out["files"] = extra.get("files")
    content_text = out.get("content") or ""
    parsed_files = []
    for m in re.finditer(r"\[File:\s*(.*?)\]\((.*?)\)", content_text):
        name = (m.group(1) or "file").strip()
        url = (m.group(2) or "").strip()
        if not url:
            continue
        lower = url.lower()
        parsed_files.append({
            "original_name": name, "url": url,
            "is_image": lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")),
            "is_audio": lower.endswith((".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")),
            "is_video": lower.endswith((".mp4", ".webm", ".mov", ".avi", ".mkv")),
        })
    for m in re.finditer(r"<image>(.*?)</image>", content_text, flags=re.IGNORECASE | re.DOTALL):
        url = (m.group(1) or "").strip()
        if not url:
            continue
        parsed_files.append({"original_name": "image", "url": url, "is_image": True, "is_audio": False, "is_video": False})
    if parsed_files and not out["files"]:
        out["files"] = parsed_files
    if not out["images"] and out["files"]:
        out["images"] = [
            (f.get("url") or f.get("path") or f.get("src")) for f in out["files"]
            if isinstance(f, dict) and (f.get("url") or f.get("path") or f.get("src"))
            and (f.get("is_image") or str(f.get("content_type", "")).startswith("image/"))
        ]
    if not out["attachments"] and out["files"]:
        out["attachments"] = [
            {"name": f.get("original_name") or f.get("filename") or "file", "url": f.get("url"),
             "type": "video" if f.get("is_video") else ("audio" if f.get("is_audio") else "file")}
            for f in out["files"] if isinstance(f, dict) and f.get("url") and not f.get("is_image")
        ]
    if isinstance(out.get("content"), str) and "[File:" in out["content"]:
        cleaned = re.sub(r"\n?\s*\[File:\s*.*?\]\(.*?\)", "", out["content"]).strip()
        out["content"] = cleaned
    return out


def normalize_session_payload(session: Optional[dict]) -> Optional[dict]:
    """Normalize a session payload."""
    if not isinstance(session, dict):
        return session
    out = dict(session)
    messages = out.get("messages") if isinstance(out.get("messages"), list) else []
    out["messages"] = [normalize_session_message(m) for m in messages]
    return out
