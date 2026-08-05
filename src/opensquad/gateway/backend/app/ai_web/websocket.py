"""
WebSocket handler
Handles Agent registration and user conversations
"""

import asyncio
import contextlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer

from .registry import AgentInfo, registry
from .sessions import gateway_session_cache

logger = logging.getLogger(__name__)
security = HTTPBearer()

# Canonical agent disk session id last reported via the `current_session`
# WS event (agent_id -> session_id). The Gateway forwards this to the user
# `connected` event so the frontend history read uses a real disk session id
# instead of the gateway_session_key (user_id:agent_id) fallback, which is
# not a valid session file name and always 404s the history API.
_agent_current_session_id: dict[str, str] = {}


def _resolve_registered_agent_id(agent_id: str) -> str:
    """Map UI/dir aliases (e.g. ``agent305``) to the registered WS agent_id.

    The registry is keyed by config ``agent_id`` (often ``agent305-001``), but the
    web UI / localStorage may reopen chat with the on-disk folder name. Without
    this alias, user WS gets 1013 ``agent_not_ready`` forever while the agent is
    actually online under the longer id.
    """
    if not agent_id:
        return agent_id
    if registry.get_agent(agent_id):
        return agent_id
    try:
        agents = registry.list_agents()
    except Exception:
        return agent_id
    for a in agents:
        aid = (a.agent_id or "").strip()
        if not aid:
            continue
        if aid.startswith(agent_id + "-") or aid.rsplit("-", 1)[0] == agent_id:
            return aid
    return agent_id


def _check_node_secret(received: str) -> bool:
    """
    Validate node_secret using a constant-time comparison.

    - If Gateway has not configured auth.node_secret (empty string), the
      comparison falls back to permitting the connection for local dev
      convenience, but logs a prominent warning so production deployments
      are not silently left open.
    - If configured, use ``hmac.compare_digest`` to prevent timing side-channels.
    """
    from opensquad.system_config import syscfg

    expected = syscfg.node_secret()
    if not expected:
        logging.warning(
            "[WS] auth.node_secret is NOT configured — /ai-ws/register and "
            "/ai-ws/launcher are open. Set auth.node_secret in production!"
        )
        return True  # local dev fallback
    if not isinstance(received, str):
        received = received or ""
    return hmac.compare_digest(received, expected)


