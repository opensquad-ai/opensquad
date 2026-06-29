# plugins/ - OpenSquad Plugin Directory
"""
Common utilities shared across plugins.
"""
from typing import Dict, Any


def get_current_source_chat_id() -> Dict[str, Any]:
    """
    Get the source_chat_id of the current incoming message (if from Feishu/Telegram).
    Useful when you want to reply to the same chat where the message came from.

    Returns the chat_id, channel, sender_name, and chat_name from the current context.

    This is a common function shared by feishu_send and telegram_send plugins.
    """
    try:
        from opensquad import _runtime_ctx
        return {
            "status": "success",
            "source_chat_id": _runtime_ctx.get("source_chat_id", ""),
            "channel": _runtime_ctx.get("channel", ""),
            "sender_name": _runtime_ctx.get("sender_name", ""),
            "chat_name": _runtime_ctx.get("chat_name", ""),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
