import asyncio
import contextlib
import logging

from opensquad import bus
from opensquad.input_hub import input_hub
from opensquad.sdk import AgentConfig, BaseAgent
from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)

_TOP_LEVEL_CMD_SKIP = frozenset({"type", "command", "user_id", "data", "seq", "timestamp"})
_TOP_LEVEL_CMD_FALLBACK_KEYS = (
    "id",
    "request_id",
    "chosen_option_id",
    "chosen_option_ids",
    "option_id",
    "option_ids",
    "custom_answer",
    "custom",
    "ignored",
    "mode",
    "agent_mode",
    "approved_request_id",
    "reason",
    "card",
    "card_name",
    "effort",
    "reasoning_effort",
    "session_id",
    "content",
)


def coerce_command_data(message: dict) -> dict:
    """Return the command payload dict for gateway_adapter handlers.

    Prefer ``message["data"]``. If callers put fields at the top level (a bug that
    used to break group ``resolve_proposed_options``), merge those in so the
    agent still wakes.
    """
    if not isinstance(message, dict):
        return {}
    raw = message.get("data", {})
    cmd_data = dict(raw) if isinstance(raw, dict) else {}
    if not cmd_data:
        return {k: v for k, v in message.items() if k not in _TOP_LEVEL_CMD_SKIP}
    if cmd_data.get("id") or cmd_data.get("request_id"):
        return cmd_data
    if not (message.get("id") or message.get("request_id")):
        return cmd_data
    merged = dict(cmd_data)
    for key in _TOP_LEVEL_CMD_FALLBACK_KEYS:
        if key in message and key not in merged:
            merged[key] = message[key]
    return merged


