"""
IM Chat Tools v1.0
Allows agents to deeply interact with the ChatPro group chat system.
Supports account registration, joining/leaving groups, sending messages,
retrieving history, and more.

Account lifecycle belongs here (mandatory core tool), not only in the optional
``chat_account`` plugin. Prefer ``im.register_account`` / ``im.join_group`` for
the current agent's own IM identity; use ``chat_account`` when managing *other*
accounts (email/password override).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from .. import bridge as bridge_module
from ..input_hub import input_hub

logger = logging.getLogger(__name__)


def _bridge():
    """Get the currently active bridge instance (supports runtime replacement in boot.py)."""
    return bridge_module.bridge


def _agent_config_path() -> str | None:
    agent_dir = getattr(input_hub, "agent_dir", None) or os.environ.get("OPENSQUAD_AGENT_DIR", "")
    if not agent_dir:
        return None
    path = os.path.join(agent_dir, "config.json")
    return path if os.path.isfile(path) else None


def _load_agent_config() -> dict[str, Any]:
    path = _agent_config_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _persist_group_chat(
    *,
    email: str | None = None,
    password: str | None = None,
    enabled: bool | None = None,
    add_group: str | None = None,
    remove_group: str | None = None,
) -> bool:
    """Write group_chat fields back to the agent's config.json. Returns True on success."""
    path = _agent_config_path()
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return False
        gc = cfg.setdefault("group_chat", {})
        if not isinstance(gc, dict):
            gc = {}
            cfg["group_chat"] = gc
        if email is not None:
            gc["email"] = email
        if password is not None:
            gc["password"] = password
        if enabled is not None:
            gc["enabled"] = enabled
        groups = gc.get("groups")
        if not isinstance(groups, list):
            groups = []
            gc["groups"] = groups
        if add_group and add_group not in groups:
            groups.append(add_group)
        if remove_group and remove_group in groups:
            groups.remove(remove_group)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning("[im] Failed to persist group_chat config: %s", e)
        return False


def _schedule_ws_connect(bridge_inst: Any) -> None:
    """Best-effort: start WS listening after a fresh login."""
    try:
        import asyncio

        if getattr(bridge_inst, "_connected", False) and getattr(bridge_inst, "ws", None):
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(bridge_inst.connect_ws())
        _ = task
    except RuntimeError:
        # No running loop (sync tool path without boot bridge) — reconnect later.
        pass
    except Exception as e:
        logger.debug("[im] WS connect schedule skipped: %s", e)