class AgentWebSocketHandler:
    """Handle Agent's WebSocket connection (registration and messages)"""

    async def handle_agent_register(self, websocket: WebSocket):
        """Handle Agent registration connection"""
        await websocket.accept()
        agent_id = None

        try:
            # Wait for registration message
            data = await websocket.receive_json()

            if data.get("action") != "register":
                await websocket.send_json({"status": "error", "message": "First message must be register"})
                await websocket.close()
                return

                # Validate node_secret
            if not _check_node_secret(data.get("node_secret", "")):
                logger.warning(f"Agent registration rejected: invalid node_secret (agent_id={data.get('agent_id')})")
                await websocket.send_json({"status": "error", "message": "Unauthorized: invalid node_secret"})
                await websocket.close()
                return

            # Create AgentInfo
            agent_info = AgentInfo(
                agent_id=data["agent_id"],
                agent_name=data["agent_name"],
                agent_type=data.get("agent_type", "general"),
                capabilities=data.get("capabilities", []),
                description=data.get("description", ""),
                status="online",
                load_percent=0,
                today_chats=0,
                total_chats=0,
                node_id=data.get("node_id", ""),
                node_label=data.get("node_label", ""),
            )

            agent_id = agent_info.agent_id

            # Register
            old_ws = registry.register(agent_info, websocket)
            if old_ws is not None:
                # Close the stale connection from a previous registration to
                # prevent zombie sockets and double-delivery.
                with contextlib.suppress(Exception):
                    asyncio.create_task(old_ws.close(code=4000, reason="replaced by new registration"))
            # Auto-create a default session (using a fixed system user ID), ensuring the Agent can immediately respond to group chat messages
            # Even if the user hasn't connected via the Web interface yet, the Agent can work normally
            default_user_id = "system"
            await gateway_session_cache.async_get_or_create_session(default_user_id, agent_id)
            logger.info(f"Auto-created default session for agent {agent_id}")

            # Send confirmation
            await websocket.send_json(
                {
                    "status": "registered",
                    "message": f"Agent {agent_id} registered successfully",
                    "assigned_route": f"/ai-web/chat/{agent_id}",
                }
            )

            logger.info(f"Agent {agent_id} registered successfully")

            # Enter message loop
            await self._agent_message_loop(agent_id, websocket)

        except WebSocketDisconnect:
            logger.info(f"Agent {agent_id} disconnected")
            if agent_id:
                registry.unregister(agent_id)
        except Exception as e:
            logger.error(f"Agent register error: {e}")
            if agent_id:
                registry.unregister(agent_id)
            with contextlib.suppress(Exception):
                await websocket.close()

    async def _agent_message_loop(self, agent_id: str, websocket: WebSocket):
        """Agent message loop"""
        try:
            while True:
                message = await websocket.receive_json()
                action = message.get("action")
                msg_type = message.get("type")

                if action == "heartbeat":
                    # Heartbeat response
                    stats = message.get("stats", {})
                    registry.update_heartbeat(agent_id, stats)
                    await websocket.send_json({"action": "pong"})

                elif msg_type == "pong" or action == "pong":
                    # Application-level pong answering Gateway probe_agent ping.
                    # Proves the agent *message recv loop* is alive (unlike
                    # outbound heartbeats which only prove the writer works).
                    registry.note_pong(agent_id)

                elif action == "status":
                    # Status update (optional per-session)
                    status = message.get("status", "online")
                    session_id = str(message.get("session_id") or "").strip()
                    if session_id:
                        registry.set_session_busy(agent_id, session_id, status == "busy")
                    elif status == "busy":
                        registry.set_busy(agent_id, True)
                    else:
                        registry.set_busy(agent_id, False)

                elif msg_type in [
                    "message",
                    "response",
                    "thought",
                    "stream",
                    "tool_call",
                    "tool_call_delta",
                    "tool_result",
                    "state",
                    "wake",
                    "sleep",
                    "info",
                    "status",
                    "turn_start",
                    "turn_elapsed",
                    "token_stats",
                    "current_session",
                    "history_sync",
                    "session_list",
                    "busy_sessions",
                    "primary_session",
                    "file_push",
                    "plan",
                    "prompt_update",
                    "output_media",
                    "summary_stream",
                    "compression_progress",
                    "job_stdout",
                    "job_status",
                    # StepAudio realtime voice (browser <-> agent bridge)
                    "voice_realtime_status",
                    "voice_audio_out",
                    "voice_transcript",
                    "scheduled_task_turn_done",
                ]:
                    # Agent's response message, forward to user
                    user_id = message.get("user_id")
                    # Capture the disk session_id for a scheduled-task execution
                    # from ANY event the Agent streams back (stream / state /
                    # thought / current_session / message ...). Every forwarded
                    # event carries `sid` = the turn's disk session_id (runner
                    # sets _turn_sid = get_current_session_id()). Relying solely
                    # on the `current_session` event is fragile: for external
                    # ingress the turn runs on the PRIMARY session, and the runner
                    # only emits `current_session` when sid == focused — so for
                    # scheduled tasks it never fires and session_id stayed null
                    # ("尚未创建会话"). Capturing from the first event with a
                    # non-empty sid makes the workflow loadable immediately.
                    if user_id and isinstance(user_id, str) and user_id.startswith("scheduled-task:"):
                        _st_exec_id = user_id.split(":", 1)[1]
                        _st_sess_id = str(message.get("sid") or "").strip()
                        if not _st_sess_id:
                            _st_c = message.get("content")
                            if isinstance(_st_c, dict):
                                _st_sess_id = str(_st_c.get("id") or _st_c.get("session_id") or "").strip()
                        if _st_exec_id and _st_sess_id:
                            try:
                                from opensquad.scheduled_tasks import set_execution_session_by_exec_id

                                set_execution_session_by_exec_id(_st_exec_id, _st_sess_id)
                            except Exception as _st_e:
                                logger.warning("[WS] scheduled-task session capture failed: %s", _st_e)

                    if msg_type == "scheduled_task_turn_done":
                        try:
                            from opensquad.scheduled_tasks import mark_execution_done_by_exec_id

                            _pdata = message.get("content") or message.get("data") or {}
                            if not isinstance(_pdata, dict):
                                _pdata = {}
                            _done_exec = str(_pdata.get("exec_id") or "").strip()
                            if (
                                not _done_exec
                                and user_id
                                and isinstance(user_id, str)
                                and user_id.startswith("scheduled-task:")
                            ):
                                _done_exec = user_id.split(":", 1)[1]
                            _done_status = str(_pdata.get("status") or "success").strip() or "success"
                            if _done_status not in ("success", "failed", "stopped"):
                                _done_status = "success"
                            if _done_exec:
                                mark_execution_done_by_exec_id(_done_exec, status=_done_status)
                        except Exception as _done_e:
                            logger.warning("[WS] scheduled-task turn done failed: %s", _done_e)
                    if msg_type == "info":
                        try:
                            info_payload = message.get("content") or message.get("data") or {}
                            if isinstance(info_payload, dict):
                                evt = info_payload.get("event")
                                trace_id = info_payload.get("trace_id")
                                if evt and str(evt).startswith("context_compress"):
                                    logger.info(
                                        "[Gateway] Forward info event=%s trace_id=%s user_id=%s agent_id=%s",
                                        evt,
                                        trace_id,
                                        user_id,
                                        agent_id,
                                    )
                        except Exception:
                            pass
                    if msg_type == "summary_stream":
                        try:
                            sdata = message.get("content") or message.get("data") or {}
                            if isinstance(sdata, dict):
                                logger.info(
                                    "[Gateway] Forward summary_stream id=%s done=%s delta_len=%s text_len=%s user_id=%s agent_id=%s",
                                    sdata.get("id"),
                                    sdata.get("done"),
                                    len(sdata.get("delta", "") or ""),
                                    len(sdata.get("text", "") or ""),
                                    user_id,
                                    agent_id,
                                )
                        except Exception:
                            pass
                    if msg_type == "history_sync":
                        try:
                            hdata = message.get("content") or message.get("data") or {}
                            if isinstance(hdata, dict):
                                logger.info(
                                    "[Gateway] Forward history_sync session_id=%s messages=%d events=%d reason=%s user_id=%s agent_id=%s",
                                    hdata.get("session_id"),
                                    len(hdata.get("messages", []) or []),
                                    len(hdata.get("events", []) or []),
                                    hdata.get("reason"),
                                    user_id,
                                    agent_id,
                                )
                                # Compression / withdraw rewrite current_session
                                # on disk — drop the cached reader so the next
                                # HTTP hydrate sees the truncated snapshot.
                                if hdata.get("reason") in ("compression", "withdraw"):
                                    from .agent_sessions import invalidate_reader

                                    invalidate_reader(agent_id)
                        except Exception:
                            pass

                    if msg_type == "busy_sessions":
                        try:
                            from opensquad.scheduled_tasks import reconcile_executions_for_busy_sessions

                            _pdata = message.get("content") or message.get("data") or {}
                            if isinstance(_pdata, list):
                                _busy_sids = [str(s) for s in _pdata]
                            elif isinstance(_pdata, dict):
                                _busy_sids = [str(s) for s in (_pdata.get("sessions") or [])]
                            else:
                                _busy_sids = []
                            reconcile_executions_for_busy_sessions(agent_id, _busy_sids)
                        except Exception as _busy_e:
                            logger.warning("[WS] scheduled-task busy reconcile failed: %s", _busy_e)

                    if msg_type == "current_session":
                        try:
                            from .agent_sessions import invalidate_reader

                            invalidate_reader(agent_id)
                        except Exception:
                            pass
                        # Track the agent's canonical disk session id so the next
                        # user `connected` event exposes a real session instead of
                        # the gateway_session_key fallback (which is not a disk
                        # session file and breaks HTTP history reads).
                        _cs = message.get("content")
                        if isinstance(_cs, dict):
                            _csid = str(_cs.get("id") or _cs.get("session_id") or "").strip()
                        else:
                            _csid = str(_cs or "").strip()
                        if _csid:
                            _agent_current_session_id[agent_id] = _csid
                        # Also invalidate Gateway in-memory cache so stale
                        # user messages from previous session aren't served
                        # on reconnect.
                        with contextlib.suppress(Exception):
                            gateway_session_cache.invalidate(user_id, agent_id)
                        # Correlate the Agent's disk session_id back to the
                        # scheduled-task execution that triggered this turn.
                        # user_id is "scheduled-task:{exec_id}" (set by
                        # ScheduledTaskManager._send_to_agent).
                        if user_id and isinstance(user_id, str) and user_id.startswith("scheduled-task:"):
                            _exec_id = user_id.split(":", 1)[1]
                            _sess_id = ""
                            _c = message.get("content")
                            if isinstance(_c, dict):
                                _sess_id = str(_c.get("id") or "").strip()
                            if not _sess_id:
                                _sess_id = str(message.get("sid") or "").strip()
                            if _exec_id and _sess_id:
                                try:
                                    from opensquad.scheduled_tasks import set_execution_session_by_exec_id

                                    set_execution_session_by_exec_id(_exec_id, _sess_id)
                                except Exception as e:
                                    logger.warning("[WS] scheduled-task session capture failed: %s", e)

                    # Persist final assistant replies in Gateway WS history so refresh
                    # still works when the disk-session HTTP API is slow or unavailable.
                    # Streaming chunks (stream/thought/tool_*) are NOT saved here.
                    if user_id and msg_type in ("message", "response", "to_user_end_task"):
                        content = message.get("content", "")
                        if isinstance(content, str) and content.strip():
                            await gateway_session_cache.async_add_message(
                                user_id,
                                agent_id,
                                "assistant",
                                content,
                                message_id=message.get("message_id"),
                                end_task=(msg_type == "to_user_end_task"),
                            )

                    if user_id:
                        if (
                            user_id in ("adapter-user",)
                            or user_id.startswith("feishu_")
                            # Scheduled-task turns use a synthetic user_id with no
                            # browser WS. Broadcast so ExecWorkflowView / Agent Web
                            # panes watching this agent receive live events (sid-
                            # filtered on the client) instead of HTTP-poll-only lag.
                            or user_id.startswith("scheduled-task:")
                            # token_stats must reach every pane on this agent WS
                            # (parallel sessions / exec view filter by sid).
                            or msg_type == "token_stats"
                        ):
                            await user_handler.broadcast_to_agent(agent_id, message)
                        else:
                            await user_handler.forward_to_user(user_id, agent_id, message)
                    else:
                        await user_handler.broadcast_to_agent(agent_id, message)

                elif action == "chat_response":
                    user_id = message.get("user_id")
                    if user_id:
                        if (
                            user_id in ("adapter-user",)
                            or user_id.startswith("feishu_")
                            or user_id.startswith("scheduled-task:")
                        ):
                            await user_handler.broadcast_to_agent(
                                agent_id,
                                {"type": "message", "role": "assistant", "content": message.get("content", "")},
                            )
                        else:
                            await user_handler.forward_to_user(
                                user_id,
                                agent_id,
                                {"type": "message", "role": "assistant", "content": message.get("content", "")},
                            )

                else:
                    logger.warning(f"Unknown message from agent {agent_id}: {message}")

        except WebSocketDisconnect:
            logger.info(f"Agent {agent_id} disconnected")
            registry.unregister(agent_id)
        except Exception as e:
            logger.error(f"Agent message loop error: {e}")
            registry.unregister(agent_id)