class GatewayAdapter(BaseAgent):
    """
    Adapter that connects the Main Runner to the Gateway.
    1. Receives Gateway messages -> pushes to InputHub
    2. Listens to Runner events -> forwards back to Gateway

    Event forwarding strategy:
    - Has current_user_id (work triggered by a Web user) -> directed push to that user
    - No current_user_id (triggered by group chat/wakeup) -> broadcast to all Web users connected to this agent
    """

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.current_user_id = None
        # Per-session user routing: parallel turns must not overwrite each
        # other's outbound user_id via the single current_user_id field.
        self._user_id_by_sid: dict[str, str] = {}
        # Per-session stream debounce (sid -> chunks / flush task). A single
        # shared buffer mixed A/B stream chunks under concurrent turns.
        self._stream_buffers: dict[str, list] = {}
        self._stream_flush_tasks: dict[str, asyncio.Task] = {}
        self._max_chunks = 1000  # P2: hard cap to prevent unbounded growth
        # Bus subscription tracking — enables clean dispose()
        self._subscriptions: list[tuple[str, callable]] = []

        def _sub(event_type: str, callback: callable):
            """Subscribe and track for later cleanup."""
            bus.subscribe(event_type, callback)
            self._subscriptions.append((event_type, callback))

        # Listen to Runner output events
        _sub("to_user_final", self.on_runner_output)
        _sub("to_user_reply", self.on_runner_output)
        _sub("to_user_end_task", self.on_runner_end_task)
        _sub("thought", self.on_runner_thought)
        _sub("to_user_stream", self.on_runner_stream)
        _sub("tool_call", self.on_tool_call)
        _sub("tool_call_delta", self.on_tool_call_delta)
        _sub("tool_result", self.on_tool_result)
        # Subscribe to state and system notification events
        _sub("state", self.on_runner_state)
        _sub("wake", self.on_generic_event("wake"))
        _sub("sleep", self.on_generic_event("sleep"))
        _sub("info", self.on_generic_event("info"))
        _sub("status", self.on_generic_event("status"))
        _sub("turn_start", self.on_generic_event("turn_start"))
        # Subscribe to Token stats events
        _sub("token_stats", self.on_generic_event("token_stats"))
        # Subscribe to session management events
        _sub("current_session", self.on_generic_event("current_session"))
        _sub("history_sync", self.on_generic_event("history_sync"))
        # Ready-stage notifications (extensions/MCP finished loading)
        _sub("agent_ready_stage", self.on_generic_event("agent_ready_stage"))
        _sub("session_list", self.on_generic_event("session_list"))
        # Subscribe to plan events
        _sub("plan", self.on_generic_event("plan"))
        # Subscribe to workflow elapsed-time events
        _sub("turn_elapsed", self.on_generic_event("turn_elapsed"))
        # Subscribe to prompt_update events
        _sub("prompt_update", self.on_generic_event("prompt_update"))
        # Subscribe to model output media events (audio/image)
        _sub("output_media", self.on_generic_event("output_media"))
        # Realtime voice events
        _sub("voice_audio_out", self.on_generic_event("voice_audio_out"))
        _sub("voice_transcript", self.on_generic_event("voice_transcript"))
        _sub("voice_realtime_status", self.on_generic_event("voice_realtime_status"))
        # Subscribe to context compression summary stream events
        _sub("summary_stream", self.on_generic_event("summary_stream"))
        _sub("group_member_update", self.on_generic_event("group_member_update"))
        _sub("user_status_update", self.on_generic_event("user_status_update"))
        # Shell / background job live output for CMD-style web panel
        _sub("job_stdout", self.on_generic_event("job_stdout"))
        _sub("job_status", self.on_generic_event("job_status"))
        _sub("busy_sessions", self.on_generic_event("busy_sessions"))
        _sub("scheduled_task_turn_done", self.on_generic_event("scheduled_task_turn_done"))
        _sub("compression_progress", self.on_generic_event("compression_progress"))

    def dispose(self):
        """Unsubscribe all event bus handlers.
        Call this when the adapter is being replaced (e.g. during agent hot-restart)
        to prevent stale handlers from piling up and causing duplicate event forwarding.
        """
        count = 0
        for event_type, callback in self._subscriptions:
            try:
                bus.unsubscribe(event_type, callback)
                count += 1
            except Exception as e:
                logger.warning(f"[GatewayAdapter] Failed to unsubscribe {event_type}: {e}")
        self._subscriptions.clear()
        for _sid, cancel_task in list(self._stream_flush_tasks.items()):
            if cancel_task and not cancel_task.done():
                cancel_task.cancel()
        self._stream_flush_tasks.clear()
        self._stream_buffers.clear()
        logger.info(f"[GatewayAdapter] Disposed: unsubscribed {count} handlers")

    @staticmethod
    def _unwrap(data):
        """
        Unwrap the {"sid": ..., "data": ...} wrapper produced by Runner._emit.
        sid is the internal event bus session routing tag; Gateway/React does not need it.
        Returns the actual content (the value of the "data" field), or returns data unchanged
        if it is not a wrapped structure.

        Note: len(data) == 2 is not enforced because some events (e.g. token_stats)
        carry extra fields like agent_id, but the actual content is still in the "data" key.
        """
        if isinstance(data, dict) and "sid" in data and "data" in data:
            return data["data"]
        return data

    @staticmethod
    def _extract_sid(data) -> str:
        """Extract session ID from EventBus wrapper, if present."""
        if isinstance(data, dict) and "sid" in data and "data" in data:
            return data["sid"]
        return ""

    async def _send_event(self, content, msg_type: str = "message", sid: str = ""):
        """
        Unified event dispatch: directed push when user_id is set, broadcast otherwise.
        Includes session_id so the frontend can filter cross-session messages.

        Prefer the per-session user map (populated by _handle_chat) so parallel
        turns do not steal each other's outbound routing via current_user_id.
        """
        uid = ""
        if sid:
            uid = (self._user_id_by_sid.get(sid) or "").strip()
        if not uid:
            uid = (self.current_user_id or "").strip() if self.current_user_id else ""
        if uid:
            await self.send_response_to_user(uid, content, msg_type, sid=sid)
        else:
            await self.send_response(content, msg_type, sid=sid)

    async def on_runner_state(self, data):
        """When Runner state changes: forward the event to the frontend and update local load_percent."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            # content may be a string (e.g. "working") or a dict (e.g. {"state": "working", ...})
            state_value = (
                content if isinstance(content, str) else (content.get("state") if isinstance(content, dict) else "")
            )
            if state_value == "working":
                self._load_percent = 100
            else:
                self._load_percent = 0
            logger.debug(f"[GatewayAdapter] State changed to '{state_value}', load_percent={self._load_percent}")
            await self._send_event(content, "state", sid=sid)

    def on_generic_event(self, event_type):
        """Generic event forwarder."""

        async def handler(data):
            if self.connected:
                sid = self._extract_sid(data)
                content = self._unwrap(data)
                logger.debug(f"[GatewayAdapter] Event {event_type}: {str(content)[:50]}...")
                await self._send_event(content, event_type, sid=sid)

        return handler

    async def send_files_to_chat(self, files: list, message: str = "", user_id: str | None = None):
        """
        Push local files to the AI Chat panel (via Gateway /agent-push/chat).
        files: [{path, original_name, size, content_type, is_image, is_audio, is_video}, ...]
        """
        import httpx

        try:
            payload = {
                "agent_id": self.config.agent_id,
                "message": message or "",
                "files": files or [],
            }
            if user_id:
                payload["user_id"] = user_id
            url = f"{syscfg.gateway_http()}/api/ai-web/agent-push/chat"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[GatewayAdapter] send_files_to_chat failed: {e}")
            return False

    async def _handle_command(self, data: dict):
        """Handle command messages from the Gateway (separate channel from chat)."""
        command = data.get("command", "")
        cmd_data = coerce_command_data(data)
        user_id = data.get("user_id", "")

        # Route session/control events back to the requesting web user (not broadcast).
        if user_id:
            self.current_user_id = user_id
            stop_sid = str(cmd_data.get("session_id") or "").strip()
            if stop_sid:
                self._user_id_by_sid[stop_sid] = user_id

        logger.info(f"[Adapter] Command received from Gateway ({user_id}): {command}")

        if command == "stop_task":
            stop_sid = str(cmd_data.get("session_id") or "").strip()
            stop_all = bool(cmd_data.get("all"))
            if stop_all:
                input_hub.request_stop()
                logger.info(f"[Adapter] Stop task (all) requested by user {user_id}")
            elif stop_sid:
                input_hub.request_stop_session(stop_sid)
                logger.info(f"[Adapter] Stop task for session {stop_sid} by user {user_id}")
            else:
                # Legacy callers omitted session_id. Prefer focused session only —
                # never cancel every parallel turn (that froze the other pane).
                focused = ""
                try:
                    from opensquad.session_manager import get_session_manager

                    focused = str(get_session_manager().get_focused_session_id() or "").strip()
                except Exception:
                    focused = ""
                if focused:
                    input_hub.request_stop_session(focused)
                    logger.info(f"[Adapter] Stop task (legacy no-sid) → focused session {focused} by user {user_id}")
                else:
                    input_hub.request_stop()
                    logger.info(f"[Adapter] Stop task (legacy no-sid, no focus) → all by user {user_id}")
            return

        if command == "set_primary_session":
            sid = str(cmd_data.get("session_id") or "").strip()
            sm = None
            try:
                from opensquad._context import get_current_context

                ctx = get_current_context()
                sm = ctx.session_manager if ctx else None
            except Exception:
                sm = None
            if sm is None:
                from opensquad.session_manager import get_session_manager

                sm = get_session_manager()
            ok = bool(sid and sm.set_primary_session_id(sid))
            logger.info(f"[Adapter] set_primary_session sid={sid} ok={ok}")
            if self.connected:
                await self._send_event(
                    {"ok": ok, "primary_session_id": sm.get_primary_session_id()},
                    "primary_session",
                    sid=sm.get_primary_session_id(),
                )
            return

        if command == "watch_session":
            # ExecWorkflowView / parallel pane: bind this browser user to a
            # non-focused session so outbound events + token_stats route like
            # normal Agent Web (forward_to_user) instead of synthetic scheduled-task.
            sid = str(cmd_data.get("session_id") or "").strip()
            if user_id and sid:
                self._user_id_by_sid[sid] = user_id
                logger.info("[Adapter] watch_session bind user=%s sid=%s", user_id, sid)
            if sid:
                payload = f"__REQUEST_TOKEN_STATS__:{sid}"
                input_hub.push_urgent(payload, source="gateway", session_id=sid)
                await self._try_wake_agent("urgent-command")
            return

        if command == "request_token_stats":
            # Optional session_id: scheduled-task / parallel panes need stats for
            # a non-focused session. Without it the runner falls back to focused.
            sid = str(cmd_data.get("session_id") or "").strip()
            if user_id and sid:
                # Same bind as watch_session — opening the exec pane claims the turn.
                self._user_id_by_sid[sid] = user_id
            logger.info(f"[Adapter] request_token_stats from user {user_id} sid={sid or '-'}")
            payload = f"__REQUEST_TOKEN_STATS__:{sid}" if sid else "__REQUEST_TOKEN_STATS__"
            input_hub.push_urgent(payload, source="gateway", session_id=sid or "")
            # Wake so idle agents drain the urgent queue and rebroadcast soon.
            await self._try_wake_agent("urgent-command")
            return

        if command == "new_session":
            # Abort sub-agents immediately (don't wait for Runner to drain urgent queue).
            try:
                from opensquad.sub_agent_runner import job_manager

                n = job_manager.cancel_all("new_session")
                if n:
                    logger.info(f"[Adapter] cancelled {n} sub-agent job(s)/runner(s) on new_session")
            except Exception:
                logger.debug("[Adapter] sub-agent cancel_all on new_session skipped", exc_info=True)
            input_hub.push_urgent("__NEW_SESSION__", source="gateway")
            logger.info("[Adapter] New session command sent via urgent queue")
            await self._try_wake_agent("urgent-command")
            return

        if command == "withdraw_turn":
            # Stop any in-flight turn, then truncate session from the user message timestamp.
            input_hub.request_stop()
            ts = str(cmd_data.get("timestamp") or "").strip()
            mid = str(cmd_data.get("message_id") or "").strip()
            # Prefer ISO timestamp for cut; keep message_id for checkpoint / id lookup.
            if ts or mid:
                payload = f"{ts}|{mid}" if mid else ts
                input_hub.push_urgent(f"__WITHDRAW_TURN__:{payload}", source="gateway")
                logger.info(f"[Adapter] withdraw_turn queued ts={ts!r} message_id={mid!r}")
                await self._try_wake_agent("urgent-command")
            else:
                logger.warning("[Adapter] withdraw_turn missing timestamp/message_id")
            return

        if command == "compress_context":
            input_hub.push_urgent("__COMPRESS_CONTEXT__", source="gateway")
            logger.info("[Adapter] Compress context command sent via urgent queue")
            await self._try_wake_agent("urgent-command")
            return

        if command == "switch_and_reply":
            sid = cmd_data.get("session_id", "")
            reply = cmd_data.get("content", "")
            # Prefer per-session inbox over global SWITCH so parallel turns stay isolated.
            if sid and reply:
                input_hub.push(
                    reply,
                    source="gateway",
                    session_id=str(sid),
                    images=cmd_data.get("images"),
                    attachments=cmd_data.get("attachments"),
                    channel=str(cmd_data.get("channel") or "web"),
                )
                logger.info(f"[Adapter] switch_and_reply → session inbox sid={sid}, content_len={len(str(reply))}")
            else:
                cmd = f"__SWITCH_AND_REPLY__:{sid}:{reply}"
                input_hub.push_urgent(cmd, source="gateway", session_id=str(sid) if sid else "")
                logger.info(f"[Adapter] Switch and reply command sent via urgent queue: {cmd[:80]}")
            await self._try_wake_agent("urgent-command")
            return

        if command == "switch_model":
            # Runtime model switch. Prefer a direct await so we never depend on
            # EventBus weakref/task scheduling (which previously dropped switches
            # silently — UI label changed, but LLM kept using the default OpenCode).
            card_name = cmd_data.get("card", "") or cmd_data.get("card_name", "")
            sid = str(cmd_data.get("session_id") or "").strip()
            if not card_name:
                logger.warning("[Adapter] switch_model command missing 'card' field")
                return
            logger.warning("[Adapter] switch_model DIRECT card=%s sid=%s", card_name, sid or "-")
            try:
                from opensquad.model_switch import is_ready, switch_to_card

                if not is_ready():
                    logger.warning("[Adapter] switch_model deferred: coordinator not ready yet")
                result = await switch_to_card(str(card_name), session_id=sid or None)
                logger.warning("[Adapter] switch_model result=%s", result)
            except Exception as e:
                logger.warning("[Adapter] switch_model direct failed (%s); bus fallback", e)
                payload = {"card": card_name}
                if sid:
                    payload["session_id"] = sid
                bus.emit("model.switch.requested", payload)
            return

        if command == "set_voice_config":
            # Update voice.*_card (and optional realtime_voice) in memory + config.json.
            try:
                from opensquad import agent_runtime_context as arc
                from plugins.step_voice import step_voice_tools as _sv_tools

                voice_patch = {}
                for key in ("asr_card", "tts_card", "realtime_card", "realtime_voice"):
                    if key in cmd_data:
                        voice_patch[key] = cmd_data.get(key) or ""
                if not voice_patch:
                    logger.warning("[Adapter] set_voice_config missing voice fields")
                    return

                cfg = dict(arc.agent_config or {})
                voice = dict(cfg.get("voice") or {})
                voice.update(voice_patch)
                cfg["voice"] = voice
                arc.set_context(config=cfg)
                try:
                    _sv_tools.set_agent_config(cfg)
                except Exception:
                    pass

                # Persist to agent config.json when agent_dir is known
                agent_dir = (arc.agent_dir or "").strip()
                if agent_dir:
                    import json
                    import os

                    cfg_path = os.path.join(agent_dir, "config.json")
                    if os.path.isfile(cfg_path):
                        with open(cfg_path, encoding="utf-8") as f:
                            disk = json.load(f)
                        disk_voice = dict(disk.get("voice") or {})
                        disk_voice.update(voice_patch)
                        disk["voice"] = disk_voice
                        with open(cfg_path, "w", encoding="utf-8") as f:
                            json.dump(disk, f, ensure_ascii=False, indent=2)
                            f.write("\n")

                await self._send_event(
                    {
                        "event": "voice_config_updated",
                        "voice": {
                            "asr_card": voice.get("asr_card") or "",
                            "tts_card": voice.get("tts_card") or "",
                            "realtime_card": voice.get("realtime_card") or "",
                            "realtime_voice": voice.get("realtime_voice") or "",
                        },
                    },
                    msg_type="info",
                )
                logger.info("[Adapter] set_voice_config applied: %s", voice_patch)
            except Exception as e:
                logger.warning("[Adapter] set_voice_config failed: %s", e)
            return

        if command == "set_reasoning_effort":
            effort = cmd_data.get("effort", "") or cmd_data.get("reasoning_effort", "")
            sid = str(cmd_data.get("session_id") or "").strip()
            if effort:
                payload = {"effort": effort}
                if sid:
                    payload["session_id"] = sid
                bus.emit("model.reasoning_effort.requested", payload)
                logger.info(f"[Adapter] set_reasoning_effort requested: effort={effort} sid={sid or '-'}")
            else:
                logger.warning("[Adapter] set_reasoning_effort command missing 'effort' field")
            return

        if command == "set_agent_mode":
            mode = cmd_data.get("mode", "") or cmd_data.get("agent_mode", "")
            req_id = cmd_data.get("id") or cmd_data.get("approved_request_id")
            sid = str(cmd_data.get("session_id") or "").strip()
            if mode:
                payload = {"mode": mode, "id": req_id, "approved_request_id": req_id}
                if sid:
                    payload["session_id"] = sid
                bus.emit("agent.mode.requested", payload)
                logger.info(f"[Adapter] set_agent_mode requested: mode={mode} sid={sid or '-'}")
            else:
                logger.warning("[Adapter] set_agent_mode command missing 'mode' field")
            return

        if command == "set_goal":
            action = cmd_data.get("action", "") or cmd_data.get("op", "") or "status"
            objective = cmd_data.get("objective", "") or cmd_data.get("goal", "") or ""
            try:
                from opensquad.goal_mode import apply_goal_action

                result = await apply_goal_action(
                    str(action),
                    objective=str(objective),
                    nudge=bool(cmd_data.get("nudge", True)),
                )
                logger.info(
                    f"[Adapter] set_goal action={action} ok={result.get('ok')} "
                    f"status={(result.get('goal') or {}).get('status')}"
                )
            except Exception as e:
                logger.warning(f"[Adapter] set_goal failed: {e}")
            return

        if command == "deny_mode_switch":
            req_id = cmd_data.get("id", "")
            bus.emit("agent.mode.requested", {"action": "deny", "id": req_id, "reason": cmd_data.get("reason", "")})
            logger.info(f"[Adapter] deny_mode_switch: id={req_id}")
            return

        if command == "resolve_proposed_options":
            req_id = cmd_data.get("id", "") or cmd_data.get("request_id", "")
            chosen = cmd_data.get("chosen_option_id", "") or cmd_data.get("option_id", "")
            chosen_ids_raw = cmd_data.get("chosen_option_ids") or cmd_data.get("option_ids") or []
            if isinstance(chosen_ids_raw, str):
                chosen_ids = [p.strip() for p in chosen_ids_raw.split(",") if p.strip()]
            elif isinstance(chosen_ids_raw, list):
                chosen_ids = [str(x).strip() for x in chosen_ids_raw if str(x).strip()]
            else:
                chosen_ids = []
            custom = cmd_data.get("custom_answer", "") or cmd_data.get("custom", "")
            ignored = bool(cmd_data.get("ignored", False))
            if req_id:
                try:
                    from opensquad.model_switch import resolve_proposed_options

                    await resolve_proposed_options(
                        str(req_id),
                        chosen_option_id=str(chosen),
                        chosen_option_ids=chosen_ids,
                        custom_answer=str(custom),
                        ignored=ignored,
                    )
                    logger.info(
                        f"[Adapter] resolve_proposed_options: id={req_id} "
                        f"chosen={chosen_ids or chosen or custom or ('ignored' if ignored else '?')}"
                    )
                except Exception as e:
                    logger.warning(f"[Adapter] resolve_proposed_options failed: {e}")
            else:
                logger.warning("[Adapter] resolve_proposed_options command missing 'id' field")
            return

        if command == "voice_realtime_start":
            from opensquad.audio import realtime_manager as rtm

            try:
                logger.info("[Adapter] voice_realtime_start begin user=%s", user_id)
                result = await asyncio.wait_for(
                    rtm.start_session(
                        voice=cmd_data.get("voice", ""),
                        instructions=cmd_data.get("instructions", ""),
                        force_ask_agent=cmd_data.get("force_ask_agent", True),
                    ),
                    timeout=25.0,
                )
                if isinstance(result, dict) and not result.get("status"):
                    result = {
                        **result,
                        "status": "connected" if result.get("ok") else "error",
                    }
                logger.info("[Adapter] voice_realtime_start done: %s", result)
                await self._send_event(result, "voice_realtime_status")
            except TimeoutError:
                logger.error("[Adapter] voice_realtime_start timed out")
                await self._send_event(
                    {"ok": False, "status": "error", "error": "Realtime connect timed out"},
                    "voice_realtime_status",
                )
            except Exception as e:
                logger.error("[Adapter] voice_realtime_start failed: %s", e)
                await self._send_event({"ok": False, "status": "error", "error": str(e)}, "voice_realtime_status")
            return

        if command == "voice_realtime_stop":
            from opensquad.audio import realtime_manager as rtm

            try:
                result = await rtm.stop_session()
                await self._send_event(result, "voice_realtime_status")
            except Exception as e:
                logger.error("[Adapter] voice_realtime_stop failed: %s", e)
            return

        if command == "voice_realtime_query":
            from opensquad.audio import realtime_manager as rtm

            try:
                result = rtm.get_session_status()
                await self._send_event(result, "voice_realtime_status")
            except Exception as e:
                logger.error("[Adapter] voice_realtime_query failed: %s", e)
                await self._send_event(
                    {"ok": False, "status": "error", "error": str(e)},
                    "voice_realtime_status",
                )
            return

        if command == "voice_realtime_options":
            from opensquad.audio import realtime_manager as rtm

            try:
                result = rtm.set_session_options(
                    force_ask_agent=cmd_data.get("force_ask_agent"),
                )
                await self._send_event(result, "voice_realtime_status")
            except Exception as e:
                logger.error("[Adapter] voice_realtime_options failed: %s", e)
            return

        if command == "voice_audio_commit":
            from opensquad.audio import realtime_manager as rtm

            await rtm.commit_audio()
            return

        if command == "voice_mouthpiece_utterance":
            from opensquad.audio import realtime_manager as rtm

            try:
                audio = cmd_data.get("audio") or ""
                sample_rate = int(cmd_data.get("sample_rate") or 24000)
                result = await rtm.handle_mouthpiece_utterance(
                    audio,
                    sample_rate=sample_rate,
                )
                if isinstance(result, dict) and not result.get("ok", True):
                    await self._send_event(
                        {
                            "ok": False,
                            "status": "error",
                            "error": result.get("error") or "mouthpiece utterance failed",
                            "mode": "mouthpiece",
                        },
                        "voice_realtime_status",
                    )
            except Exception as e:
                logger.error("[Adapter] voice_mouthpiece_utterance failed: %s", e)
                await self._send_event(
                    {"ok": False, "status": "error", "error": str(e), "mode": "mouthpiece"},
                    "voice_realtime_status",
                )
            return

        logger.warning(f"[Adapter] Unknown command: {command}, falling back to base handler")
        await super()._handle_command(data)

    async def _try_wake_agent(self, reason: str = "urgent-command", *, inject_sentinel: bool = False):
        """Wake the agent if it is sleeping.

        Args:
            reason: Passed to sleep_controller.wake_up (appears in wake tool result).
            inject_sentinel: When True, also push a synthetic ``[wakeup-urgent-command]``
                into input_hub so the idle loop notices the wake even without a real
                user message.  MUST be False for normal chat — the real user payload
                is pushed right after wake, and the sentinel would otherwise be
                treated as the user's message (or drown it out).
        """
        try:
            from opensquad.sleep_controller import sleep_controller
            from opensquad.state_manager import state_manager

            ai_state = await state_manager.get_state()
            if ai_state == "sleeping":
                sleep_controller.wake_up(reason)
                if inject_sentinel:
                    input_hub.push("[wakeup-urgent-command]", source="wake")
                logger.info(f"[Adapter] Woke agent from sleep for {reason} (sentinel={inject_sentinel})")
        except Exception as e:
            logger.warning(f"[Adapter] Wake-up for {reason} failed: {e}")

    async def _handle_chat(self, data: dict):
        """Handle user messages from the Gateway."""
        user_id = data.get("user_id")
        content = data.get("content")
        images = data.get("images", [])
        attachments = data.get("attachments", [])
        channel = data.get("channel", "web")
        sender_name = data.get("sender_name", "")
        chat_name = data.get("chat_name", "")
        source_chat_id = data.get("source_chat_id", "")
        client_id = str(data.get("client_id") or data.get("message_id") or "").strip()
        session_id = str(data.get("session_id") or "").strip()
        # Pane picker card — applied on the turn even if switch_model was lost.
        model_card = str(data.get("model_card") or data.get("card") or "").strip()
        self.current_user_id = user_id

        # Scheduled-task initial fire (user_id="scheduled-task:{exec_id}", no
        # session_id): spawn a brand-new parallel session that does NOT steal
        # the user's focused web-chat pane. Each execution gets its own empty
        # timeline ("真分屏"). Follow-ups already carry session_id and reuse it.
        is_scheduled = (
            isinstance(user_id, str) and user_id.startswith("scheduled-task:") and bool(user_id.split(":", 1)[1])
        )
        if is_scheduled and not session_id:
            try:
                from opensquad.session_manager import get_session_manager

                title = str(data.get("session_title") or "").strip()
                if not title and isinstance(content, str):
                    # Content includes "[Scheduled Task: {name}]" (may follow
                    # <user_send_skill> tags) — recover the name for UI.
                    import re

                    m = re.search(r"\[Scheduled Task:\s*(.+?)\]", content)
                    if m:
                        title = m.group(1).strip()
                sid = get_session_manager().create_parallel_session(
                    title=title or None,
                    origin="scheduled_task",
                )
                session_id = sid
                # Force web channel so IngressPolicy keeps the given sid
                # (external would remap to primary and defeat per-exec isolation).
                channel = "web"
                # Bind BEFORE announcing so current_session carries
                # user_id=scheduled-task:{exec} and the UI can ignore focus steal.
                if session_id and user_id:
                    self._user_id_by_sid[session_id] = user_id
                # Scheduled tasks run unattended — always Build (no Plan approval gate).
                try:
                    from opensquad.agent_mode import MODE_BUILD
                    from opensquad.model_switch import apply_agent_mode

                    await apply_agent_mode(MODE_BUILD, session_id=session_id)
                except Exception as mode_e:
                    logger.debug("[Adapter] scheduled-task build mode set failed: %s", mode_e)
                logger.info(
                    "[Adapter] Scheduled-task spawn parallel session=%s title=%s exec=%s",
                    sid,
                    title or "-",
                    user_id,
                )
                # Announce immediately so the gateway can bind exec.session_id
                # before the turn even starts (UI: workflow pane loads empty
                # session instead of "尚未创建会话").
                if self.connected:
                    await self._send_event(
                        {"id": sid, "title": title or "Scheduled Task"},
                        "current_session",
                        sid=sid,
                    )
            except Exception as e:
                logger.error("[Adapter] Scheduled-task parallel session spawn failed: %s", e)

        # Route via IngressPolicy: external → primary; web keeps/falls back focused
        try:
            from opensquad.ingress_policy import classify, resolve_session_id

            session_id = resolve_session_id(
                source="gateway",
                channel=channel,
                session_id=session_id,
            )
            kind = classify("gateway", channel)
            if kind == "external":
                logger.info(
                    "[Adapter] External channel=%s → primary session %s",
                    channel,
                    session_id,
                )
            elif kind == "web" and not str(data.get("session_id") or "").strip() and not is_scheduled:
                logger.warning(
                    "[Adapter] Web chat missing session_id; falling back to focused=%s",
                    session_id,
                )
        except Exception as e:
            logger.debug("[Adapter] session routing skipped: %s", e)

        # Bind this session's outbound events to the requesting user so parallel
        # turns do not steal routing when current_user_id is overwritten.
        if session_id and user_id:
            self._user_id_by_sid[session_id] = user_id

        logger.info(
            f"[Adapter] Received from Gateway ({user_id}, channel={channel}, sid={session_id}): {content}"
            + (f" images={len(images)}" if images else "")
            + (f" attachments={len(attachments)}" if attachments else "")
        )

        # Fresh user chat must never be blocked by a sticky Stop latch.
        # New Chat always sends stop_task first; if __STOP__/__NEW_SESSION__
        # ordering leaves _stop_requested=True, the parallel dispatcher used
        # to drop every subsequent user message (UI: send OK, zero reaction).
        try:
            if session_id:
                input_hub.clear_session_stop(session_id)
            input_hub.clear_stop_request()
        except Exception:
            logger.debug("[Adapter] clear stop latch before chat skipped", exc_info=True)

        # Wake only — do NOT inject a fake wakeup message; the real payload follows.
        await self._try_wake_agent("web-message", inject_sentinel=False)

        logger.info(
            "[Adapter] Push chat → input_hub content_len=%s channel=%s sid=%s stop=%s",
            len(content or ""),
            channel,
            session_id or "-",
            input_hub.is_stop_requested(),
        )
        if model_card:
            logger.warning(
                "[Adapter] chat carries model_card=%s sid=%s",
                model_card,
                session_id or "-",
            )
        from opensquad.ingress_policy import push_ingress

        push_ingress(
            content,
            source="gateway",
            images=images if images else None,
            attachments=attachments if attachments else None,
            channel=channel,
            sender_name=sender_name,
            chat_name=chat_name,
            source_chat_id=source_chat_id,
            user_id=user_id or "",
            client_id=client_id,
            session_id=session_id or "",
            model_card=model_card,
        )

    async def on_runner_output(self, data):
        """When Runner finishes a reply (final text response; content should be a string)."""
        logger.info(f"[GatewayAdapter] on_runner_output called, connected={self.connected}, data={str(data)[:200]}")
        sid = self._extract_sid(data)
        # Flush any pending stream debounce so clients don't keep a truncated preview.
        await self._flush_stream_buffer_now(sid)
        if self.connected:
            content = self._unwrap(data)
            if content:
                logger.info(
                    f"[GatewayAdapter] Sending final response (user={self._user_id_by_sid.get(sid) or self.current_user_id or 'broadcast'}), content_len={len(str(content))}, content_preview={str(content)[:100]}"
                )
                await self._send_event(content, "message", sid=sid)
            else:
                logger.warning("[GatewayAdapter] on_runner_output called but content is empty")
        else:
            logger.warning("[GatewayAdapter] on_runner_output called but not connected, discarding response")

    async def on_runner_end_task(self, data):
        """Complex-task final report — distinct WS type so the UI can fold the process."""
        logger.info(f"[GatewayAdapter] on_runner_end_task called, connected={self.connected}, data={str(data)[:200]}")
        sid = self._extract_sid(data)
        await self._flush_stream_buffer_now(sid)
        if not self.connected:
            logger.warning("[GatewayAdapter] on_runner_end_task called but not connected, discarding response")
            return
        content = self._unwrap(data)
        if not content:
            logger.warning("[GatewayAdapter] on_runner_end_task called but content is empty")
            return
        await self._send_event(content, "to_user_end_task", sid=sid)

    async def on_runner_thought(self, data):
        """When Runner is thinking (content may be a {"text":...,"final":...} object)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "thought", sid=sid)

    async def on_runner_stream(self, data):
        """When Runner streams output -- uses 30ms debounce batching to reduce WS frame count."""
        if not self.connected:
            return
        sid = self._extract_sid(data) or ""
        content = self._unwrap(data)
        if not content:
            return

        buf = self._stream_buffers.setdefault(sid, [])
        # First frame goes out immediately (no 30ms debounce) to minimize TTFT;
        # subsequent chunks keep the debounce merge to limit WS frame count.
        pending_task = self._stream_flush_tasks.get(sid)
        first_chunk = len(buf) == 0 and (pending_task is None or pending_task.done())
        buf.append(content)
        if first_chunk:
            self._stream_flush_tasks[sid] = asyncio.create_task(self._emit_stream_buffer(sid))
            return
        # P2: enforce max_chunks limit — flush immediately if exceeded
        if len(buf) >= self._max_chunks:
            task = self._stream_flush_tasks.get(sid)
            if task is None or task.done():
                self._stream_flush_tasks[sid] = asyncio.create_task(self._flush_stream_buffer(sid))

        # Don't create a duplicate flush task if one is already pending for this sid
        task = self._stream_flush_tasks.get(sid)
        if task is not None and not task.done():
            return

        # Schedule a flush task after 30ms
        self._stream_flush_tasks[sid] = asyncio.create_task(self._flush_stream_buffer(sid))

    async def _flush_stream_buffer(self, sid: str = ""):
        """Wait 30ms then send all buffered chunks for *sid* merged into one frame."""
        try:
            await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            return
        await self._emit_stream_buffer(sid)

    async def _flush_stream_buffer_now(self, sid: str | None = None) -> None:
        """Immediately flush pending stream chunks (cancel debounce timer if any).

        If sid is given, flush only that session's buffer; otherwise flush all.
        """
        sids = [sid] if sid is not None else list(self._stream_flush_tasks.keys()) + list(self._stream_buffers.keys())
        # Deduplicate while preserving order
        seen: set[str] = set()
        ordered: list[str] = []
        for s in sids:
            key = s or ""
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        current = asyncio.current_task()
        for s in ordered:
            task = self._stream_flush_tasks.get(s)
            if task is not None and not task.done() and task is not current:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._stream_flush_tasks.pop(s, None)
            await self._emit_stream_buffer(s)

    async def _emit_stream_buffer(self, sid: str = "") -> None:
        """Send and clear whatever is currently in the stream debounce buffer for *sid*."""
        buf = self._stream_buffers.get(sid) or []
        if not buf:
            return
        chunks = buf[:]
        buf.clear()
        self._stream_buffers[sid] = []
        combined = "".join(c for c in chunks if isinstance(c, str))
        if combined and self.connected:
            await self._send_event(combined, "stream", sid=sid)
        for c in chunks:
            if not isinstance(c, str) and self.connected:
                await self._send_event(c, "stream", sid=sid)

    async def on_tool_call(self, data):
        """When a tool is called (content is a {"name":...,"args":...,"id":...} object)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "tool_call", sid=sid)

    async def on_tool_call_delta(self, data):
        """Incremental native-FC tool arguments (file write/edit streaming preview)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "tool_call_delta", sid=sid)

    async def on_tool_result(self, data):
        """When a tool result is returned (content is a {"id":...,"name":...,"result":...} object)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "tool_result", sid=sid)


async def start_gateway_adapter():
    config = AgentConfig(
        gateway_url=syscfg.gateway_register_url(),
        agent_id="legacy-main",  # This ID must match what the frontend accesses
        agent_name="Primary Controller Agent",
        agent_type="general",
        capabilities=["core", "tools", "legacy"],
        description="Main.py core engine",
        node_id=syscfg.node_id(),
        node_label=syscfg.node_label(),
    )

    adapter = GatewayAdapter(config)
    # Run in the background
    asyncio.create_task(adapter.start())
