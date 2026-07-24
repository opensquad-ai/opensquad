"""Unified ingress policy: external traffic → primary session.

Single source of truth for classifying and routing inbound messages
(group chat, ChatPro DM, Feishu/Telegram/API, wake triggers, reminders).
Web UI traffic keeps its pane ``session_id``; external always binds primary.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

IngressKind = Literal["external", "web", "system"]

# Channels that always route to the agent's primary session (external ingress).
EXTERNAL_CHANNELS = frozenset(
    {
        "telegram",
        "telegram_group",
        "telegram_private",
        "feishu",
        "feishu_group",
        "feishu_private",
        "wecom",
        "dingtalk",
        "qq",
        "whatsapp",
        "discord",
        "slack",
        "api",
        "external",
        "group",
        "chatpro",
        "chatpro_group",
        "chatpro_dm",
        "reminder",
    }
)

_SYSTEM_SOURCES = frozenset({"system", "cli", "task_watch", "task_supervisor", "collaboration"})
_WEB_SOURCES = frozenset({"gateway", "web", "voice"})
_WEB_CHANNELS = frozenset({"web", "cli", "gateway", "voice", ""})


def is_external_channel(channel: str | None) -> bool:
    ch = (channel or "").strip().lower()
    if not ch or ch in ("web", "cli", "gateway", "voice"):
        return False
    if ch in EXTERNAL_CHANNELS:
        return True
    return any(ch.startswith(prefix) for prefix in ("telegram", "feishu", "wecom", "dingtalk", "chatpro", "group"))


def is_external_ingress(source: str | None = None, channel: str | None = None) -> bool:
    """True when an input_hub item should bind to the primary session."""
    if is_external_channel(channel):
        return True
    src = (source or "").strip().lower()
    if not src:
        return False
    if src in ("chatpro", "wake", "group", "reminder"):
        return True
    return src.startswith("group:") or src.startswith("wake") or src.startswith("dm") or src.startswith("reminder")


def resolve_primary_session_id(sm: Any | None = None) -> str:
    """Return the agent's primary ingress session id (fallback: focused)."""
    if sm is None:
        from opensquad.session_manager import get_session_manager

        sm = get_session_manager()
    try:
        sid = (sm.get_primary_session_id() or "").strip()
        if sid and sid != "unknown":
            return sid
    except Exception:
        pass
    try:
        sid = (sm.get_focused_session_id() or sm.get_current_session_id() or "").strip()
        return sid if sid != "unknown" else ""
    except Exception:
        return ""


def _focused_session_id(sm: Any | None = None) -> str:
    if sm is None:
        from opensquad.session_manager import get_session_manager

        sm = get_session_manager()
    try:
        sid = (sm.get_focused_session_id() or sm.get_current_session_id() or "").strip()
        return sid if sid and sid != "unknown" else ""
    except Exception:
        return ""


def classify(source: str | None = None, channel: str | None = None) -> IngressKind:
    """Classify an inbound item as external, web, or system."""
    if is_external_ingress(source, channel):
        return "external"
    src = (source or "").strip().lower()
    ch = (channel or "").strip().lower()
    if src in _SYSTEM_SOURCES:
        return "system"
    if src in _WEB_SOURCES or ch in _WEB_CHANNELS:
        return "web"
    if not src and not ch:
        return "web"
    # Unknown source with no external channel → treat as system (keep sid, no primary steal)
    return "system"


def resolve_session_id(
    *,
    source: str | None = None,
    channel: str | None = None,
    session_id: str = "",
    sm: Any | None = None,
) -> str:
    """Resolve the target session for an inbound item.

    - external → primary (fallback focused)
    - web → keep ``session_id``, else focused
    - system → keep ``session_id``, else focused (never steal primary)
    """
    kind = classify(source, channel)
    given = (session_id or "").strip()
    if kind == "external":
        return resolve_primary_session_id(sm)
    if given and given != "unknown":
        return given
    return _focused_session_id(sm)


def push_ingress(
    content: str,
    *,
    source: str = "gateway",
    channel: str = "",
    session_id: str = "",
    images: list | None = None,
    attachments: list | None = None,
    sender_name: str = "",
    chat_name: str = "",
    source_chat_id: str = "",
    user_id: str = "",
    client_id: str = "",
    model_card: str = "",
    urgent: bool = False,
) -> str:
    """Resolve session via policy and push into input_hub. Returns resolved sid."""
    from opensquad.input_hub import get_input_hub

    kind = classify(source, channel)
    sid = resolve_session_id(source=source, channel=channel, session_id=session_id)
    logger.info(
        "[Ingress] kind=%s sid=%s channel=%s source=%s content_len=%s urgent=%s",
        kind,
        sid or "-",
        channel or "-",
        source or "-",
        len(content or ""),
        urgent,
    )
    hub = get_input_hub()
    if urgent:
        hub.push_urgent(
            content,
            source=source,
            images=images,
            attachments=attachments,
            channel=channel,
            session_id=sid,
        )
    else:
        hub.push(
            content,
            source=source,
            images=images,
            attachments=attachments,
            channel=channel,
            sender_name=sender_name,
            chat_name=chat_name,
            source_chat_id=source_chat_id,
            user_id=user_id,
            client_id=client_id,
            session_id=sid,
            model_card=model_card,
        )
    return sid


def trigger_process_queue(
    *,
    source: str = "chatpro",
    channel: str = "chatpro_group",
    images: list | None = None,
) -> str:
    """Push ``__PROCESS_QUEUE__`` onto the primary ingress session."""
    return push_ingress(
        "__PROCESS_QUEUE__",
        source=source,
        channel=channel,
        images=images,
    )
