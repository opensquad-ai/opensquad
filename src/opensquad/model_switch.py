"""
Runtime model switching + session-scoped card coordination.

Authority
---------
- Agent default: ``config.json`` / root ChatAPI. UI model picks (including
  pane/session-scoped switches) also promote to this default so refresh and
  restart keep the last manually selected card.
- Session override: ``session_model`` store + ``session_data["model_card"]``.
- Every parallel turn binds via ``session_model.bind_for_turn`` (chat payload
  preferred card wins).

Entry points
------------
- ``switch_to_card(card, session_id=?)`` — called directly from gateway_adapter
  (bus is notification-only fallback).
- ``bind_session_chat_api`` / ``session_model.bind_for_turn`` — turn start.

Security: only the card *name* travels over the WebSocket; api_key is read
from local ``model_cards/<card>.json`` inside the agent process.
"""

import asyncio
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
        logger.warning(
            "[model_switch] Coordinator registered (config_path=%s).",
            _config_path or "<none>",
        )
    else:
        logger.warning(
            "[model_switch] Coordinator refreshed runner (config_path=%s).",
            _config_path or "<none>",
        )


def expand_secret_value(value: str) -> str:
    """Expand ``${ENV_VAR}`` / ``${ENV_VAR:-default}`` placeholders in a secret.

    Model cards may reference credentials via environment variables instead of
    embedding raw keys (SEC-1).  A raw key is returned unchanged so existing
    deployments keep working; a placeholder with no matching env var yields an
    empty string (callers log a clear "not configured" error).
    """
    if not value or "${" not in value:
        return value

    def _repl(match: "re.Match[str]") -> str:
        name = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(name)
        if env_val:
            return env_val
        return default if default is not None else ""

    import re

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}", _repl, value)


