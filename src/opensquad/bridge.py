"""
Agent bridge to ChatPro group chat system v2.0
Implements async non-blocking real-time listening via message pipeline
"""

import asyncio
import json
import logging
import uuid
from typing import Any

import requests
import websockets

from opensquad import bus
from opensquad.input_hub import input_hub
from opensquad.message_queue import message_queue
from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)


class ChatProBridge:
    """
    ChatPro Bridge v2
    - WebSocket receives messages -> places them into the message pipeline
    - Does not invoke AI directly; responsible only for sending/receiving
    - AI periodically consumes from the pipeline
    """

    # Group cache TTL (seconds): entries older than this are considered stale and force-refreshed on next miss
    _GROUP_CACHE_TTL = 300  # 5 minutes

    def __init__(self, base_url=None, email="ai@ai", password="aaaaaa", agent_name: str = ""):
        if base_url is None:
            base_url = syscfg.gateway_http()
        self.base_url = base_url
        self.ws_url = base_url.replace("http", "ws") + "/ws"
        self.email = email
        self.password = password
        # Display name used for auto-registration when the account does not exist (taken from config.agent_name)
        self.agent_name = agent_name or email.split("@")[0]

        self.token = None
        self.user_id = None
        self.user_name = "ai"
        self.user_avatar = None
        self.ws = None
        self._connected = False
        self._subscriptions = set()  # Subscribed groups
        self._group_cache = {}  # {group_id: {"name": ..., "members": {user_id: name}}}
        self._group_cache_ts = 0.0  # Timestamp of last cache refresh (time.monotonic)

    def login(self) -> bool:
        """Login and obtain a Token. If the account does not exist (401), auto-register and retry."""
        try:
            logger.info(f"[Bridge] Login to {self.base_url} as {self.email}...")
            r = requests.post(
                f"{self.base_url}/api/auth/login", json={"email": self.email, "password": self.password}, timeout=5
            )

            # Auto-register if account does not exist
            if r.status_code == 401:
                logger.info(f"[Bridge] Login 401, attempting auto-register for {self.email}...")
                reg = requests.post(
                    f"{self.base_url}/api/auth/register",
                    json={"email": self.email, "password": self.password, "name": self.agent_name},
                    timeout=5,
                )
                if reg.status_code in (200, 201):
                    logger.info(f"[Bridge] Auto-registered {self.email} as '{self.agent_name}', retrying login...")
                    r = requests.post(
                        f"{self.base_url}/api/auth/login",
                        json={"email": self.email, "password": self.password},
                        timeout=5,
                    )
                elif reg.status_code == 400:
                    # Email already registered -- password mismatch.
                    # Attempt force-reset password via admin endpoint, then retry login.
                    logger.info("[Bridge] Auto-register 400 (email exists), attempting password reset...")
                    try:
                        reset = requests.post(
                            f"{self.base_url}/api/auth/reset-password",
                            json={
                                "email": self.email,
                                "new_password": self.password,
                                "node_secret": syscfg.node_secret(),
                            },
                            timeout=5,
                        )
                        if reset.status_code in (200, 201):
                            logger.info(f"[Bridge] Password reset OK for {self.email}, retrying login...")
                            r = requests.post(
                                f"{self.base_url}/api/auth/login",
                                json={"email": self.email, "password": self.password},
                                timeout=5,
                            )
                        else:
                            try:
                                err = reset.json().get("detail", reset.text[:200])
                            except Exception:
                                err = reset.text[:200]
                            logger.error(f"[Bridge] Password reset failed ({reset.status_code}): {err}")
                            return False
                    except Exception as e:
                        logger.error(f"[Bridge] Password reset request failed: {e}")
                        return False
                else:
                    try:
                        err = reg.json().get("detail", reg.text[:200])
                    except Exception:
                        err = reg.text[:200]
                    logger.error(f"[Bridge] Auto-register failed ({reg.status_code}): {err}")
                    return False

            r.raise_for_status()
            data = r.json()
            self.token = data["access_token"]
            self.user_id = data["user"]["id"]
            self.user_name = data["user"]["name"]
            self.user_avatar = data["user"].get("avatar")
            logger.info(f"[Bridge] Logged in as {self.user_name} ({self.user_id})")
            return True
        except Exception as e:
            logger.error(f"[Bridge] Login failed: {e}")
            return False

    async def reconnect(self) -> bool:
        """
        Re-login and rejoin all configured groups, then restart the WebSocket connection.
        Used for runtime recovery: called by the agent when bridge initialization fails or WebSocket disconnects.

        The group list is taken from self._config_groups stored during create_bridge().
        If self._config_groups is empty (directly constructed instance), the join step is skipped.

        Returns:
            True  -- Login succeeded; WebSocket task created
            False -- Login failed
        """
        if not self.login():
            logger.error("[Bridge] reconnect(): login failed")
            return False
        for gid in getattr(self, "_config_groups", []):
            self.join_group_api(gid)
        asyncio.create_task(self.connect_ws())
        logger.info("[Bridge] reconnect(): login OK, WebSocket task created")
        return True

    def _ensure_token(self) -> bool:
        """Ensure a valid token exists; attempt login if not."""
        if not self.token:
            logger.warning("[Bridge] No token, trying to login...")
            return self.login()
        return True

    async def _refresh_group_cache(self):
        """Asynchronously refresh group info cache (group name + member names) -- all HTTP calls wrapped with asyncio.to_thread to avoid blocking the event loop."""
        import time

        try:
            if not self._ensure_token():
                return
            groups = await asyncio.to_thread(self.list_groups_api)
            for g in groups:
                if not isinstance(g, dict):
                    continue
                gid = g.get("id", "")
                if not gid:
                    continue
                # Try to fetch group details (including member list)
                try:
                    r = await asyncio.to_thread(
                        requests.get,
                        f"{self.base_url}/api/groups/{gid}",
                        **{"params": {"token": self.token}, "timeout": 5},
                    )
                    if r.status_code == 200:
                        detail = r.json()
                        members = {}
                        for m in detail.get("members", []):
                            if isinstance(m, dict):
                                members[m.get("id", "")] = m.get("name", "")
                        self._group_cache[gid] = {"name": detail.get("name", g.get("name", gid)), "members": members}
                    else:
                        self._group_cache[gid] = {"name": g.get("name", gid), "members": {}}
                except Exception:
                    self._group_cache[gid] = {"name": g.get("name", gid), "members": {}}
            self._group_cache_ts = time.monotonic()
            logger.info(f"[Bridge] Group cache refreshed: {list(self._group_cache.keys())}")
        except Exception as e:
            # Negative caching: update the timestamp even on failure so we
            # don't hammer the gateway with a full refresh on every single
            # message while the group API is down.
            self._group_cache_ts = time.monotonic()
            logger.error(f"[Bridge] Failed to refresh group cache: {e}")

    async def _resolve_group_name(self, group_id: str) -> str:
        """Get the group name from group_id (async; refresh cache on miss or TTL expiry)."""
        import time

        cached = self._group_cache.get(group_id)
        cache_age = time.monotonic() - self._group_cache_ts
        if cached and cache_age < self._GROUP_CACHE_TTL:
            return cached.get("name", group_id)
        # Cache miss or expired; refresh
        await self._refresh_group_cache()
        cached = self._group_cache.get(group_id)
        return cached.get("name", group_id) if cached else group_id

    async def _resolve_sender_name(self, group_id: str, sender_id: str) -> str:
        """Get the sender's name from group_id + sender_id (async; refresh cache on miss or TTL expiry)."""
        import time

        cached = self._group_cache.get(group_id)
        cache_age = time.monotonic() - self._group_cache_ts
        if cached and cache_age < self._GROUP_CACHE_TTL:
            name = cached.get("members", {}).get(sender_id)
            if name:
                return name
        # Cache miss, member not found, or expired; refresh
        await self._refresh_group_cache()
        cached = self._group_cache.get(group_id)
        if cached:
            return cached.get("members", {}).get(sender_id, sender_id)
        return sender_id

    def list_groups_api(self) -> list[dict]:
        """Fetch joined groups."""
        try:
            if not self._ensure_token():
                return []
            r = requests.get(f"{self.base_url}/api/groups", params={"token": self.token}, timeout=5)
            if r.status_code != 200:
                logger.error(f"[Bridge] Failed to fetch groups: HTTP {r.status_code} - {r.text[:200]}")
                # Auto re-login when token expires
                if r.status_code == 401:
                    logger.info("[Bridge] Token expired, re-logging in...")
                    if self.login():
                        r = requests.get(f"{self.base_url}/api/groups", params={"token": self.token}, timeout=5)
                        if r.status_code != 200:
                            return []
                    else:
                        return []
                else:
                    return []
            data = r.json()
            # Defensive check: ensure response is a list
            if not isinstance(data, list):
                logger.error(f"[Bridge] Unexpected groups response format: {type(data)} - {str(data)[:200]}")
                return []
            return data
        except Exception as e:
            logger.error(f"[Bridge] Failed to fetch groups: {e}")
            return []

    def get_group_detail_api(self, group_id: str) -> dict:
        """Fetch group detail including member list. Returns dict with group info or empty dict on failure.

        Automatically re-logs in on 401.
        """
        try:
            if not self._ensure_token():
                return {}

            def _fetch(tid):
                return requests.get(
                    f"{self.base_url}/api/groups/{tid}",
                    params={"token": self.token},
                    timeout=5,
                )

            r = _fetch(group_id)
            if r.status_code == 401:
                logger.info("[Bridge] Token expired, re-logging in...")
                if self.login():
                    r = _fetch(group_id)
            if r.status_code != 200:
                logger.warning(f"[Bridge] Failed to fetch group detail for {group_id}: HTTP {r.status_code}")
                return {}
            return r.json()
        except Exception as e:
            logger.error(f"[Bridge] Failed to fetch group detail for {group_id}: {e}")
            return {}

    def join_group_api(self, group_id: str) -> dict:
        """Join a group. Returns {"ok": bool, "detail": str}.

        After a successful HTTP join, if the WebSocket is already connected,
        an async subscribe message is scheduled so the Gateway starts delivering
        new_message events for this group immediately (without requiring a WS reconnect).
        """
        try:
            if not self._ensure_token():
                return {"ok": False, "detail": "Bridge not logged in and auto-login failed"}

            joined = False
            r = requests.post(f"{self.base_url}/api/groups/{group_id}/join", params={"token": self.token}, timeout=5)
            if r.status_code == 200:
                logger.info(f"[Bridge] Joined group {group_id}")
                joined = True
            elif r.status_code == 401:
                # Auto re-login when token expires and retry
                logger.info("[Bridge] Token expired, re-logging in...")
                if self.login():
                    r = requests.post(
                        f"{self.base_url}/api/groups/{group_id}/join", params={"token": self.token}, timeout=5
                    )
                    if r.status_code == 200:
                        logger.info(f"[Bridge] Joined group {group_id} (after re-login)")
                        joined = True

            if joined:
                # --- WebSocket live-subscribe so new_message events arrive immediately ---
                self._ws_subscribe_group(group_id)
                return {"ok": True, "detail": "success"}

            # Try to parse error details from server response
            try:
                err_body = r.json()
                err_detail = err_body.get("detail", err_body.get("message", r.text[:200]))
            except Exception:
                err_detail = r.text[:200]

            logger.warning(f"[Bridge] Join group {group_id} returned {r.status_code}: {err_detail}")
            return {"ok": False, "detail": f"HTTP {r.status_code}: {err_detail}"}
        except Exception as e:
            logger.error(f"[Bridge] Failed to join group {group_id}: {e}")
            return {"ok": False, "detail": str(e)}

    def _ws_subscribe_group(self, group_id: str):
        """Send a WebSocket subscribe message for *group_id* if the WS is connected.

        This is safe to call from both sync and async contexts:
        - If an event loop is running, it schedules a coroutine via create_task.
        - Otherwise, it is a no-op (the next connect_ws cycle will subscribe anyway).
        """
        if group_id in self._subscriptions:
            return  # already subscribed
        if not self._connected or self.ws is None:
            logger.debug(f"[Bridge] WS not connected, skipping live-subscribe for {group_id}")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug(f"[Bridge] No running event loop, skipping live-subscribe for {group_id}")
            return

        async def _do_subscribe():
            try:
                await self.ws.send(json.dumps({"type": "subscribe", "data": {"group_id": group_id}}))
                self._subscriptions.add(group_id)
                logger.info(f"[Bridge] Live-subscribed to group {group_id} on existing WS")
                # Also refresh group cache so we have names/members
                await self._refresh_group_cache()
            except Exception as exc:
                logger.warning(f"[Bridge] Live-subscribe failed for {group_id}: {exc}")

        loop.create_task(_do_subscribe())

    async def connect_ws(self):
        """WebSocket connection - responsible only for receiving messages into the pipeline."""
        if not self.token:
            logger.warning("[Bridge] No token, trying to login before WS connect...")
            if not self.login():
                logger.error("[Bridge] Login failed, cannot connect WS")
                return

        url = f"{self.ws_url}?token={self.token}"
        logger.info("[Bridge] Connecting WebSocket...")

        _reconnect_attempts = 0
        while True:  # Infinite reconnect loop
            try:
                async with websockets.connect(url, proxy=None) as ws:
                    self.ws = ws
                    self._connected = True
                    _reconnect_attempts = 0  # reset backoff after a successful connect
                    logger.info("[Bridge] WebSocket connected")

                    # Subscribe to all groups
                    groups = self.list_groups_api()
                    # If the group list is empty, the join_group_api() calls may not
                    # have propagated yet (race between HTTP join and API query).
                    # Wait briefly and retry once.
                    if not groups:
                        logger.info("[Bridge] Group list empty after WS connect, retrying in 1s...")
                        await asyncio.sleep(1)
                        groups = self.list_groups_api()
                    logger.info(f"[Bridge] Found {len(groups)} groups, subscribing...")
                    for g in groups:
                        if not isinstance(g, dict):
                            continue
                        gid = g.get("id", "")
                        if not gid:
                            continue
                        await ws.send(json.dumps({"type": "subscribe", "data": {"group_id": gid}}))
                        self._subscriptions.add(gid)
                        logger.info(f"[Bridge] Subscribed to group: {g.get('name', gid)}")

                    # Refresh group info cache (group name + member name mapping)
                    await self._refresh_group_cache()

                    # Message receive loop
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            logger.warning(f"[Bridge] Invalid JSON: {message[:100]}")
                        except Exception as e:
                            logger.error(f"[Bridge] Handle message error: {e}")

            except websockets.ConnectionClosed as exc:
                self._connected = False
                self._subscriptions.clear()
                _reconnect_attempts += 1
                # Gateway closes with code 4001 when the JWT is expired/invalid.
                # Re-login to get a fresh token before reconnecting, otherwise
                # we'd loop forever with the same dead token.
                if getattr(exc, "code", None) == 4001:
                    logger.warning("[Bridge] WS rejected with 4001 (token expired), re-logging in...")
                    if self.login():
                        url = f"{self.ws_url}?token={self.token}"
                        logger.info("[Bridge] Re-login successful, will reconnect with new token")
                    else:
                        logger.error("[Bridge] Re-login failed; will retry connection with old token")
                _delay = min(60, (2 ** min(_reconnect_attempts, 6))) + (uuid.uuid4().int % 1000) / 1000.0
                logger.warning(
                    f"[Bridge] WebSocket disconnected (attempt {_reconnect_attempts}), reconnecting in {round(_delay, 1)}s..."
                )
                await asyncio.sleep(_delay)
            except Exception as e:
                self._connected = False
                self._subscriptions.clear()
                _reconnect_attempts += 1
                # Some servers reject the WS handshake (before a close code is
                # available) when the token is expired — the error message
                # usually mentions 401/Unauthorized. Treat that as auth failure.
                _msg = str(e).lower()
                if "401" in _msg or "unauthorized" in _msg or ("invalid" in _msg and "token" in _msg):
                    logger.warning(f"[Bridge] WS handshake rejected (looks like auth failure: {e}), re-logging in...")
                    if self.login():
                        url = f"{self.ws_url}?token={self.token}"
                        logger.info("[Bridge] Re-login successful, will reconnect with new token")
                    else:
                        logger.error("[Bridge] Re-login failed; will retry connection with old token")
                _delay = min(60, (2 ** min(_reconnect_attempts, 6))) + (uuid.uuid4().int % 1000) / 1000.0
                logger.error(
                    f"[Bridge] WebSocket error: {e} (attempt {_reconnect_attempts}), reconnecting in {round(_delay, 1)}s..."
                )
                await asyncio.sleep(_delay)

    async def _handle_message(self, data: dict):
        """Handle received WebSocket messages using the message router."""
        from opensquad.message_router import message_router

        msg_type = data.get("type")
        logger.info(f"[Bridge] WS received type={msg_type}")

        # Compatible with two message type labels
        if msg_type in ["new_message", "message"]:
            # Extract message data
            msg_data = data.get("data", {})

            # Filter out our own messages
            sender_id = msg_data.get("sender_id")
            if sender_id == self.user_id:
                return

            # Supplement group_name and sender_name (ChatPro WS messages don't include these fields)
            group_id = msg_data.get("group_id", "")
            if group_id and not msg_data.get("group_name"):
                msg_data["group_name"] = await self._resolve_group_name(group_id)
            if sender_id and not msg_data.get("sender_name"):
                msg_data["sender_name"] = await self._resolve_sender_name(group_id, sender_id)

            # Extract image attachments and download them locally
            image_paths = await self._download_attachments(msg_data)
            if image_paths:
                msg_data["_image_paths"] = image_paths

            # Fix issue where content is empty due to voice/file: if content is empty but attachments exist, manually fill in filename and local path
            content = msg_data.get("content", "").strip()
            attachments = msg_data.get("attachments", [])
            if attachments:
                att_info = []
                for att in attachments:
                    name = att.get("name", "Unnamed file")
                    att_type = att.get("type", "file")
                    local_path = att.get("local_path", "")

                    # Translate common type names
                    type_map = {"image": "image", "voice": "voice", "video": "video", "file": "file"}
                    display_type = type_map.get(att_type, att_type)

                    info = f"[{display_type}: {name}]"
                    if local_path:
                        info += f" (local path: {local_path})"
                    att_info.append(info)

                # If original content is empty, fill with attachment info; otherwise append after existing content
                if not content:
                    msg_data["content"] = " ".join(att_info)
                else:
                    msg_data["content"] = content + "\n[Attached files]: " + " ".join(att_info)

                logger.info(f"[Bridge] Content updated with attachment info: {msg_data['content']}")

            # If the message quotes another message, prepend a summary of the quoted message so the agent has context
            reply_to_msg = msg_data.get("reply_to_message")
            if reply_to_msg and not reply_to_msg.get("is_deleted"):
                reply_sender = reply_to_msg.get("sender_name", "Unknown")
                reply_type = reply_to_msg.get("type", "TEXT")
                reply_content = reply_to_msg.get("content", "").strip()
                reply_atts = reply_to_msg.get("attachments", [])

                # Build a human-readable description of the quoted content
                type_map = {"TEXT": "text", "IMAGE": "image", "FILE": "file", "VIDEO": "video", "VOICE": "voice"}
                if reply_type == "TEXT":
                    # Strip HTML tags
                    import re as _re

                    clean = _re.sub(r"<[^>]+>", "", reply_content)
                    quoted_desc = clean[:100] + ("..." if len(clean) > 100 else "")
                elif reply_type in ("IMAGE", "VIDEO", "FILE", "VOICE"):
                    type_label = type_map.get(reply_type, reply_type)
                    if reply_atts:
                        names = ", ".join(a.get("name", "file") for a in reply_atts[:3])
                        quoted_desc = f"[{type_label}: {names}]"
                    elif reply_content:
                        quoted_desc = f"[{type_label}] {reply_content[:60]}"
                    else:
                        quoted_desc = f"[{type_label}]"
                else:
                    quoted_desc = reply_content[:100] if reply_content else f"[{reply_type}]"

                reply_prefix = f"[Quote from {reply_sender}: {quoted_desc}]\n"
                current_content = msg_data.get("content", "")
                msg_data["content"] = reply_prefix + current_content
                logger.info(f"[Bridge] Prepended reply context from {reply_sender}")

            # Use message router
            result = await message_router.route_group_message(msg_data)
            logger.info(f"[Bridge] Routed: {result['action']}")

        elif msg_type == "new_direct_message":
            # Direct messages: similar handling
            msg_data = data.get("data", {})
            sender_id = msg_data.get("sender_id")
            if sender_id == self.user_id:
                return

            # Direct messages: push directly (simplified)
            from opensquad.sleep_controller import sleep_controller
            from opensquad.state_manager import state_manager

            ai_state = await state_manager.get_state()
            sender_name = msg_data.get("sender_name", "Unknown user")

            # Extract image attachments and download locally
            image_paths = await self._download_attachments(msg_data)

            # Fix case where content is empty but attachments exist, and include local paths
            content = msg_data.get("content", "").strip()
            attachments = msg_data.get("attachments", [])
            if attachments:
                att_info = []
                for att in attachments:
                    name = att.get("name", "Unnamed file")
                    att_type = att.get("type", "file")
                    local_path = att.get("local_path", "")

                    type_map = {"image": "image", "voice": "voice", "video": "video", "file": "file"}
                    display_type = type_map.get(att_type, att_type)

                    info = f"[{display_type}: {name}]"
                    if local_path:
                        info += f" (local path: {local_path})"
                    att_info.append(info)

                content = " ".join(att_info) if not content else content + "\n[Attached files]: " + " ".join(att_info)

                msg_data["content"] = content

            # Put into queue
            from datetime import datetime

            from opensquad.message_queue import QueueMessage, message_queue

            queue_msg = QueueMessage(
                id=msg_data.get("id", f"dm_{datetime.now().timestamp()}"),
                type="dm",
                source_id=sender_id,
                source_name="Direct Message",
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                timestamp=msg_data.get("timestamp", datetime.now().timestamp()),
                mentions=[],
                raw_data=msg_data,
                images=image_paths,
            )
            await message_queue.put(queue_msg)

            # Direct messages always push (unless sleeping and need wake-up)
            if ai_state == "sleeping":
                sleep_controller.wake_up(f"DM-{sender_name}")
                input_hub.push(f"[Wake-DM-{sender_name}]", source="wake", images=image_paths if image_paths else None)
            else:
                input_hub.push(
                    f"[DM] {sender_name}: {content}", source="chatpro", images=image_paths if image_paths else None
                )
        elif msg_type == "presence":
            # Presence update, optional handling
            logger.debug(f"[Bridge] Presence update: {data}")
        elif msg_type in ("member_join", "member_leave", "member_add", "member_remove"):
            # Member list changed → refresh group cache so _resolve_sender_name finds new members
            group_id = data.get("data", {}).get("group_id", "")
            if group_id:
                logger.info(f"[Bridge] Member change ({msg_type}) in {group_id}, refreshing cache")
                # Purge only the affected group to force immediate refresh on next lookup
                self._group_cache.pop(group_id, None)
                # Notify frontend via bus → GatewayAdapter → WebSocket
                try:
                    await bus.emit_async(
                        "group_member_update",
                        {
                            "group_id": group_id,
                            "event": msg_type,
                            "data": data.get("data", {}),
                        },
                    )
                except Exception as e:
                    logger.warning(f"[Bridge] Failed to emit group_member_update event: {e}")
        elif msg_type in ("user_online", "user_offline", "status_change"):
            # User online/offline/status change → notify frontend in real time
            user_data = data.get("data", {})
            user_id = user_data.get("user_id", "") or user_data.get("id", "")
            status = msg_type.replace("user_", "").replace("_change", "")
            if user_id:
                logger.info(f"[Bridge] User status: {user_id} -> {status}")
                try:
                    await bus.emit_async(
                        "user_status_update",
                        {
                            "user_id": user_id,
                            "status": status,
                            "data": user_data,
                        },
                    )
                except Exception as e:
                    logger.warning(f"[Bridge] Failed to emit user_status_update event: {e}")
        else:
            logger.debug(f"[Bridge] Unknown message type: {msg_type}")

    async def _download_attachments(self, msg_data: dict) -> list:
        """Extract attachments from message data, download them locally, update the local_path field on attachment objects, and return a list of image paths."""
        import os

        image_paths = []
        attachments = msg_data.get("attachments", [])
        if not attachments:
            return image_paths

        # Bug 6 fix: download directory changed to match the path used by input_hub._fix_path (workspace/data/uploads),
        # previously used opensquad package root/../uploads which didn't match the path Runner/InputHub looks for.
        try:
            from opensquad.system_config import syscfg

            upload_dir = syscfg.workspace_uploads_dir()
        except Exception:
            # Fallback: temp dir — never the read-only install dir (frozen mode).
            import tempfile

            upload_dir = os.path.join(tempfile.gettempdir(), "opensquad_uploads")
        os.makedirs(upload_dir, exist_ok=True)

        for att in attachments:
            att_type = att.get("type", "")
            att_url = att.get("url", "")
            if att_url:
                try:
                    # att_url format: /uploads/xxx.jpg -- needs to be downloaded from ChatPro
                    full_url = f"{self.base_url}{att_url}"
                    headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
                    # Wrap synchronous requests.get with asyncio.to_thread to avoid blocking the event loop
                    r = await asyncio.to_thread(requests.get, full_url, **{"headers": headers, "timeout": 10})
                    r.raise_for_status()

                    # Save locally
                    filename = os.path.basename(att_url)
                    local_path = os.path.abspath(os.path.join(upload_dir, filename))
                    with open(local_path, "wb") as f:
                        f.write(r.content)

                    # Update original data with local path field
                    att["local_path"] = local_path.replace("\\", "/")

                    # Only add images to image_paths for use by Vision model
                    if att_type == "image":
                        image_paths.append(local_path)

                    logger.info(f"[Bridge] Downloaded {att_type}: {att_url} -> {local_path}")
                except Exception as e:
                    logger.error(f"[Bridge] Failed to download {att_type} {att_url}: {e}")

        return image_paths

    def upload_file(self, file_path: str) -> dict | None:
        """
        Upload a local file to ChatPro's /api/upload endpoint.
        Returns {"url": "/uploads/xxx", "name": "original filename", "size": "1.2MB", "type": "image"} or None.
        """
        import os

        try:
            if not self._ensure_token():
                return None
            if not os.path.exists(file_path):
                logger.error(f"[Bridge] File not found: {file_path}")
                return None

            filename = os.path.basename(file_path)
            # Guess MIME type from extension
            import mimetypes

            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "application/octet-stream"

            with open(file_path, "rb") as f:
                files = {"file": (filename, f, mime_type)}
                r = requests.post(f"{self.base_url}/api/upload", params={"token": self.token}, files=files, timeout=30)
                # Retry on token expiry
                if r.status_code == 401:
                    logger.info("[Bridge] Token expired during upload, re-logging in...")
                    if self.login():
                        f.seek(0)
                        files = {"file": (filename, f, mime_type)}
                        r = requests.post(
                            f"{self.base_url}/api/upload", params={"token": self.token}, files=files, timeout=30
                        )
                    else:
                        return None

            r.raise_for_status()
            result = r.json()
            logger.info(f"[Bridge] File uploaded: {filename} -> {result.get('url')}")
            return result
        except Exception as e:
            logger.error(f"[Bridge] Failed to upload file {file_path}: {e}")
            return None

    def send_message(
        self,
        content: str,
        target_id: str,
        target_type: str = "group",
        file_paths: list[str] | None = None,
        retries: int = 3,
    ) -> bool:
        """
        Send a message to ChatPro (supports attachments and retries).

        Args:
            content: Text content
            target_id: Target ID (group ID or username)
            target_type: "group" or "dm"
            file_paths: List of local file paths to attach; files are uploaded first then sent as attachments
            retries: Number of retry attempts on failure, default 3
        """
        import time

        last_error = None

        for attempt in range(retries):
            try:
                if not self._ensure_token():
                    last_error = "Token verification failed"
                    continue

                # Upload all files first, collect attachment info
                attachments = []
                if file_paths:
                    for fp in file_paths:
                        upload_result = self.upload_file(fp)
                        if upload_result:
                            attachments.append(
                                {
                                    "name": upload_result["name"],
                                    "size": upload_result.get("size", "0"),
                                    "url": upload_result["url"],
                                    "type": upload_result.get("type", "file"),
                                }
                            )
                        else:
                            logger.warning(f"[Bridge] Skipping failed upload: {fp}")

                # Derive message type from attachment types
                # Always use TEXT when there's text content + attachments, so ChatPro renders both
                def _derive_msg_type(atts: list) -> str:
                    if not atts:
                        return "TEXT"
                    # If there's text content, keep it as TEXT so the text is visible alongside attachments
                    if content.strip():
                        return "TEXT"
                    types = {a.get("type", "file") for a in atts}
                    if any(t == "video" for t in types):
                        return "VIDEO"
                    if types == {"image"}:
                        return "IMAGE"
                    return "FILE"

                msg_type = _derive_msg_type(attachments)

                if target_type == "group":
                    url = f"{self.base_url}/api/groups/{target_id}/messages"
                    payload = {"content": content, "type": msg_type, "group_id": target_id}
                    if attachments:
                        payload["attachments"] = attachments
                elif target_type == "dm":
                    url = f"{self.base_url}/api/direct-messages"
                    payload = {"recipient_name": target_id, "content": content, "title": "AI Message", "type": msg_type}
                    # DM attachments are a JSON string
                    if attachments:
                        payload["attachments"] = json.dumps(attachments)
                else:
                    logger.error(f"[Bridge] Unknown target type: {target_type}")
                    return False

                logger.info(
                    f"[Bridge] Sending {target_type} message to {target_id} (attempt {attempt + 1}/{retries})..."
                )
                r = requests.post(url, params={"token": self.token}, json=payload, timeout=15)

                # Auto re-login when token expires and retry
                if r.status_code == 401:
                    logger.info("[Bridge] Token expired, re-logging in...")
                    if self.login():
                        # Retry -- simply let the loop continue (a re-post here would be better, kept simple)
                        r = requests.post(url, params={"token": self.token}, json=payload, timeout=15)
                    else:
                        last_error = "Re-login failed"
                        continue

                # 403 Forbidden: for group messages, this may mean not a member; try auto-join and retry
                if r.status_code == 403 and target_type == "group":
                    logger.warning(f"[Bridge] 403 Forbidden for group {target_id}, attempting to join and retry...")
                    join_result = self.join_group_api(target_id)
                    if join_result.get("ok"):
                        logger.info(f"[Bridge] Successfully joined group {target_id}, retrying send...")
                        r = requests.post(url, params={"token": self.token}, json=payload, timeout=15)
                    else:
                        last_error = f"403 Forbidden and join failed: {join_result.get('detail')}"
                        logger.error(f"[Bridge] {last_error}")
                        continue

                r.raise_for_status()
                logger.info("[Bridge] Message sent successfully")
                return True

            except Exception as e:
                logger.error(f"[Bridge] Failed to send message (attempt {attempt + 1}): {e}")
                last_error = e
                # Exponential backoff: 1s, 2s, 4s...
                if attempt < retries - 1:
                    sleep_time = 2**attempt
                    time.sleep(sleep_time)

        logger.error(f"[Bridge] Message sending failed after {retries} attempts. Last error: {last_error}")
        return False

    def get_group_history(self, group_id: str, limit: int = 10) -> list[dict]:
        """Fetch group history."""
        try:
            if not self._ensure_token():
                return []
            r = requests.get(
                f"{self.base_url}/api/groups/{group_id}/messages",
                params={"token": self.token, "limit": limit},
                timeout=5,
            )
            if r.status_code != 200:
                logger.error(f"[Bridge] Failed to get history: HTTP {r.status_code} - {r.text[:200]}")
                # Auto re-login on token expiry
                if r.status_code == 401:
                    logger.info("[Bridge] Token expired, re-logging in...")
                    if self.login():
                        r = requests.get(
                            f"{self.base_url}/api/groups/{group_id}/messages",
                            params={"token": self.token, "limit": limit},
                            timeout=5,
                        )
                        if r.status_code != 200:
                            return []
                    else:
                        return []
                else:
                    return []
            data = r.json()
            if not isinstance(data, list):
                logger.error(f"[Bridge] Unexpected history response format: {type(data)} - {str(data)[:200]}")
                return []
            return data
        except Exception as e:
            logger.error(f"[Bridge] Failed to get history: {e}")
            return []

    @property
    def is_connected(self) -> bool:
        return self._connected and self.ws is not None