class UserWebSocketHandler:
    """Handle user's WebSocket connection (conversation).

    Supports multiple simultaneous connections per user:agent pair
    (e.g. phone + laptop + desktop all receiving real-time streaming).
    Uses a dict of lists: conn_key -> [WebSocket, ...]
    """

    def __init__(self):
        # Store user connections: "user_id:agent_id" -> list[WebSocket]
        self.user_connections: dict[str, list[WebSocket]] = {}
        self._kv_lock = asyncio.Lock()

    async def handle_user_chat(
        self,
        websocket: WebSocket,
        agent_id: str,
        user_id: str,  # parsed from token
    ):
        """Handle conversation between user and Agent"""
        await websocket.accept()

        # UI may pass dir_name (agent305) while registry keys agent_id (agent305-001).
        requested_id = agent_id
        agent_id = _resolve_registered_agent_id(agent_id)
        if agent_id != requested_id:
            logger.info("[WS] Resolved user-chat agent alias %r -> %r", requested_id, agent_id)

        # Wait briefly for agent startup. If still offline, fail fast with 1013
        # so frontend can retry quickly instead of hanging for a long handshake wait.
        # Event-driven: registry sets the per-agent event on register()/unregister(),
        # so this wakes the moment the agent appears (or disappears) instead of
        # polling every 0.3s.
        agent = registry.get_agent(agent_id)
        if not agent or agent.status == "offline":
            max_wait = 3  # seconds
            logger.info(f"Agent {agent_id} not ready, waiting up to {max_wait}s for it to register...")
            try:
                await asyncio.wait_for(registry.registration_event(agent_id).wait(), timeout=max_wait)
            except asyncio.TimeoutError:
                pass
            agent = registry.get_agent(agent_id)

        if not agent or agent.status == "offline":
            status_msg = f"Agent {agent_id} is offline" if agent else f"Agent {agent_id} not found"
            try:
                await websocket.send_json({"type": "error", "message": status_msg})
                # Explicit 1013 (Try Again Later) helps frontend treat this as startup delay,
                # avoiding confusing normal-close(1000) reconnect churn.
                await websocket.close(code=1013, reason="agent_not_ready")
            except Exception:
                # Client may have already disconnected (code 1005/1006), just ignore
                pass
            return

        # Get or create session
        session = await gateway_session_cache.async_get_or_create_session(user_id, agent_id)

        # Register connection (supports multiple devices per user:agent pair)
        conn_key = f"{user_id}:{agent_id}"
        async with self._kv_lock:
            if conn_key not in self.user_connections:
                self.user_connections[conn_key] = []
            self.user_connections[conn_key].append(websocket)
        logger.info(
            f"[WS] User {user_id} registered for agent {agent_id}, total connections for this pair: {len(self.user_connections[conn_key])}"
        )

        # [Fix P2-A regression] Wrap all post-registration operations in the same try/finally
        # Originally only _user_message_loop was in try; send_json(connected) and history sending were outside the guard:
        # If these two steps threw WebSocketDisconnect, the function escaped directly, finally never ran,
        # leaving dead sockets permanently in user_connections, causing subsequent Agent broadcasts to keep hitting dead connections.
        try:
            # Send history (most recent 20)
            # Send the full history as a fallback for when the disk session API
            # is temporarily unavailable during refresh/reconnect.
            history = await gateway_session_cache.async_get_history(user_id, agent_id, limit=20)

            # Send session info.
            # Prefer the agent's canonical disk session id (reported via the
            # `current_session` WS event) — it is a real history key the
            # frontend can read via /agent-sessions/{agent}/{sid}/paged.
            # Fall back to gateway_session_key only when the agent has not
            # reported a disk session yet (it always resolves for new chats).
            session_key = session["session_key"]
            canonical_sid = _agent_current_session_id.get(agent_id, "")
            await websocket.send_json(
                {
                    "type": "connected",
                    "agent_id": agent_id,
                    "agent_name": agent.agent_name,
                    "agent_status": agent.status,
                    "session_id": canonical_sid or session_key,
                    "gateway_session_key": session_key,
                    "history_count": len(history),
                }
            )
            if history:
                for msg in history:
                    payload = dict(msg)
                    payload_msg_type = payload.pop("type", "text")
                    if isinstance(payload.get("extra"), dict):
                        extra = payload.pop("extra")
                        if "images" in extra and "images" not in payload:
                            payload["images"] = extra.get("images", [])
                        if "attachments" in extra and "attachments" not in payload:
                            payload["attachments"] = extra.get("attachments", [])
                        if payload_msg_type == "file_push":
                            payload["files"] = extra.get("files", payload.get("files", []))
                            payload["message"] = extra.get(
                                "message", payload.get("message", payload.get("content", ""))
                            )
                    await websocket.send_json(
                        {
                            "type": "history",
                            "msg_type": payload_msg_type,
                            **payload,
                        }
                    )

            logger.info(f"User {user_id} connected to agent {agent_id}")

            # Request Agent to immediately broadcast latest token stats (resolves post-refresh stats delay issue)
            # Debounce: skip if we already sent one within the last 2 seconds for this agent.
            # Prevents flooding the agent's urgent queue when multiple devices reconnect rapidly.
            now = time.time()
            _last_token_req = getattr(user_handler, "_last_token_req_time", {})
            last = _last_token_req.get(agent_id, 0)
            if now - last > 2.0:
                _last_token_req[agent_id] = now
                user_handler._last_token_req_time = _last_token_req
                asyncio.create_task(
                    registry.send_to_agent(
                        agent_id,
                        {
                            "type": "command",
                            "user_id": user_id,
                            "command": "request_token_stats",
                        },
                    )
                )

            # Enter message loop
            await self._user_message_loop(user_id, agent_id, websocket)
        except Exception as e:
            logger.error(f"User chat error: {e}", exc_info=True)
        finally:
            # Cleanup connection — remove own WS from the list, keep others alive
            async with self._kv_lock:
                conns = self.user_connections.get(conn_key)
                if conns:
                    if websocket in conns:
                        conns.remove(websocket)
                    if not conns:
                        del self.user_connections[conn_key]
            logger.info(
                f"User {user_id} disconnected from agent {agent_id}, remaining connections for this pair: {len(conns) if conns else 0}"
            )

    async def _user_message_loop(self, user_id: str, agent_id: str, user_ws: WebSocket):
        """User message loop"""
        try:
            while True:
                # Receive user message
                message = await user_ws.receive_json()
                msg_type = message.get("type")

                if msg_type == "command":
                    command = message.get("command", "")
                    data = message.get("data", {})
                    logger.info(f"[AI-WS][command-in] user={user_id} agent={agent_id} command={command!r}")
                    if not command:
                        continue

                    if command == "new_session":
                        # Reset Gateway WS fallback history immediately so reconnect/refresh
                        # does not replay the previous conversation while waiting for Runner.
                        await gateway_session_cache.async_clear_session(user_id, agent_id)
                        await gateway_session_cache.async_add_message(
                            user_id,
                            agent_id,
                            "user",
                            "__NEW_SESSION__",
                            msg_type="system",
                        )

                    if command == "abandon_current_draft":
                        # Same gateway-side cache reset as new_session — we are about
                        # to switch focus to a fresh sid, so any cached fallback history
                        # for the abandoned session must not survive a refresh.
                        await gateway_session_cache.async_clear_session(user_id, agent_id)
                        await gateway_session_cache.async_add_message(
                            user_id,
                            agent_id,
                            "user",
                            "__ABANDON_CURRENT_DRAFT__",
                            msg_type="system",
                        )

                    # Forward command to agent — never saved to session history
                    sent = await registry.send_to_agent(
                        agent_id,
                        {
                            "type": "command",
                            "user_id": user_id,
                            "command": command,
                            "data": data if isinstance(data, dict) else {},
                        },
                    )
                    if not sent:
                        await user_ws.send_json(
                            {
                                "type": "error",
                                "message": f"Agent {agent_id} is not connected; command {command!r} not delivered",
                            }
                        )
                        if command in ("voice_realtime_start", "voice_realtime_stop"):
                            await user_ws.send_json(
                                {
                                    "type": "voice_realtime_status",
                                    "content": {
                                        "ok": False,
                                        "status": "error",
                                        "error": f"Agent {agent_id} is not connected",
                                    },
                                }
                            )
                    continue

                if msg_type == "chat":
                    content = message.get("content", "").strip()
                    images = message.get("images", [])
                    attachments = message.get("attachments", [])
                    logger.info(
                        f"[AI-WS][chat-in] user={user_id} agent={agent_id} "
                        f"content_head={(content or '')[:120]!r} images={len(images) if isinstance(images, list) else -1} "
                        f"attachments={len(attachments) if isinstance(attachments, list) else -1}"
                    )
                    has_media = (isinstance(images, list) and len(images) > 0) or (
                        isinstance(attachments, list) and len(attachments) > 0
                    )
                    if not content and not has_media:
                        continue

                    # Check if Agent is online
                    agent = registry.get_agent(agent_id)
                    if not agent or agent.status == "offline":
                        await user_ws.send_json({"type": "error", "message": "Agent is offline"})
                        continue

                    # Save user message (preserve structured media metadata for history replay)
                    images = message.get("images", [])
                    attachments = message.get("attachments", [])
                    await gateway_session_cache.async_add_message(
                        user_id,
                        agent_id,
                        "user",
                        content,
                        images=images if isinstance(images, list) else [],
                        attachments=attachments if isinstance(attachments, list) else [],
                        extra={
                            "images": images if isinstance(images, list) else [],
                            "attachments": attachments if isinstance(attachments, list) else [],
                        },
                    )

                    # Forward to Agent
                    # NOTE: no `history` field here — the agent maintains its own
                    # authoritative disk session and never reads gateway history
                    # (gateway_adapter._handle_chat ignores it). Previously we
                    # serialized + sent the last 10 messages on EVERY chat turn,
                    # a pure overhead (thread-pool hop + lock + JSON bytes).
                    message_to_agent = {
                        "type": "chat",
                        "user_id": user_id,
                        "content": content,
                        "channel": message.get("channel", "web"),
                    }
                    # Pass through session_id for parallel multi-session routing
                    _sid = str(message.get("session_id") or "").strip()
                    if _sid:
                        message_to_agent["session_id"] = _sid
                    # Pane-selected model card — turn bind uses this even if switch_model was dropped
                    _card = str(message.get("model_card") or message.get("card") or "").strip()
                    if _card:
                        message_to_agent["model_card"] = _card
                    # Pass through context fields
                    for _ctx_key in ("sender_name", "chat_name", "source_chat_id", "client_id", "message_id"):
                        _ctx_val = message.get(_ctx_key, "")
                        if _ctx_val:
                            message_to_agent[_ctx_key] = _ctx_val
                    # Pass through image paths (if any)
                    images = message.get("images", [])
                    if images:
                        message_to_agent["images"] = images
                    attachments = message.get("attachments", [])
                    if attachments:
                        message_to_agent["attachments"] = attachments
                    logger.info(f"Sending to agent {agent_id}: {message_to_agent}")
                    success = await registry.send_to_agent(agent_id, message_to_agent)

                    if success:
                        logger.info(f"Successfully forwarded message from {user_id} to agent {agent_id}")
                        # Increment today's chat count
                        registry.increment_today_chats(agent_id)
                        # Set Agent/session busy
                        if _sid:
                            registry.set_session_busy(agent_id, _sid, True)
                        else:
                            registry.set_busy(agent_id, True)

                        # Broadcast user message to other connected devices (multi-device sync)
                        broadcast_msg = {
                            "type": "message",
                            "role": "user",
                            "content": content,
                            "images": images if isinstance(images, list) else [],
                            "attachments": attachments if isinstance(attachments, list) else [],
                            "message_id": f"user_{int(time.time() * 1000)}_{user_id[:8]}",
                            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                        }
                        conns = self.user_connections.get(f"{user_id}:{agent_id}", [])

                        async def _bcast(ws):
                            if ws is not user_ws:  # Don't echo back to sender
                                with contextlib.suppress(Exception):
                                    await ws.send_json(broadcast_msg)

                        if len(conns) > 1:
                            await asyncio.gather(*[_bcast(ws) for ws in conns])
                    else:
                        logger.error(f"Failed to forward message to agent {agent_id} - agent connection not found")
                        await user_ws.send_json({"type": "error", "message": "Agent connection not available"})

                elif msg_type == "ping":
                    await user_ws.send_json({"type": "pong"})

                elif msg_type == "voice_audio_in":
                    # Forward PCM16 chunks to agent without saving to history
                    audio = message.get("audio") or message.get("data") or ""
                    if audio:
                        await registry.send_to_agent(
                            agent_id,
                            {
                                "type": "voice_audio_in",
                                "user_id": user_id,
                                "audio": audio,
                            },
                        )

                elif msg_type == "voice_mouthpiece_utterance":
                    audio = message.get("audio") or ""
                    sample_rate = message.get("sample_rate") or 24000
                    if audio:
                        await registry.send_to_agent(
                            agent_id,
                            {
                                "type": "voice_mouthpiece_utterance",
                                "user_id": user_id,
                                "audio": audio,
                                "sample_rate": sample_rate,
                            },
                        )

                else:
                    logger.warning(f"Unknown message type from user: {msg_type}")

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"User message loop error: {e}", exc_info=True)
            import traceback

            logger.error(traceback.format_exc())

    async def forward_to_user(self, user_id: str, agent_id: str, message: dict):
        """Forward Agent message to ALL connected devices for this user:agent pair.
        Uses asyncio.gather for parallel sending so all devices receive simultaneously."""
        conn_key = f"{user_id}:{agent_id}"
        # Take a snapshot copy so that concurrent disconnect/broadcast does not
        # mutate the list we are iterating, which would cause index-based
        # pruning to remove the wrong (live) connections.
        conns = list(self.user_connections.get(conn_key, []))

        if not conns:
            logger.warning(
                f"[forward_to_user] No connection found for key='{conn_key}', available keys: {list(self.user_connections.keys())}"
            )
            return False

        async def _send(ws: WebSocket) -> bool:
            try:
                await ws.send_json(message)
                return True
            except Exception as e:
                logger.warning(f"[forward_to_user] Dead connection for {user_id}:{agent_id}: {e}")
                return False

        # return_exceptions=True so one failing device does not abort sends to
        # the others (aligns with broadcast_to_agent's behaviour).
        results = await asyncio.gather(*[_send(ws) for ws in conns], return_exceptions=True)
        sent_any = any(r is True for r in results)
        # Collect dead connections by object identity, not by index — the live
        # list may have been modified concurrently during the gather.
        dead_ws = {conns[i] for i, r in enumerate(results) if r is not True}

        if dead_ws:
            async with self._kv_lock:
                remaining = self.user_connections.get(conn_key, [])
                remaining[:] = [w for w in remaining if w not in dead_ws]
                if conn_key in self.user_connections and not self.user_connections[conn_key]:
                    del self.user_connections[conn_key]

        return sent_any

    async def broadcast_to_agent(self, agent_id: str, message: dict) -> set:
        """Broadcast message to ALL connections of ALL users for this agent.

        Uses asyncio.gather for parallel sending — all devices receive the
        message simultaneously instead of sequentially, which is critical
        for multi-device streams (3 devices = 3x latency improvement).
        Dead connections are pruned in a single batch afterwards.
        """
        # Flatten all connections for this agent into a single send-list.
        # conn_key format is "{user_id}:{agent_id}". Use an exact split rather
        # than endswith() so that agent IDs which are suffixes of each other
        # (e.g. "a" vs "xa") or user_ids containing ":" (e.g. "feishu_x:123")
        # do not cause cross-agent leakage.
        sends: list[tuple[str, WebSocket]] = []
        for conn_key, conn_list in list(self.user_connections.items()):
            uid, sep, aid = conn_key.rpartition(":")
            if sep and aid == agent_id:
                for ws in conn_list:
                    sends.append((uid, ws))

        if not sends:
            return set()

        async def _send_one(uid: str, ws: WebSocket) -> tuple[str, bool]:
            try:
                await ws.send_json(message)
                return (uid, True)
            except Exception:
                return (uid, False)

        results = await asyncio.gather(
            *[_send_one(uid, ws) for (uid, ws) in sends],
            return_exceptions=True,
        )

        # Separate delivered from dead in a single pass
        delivered: set[str] = set()
        dead: dict[str, list[WebSocket]] = {}
        for (uid, ws), result in zip(sends, results, strict=False):
            if isinstance(result, tuple) and result[1]:
                delivered.add(uid)
            else:
                dead.setdefault(f"{uid}:{agent_id}", []).append(ws)

        # Batch-cleanup dead sockets
        if dead:
            async with self._kv_lock:
                for conn_key, dead_sockets in dead.items():
                    remaining = self.user_connections.get(conn_key)
                    if remaining:
                        remaining[:] = [ws for ws in remaining if ws not in dead_sockets]
                        if not remaining:
                            del self.user_connections[conn_key]

        return delivered