def _resolve_card(card_name: str) -> dict:
    """Load a model card JSON from the workspace ``model_cards/`` directory.

    Model cards hold private credentials (api_key) and must live in the
    workspace — never under ``src/`` / builtin install trees.  Web UI
    create/list/save already use ``workspace_model_cards_dir``; runtime
    switches must resolve the same path.

    Returns the card dict with ``_card`` stamped in (so it round-trips through
    config.json the same way the admin assign endpoint records it).  Raises
    ``FileNotFoundError`` / ``ValueError`` on missing/invalid cards -- callers
    catch and report.
    """
    if not card_name or not isinstance(card_name, str):
        raise ValueError("card name is required")
    # Defensive: card names map 1:1 to filenames; reject path separators.
    safe = os.path.basename(card_name)
    if safe != card_name:
        raise ValueError(f"invalid card name: {card_name!r}")

    cards_dir = syscfg.workspace_model_cards_dir()
    card_path = os.path.join(cards_dir, f"{safe}.json")
    if not os.path.isfile(card_path):
        raise FileNotFoundError(f"model card not found: {safe} ({card_path})")
    with open(card_path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("model_name"):
        raise ValueError(f"model card {safe!r} has no model_name")
    # SEC-1: expand ${ENV_VAR} placeholders so model cards never need to embed raw keys.
    if cfg.get("api_key"):
        cfg["api_key"] = expand_secret_value(str(cfg["api_key"]))
    cfg["_card"] = safe
    return cfg


# Public alias used by session_model.bind_for_turn
resolve_card = _resolve_card


def is_ready() -> bool:
    """True when switch/bind may run (runner wired)."""
    return _ensure_runner() is not None


def _ensure_runner():
    """Return the live AgentRunner, recovering from late/missed init if needed."""
    global _runner, _config_path
    if _runner is not None:
        return _runner
    try:
        from opensquad.runner import _active_runner

        if _active_runner is not None:
            _runner = _active_runner
            if not _config_path:
                _config_path = str(getattr(_active_runner, "_config_path", "") or "")
            logger.warning("[model_switch] Recovered runner from _active_runner (late init)")
            return _runner
    except Exception as e:
        logger.warning("[model_switch] _active_runner recovery failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Card resolution helpers (session store lives in session_model)
# ---------------------------------------------------------------------------
def _preserve_effort(new_cfg: dict, chat_api) -> None:
    """Keep live reasoning_effort when the card omits it."""
    try:
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


async def bind_session_chat_api(runner, sid: str, *, preferred_card: str | None = None):
    """Delegate to :mod:`opensquad.session_model` (single bind authority)."""
    from opensquad.session_model import bind_for_turn

    return await bind_for_turn(runner, sid, preferred_card=preferred_card)


async def _emit_switch_failed(card_name: str, error: str, session_id: str | None = None) -> None:
    """Tell the web UI to clear the 'switching…' spinner on failure."""
    try:
        payload = {
            "event": "model_card_switch_failed",
            "card": card_name,
            "error": error,
            "text": f"Model switch failed: {error}",
        }
        sid = (session_id or "").strip()
        if sid:
            payload["session_id"] = sid
        await bus.emit_async("info", payload)
    except Exception as e:
        logger.warning("[model_switch] switch_failed emit failed: %s", e)


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
async def apply_model_reload(runner, new_model: dict, *, chat_api=None) -> None:
    """Apply a model config change to a runner's chat_api in place.

    Same provider  -> ``await chat_api.reload_model(new_model)`` (closes the
    old httpx pool, rebuilds the client, preserves conversation history).
    Cross provider -> destroy + recreate the ChatAPI instance and migrate
    state (req / template / sid+user providers / history paths / token
    counters) onto the new instance, mirroring the original runner.py logic.

    Updates ``runner._model_config`` and ``runner._is_img_mode`` regardless of
    branch so subsequent polls/events see the new baseline.
    When *chat_api* is provided (session-scoped switch), only that instance is
    reloaded and runner-level defaults are left unchanged.
    """
    from .agents_boot import create_chat_api_from_config, resolve_provider

    session_scoped = chat_api is not None
    if not session_scoped:
        old_model = runner._model_config
        runner._model_config = new_model
        runner._is_img_mode = new_model.get("is_image", False)
        chat_api = runner.chat_api
    else:
        old_model = getattr(chat_api, "model_config", None) or getattr(runner, "_model_config", {}) or {}

    new_provider = resolve_provider(new_model)
    old_provider = resolve_provider(old_model) if old_model else new_provider

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
        if session_scoped:
            # Caller must reassign into _session_chat_apis[sid]
            pass
        else:
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

    if session_scoped:
        return chat_api
    return None


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
async def switch_to_card(card_name: str, session_id: str | None = None) -> dict:
    """Switch agent default model, or one session's model when *session_id* is set.

    Returns ``{"ok": True, "card", "model"}`` on success or
    ``{"ok": False, "error"}`` on failure.  Safe to call directly from Python
    (e.g. for testing) or via the event-bus handler.
    """
    from opensquad.session_dispatcher import _clone_chat_api
    from opensquad.session_model import (
        get as session_get,
    )
    from opensquad.session_model import (
        session_api_map,
        set_session_card,
    )

    if not is_ready():
        err = "model_switch not ready (no runner)"
        await _emit_switch_failed(card_name, err, session_id=session_id)
        return {"ok": False, "error": err}
    try:
        new_cfg = _resolve_card(card_name)
    except (FileNotFoundError, ValueError) as e:
        logger.warning("[model_switch] %s", e)
        await _emit_switch_failed(card_name, str(e), session_id=session_id)
        return {"ok": False, "error": str(e)}

    sid = (session_id or "").strip()

    # Preserve session reasoning_effort across card switches when the card
    # does not define its own default.
    try:
        chat_api = None
        if sid:
            chat_api = session_api_map(_runner).get(sid)
        if chat_api is None:
            chat_api = getattr(_runner, "chat_api", None) if _runner else None
        _preserve_effort(new_cfg, chat_api)
    except Exception:
        pass

    model_name = new_cfg.get("model_name", "?")
    api_protocol = new_cfg.get("api_protocol", "?")

    try:
        if sid:
            # Session-scoped bind for this pane…
            apis = session_api_map(_runner)
            api = apis.get(sid)
            if api is None:
                root = getattr(_runner, "_root_chat_api", None) or getattr(_runner, "chat_api", None)
                api = _clone_chat_api(root)
                api._sid_provider = lambda s=sid: s
                api._user_id_provider = lambda: getattr(_runner, "_current_user_id", "")
                apis[sid] = api
            refreshed = await apply_model_reload(_runner, new_cfg, chat_api=api)
            if refreshed is not None:
                apis[sid] = refreshed
            set_session_card(_runner, sid, card_name)

            # …and also promote to agent default so refresh / restart / new chats
            # keep the last UI-selected model (not the previous config default).
            await apply_model_reload(_runner, new_cfg)
            try:
                from .tools.delegate import set_chat_api_cfg

                set_chat_api_cfg(new_cfg)
            except Exception as e:
                logger.warning("[model_switch] Delegate cfg update failed: %s", e)
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, _persist_config, _config_path, new_cfg)
            except Exception:
                _persist_config(_config_path, new_cfg)

            # Sessions without their own override follow the new default.
            for sess_id, other in list(apis.items()):
                if other is None or sess_id == sid:
                    continue
                if session_get(_runner, sess_id):
                    continue
                refreshed_other = await apply_model_reload(_runner, new_cfg, chat_api=other)
                if refreshed_other is not None:
                    apis[sess_id] = refreshed_other
        else:
            # Agent default: root ChatAPI + config.json.
            await apply_model_reload(_runner, new_cfg)
            try:
                from .tools.delegate import set_chat_api_cfg

                set_chat_api_cfg(new_cfg)
            except Exception as e:
                logger.warning("[model_switch] Delegate cfg update failed: %s", e)
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, _persist_config, _config_path, new_cfg)
            except Exception:
                _persist_config(_config_path, new_cfg)

            apis = session_api_map(_runner)
            for sess_id, api in list(apis.items()):
                if api is None:
                    continue
                if session_get(_runner, sess_id):
                    continue
                refreshed = await apply_model_reload(_runner, new_cfg, chat_api=api)
                if refreshed is not None:
                    apis[sess_id] = refreshed
    except Exception as e:
        logger.warning("[model_switch] Model reload failed: %s", e)
        err = f"reload failed: {e}"
        await _emit_switch_failed(card_name, err, session_id=sid or None)
        return {"ok": False, "error": err}

    try:
        info = {
            "event": "model_card_switched",
            "card": card_name,
            "model": model_name,
            "api_protocol": api_protocol,
            "text": f"Model switched to {model_name}",
        }
        if sid:
            info["session_id"] = sid
        await bus.emit_async("info", info)
    except Exception as e:
        logger.warning("[model_switch] info emit failed: %s", e)

    logger.warning(
        "[model_switch] Switched to card=%s model=%s base=%s sid=%s scope=%s",
        card_name,
        model_name,
        (new_cfg.get("base_url") or "")[:60],
        sid or "-",
        "session" if sid else "default",
    )
    return {
        "ok": True,
        "card": card_name,
        "model": model_name,
        "api_protocol": api_protocol,
        "session_id": sid or None,
        "scope": "session" if sid else "default",
    }


