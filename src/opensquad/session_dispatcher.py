"""Parallel session dispatcher — schedules concurrent turns on AgentRunner."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from opensquad.input_hub import get_input_hub
from opensquad.session_parallel import (
    MAX_PARALLEL_TURNS,
    ParallelTurnScheduler,
    get_tool_write_lock,
)

if TYPE_CHECKING:
    from opensquad.runner import AgentRunner

logger = logging.getLogger(__name__)


def _clone_chat_api(base) -> Any:
    """Create an isolated ChatAPI (or ClaudeAPI) sharing model config with *base*.

    Prefer a config dict rebuilt from *live* credentials after hot-reload —
    ``reload_model`` historically updated api_key/base_url/model without
    rewriting ``base.config``, which caused clones to keep calling the old
    provider (e.g. OpenCode after switching away).
    """
    cfg = getattr(base, "config", None)
    if isinstance(cfg, dict):
        cfg = dict(cfg)
        for cfg_key, attr in (
            ("api_key", "api_key"),
            ("base_url", "base_url"),
            ("model_name", "model"),
            ("temperature", "temperature"),
            ("token_max", "token_max"),
        ):
            val = getattr(base, attr, None)
            if val is not None and val != "":
                cfg[cfg_key] = val
        card = getattr(base, "model_config", None)
        if isinstance(card, dict) and card.get("_card"):
            cfg["_card"] = card["_card"]
    cls = type(base)
    try:
        if cfg is not None:
            api = cls(config=cfg)
        else:
            api = cls(
                api_key=getattr(base, "api_key", None),
                model=getattr(base, "model", None),
                base_url=getattr(base, "base_url", None),
                prompt=getattr(base, "prompt", None),
            )
    except TypeError:
        api = cls()
        for attr in (
            "api_key",
            "model",
            "base_url",
            "prompt",
            "timeout",
            "token_max",
            "temperature",
            "config",
        ):
            if hasattr(base, attr):
                try:
                    setattr(api, attr, getattr(base, attr))
                except Exception:
                    pass
    return api


async def run_parallel_dispatcher(runner: AgentRunner, initial_query: str | None = None, **kwargs):
    """Replace AgentRunner.run()'s outer idle loop with a multi-session dispatcher."""
    from opensquad.session_manager import get_session_manager

    hub = get_input_hub()
    sm = get_session_manager()
    scheduler = ParallelTurnScheduler(max_parallel=MAX_PARALLEL_TURNS)
    runner._parallel_scheduler = scheduler
    runner._session_chat_apis = getattr(runner, "_session_chat_apis", {})
    runner._tool_write_lock = get_tool_write_lock()

    runner._agent_ready = True
    try:
        from opensquad.events import bus

        bus.emit("agent_ready", {"agent_id": getattr(runner, "_agent_id", "")})
    except Exception:
        pass
    if hasattr(runner, "_replay_pending"):
        runner._replay_pending()

    # Seed first message on focused session
    if initial_query:
        sid = sm.get_focused_session_id()
        hub.push(initial_query, source="cli", session_id=sid or "")

    logger.warning(
        "[Dispatcher] Parallel session dispatcher started (max_parallel=%s)",
        MAX_PARALLEL_TURNS,
    )

    while True:
        scheduler.reap()
        # Emit busy_sessions for UI
        try:
            await runner._emit_busy_sessions(scheduler.busy_sessions)
        except Exception:
            pass

        got = await hub.wait_any(timeout=5.0)
        if got is None:
            # Idle timeout: hot-reload / health checks on runner if available
            if hasattr(runner, "_dispatcher_idle_tick"):
                await runner._dispatcher_idle_tick()
            continue

        sid, item = got
        content = str(item.get("content") or "")
        logger.info(
            "[Dispatcher] popped sid=%s content=%r busy=%s stop=%s",
            sid,
            (content[:120] + ("…" if len(content) > 120 else "")),
            bool(sid and scheduler.is_session_busy(str(sid))),
            bool(sid and hub.is_session_stop_requested(str(sid))),
        )

        # Agent-level system commands — always handle globally (even if a sid
        # was attached). Otherwise __NEW_SESSION__ can be mis-routed / dropped
        # when a prior stop latch is still set on the focused session.
        _AGENT_LEVEL = (
            "__NEW_SESSION__",
            "__STOP__",
            "__REQUEST_TOKEN_STATS__",
            "__COMPRESS_CONTEXT__",
        )
        if content in _AGENT_LEVEL or (
            sid is None
            and content.startswith("__")
            and not content.startswith("__SWITCH_AND_REPLY__:")
            and not content.startswith("__LOAD_SESSION__:")
            and not content.startswith("__WITHDRAW_TURN__:")
        ):
            await runner._handle_agent_level_command(item)
            continue

        # Resolve session id
        if not sid:
            sid = str(item.get("session_id") or "").strip() or sm.get_focused_session_id()

        # SWITCH_AND_REPLY legacy form on a session urgent queue
        if content.startswith("__SWITCH_AND_REPLY__:"):
            parts = content.split(":", 2)
            if len(parts) >= 2 and parts[1]:
                sid = parts[1]
            reply = parts[2] if len(parts) >= 3 else ""
            item = {**item, "content": reply, "session_id": sid}
            # Empty reply = focus/load only — never start a blank LLM turn.
            # (Serial path in _input_handler returns early; parallel must match.)
            if not str(reply).strip():
                try:
                    if sid and sid != sm.get_current_session_id():
                        if hasattr(sm, "load_history_session"):
                            sm.load_history_session(sid)
                        else:
                            sm.ensure_session_loaded(sid)
                except Exception as exc:
                    logger.warning("[Dispatcher] empty SWITCH_AND_REPLY load failed: %s", exc)
                try:
                    from opensquad.events import bus

                    await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                    await bus.emit_async("session_list", sm.get_session_list())
                except Exception:
                    pass
                continue
            content = reply

        # STOP marker: cancel in-flight turn for this sid, then continue.
        # Do NOT drop subsequent user messages while a stop latch is set —
        # that permanently blacks out chat after Stop / New Chat (global latch
        # is sticky until clear_stop_request). A new user turn clears the latch
        # in _parallel_session_turn and must be allowed to start.
        if content == "__STOP__":
            scheduler.request_stop_session(sid)
            hub.clear_session_stop(sid)
            continue

        if hub.is_session_stop_requested(sid) and not str(content).startswith("__"):
            # Stale latch from a prior Stop — clear and process this message.
            hub.clear_session_stop(sid)
            try:
                hub.clear_stop_request()
            except Exception:
                pass

        # Same session already running → leave in queue (re-push) wait_any already popped
        if scheduler.is_session_busy(sid):
            # Re-queue at front of session inbox
            hub.push(
                item.get("content", ""),
                source=item.get("source", "gateway"),
                images=item.get("images"),
                attachments=item.get("attachments"),
                channel=item.get("channel", ""),
                sender_name=item.get("sender_name", ""),
                chat_name=item.get("chat_name", ""),
                source_chat_id=item.get("source_chat_id", ""),
                user_id=item.get("user_id", ""),
                client_id=item.get("client_id", ""),
                session_id=sid,
            )
            # Wait briefly for current turn to finish before retrying
            await asyncio.sleep(0.05)
            continue

        # Acquire parallel slot (returns False if sid busy or capacity timeout)
        ok = await scheduler.acquire_slot(sid)
        if not ok:
            logger.info("[Dispatcher] slot busy/full — requeue sid=%s", sid)
            hub.push(
                item.get("content", ""),
                source=item.get("source", "gateway"),
                session_id=sid,
                images=item.get("images"),
                attachments=item.get("attachments"),
                channel=item.get("channel", ""),
                client_id=item.get("client_id", ""),
                user_id=item.get("user_id", ""),
            )
            await asyncio.sleep(0.05)
            continue

        logger.info(
            "[Dispatcher] start turn sid=%s content_len=%s stop=%s",
            sid,
            len(content),
            hub.is_stop_requested(),
        )
        scheduler.start(sid, runner._parallel_session_turn(sid, item))