# Handler instances
agent_handler = AgentWebSocketHandler()
user_handler = UserWebSocketHandler()


# ============================================================
# Launcher ↔ Gateway WebSocket RPC tunnel
# Launcher (home) connects here; Gateway sends admin_request
# and receives admin_response — no inbound port needed on home.
# ============================================================


class LauncherWebSocketHandler:
    """
    Bidirectional RPC over the WS connection that Launcher establishes.
    - Launcher connects to /ai-ws/launcher and sends launcher_register.
    - Gateway calls rpc() to forward admin HTTP requests to Launcher.
    - Launcher relays to its local HTTP server and replies admin_response.
    """

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}  # node_id -> ws
        self._pending: dict[str, asyncio.Future] = {}  # req_id  -> future

    def has_connections(self) -> bool:
        return bool(self._connections)

    def connected_nodes(self) -> list:
        return list(self._connections.keys())

    def get_any_node_id(self) -> str | None:
        """Return the first connected node_id, or None."""
        return next(iter(self._connections), None)

    async def handle_launcher_connect(self, websocket: WebSocket):
        """Accept a Launcher WS connection and dispatch messages."""
        await websocket.accept()
        node_id: str | None = None
        try:
            async for raw in websocket.iter_text():
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                msg_type = msg.get("type")

                if msg_type == "launcher_register":
                    node_id = msg.get("node_id", "")
                    # Validate node_secret
                    if not _check_node_secret(msg.get("node_secret", "")):
                        logger.warning(f"Launcher registration rejected: invalid node_secret (node_id={node_id!r})")
                        await websocket.close(code=4003, reason="Unauthorized: invalid node_secret")
                        return
                    if node_id:
                        self._connections[node_id] = websocket
                        logger.info(f"Launcher '{node_id}' WS tunnel connected")
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "launcher_ack",
                                    "node_id": node_id,
                                }
                            )
                        )

                elif msg_type == "keepalive":
                    # Launcher periodic heartbeat — nothing to do
                    pass

                elif msg_type == "admin_response":
                    req_id = msg.get("req_id")
                    fut = self._pending.pop(req_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"Launcher WS error (node={node_id}): {e}")
        finally:
            if node_id:
                self._connections.pop(node_id, None)
                # Cancel any pending RPCs for this node
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.cancel()
                logger.info(f"Launcher '{node_id}' WS tunnel disconnected")

    async def rpc(
        self,
        node_id: str,
        method: str,
        path: str,
        body=None,
        timeout: float = 20.0,
    ) -> dict:
        """
        Send an admin RPC request to Launcher and await the response.
        Raises HTTPException on error / timeout.
        """
        ws = self._connections.get(node_id)
        if ws is None:
            raise HTTPException(502, f"Launcher '{node_id}' not connected via WS tunnel")

        req_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut

        try:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "admin_request",
                        "req_id": req_id,
                        "method": method,
                        "path": path,
                        "body": body,
                    }
                )
            )
            result = await asyncio.wait_for(fut, timeout=timeout)
            status = result.get("status", 200)
            resp_body = result.get("body", {})
            if status >= 400:
                raise HTTPException(status, resp_body.get("error", "Launcher error"))
            return resp_body
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise HTTPException(504, "Launcher RPC timeout")
        except asyncio.CancelledError:
            self._pending.pop(req_id, None)
            raise HTTPException(502, "Launcher disconnected during RPC")
        except HTTPException:
            raise
        except Exception as e:
            self._pending.pop(req_id, None)
            raise HTTPException(502, f"Launcher RPC error: {e}")


launcher_handler = LauncherWebSocketHandler()