# ---------------------------------------------------------------------------
# Reasoning effort (thinking depth) — Cursor-style low/medium/high
# ---------------------------------------------------------------------------
async def apply_reasoning_effort(effort: str, session_id: str | None = None) -> dict:
    """Update live chat_api reasoning_effort and persist to config.json."""
    from opensquad.reasoning_effort import effort_to_claude_budget, normalize_effort
    from opensquad.session_model import session_api_map

    if _ensure_runner() is None:
        return {"ok": False, "error": "model_switch not initialised (no runner)"}

    effort_n = normalize_effort(effort)
    chat_api = getattr(_runner, "chat_api", None)
    if chat_api is None:
        return {"ok": False, "error": "no chat_api"}

    def _apply_to_api(api) -> None:
        if api is None:
            return
        api.reasoning_effort = effort_n
        if getattr(api, "is_think", False) and hasattr(api, "thinking_budget_tokens"):
            api.thinking_budget_tokens = effort_to_claude_budget(effort_n)

    try:
        _apply_to_api(chat_api)
        # Keep session-scoped clones in sync (composer is usually session-scoped).
        sid = (session_id or "").strip()
        apis = session_api_map(_runner)
        if sid and sid in apis:
            _apply_to_api(apis.get(sid))
        else:
            for api in list(apis.values()):
                _apply_to_api(api)
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
        info = {
            "event": "reasoning_effort_changed",
            "effort": effort_n,
            "text": f"Reasoning effort set to {effort_n}",
        }
        sid = (session_id or "").strip()
        if sid:
            info["session_id"] = sid
        await bus.emit_async("info", info)
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
    await apply_reasoning_effort(
        str(effort),
        session_id=str(data.get("session_id") or "").strip() or None,
    )


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