def register_account(email: str, password: str, name: str = "") -> dict[str, Any]:
    """
    Register (or ensure) an IM account for this agent, save credentials to config.json,
    and log the Bridge in so join/send/history work immediately.

    Args:
        email: Login email. Prefer a unique agent address ending with ``@ai``
               (e.g. ``news2theme_agent@ai``). Do not reuse the placeholder ``ai@ai``.
        password: Login password (stored in this agent's config.json).
        name: Display name. Defaults to config ``agent_name`` when empty.
    """
    email = (email or "").strip()
    password = (password or "").strip()
    if not email or not password:
        return {"status": "error", "message": "email and password are required"}
    if email.lower() == "ai@ai":
        return {
            "status": "error",
            "message": "Refuse to use placeholder email ai@ai. Use a unique address like <agent_folder>@ai.",
        }

    cfg = _load_agent_config()
    display_name = (name or "").strip() or str(cfg.get("agent_name") or email.split("@")[0])

    from opensquad.system_config import syscfg

    base_url = syscfg.gateway_http()
    headers = {}
    secret = syscfg.node_secret() or ""
    if secret and secret not in ("YOUR_NODE_SECRET_HERE", "opensquad-gateway-simple-token"):
        headers["X-Node-Secret"] = secret

    try:
        reg = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": password, "name": display_name},
            headers=headers,
            timeout=10,
        )
    except Exception as e:
        return {"status": "error", "message": f"Register request failed: {e}"}

    created = False
    user_id = ""
    if reg.status_code in (200, 201):
        created = True
        user = (reg.json() or {}).get("user") or {}
        user_id = str(user.get("id") or "")
    elif reg.status_code == 400:
        detail = ""
        try:
            detail = str((reg.json() or {}).get("detail") or reg.text[:200])
        except Exception:
            detail = reg.text[:200]
        if "already registered" not in detail.lower():
            return {"status": "error", "message": f"Register failed: {detail}"}
        # Account exists — verify password via login (or reset with node_secret).
        login = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": email, "password": password},
            timeout=10,
        )
        if login.status_code != 200:
            reset = requests.post(
                f"{base_url}/api/auth/reset-password",
                json={"email": email, "new_password": password, "node_secret": secret},
                timeout=10,
            )
            if reset.status_code not in (200, 201):
                try:
                    err = (reset.json() or {}).get("detail", reset.text[:200])
                except Exception:
                    err = reset.text[:200]
                return {"status": "error", "message": f"Account exists but password reset failed: {err}"}
            login = requests.post(
                f"{base_url}/api/auth/login",
                json={"email": email, "password": password},
                timeout=10,
            )
        if login.status_code != 200:
            return {"status": "error", "message": "Account exists but login failed after password reset"}
        user_id = str(((login.json() or {}).get("user") or {}).get("id") or "")
    else:
        try:
            detail = str((reg.json() or {}).get("detail") or reg.text[:200])
        except Exception:
            detail = reg.text[:200]
        return {"status": "error", "message": f"Register failed (HTTP {reg.status_code}): {detail}"}

    persisted = _persist_group_chat(email=email, password=password, enabled=True)
    b = _bridge()
    login_ok = b.apply_credentials(email, password, agent_name=display_name)
    if login_ok:
        _schedule_ws_connect(b)
        user_id = user_id or str(b.user_id or "")

    return {
        "status": "success" if login_ok else "error",
        "created": created,
        "email": email,
        "user_id": user_id,
        "name": display_name,
        "config_persisted": persisted,
        "bridge_logged_in": login_ok,
        "message": (
            f"IM account ready ({email}). Bridge logged in."
            if login_ok
            else f"Account saved but Bridge login failed for {email}."
        ),
    }


def leave_group(group_id: str) -> dict[str, Any]:
    """
    Leave a group by ID. Also removes it from config.json group_chat.groups when possible.

    Args:
        group_id: Group ID (e.g. g1, gcmsu1).
    """
    result = _bridge().leave_group_api(group_id)
    if isinstance(result, dict) and result.get("ok"):
        _persist_group_chat(remove_group=group_id)
        return {"status": "success", "message": f"Left group {group_id}."}
    detail = result.get("detail", "unknown error") if isinstance(result, dict) else "unknown error"
    return {"status": "error", "message": f"Failed to leave group {group_id}: {detail}"}


