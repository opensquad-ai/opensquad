"""
Event-driven runtime model switching.

This module is the single coordination point for switching the active model of
a running agent *without a restart*.  It unifies the two pre-existing inline
hot-reload implementations (``AgentRunner``'s config.json mtime poll path and
``_runner/_state_machine.py``'s ``_apply_model_reload``) behind one reusable
function, and adds an event-driven entry point so the web UI can trigger a
switch instantly instead of waiting for the next poll.

Trigger flow::

    Web UI dropdown  ──WS command "switch_model"──▶  gateway_adapter._handle_command
                                                          │ bus.emit("model.switch.requested", {card})
                                                          ▼
                                                   _on_switch_requested (subscribed at boot)
                                                          │ await switch_to_card(card)
                                                          ▼
                                                   1. read model_cards/<card>.json (local, has api_key)
                                                   2. apply_model_reload(runner, new_cfg)   # main agent
                                                   3. delegate.set_chat_api_cfg(new_cfg)     # sub-agents (soft switch)
                                                   4. persist config.json + refresh mtime     # stop the poll from re-firing
                                                   5. bus.emit("info", {event:"model_card_switched", ...})

Security: only the *card name* travels over the WebSocket.  The api_key is
read from the local ``model_cards/<card>.json`` inside the agent process and
never leaves it -- consistent with the secret_guard.py / .gitignore defenses.
"""

import json
import logging
import os

from .events import bus
from .system_config import syscfg

logger = logging.getLogger(__name__)

# Event name for the (untyped) bus channel.  Matches the codebase convention of
# bare-string event names with dict payloads for runtime control events.
EVENT_MODEL_SWITCH_REQUESTED = "model.switch.requested"
EVENT_REASONING_EFFORT_REQUESTED = "model.reasoning_effort.requested"
EVENT_AGENT_MODE_REQUESTED = "agent.mode.requested"

# Module-level handles, injected once at boot via init().  The AgentRunner
# instance is reused across runner-task restarts (see agent_boot_phases.
# await_runner_shutdown), so a single init() stays valid for the process
# lifetime.
_runner = None
_config_path: str = ""
_initialized: bool = False


# ---------------------------------------------------------------------------
# Boot wiring
# ---------------------------------------------------------------------------
def init(runner, config_path: str = "") -> None:
    """Register the coordinator with the event bus.

    Idempotent: safe to call multiple times (only subscribes once).  Stores a
    reference to the live ``AgentRunner`` and its ``config.json`` path so later
    switches can both apply the reload and persist the change back to disk.
    """
    global _runner, _config_path, _initialized
    _runner = runner
    if config_path:
        _config_path = config_path
    if not _initialized:
        bus.subscribe(EVENT_MODEL_SWITCH_REQUESTED, _on_switch_requested)
        bus.subscribe(EVENT_REASONING_EFFORT_REQUESTED, _on_reasoning_effort_requested)
        bus.subscribe(EVENT_AGENT_MODE_REQUESTED, _on_agent_mode_requested)
        _initialized = True
        logger.info(
            "[model_switch] Coordinator registered (config_path=%s).",
            _config_path or "<none>",
        )