# Global singleton (backward compatibility for legacy code / ultimate_agent.py etc. that import bridge directly)
bridge = ChatProBridge()


def create_bridge(config: dict) -> ChatProBridge:
    """
    Create an independent ChatProBridge instance from the group_chat section of config.json.

    config.group_chat supported fields:
        enabled: bool
        email: str          -- ChatPro login email
        password: str       -- ChatPro login password
        base_url: str       -- ChatPro server address (default http://localhost:9555)
        default_group: str  -- Group ID to auto-join on startup
        groups: [str]       -- Multiple group IDs to auto-join on startup (optional)
    """
    gc = config.get("group_chat", {})
    b = ChatProBridge(
        base_url=gc.get("base_url", syscfg.gateway_http()),
        email=gc.get("email", "ai@ai"),
        password=gc.get("password", "aaaaaa"),
        agent_name=config.get("agent_name", ""),
    )
    # Save configured group list for reconnect() to rejoin
    b._config_groups = gc.get("groups", [])
    return b


def setup_bridge():
    """Set up the bridge -- called by main.py / ultimate_agent.py (legacy entry point compatibility)."""
    if bridge.login():
        # Join target group
        bridge.join_group_api("g4qe7l")

        # Start WebSocket (background task)
        asyncio.create_task(bridge.connect_ws())

        logger.info("[Bridge] Setup complete, WebSocket running in background")
        return True
    return False


async def check_messages() -> list[Any]:
    """
    Check the message pipeline -- called by AI polling.
    Returns all accumulated messages.
    """
    messages = message_queue.get_all()
    if messages:
        logger.info(f"[Bridge] Consumed {len(messages)} messages from queue")
    return messages