def create_group(name: str, description: str = "", is_private: bool = False) -> dict[str, Any]:
    """
    Create a new group with the agent's current IM account. Creator is added as a member.

    Args:
        name: Group name.
        description: Optional description.
        is_private: Private groups cannot be joined via public join API.
    """
    b = _bridge()
    if not b._ensure_token():
        return {
            "status": "error",
            "message": "Bridge not logged in. Call im.register_account(...) first with a unique @ai email.",
        }
    try:
        r = requests.post(
            f"{b.base_url}/api/groups",
            params={"token": b.token},
            json={"name": name, "description": description, "is_private": is_private},
            timeout=10,
        )
        if r.status_code == 401 and b.login():
            r = requests.post(
                f"{b.base_url}/api/groups",
                params={"token": b.token},
                json={"name": name, "description": description, "is_private": is_private},
                timeout=10,
            )
        if r.status_code not in (200, 201):
            try:
                detail = (r.json() or {}).get("detail", r.text[:200])
            except Exception:
                detail = r.text[:200]
            return {"status": "error", "message": f"Create group failed: {detail}"}
        data = r.json() if r.content else {}
        gid = str((data or {}).get("id") or "")
        if gid:
            _persist_group_chat(add_group=gid, enabled=True)
            b._ws_subscribe_group(gid)
        return {
            "status": "success",
            "group_id": gid,
            "name": (data or {}).get("name", name),
            "is_private": (data or {}).get("is_private", is_private),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


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

    If Bridge is not logged in, call ``im.register_account`` first with a unique ``@ai`` email.

    Args:
        group_id: Unique identifier of the group (e.g. g1, g2).
    """
    result = _bridge().join_group_api(group_id)
    # Compatible with both old bool return and new dict return
    if isinstance(result, dict):
        if result.get("ok"):
            _persist_group_chat(add_group=group_id, enabled=True)
            return {"status": "success", "message": f"Successfully joined group {group_id}."}
        else:
            detail = result.get("detail", "unknown error")
            hint = ""
            if "auto-login failed" in str(detail).lower() or "not logged in" in str(detail).lower():
                hint = " Call im.register_account(email='<agent>@ai', password='...') first."
            return {"status": "error", "message": f"Failed to join group {group_id}: {detail}.{hint}"}
    else:
        # Legacy compatibility
        if result:
            _persist_group_chat(add_group=group_id, enabled=True)
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


def request_approval(
    title: str,
    summary: str = "",
    kind: str = "generic",
    group_id: str = "",
    to_mode: str = "",
    from_mode: str = "",
    approval_id: str = "",
) -> dict[str, Any]:
    """
    Post an interactive Approve/Reject card to a group chat (works without collaboration).

    Use this whenever you need the user to explicitly authorize something while talking
    in a group — e.g. Plan→Build mode switch, risky actions, or any gated step.

    Preferred over asking the user to type "确认" / "同意".

    Args:
        title: Short card title shown to the user.
        summary: What they should review / why you need approval.
        kind: ``generic`` (default), ``mode_switch`` (Plan/Build), or ``collab_step``.
        group_id: Target group id/name. If empty, uses the group of the current turn.
        to_mode: For ``kind=mode_switch``: ``plan`` or ``build``.
        from_mode: Optional current mode label for the card.
        approval_id: Optional fixed id (e.g. reuse the private-UI mode-switch request id).

    Returns:
        ``{status: "pending", approval_id, ...}`` — STOP and wait for the system
        follow-up after the user clicks 确定/拒绝. Do NOT assume approved.
    """
    try:
        from opensquad.agent_mode import get_current_mode, normalize_mode
        from opensquad.collab_approval import (
            KIND_MODE_SWITCH,
            build_approval_payload,
            new_approval_id,
            normalize_kind,
            post_group_approval_card,
            resolve_agent_identity,
            resolve_current_group_id,
        )

        kind_n = normalize_kind(kind)
        target = resolve_current_group_id(group_id)
        if not target:
            return {
                "status": "error",
                "message": (
                    "No group_id. Pass group_id=... or call this while handling a group message "
                    "so the current group is known."
                ),
            }

        agent_id, agent_name = resolve_agent_identity()
        appr_id = (approval_id or "").strip() or new_approval_id()

        fm = (from_mode or "").strip()
        tm = (to_mode or "").strip()
        card_title = (title or "").strip()
        card_summary = (summary or "").strip()
        if kind_n == KIND_MODE_SWITCH:
            if not tm:
                return {"status": "error", "message": "kind=mode_switch requires to_mode=plan|build"}
            tm = normalize_mode(tm)
            if not fm:
                fm = get_current_mode()
            else:
                fm = normalize_mode(fm)
            if not card_title:
                card_title = f"切换模式：{fm} → {tm}"
            if not card_summary:
                card_summary = "切换到 Build 以编辑文件/运行命令" if tm == "build" else "切换到 Plan 进行只读探索与规划"

        payload = build_approval_payload(
            approval_id=appr_id,
            title=card_title or "批准请求",
            summary=card_summary,
            agent_id=agent_id,
            agent_name=agent_name,
            group_id=target,
            status="pending",
            kind=kind_n,
            from_mode=fm,
            to_mode=tm,
        )

        posted = post_group_approval_card(payload, target)
        if not posted.get("ok"):
            return {
                "status": "error",
                "message": posted.get("error") or "Failed to post approval card",
                "approval_id": appr_id,
            }

        return {
            "status": "pending",
            "approval_id": appr_id,
            "kind": kind_n,
            "group_id": posted.get("group_id") or target,
            "message_id": posted.get("message_id"),
            "from_mode": fm or None,
            "to_mode": tm or None,
            "message": (
                f"Approval card posted to the group (kind={kind_n}). "
                "Waiting for the user to click 确定/拒绝. "
                "Do NOT proceed until you receive a system message with the decision."
            ),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