# ---------------------------------------------------------------------------
# Card resolution
# ---------------------------------------------------------------------------
def _resolve_card(card_name: str) -> dict:
    """Load a model card JSON from the builtin resources directory.

    Returns the card dict with ``_card`` stamped in (so it round-trips through
    config.json the same way the admin assign endpoint records it).  Raises
    ``FileNotFoundError`` / ``ValueError`` on missing/invalid cards -- callers
    catch and report.
    """
    if not card_name or not isinstance(card_name, str):
        raise ValueError("card name is required")
    # Defensive: card names map 1:1 to filenames; reject path separators to
    # prevent traversal even though builtin_resources_dir is read-only.
    safe = os.path.basename(card_name)
    if safe != card_name:
        raise ValueError(f"invalid card name: {card_name!r}")
    card_path = syscfg.builtin_resources_dir("model_cards", f"{safe}.json")
    if not os.path.isfile(card_path):
        raise FileNotFoundError(f"model card not found: {safe} ({card_path})")
    with open(card_path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("model_name"):
        raise ValueError(f"model card {safe!r} has no model_name")
    cfg["_card"] = safe
    return cfg


# Content part types that only multimodal models accept. When switching to a
# text-only model, any such part left in conversation history makes the next
# request 400 (e.g. DeepSeek rejecting `image_url`). We downgrade those
# messages to text-only, preserving role/order and any text parts.
_MULTIMODAL_PART_TYPES = {"image_url", "input_audio", "input_video", "file", "image"}


def _strip_unsupported_multimodal(req: list, new_model: dict) -> bool:
    """Downgrade conversation history to text-only when the new model lacks
    multimodal support.

    Returns True if any message was modified.  No-op when the new model is
    itself multimodal (is_image / is_audio_model / is_video set) -- in that
    case the existing image/audio parts are still valid.
    """
    if not req:
        return False
    supports_mm = bool(new_model.get("is_image") or new_model.get("is_audio_model") or new_model.get("is_video"))
    if supports_mm:
        return False
    changed = False
    for m in req:
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list):
            continue
        text_parts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
        if len(text_parts) != len(content):
            # There were non-text parts. Keep text parts (or a placeholder if
            # the message had none, so role/turn structure stays intact).
            if text_parts:
                m["content"] = text_parts
            else:
                m["content"] = "[multimodal content removed]"
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Core reload logic (shared by event path + config.json poll path)
# ---------------------------------------------------------------------------
async def apply_model_reload(runner, new_model: dict) -> None:
    """Apply a model config change to a runner's chat_api in place.

    Same provider  -> ``await chat_api.reload_model(new_model)`` (closes the
    old httpx pool, rebuilds the client, preserves conversation history).
    Cross provider -> destroy + recreate the ChatAPI instance and migrate
    state (req / template / sid+user providers / history paths / token
    counters) onto the new instance, mirroring the original runner.py logic.

    Updates ``runner._model_config`` and ``runner._is_img_mode`` regardless of
    branch so subsequent polls/events see the new baseline.
    """
    from .agents_boot import create_chat_api_from_config, resolve_provider

    old_model = runner._model_config
    runner._model_config = new_model
    runner._is_img_mode = new_model.get("is_image", False)

    new_provider = resolve_provider(new_model)
    old_provider = resolve_provider(old_model) if old_model else new_provider
    chat_api = runner.chat_api

    if new_provider == old_provider and hasattr(chat_api, "reload_model"):
        await chat_api.reload_model(new_model)
        chat_api.is_img_model = new_model.get("is_image", False)
        chat_api.is_audio_model = new_model.get("is_audio_model", False)
        chat_api.is_video_model = new_model.get("is_video", False)
        logger.info(
            "[model_switch] Model hot-reloaded in-place: %s",
            new_model.get("model_name"),
        )
    else:
        # Cross-provider: build a fresh API instance and migrate live state.
        logger.info(
            "[model_switch] Provider changed (%s -> %s), creating new API instance...",
            old_provider,
            new_provider,
        )
        new_api = create_chat_api_from_config(
            new_model,
            chat_api.get_system_prompt(),
            chat_api.stream_parser,
        )
        new_api.set_template(chat_api.get_template())
        new_api.req = [dict(m) for m in chat_api.req]
        new_api.prompt_message = new_api.req[0] if new_api.req else None
        new_api._sid_provider = getattr(chat_api, "_sid_provider", None)
        new_api._user_id_provider = getattr(chat_api, "_user_id_provider", None)
        new_api.history_dir = getattr(chat_api, "history_dir", "")
        new_api.history_file = getattr(chat_api, "history_file", "")
        new_api.output_media_dir = getattr(chat_api, "output_media_dir", "")
        new_api._prev_reasoning_content = getattr(chat_api, "_prev_reasoning_content", None)
        new_api.total_input_tokens = getattr(chat_api, "total_input_tokens", 0)
        new_api.total_output_tokens = getattr(chat_api, "total_output_tokens", 0)
        new_api.total_requests = getattr(chat_api, "total_requests", 0)
        new_api.total_cache_read_tokens = getattr(chat_api, "total_cache_read_tokens", 0)
        chat_api = new_api
        runner.chat_api = new_api
        logger.info(
            "[model_switch] Model API replaced: %s",
            new_model.get("model_name"),
        )

    # Switching to a text-only model: downgrade any multimodal content left in
    # conversation history, otherwise the next request 400s (e.g. DeepSeek
    # rejecting `image_url`). Reload preserves self.req by design, so we clean
    # it here instead of dropping history.
    if _strip_unsupported_multimodal(chat_api.req, new_model):
        logger.info("[model_switch] Stripped unsupported multimodal content from history (target model is text-only).")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _persist_config(config_path: str, new_model: dict) -> None:
    """Write the new model block back to config.json and refresh the mtime cache.

    Without this, the runner's 5s mtime poll would re-read the file, see the
    model block changed, and fire a second (redundant) reload.  We update
    ``runner._config_mtime`` to the new file mtime so the poll treats it as
    already-applied.
    """
    if not config_path or not os.path.isfile(config_path):
        return
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["model"] = new_model
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if _runner is not None:
            _runner._config_mtime = os.path.getmtime(config_path)
        logger.info("[model_switch] config.json persisted: %s", config_path)
    except Exception as e:  # persistence is best-effort; the live reload already happened
        logger.warning("[model_switch] Failed to persist config.json: %s", e)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def switch_to_card(card_name: str) -> dict:
    """Switch the running agent to the named model card.

    Returns ``{"ok": True, "card", "model"}`` on success or
    ``{"ok": False, "error"}`` on failure.  Safe to call directly from Python
    (e.g. for testing) or via the event-bus handler.
    """
    if _runner is None:
        return {"ok": False, "error": "model_switch not initialised (no runner)"}
    try:
        new_cfg = _resolve_card(card_name)
    except (FileNotFoundError, ValueError) as e:
        logger.warning("[model_switch] %s", e)
        return {"ok": False, "error": str(e)}

    # Preserve session reasoning_effort across card switches when the card
    # does not define its own default.
    try:
        chat_api = getattr(_runner, "chat_api", None) if _runner else None
        current_effort = getattr(chat_api, "reasoning_effort", None) if chat_api else None
        if current_effort and "reasoning_effort" not in new_cfg:
            new_cfg["reasoning_effort"] = current_effort
        elif _config_path and os.path.isfile(_config_path) and "reasoning_effort" not in new_cfg:
            with open(_config_path, encoding="utf-8") as f:
                prev = json.load(f)
            prev_effort = (prev.get("model") or {}).get("reasoning_effort")
            if prev_effort:
                new_cfg["reasoning_effort"] = prev_effort
    except Exception:
        pass

    model_name = new_cfg.get("model_name", "?")
    # api_protocol: API 协议类型 (openai / openai_compat / claude / google)
    api_protocol = new_cfg.get("api_protocol", "?")

    try:
        await apply_model_reload(_runner, new_cfg)
    except Exception as e:
        logger.warning("[model_switch] Model reload failed: %s", e)
        return {"ok": False, "error": f"reload failed: {e}"}

    # Sub-agent soft switch: new delegations pick up the new cfg; running
    # sub-agents keep their own instance and finish with the old model.
    try:
        from .tools.delegate import set_chat_api_cfg

        set_chat_api_cfg(new_cfg)
    except Exception as e:
        logger.warning("[model_switch] Delegate cfg update failed: %s", e)

    _persist_config(_config_path, new_cfg)

    # Announce to the UI.  The frontend already renders info events with
    # event=="model_card_switched" (AIChatPage.tsx), so this is the feedback
    # channel that confirms the switch and updates the header label.
    try:
        await bus.emit_async(
            "info",
            {
                "event": "model_card_switched",
                "card": card_name,
                "model": model_name,
                "api_protocol": api_protocol,
                "text": f"Model switched to {model_name}",
            },
        )
    except Exception as e:
        logger.warning("[model_switch] info emit failed: %s", e)

    logger.info(
        "[model_switch] Switched to card=%s model=%s api_protocol=%s",
        card_name,
        model_name,
        api_protocol,
    )
    return {"ok": True, "card": card_name, "model": model_name, "api_protocol": api_protocol}