async def apply_agent_mode(
    mode: str,
    *,
    approved_request_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Set Plan/Build mode — optionally scoped to one session for parallel panes."""
    from opensquad.agent_mode import normalize_mode, set_current_mode, set_session_mode
    from opensquad.session_parallel import get_turn_local

    if _ensure_runner() is None:
        return {"ok": False, "error": "model_switch not initialised (no runner)"}

    mode_n = normalize_mode(mode)
    sid = (session_id or "").strip()
    try:
        if sid:
            set_session_mode(sid, mode_n)
            modes = getattr(_runner, "_session_agent_modes", None)
            if not isinstance(modes, dict):
                modes = {}
                _runner._session_agent_modes = modes
            modes[sid] = mode_n
            tl = get_turn_local()
            if tl is not None and tl.sid == sid:
                tl.agent_mode = mode_n
            # Do not overwrite global agent_mode / config.json — other panes stay independent.
        else:
            _runner.agent_mode = mode_n
            set_current_mode(mode_n)
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
        # Always clear tool cache so next turn for any session regenerates filters
        tr = getattr(_runner, "tool_registry", None)
        if tr is not None and hasattr(tr, "_openai_tools_cache"):
            tr._openai_tools_cache.clear()
    except Exception as e:
        logger.warning("[model_switch] Failed to apply agent_mode: %s", e)
        return {"ok": False, "error": str(e)}

    payload = {
        "event": "agent_mode_changed",
        "mode": mode_n,
        "text": f"Agent mode set to {mode_n}",
    }
    if sid:
        payload["session_id"] = sid
    if approved_request_id:
        payload["approved_request_id"] = approved_request_id
        payload["event"] = "mode_switch_resolved"
        payload["status"] = "approved"
        payload["id"] = approved_request_id
        payload["to_mode"] = mode_n

    try:
        await bus.emit_async("info", payload)
        if approved_request_id:
            plain = {
                "event": "agent_mode_changed",
                "mode": mode_n,
                "text": f"Agent mode set to {mode_n}",
            }
            if sid:
                plain["session_id"] = sid
            await bus.emit_async("info", plain)
    except Exception as e:
        logger.warning("[model_switch] agent_mode info emit failed: %s", e)

    if approved_request_id:
        await _nudge_agent_after_mode_decision(
            f"[System] Mode switch approved. You are now in {mode_n} mode. "
            "Continue the task you were waiting on. Do not ask for approval again."
        )

    logger.info("[model_switch] agent_mode=%s sid=%s", mode_n, sid or "-")
    return {"ok": True, "mode": mode_n, "session_id": sid or None}


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


async def resolve_proposed_options(
    request_id: str,
    *,
    chosen_option_id: str = "",
    chosen_option_ids: list[str] | None = None,
    custom_answer: str = "",
    ignored: bool = False,
) -> dict:
    """Resolve a pending propose_options card and nudge the agent to continue."""
    ids = [str(x).strip() for x in (chosen_option_ids or []) if str(x).strip()]
    if not ids and chosen_option_id:
        # Support comma-separated multi ids from older callers.
        ids = [p.strip() for p in str(chosen_option_id).split(",") if p.strip()]
    primary = ids[0] if ids else ""
    status = "ignored" if ignored else ("custom" if custom_answer else "chosen")
    try:
        await bus.emit_async(
            "info",
            {
                "event": "propose_options_resolved",
                "id": request_id,
                "status": status,
                "chosen_option_id": primary,
                "chosen_option_ids": ids,
                "custom_answer": custom_answer,
                "text": f"Propose options {status}",
            },
        )
        try:
            from opensquad import session_manager as _sm_mod

            _sm_mod.session_manager.add_event(
                "info",
                {
                    "event": "propose_options_resolved",
                    "id": request_id,
                    "status": status,
                    "chosen_option_id": primary,
                    "chosen_option_ids": ids,
                    "custom_answer": custom_answer,
                    "text": f"Propose options {status}",
                },
            )
        except Exception as persist_err:
            logger.debug("[model_switch] propose_options resolve persist skipped: %s", persist_err)
    except Exception as e:
        logger.warning("[model_switch] propose_options resolve emit failed: %s", e)
        return {"ok": False, "error": str(e)}

    if ignored:
        cue = (
            "[System] The user ignored the proposed options. Ask whether they want a different "
            "approach, or proceed with the most sensible default if they prefer you to decide."
        )
    elif custom_answer.strip():
        cue = (
            f"[System] The user typed their own answer instead of picking a listed option: "
            f'"{custom_answer.strip()[:500]}". Follow their answer as the chosen plan.'
        )
    elif len(ids) > 1:
        joined = ", ".join(ids)
        cue = (
            f"[System] The user chose multiple options: [{joined}]. "
            "Continue with those plans now (in a sensible order). Do not ask for the choice again."
        )
    elif primary:
        cue = (
            f"[System] The user chose option '{primary}'. Continue with that plan now. Do not ask for the choice again."
        )
    else:
        cue = "[System] The user resolved the proposed options without a clear selection. Ask which option they prefer."
    await _nudge_agent_after_mode_decision(cue)
    return {"ok": True, "status": status, "chosen_option_ids": ids}


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
    await apply_agent_mode(
        str(mode),
        approved_request_id=data.get("id") or data.get("approved_request_id"),
        session_id=str(data.get("session_id") or "").strip() or None,
    )


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
    await switch_to_card(str(card), session_id=str(data.get("session_id") or "").strip() or None)
