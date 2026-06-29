# -*- coding: utf-8 -*-
"""
Telegram Send Tools (Plugin Version)
Allow agents to proactively send messages to Telegram chats (groups or individuals).
Uses Telegram Bot HTTP API directly (no python-telegram-bot dependency needed at runtime).

Reads bot credentials from system_config.json -> telegram.bots.
Each agent is bound to a specific bot via agent_id matching.

Migrated from opensquad/tools/telegram_send.py to plugins/telegram/send_tools.py
"""
import json
import logging
from typing import Dict, Any, List

import requests

logger = logging.getLogger("plugins.telegram.send_tools")

# agent_id -> bot config mapping (built once)
_agent_bot_map: Dict[str, dict] = {}
# Current agent_id (set externally during boot)
_current_agent_id: str = ""

_TELEGRAM_API = "https://api.telegram.org"


def set_agent_id(agent_id: str):
    """Set the current agent_id (called by PluginManager during initialization)."""
    global _current_agent_id
    _current_agent_id = agent_id
    logger.info(f"[TelegramSend] Agent ID set to: {agent_id}")


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
        bots = syscfg.get("telegram", "bots", [])
        for b in bots:
            if not b.get("enabled", True):
                continue
            aid = b.get("agent_id", "")
            if aid:
                _agent_bot_map[aid] = {
                    "bot_token": b.get("bot_token", ""),
                    "name": b.get("name", ""),
                    "proxy": b.get("proxy", ""),
                }
        logger.info(f"[TelegramSend] Bot map built: {list(_agent_bot_map.keys())}")
    except Exception as e:
        logger.error(f"[TelegramSend] Failed to build bot map: {e}")


def _get_bot_token() -> tuple:
    """Get the bot token for the current agent. Returns (token, proxy, error)."""
    _ensure_bot_map()
    agent_id = _current_agent_id

    if not agent_id:
        if _agent_bot_map:
            agent_id = next(iter(_agent_bot_map))
        else:
            return "", "", "No Telegram bot configured in system_config.json"

    bot_cfg = _agent_bot_map.get(agent_id)
    if not bot_cfg:
        if _agent_bot_map:
            fallback_id = next(iter(_agent_bot_map))
            bot_cfg = _agent_bot_map[fallback_id]
            logger.info(f"[TelegramSend] Agent '{agent_id}' has no bound bot, using bot for '{fallback_id}'")
        else:
            return "", "", f"No Telegram bot bound to agent '{agent_id}'"

    token = bot_cfg.get("bot_token", "")
    if not token:
        return "", "", "Bot token is empty"
    proxy = bot_cfg.get("proxy", "")
    return token, proxy, None


def send_message(chat_id: str, text: str, parse_mode: str = "") -> Dict[str, Any]:
    """
    Send a text message to a Telegram chat (group or individual).

    Use this to proactively send messages to Telegram groups or users.
    The chat_id can be obtained from the source context when processing
    a message from Telegram (shown as source_chat_id in the context tag).

    Args:
        chat_id: Telegram chat ID (numeric string, e.g. "-1001234567890" for groups,
                 "123456789" for private chats). Use source_chat_id from context.
        text: Message text content (up to 4096 characters).
        parse_mode: Optional parse mode: "", "Markdown", "MarkdownV2", or "HTML".
    """
    token, proxy, err = _get_bot_token()
    if err:
        return {"status": "error", "message": err}

    try:
        url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        proxies = {"https": proxy, "http": proxy} if proxy else None
        resp = requests.post(url, json=payload, timeout=30, proxies=proxies)
        data = resp.json()

        if data.get("ok"):
            msg_id = data.get("result", {}).get("message_id", "")
            logger.info(f"[TelegramSend] Message sent to {chat_id}: {text[:60]}...")
            return {
                "status": "success",
                "message": f"Message sent to Telegram chat {chat_id}",
                "message_id": str(msg_id),
            }
        else:
            error_desc = data.get("description", "Unknown error")
            error_code = data.get("error_code", 0)
            logger.error(f"[TelegramSend] API error: {error_code} {error_desc}")
            return {"status": "error", "message": f"Telegram API error: {error_desc}"}

    except requests.Timeout:
        return {"status": "error", "message": "Telegram API request timed out"}
    except requests.ConnectionError as e:
        return {"status": "error", "message": f"Cannot connect to Telegram API: {e}"}
    except Exception as e:
        logger.error(f"[TelegramSend] Exception: {e}", exc_info=True)
        return {"status": "error", "message": f"Failed to send: {str(e)}"}


def send_document(chat_id: str, file_path: str, caption: str = "") -> Dict[str, Any]:
    """
    Send a file/document to a Telegram chat.

    Args:
        chat_id: Telegram chat ID.
        file_path: Local file path to send.
        caption: Optional caption text for the document.
    """
    import os
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"File not found: {file_path}"}

    token, proxy, err = _get_bot_token()
    if err:
        return {"status": "error", "message": err}

    try:
        url = f"{_TELEGRAM_API}/bot{token}/sendDocument"
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption

        with open(file_path, "rb") as f:
            files = {"document": (os.path.basename(file_path), f)}
            proxies = {"https": proxy, "http": proxy} if proxy else None
            resp = requests.post(url, data=data, files=files, timeout=60, proxies=proxies)

        result = resp.json()
        if result.get("ok"):
            logger.info(f"[TelegramSend] Document sent to {chat_id}: {file_path}")
            return {"status": "success", "message": f"Document sent to Telegram chat {chat_id}"}
        else:
            error_desc = result.get("description", "Unknown error")
            return {"status": "error", "message": f"Telegram API error: {error_desc}"}

    except Exception as e:
        logger.error(f"[TelegramSend] send_document error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


# Use the common function from plugins/__init__.py
from plugins import get_current_source_chat_id