# ---------------------------------------------------------------------------
# Reasoning effort (thinking depth) — Cursor-style low/medium/high
# ---------------------------------------------------------------------------
async def apply_reasoning_effort(effort: str) -> dict:
    """Update live chat_api reasoning_effort and persist to config.json."""
    from opensquad.reasoning_effort import effort_to_claude_budget, normalize_effort

    if _runner is None:
        return {"ok": False, "error": "model_switch not initialised (no runner)"}

    effort_n = normalize_effort(effort)
    chat_api = getattr(_runner, "chat_api", None)
    if chat_api is None:
        return {"ok": False, "error": "no chat_api"}

    try:
        chat_api.reasoning_effort = effort_n
        if getattr(chat_api, "is_think", False) and hasattr(chat_api, "thinking_budget_tokens"):
            chat_api.thinking_budget_tokens = effort_to_claude_budget(effort_n)
    except Exception as e:
        logger.warning("[model_switch] Failed to apply reasoning_effort on chat_api: %s", e)
        return {"ok": False, "error": str(e)}

    # Soft-update delegate cfg so new sub-agents inherit the effort
    try:
        from .tools.delegate import get_chat_api_cfg, set_chat_api_cfg

        cfg = dict(get_chat_api_cfg() or {})
        if cfg:
            cfg["reasoning_effort"] = effort_n
            if cfg.get("is_think"):
                cfg["thinking_budget_tokens"] = effort_to_claude_budget(effort_n)
            set_chat_api_cfg(cfg)
    except Exception:
        pass

    # Persist into config.json model section without replacing the whole card
    if _config_path and os.path.isfile(_config_path):
        try:
            with open(_config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            model = cfg.get("model") or {}
            if not isinstance(model, dict):
                model = {}
            model["reasoning_effort"] = effort_n
            if model.get("is_think"):
                model["thinking_budget_tokens"] = effort_to_claude_budget(effort_n)
            cfg["model"] = model
            with open(_config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if _runner is not None:
                _runner._config_mtime = os.path.getmtime(_config_path)
        except Exception as e:
            logger.warning("[model_switch] Failed to persist reasoning_effort: %s", e)

    try:
        await bus.emit_async(
            "info",
            {
                "event": "reasoning_effort_changed",
                "effort": effort_n,
                "text": f"Reasoning effort set to {effort_n}",
            },
        )
    except Exception as e:
        logger.warning("[model_switch] reasoning_effort info emit failed: %s", e)

    logger.info("[model_switch] reasoning_effort=%s", effort_n)
    return {"ok": True, "effort": effort_n}


async def _on_reasoning_effort_requested(data: dict) -> None:
    if not isinstance(data, dict):
        return
    effort = data.get("effort") or data.get("reasoning_effort")
    if not effort:
        logger.warning("[model_switch] reasoning_effort requested with no effort: %s", data)
        return
    await apply_reasoning_effort(str(effort))


# ---------------------------------------------------------------------------
# Agent Plan / Build mode
# ---------------------------------------------------------------------------
async def _nudge_agent_after_mode_decision(message: str) -> None:
    """Wake a sleeping agent (if needed) and push a resume cue into input_hub.

    Mode-switch approvals end the agent's turn while it waits for the user.
    Without this nudge, Approve/Deny only updates mode state and the agent
    never starts a follow-up turn.
    """
    try:
        from opensquad.input_hub import input_hub

        try:
            from opensquad.sleep_controller import sleep_controller
            from opensquad.state_manager import state_manager

            ai_state = await state_manager.get_state()
            if ai_state == "sleeping":
                sleep_controller.wake_up("mode-switch-decision")
        except Exception as wake_err:
            logger.debug("[model_switch] wake after mode decision skipped: %s", wake_err)

        input_hub.push(message, source="system")
        logger.info("[model_switch] Nudged agent after mode decision")
    except Exception as e:
        logger.warning("[model_switch] Failed to nudge agent after mode decision: %s", e)


async def apply_agent_mode(mode: str, *, approved_request_id: str | None = None) -> dict:
    """Set Plan/Build mode on the running agent and persist to config.json."""
    from opensquad.agent_mode import normalize_mode, set_current_mode

    if _runner is None:
        return {"ok": False, "error": "model_switch not initialised (no runner)"}

    mode_n = normalize_mode(mode)
    try:
        _runner.agent_mode = mode_n
        set_current_mode(mode_n)
        # Clear tool schema cache so next turn regenerates (filter still applied in ContextBuilder)
        tr = getattr(_runner, "tool_registry", None)
        if tr is not None and hasattr(tr, "_openai_tools_cache"):
            tr._openai_tools_cache.clear()
    except Exception as e:
        logger.warning("[model_switch] Failed to apply agent_mode: %s", e)
        return {"ok": False, "error": str(e)}

    if _config_path and os.path.isfile(_config_path):
        try:
            with open(_config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["agent_mode"] = mode_n
            with open(_config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            _runner._config_mtime = os.path.getmtime(_config_path)
        except Exception as e:
            logger.warning("[model_switch] Failed to persist agent_mode: %s", e)

    payload = {
        "event": "agent_mode_changed",
        "mode": mode_n,
        "text": f"Agent mode set to {mode_n}",
    }
    if approved_request_id:
        payload["approved_request_id"] = approved_request_id
        payload["event"] = "mode_switch_resolved"
        payload["status"] = "approved"
        payload["id"] = approved_request_id
        payload["to_mode"] = mode_n

    try:
        await bus.emit_async("info", payload)
        if approved_request_id:
            # Also emit a plain mode change for UI state sync
            await bus.emit_async(
                "info",
                {
                    "event": "agent_mode_changed",
                    "mode": mode_n,
                    "text": f"Agent mode set to {mode_n}",
                },
            )
    except Exception as e:
        logger.warning("[model_switch] agent_mode info emit failed: %s", e)

    # Only auto-continue when the user approved a pending request_switch card.
    # Manual ModePicker changes (no request id) must not invent a new turn.
    if approved_request_id:
        await _nudge_agent_after_mode_decision(
            f"[System] Mode switch approved. You are now in {mode_n} mode. "
            "Continue the task you were waiting on. Do not ask for approval again."
        )

    logger.info("[model_switch] agent_mode=%s", mode_n)
    return {"ok": True, "mode": mode_n}


async def deny_mode_switch(request_id: str, reason: str = "") -> dict:
    from opensquad.agent_mode import get_current_mode

    try:
        await bus.emit_async(
            "info",
            {
                "event": "mode_switch_resolved",
                "id": request_id,
                "status": "denied",
                "reason": reason or "User denied",
                "text": "Mode switch denied",
            },
        )
    except Exception as e:
        logger.warning("[model_switch] deny emit failed: %s", e)
        return {"ok": False, "error": str(e)}

    if request_id:
        current = get_current_mode()
        reason_text = (reason or "").strip() or "User denied"
        await _nudge_agent_after_mode_decision(
            f"[System] Mode switch denied ({reason_text}). "
            f"You remain in {current} mode. Adapt your plan without assuming the switch happened."
        )
    return {"ok": True}


async def _on_agent_mode_requested(data: dict) -> None:
    if not isinstance(data, dict):
        return
    if data.get("action") == "deny":
        await deny_mode_switch(str(data.get("id") or ""), str(data.get("reason") or ""))
        return
    mode = data.get("mode") or data.get("agent_mode")
    if not mode:
        logger.warning("[model_switch] agent_mode requested with no mode: %s", data)
        return
    await apply_agent_mode(str(mode), approved_request_id=data.get("id") or data.get("approved_request_id"))


# ---------------------------------------------------------------------------
# Event-bus handler
# ---------------------------------------------------------------------------
async def _on_switch_requested(data: dict) -> None:
    """bus subscriber for model.switch.requested."""
    if not isinstance(data, dict):
        return
    card = data.get("card") or data.get("card_name")
    if not card:
        logger.warning("[model_switch] switch requested with no card: %s", data)
        return
    await switch_to_card(card)
