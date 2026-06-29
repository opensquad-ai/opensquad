# -*- coding: utf-8 -*-
"""
Feishu Send Tools (Plugin Version)
Allow agents to proactively send messages to Feishu chats (groups or individuals).
Uses Feishu REST API directly via lark-oapi SDK.

Reads bot credentials from system_config.json -> feishu.bots.
Each agent is bound to a specific bot via agent_id matching.

Migrated from opensquad/tools/feishu_send.py to plugins/feishu/send_tools.py
"""
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("plugins.feishu.send_tools")

# Lazy-loaded clients: {app_id: lark.Client}
_lark_clients: Dict[str, object] = {}
# agent_id -> bot config mapping (built once)
_agent_bot_map: Dict[str, dict] = {}
# Current agent_id (set externally during boot or auto-detected)
_current_agent_id: str = ""


def set_agent_id(agent_id: str):
    """Set the current agent_id (called by PluginManager during initialization)."""
    global _current_agent_id
    _current_agent_id = agent_id
    logger.info(f"[FeishuSend] Agent ID set to: {agent_id}")


def _ensure_bot_map():
    """Build agent_id -> bot config map from system_config.json (once)."""
    global _agent_bot_map
    if _agent_bot_map:
        return
    try:
        import sys, os
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys.path.insert(0, root)
        from opensquad.system_config import syscfg
        bots = syscfg.get("feishu", "bots", [])
        for b in bots:
            if not b.get("enabled", True):
                continue
            aid = b.get("agent_id", "")
            if aid:
                _agent_bot_map[aid] = {
                    "app_id": b.get("app_id", ""),
                    "app_secret": b.get("app_secret", ""),
                    "name": b.get("name", ""),
                }
        logger.info(f"[FeishuSend] Bot map built: {list(_agent_bot_map.keys())}")
    except Exception as e:
        logger.error(f"[FeishuSend] Failed to build bot map: {e}")


def _get_lark_client(app_id: str, app_secret: str):
    """Get or create a lark REST client for the given app."""
    global _lark_clients
    if app_id in _lark_clients:
        return _lark_clients[app_id]
    try:
        import lark_oapi as lark
        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        _lark_clients[app_id] = client
        logger.info(f"[FeishuSend] Lark client created for app_id={app_id[:8]}...")
        return client
    except ImportError:
        logger.error("[FeishuSend] lark-oapi SDK not installed")
        return None
    except Exception as e:
        logger.error(f"[FeishuSend] Failed to create lark client: {e}")
        return None


def _get_client_for_current_agent():
    """Get the lark client bound to the current agent."""
    _ensure_bot_map()
    agent_id = _current_agent_id

    if not agent_id:
        # Fallback: use the first available bot
        if _agent_bot_map:
            agent_id = next(iter(_agent_bot_map))
        else:
            return None, "No Feishu bot configured in system_config.json"

    bot_cfg = _agent_bot_map.get(agent_id)
    if not bot_cfg:
        # Try first available bot as fallback
        if _agent_bot_map:
            fallback_id = next(iter(_agent_bot_map))
            bot_cfg = _agent_bot_map[fallback_id]
            logger.info(f"[FeishuSend] Agent '{agent_id}' has no bound bot, using bot for '{fallback_id}'")
        else:
            return None, f"No Feishu bot bound to agent '{agent_id}'"

    client = _get_lark_client(bot_cfg["app_id"], bot_cfg["app_secret"])
    if not client:
        return None, "Failed to initialize Feishu SDK client (is lark-oapi installed?)"
    return client, None


def send_message(chat_id: str, content: str, msg_type: str = "text") -> Dict[str, Any]:
    """
    Send a message to a Feishu chat (group or individual).

    Use this to proactively send messages to Feishu groups or users.
    The chat_id can be obtained from the source context when processing
    a message from Feishu (shown as source_chat_id in the context tag).

    Args:
        chat_id: The Feishu chat ID (e.g. "oc_xxx" for groups, or "ou_xxx" for users).
                 You can use the source_chat_id from the incoming message context.
        content: Message text content.
        msg_type: Message type. Default "text". Supported: "text", "interactive".
    """
    client, err = _get_client_for_current_agent()
    if err:
        return {"status": "error", "message": err}

    try:
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        if msg_type == "text":
            body_content = json.dumps({"text": content}, ensure_ascii=False)
        else:
            body_content = content  # assume pre-formatted JSON for interactive cards

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(body_content)
                .build()
            )
            .build()
        )

        response = client.im.v1.message.create(request)
        if response.success():
            msg_id = ""
            if response.data and hasattr(response.data, "message_id"):
                msg_id = response.data.message_id or ""
            logger.info(f"[FeishuSend] Message sent to {chat_id}: {content[:60]}...")
            return {
                "status": "success",
                "message": f"Message sent to Feishu chat {chat_id}",
                "message_id": msg_id,
            }
        else:
            error_msg = f"code={response.code}, msg={response.msg}"
            logger.error(f"[FeishuSend] Send failed: {error_msg}")
            return {"status": "error", "message": f"Feishu API error: {error_msg}"}

    except Exception as e:
        logger.error(f"[FeishuSend] Exception: {e}", exc_info=True)
        return {"status": "error", "message": f"Failed to send: {str(e)}"}


def list_chats(page_size: int = 20) -> Dict[str, Any]:
    """
    List Feishu chats (groups) the bot has joined.
    Returns chat_id and name for each chat, which can be used with send_message.

    Args:
        page_size: Number of chats to return (max 100, default 20).
    """
    client, err = _get_client_for_current_agent()
    if err:
        return {"status": "error", "message": err}

    try:
        from lark_oapi.api.im.v1 import ListChatRequest

        request = (
            ListChatRequest.builder()
            .page_size(min(page_size, 100))
            .build()
        )

        response = client.im.v1.chat.list(request)
        if response.success() and response.data:
            chats = []
            items = response.data.items or []
            for item in items:
                chats.append({
                    "chat_id": getattr(item, "chat_id", ""),
                    "name": getattr(item, "name", ""),
                    "description": getattr(item, "description", ""),
                    "chat_mode": getattr(item, "chat_mode", ""),
                })
            return {
                "status": "success",
                "count": len(chats),
                "chats": chats,
            }
        else:
            error_msg = f"code={response.code}, msg={response.msg}"
            return {"status": "error", "message": f"Feishu API error: {error_msg}"}

    except Exception as e:
        logger.error(f"[FeishuSend] list_chats error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


# Use the common function from plugins/__init__.py
from plugins import get_current_source_chat_id
