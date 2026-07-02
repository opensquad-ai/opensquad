"""
IM Chat Tools v1.0
Allows agents to deeply interact with the ChatPro group chat system.
Supports joining groups, sending messages, retrieving history, and more.
"""

from typing import Any

from .. import bridge as bridge_module
from ..input_hub import input_hub


def _bridge():
    """Get the currently active bridge instance (supports runtime replacement in boot.py)."""
    return bridge_module.bridge


def list_groups() -> dict[str, Any]:
    """
    Get a list of all groups the agent has currently joined.
    Returns each group's ID, name, and description.
    """
    try:
        groups = _bridge().list_groups_api()
        if not groups:
            return {"status": "success", "count": 0, "groups": []}
        return {
            "status": "success",
            "count": len(groups),
            "groups": [
                {
                    "id": g.get("id", "") if isinstance(g, dict) else str(g),
                    "name": g.get("name", "") if isinstance(g, dict) else "",
                    "description": g.get("description", "") if isinstance(g, dict) else "",
                }
                for g in groups
            ],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def join_group(group_id: str) -> dict[str, Any]:
    """
    Let the agent join a specific group by group ID.
    After joining, the agent will start listening to messages from that group.

    Args:
        group_id: Unique identifier of the group (e.g. g1, g2).
    """
    result = _bridge().join_group_api(group_id)
    # Compatible with both old bool return and new dict return
    if isinstance(result, dict):
        if result.get("ok"):
            return {"status": "success", "message": f"Successfully joined group {group_id}."}
        else:
            detail = result.get("detail", "unknown error")
            return {"status": "error", "message": f"Failed to join group {group_id}: {detail}"}
    else:
        # Legacy compatibility
        if result:
            return {"status": "success", "message": f"Successfully joined group {group_id}."}
        else:
            return {
                "status": "error",
                "message": f"Failed to join group {group_id}. Check if ID is correct or group is public.",
            }


def send_message(
    content: str,
    target_id: str,
    target_type: str = "group",
    wakeup_delay: float = 0.0,
    file_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    Proactively send a message to a specific target (non-reply mode), with optional file attachments
    (supports automatic split-archive compression).
    Note: Do NOT auto-call this tool to reply when receiving group messages, unless the web UI user
    explicitly requests it.

    Args:
        content: Message text content.
        target_id: Target ID. Pass group ID for groups, or recipient's username (User Name) for DMs.
        target_type: Target type, options: 'group' (group chat), 'dm' (direct message). Default: 'group'.
        wakeup_delay: Seconds to wait for auto-wakeup after sending a group message (float, e.g. 10.5).
                      Default 0.0 means no auto-wakeup. When set, the agent stays in interruptible
                      sleep for this duration; wakes early if there is a reply, otherwise times out.
        file_paths: List of local file paths to attach (any format, absolute paths supported).
                    Files over 100MB will be automatically ZIP-compressed and split into parts.
    """
    if not content.strip() and not file_paths:
        return {"status": "error", "message": "Content is empty and no files provided."}

    try:
        # Support sending messages by group name: if target_type is group, try to look up the ID
        if target_type == "group":
            # Check cache first: if target_id is already in cached key set, treat it as valid ID, skip HTTP
            bridge_inst = _bridge()
            if target_id in bridge_inst._group_cache:
                pass  # Already a valid group_id, no lookup needed
            else:
                groups = bridge_inst.list_groups_api()
                # Prefer exact ID match
                is_id = any(g.get("id") == target_id for g in groups if isinstance(g, dict))
                if not is_id:
                    # Try matching by name
                    for g in groups:
                        if isinstance(g, dict) and g.get("name") == target_id:
                            target_id = g.get("id")
                            break

        final_files = []
        if file_paths:
            from ..utils.archive_util import cleanup_temp, prepare_file_for_sending

            for fp in file_paths:
                # Auto-handle split-archive compression
                prepared = prepare_file_for_sending(fp)
                if prepared:
                    final_files.extend(prepared)

        success = _bridge().send_message(content, target_id=target_id, target_type=target_type, file_paths=final_files)

        # Clean up temporary split parts
        if file_paths:
            from ..utils.archive_util import cleanup_temp

            cleanup_temp()

        if success:
            if target_type == "group" and wakeup_delay > 0:
                from ..message_router import message_router

                message_router.set_wakeup_delay(wakeup_delay)
            return {
                "status": "success",
                "message": f"Message and {len(final_files)} file(s) sent to {target_type} {target_id}.",
            }
        else:
            return {
                "status": "error",
                "message": "Failed to send message via bridge (Max retries exceeded or network error). Check agent logs for details.",
            }
    except Exception as e:
        return {"status": "error", "message": f"Tool execution error: {e!s}"}


def send_file(
    file_paths: list[str], target_id: str, target_type: str = "group", message: str = "", cooldown: float = 10
) -> dict[str, Any]:
    """
    Send one or more files to a specific target. Files are first uploaded to the server
    then sent as attachments. Supports images (inline display in group chat), archives,
    documents, and any other file type.

    Args:
        file_paths: List of local file paths to send. E.g. ["C:/data/report.pdf", "C:/images/chart.png"].
        target_id: Target ID. Pass group ID for groups, or recipient's username (User Name) for DMs.
        target_type: Target type, options: 'group' (group chat), 'dm' (direct message). Default: 'group'.
        message: Accompanying text message. Defaults to "Sent a file" if empty.
        cooldown: Cooldown seconds after sending a group message, default 10 seconds. Set to 0 for no cooldown.
    """
    if not file_paths:
        return {"status": "error", "message": "No file paths provided."}

    # Validate files exist
    import os

    missing = [fp for fp in file_paths if not os.path.exists(fp)]
    if missing:
        return {"status": "error", "message": f"Files not found: {missing}"}

    content = message if message else "Sent a file"
    return send_message(content=content, target_id=target_id, target_type=target_type, file_paths=file_paths)


def set_cooldown(seconds: float = 10) -> dict[str, Any]:
    """
    Set the group message cooldown period. During cooldown, regular group messages are only
    queued and do not trigger AI processing (@mentions are exempt).
    Used to avoid feedback loops in multi-agent group chats. Choose an appropriate duration
    based on current discussion activity and context.

    Args:
        seconds: Cooldown seconds, default 10 seconds. Set to 0 to cancel cooldown immediately.
    """
    try:
        from ..message_router import message_router

        message_router.set_cooldown(seconds)
        if seconds > 0:
            return {
                "status": "success",
                "message": f"Cooldown set to {seconds}s. Group messages will be queued during this period.",
            }
        else:
            return {
                "status": "success",
                "message": "Cooldown cleared. Group messages will trigger processing immediately.",
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_history(group_id: str, limit: int = 20) -> dict[str, Any]:
    """
    Get message history for a specified group.

    IMPORTANT: Do NOT call this function in a polling loop to wait for replies.
    After sending a message, use <sleep>N</sleep> to wait -- you will be automatically
    woken up when a new message arrives via WebSocket push. Repeated calls to get_history
    waste tokens and do not guarantee receiving new messages faster.

    Args:
        group_id: Group ID.
        limit: Number of messages to retrieve, default 20.
    """
    try:
        history = _bridge().get_group_history(group_id, limit)
        if not history:
            return {"status": "success", "history": []}
        messages = []
        for m in history:
            if not isinstance(m, dict):
                messages.append({"sender": "unknown", "content": str(m), "time": 0})
                continue
            msg = {
                "sender": m.get("sender_id", "unknown"),
                "content": m.get("content", ""),
                "time": m.get("timestamp", 0),
            }

            # Fix relative paths in text content (Markdown images, etc.)
            if "/uploads/" in msg["content"]:
                # Use InputHub logic; since _fix_path handles a single path, we can do a simple regex replace.
                # For safety, at least replace /uploads/ with the absolute path prefix.
                # This assumes input_hub is initialized and knows agent_dir.
                import os

                # Uploads live in the writable workspace (data/uploads), NOT the
                # install dir. In frozen mode the install dir is read-only and has
                # no uploads/ at all, so resolving against __file__ would yield a
                # nonexistent path and images would silently fail to render.
                try:
                    from ..system_config import syscfg

                    uploads_abs = syscfg.workspace_uploads_dir().replace("\\", "/")
                except Exception:
                    # Last-resort fallback: keep old behaviour for non-syscfg envs.
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    uploads_abs = os.path.join(project_root, "data", "uploads").replace("\\", "/")
                # Simple replace /uploads -> C:/.../uploads
                # Note: this does not trigger InputHub file-copy logic (set_agent_context).
                # To copy, the full path must be parsed and _fix_path called.
                msg["content"] = msg["content"].replace("/uploads", uploads_abs)

            # Include attachment info (images, files, etc.)
            attachments = m.get("attachments", [])
            if attachments:
                msg["attachments"] = [
                    {
                        "name": a.get("name", ""),
                        "type": a.get("type", ""),
                        "url": input_hub._fix_path(a.get("url", "")),  # use input_hub to fix and copy file
                        "size": a.get("size", 0),
                    }
                    for a in attachments
                    if isinstance(a, dict)
                ]
            # Include @mention list
            mentions = m.get("mentions", [])
            if mentions:
                msg["mentions"] = mentions
            messages.append(msg)

        return {"status": "success", "history": messages}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def check_connection() -> dict[str, Any]:
    """
    Check the connection status and account information of the current bridge instance.
    For self-diagnostics: verify bridge is correctly initialized, current login account,
    and whether WebSocket is connected.

    Returns:
        Dict with the following fields:
        - email (str): login email used by bridge
        - user_id (str|None): user ID assigned by server after login, None if not logged in
        - user_name (str): display name after login, default value if not logged in
        - has_token (bool): whether a valid token is held (True means logged in successfully before)
        - ws_connected (bool): whether WebSocket is in a connected state
        - groups (list): list of currently joined groups (queried when token is present, otherwise empty)
    """
    try:
        b = _bridge()
        groups = b.list_groups_api() if b.token else []
        return {
            "status": "success",
            "email": b.email,
            "user_id": b.user_id,
            "user_name": b.user_name,
            "has_token": bool(b.token),
            "ws_connected": b._connected,
            "groups": groups,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def reconnect() -> dict[str, Any]:
    """
    Proactively trigger bridge reconnection: re-login -> re-join configured groups -> restart WebSocket.
    Use for runtime recovery when a connection anomaly is detected (e.g. ws_connected=False, has_token=False).

    Reconnection runs asynchronously in the background (fire-and-forget); this function returns immediately.
    Call check_connection() again afterward to confirm status.

    Returns:
        {"status": "reconnecting", "email": ..., "config_groups": [...]}
        or
        {"status": "error", "message": ...} (when the event loop cannot be obtained)
    """
    try:
        import asyncio

        b = _bridge()
        config_groups = getattr(b, "_config_groups", [])
        loop = asyncio.get_event_loop()
        loop.create_task(b.reconnect())
        return {
            "status": "reconnecting",
            "email": b.email,
            "config_groups": config_groups,
            "message": "Reconnect task scheduled in background. Call check_connection() after a few seconds to verify.",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
