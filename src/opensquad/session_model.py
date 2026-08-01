"""
Session-scoped model card store — single source of truth for pane models.

Authority rules
---------------
1. Agent default model lives in ``agents/*/config.json`` (``model._card``).
2. Per-session override lives in ``session_data["model_card"]`` (+ runner memory map).
3. Every parallel turn MUST call :func:`bind_for_turn` before any LLM request.
   Preferred card from the chat WS payload wins over memory/disk so a lost
   ``switch_model`` command cannot leave the turn on the wrong provider.

This module owns get/set/persist/bind. Hot-reload of credentials still goes
through ``model_switch.apply_model_reload`` / ``_resolve_card``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def session_card_map(runner: Any) -> dict[str, str]:
    cards = getattr(runner, "_session_model_cards", None)
    if not isinstance(cards, dict):
        cards = {}
        runner._session_model_cards = cards
    return cards


def session_api_map(runner: Any) -> dict[str, Any]:
    apis = getattr(runner, "_session_chat_apis", None)
    if not isinstance(apis, dict):
        apis = {}
        runner._session_chat_apis = apis
    return apis


def current_api_card(chat_api: Any) -> str | None:
    if chat_api is None:
        return None
    mc = getattr(chat_api, "model_config", None)
    if isinstance(mc, dict) and mc.get("_card"):
        return str(mc["_card"]).strip() or None
    cfg = getattr(chat_api, "config", None)
    if isinstance(cfg, dict) and cfg.get("_card"):
        return str(cfg["_card"]).strip() or None
    return None


def get(runner: Any, sid: str) -> str | None:
    """Return the session override card (memory, then disk). None → use agent default."""
    sid = (sid or "").strip()
    if not sid:
        return None
    cards = session_card_map(runner)
    cached = cards.get(sid)
    if isinstance(cached, str) and cached.strip():
        return cached.strip()
    try:
        from opensquad.session_manager import get_session_manager

        data = get_session_manager().ensure_session_loaded(sid)
        card = (data or {}).get("model_card") if isinstance(data, dict) else None
        if isinstance(card, str) and card.strip():
            cards[sid] = card.strip()
            return card.strip()
    except Exception:
        pass
    return None


def set_memory(runner: Any, sid: str, card_name: str) -> None:
    sid = (sid or "").strip()
    card = (card_name or "").strip()
    if not sid or not card:
        return
    session_card_map(runner)[sid] = card


def persist(sid: str, card_name: str) -> bool:
    """Write ``model_card`` onto the live session dict and flush to disk."""
    sid = (sid or "").strip()
    card = (card_name or "").strip()
    if not sid or not card:
        return False
    try:
        from opensquad.session_manager import get_session_manager

        sm = get_session_manager()
        data = sm.ensure_session_loaded(sid)
        if data is None:
            logger.info(
                "[session_model] sid=%s not on disk yet; model_card=%s memory-only",
                sid,
                card,
            )
            return False
        # Already persisted with this card — skip the full synchronous
        # serialize + double-file write (current_session.json + history/{sid}.json).
        # The chat WS payload carries model_card on *every* message, so without
        # this guard each turn re-writes the whole session before the LLM call.
        if (data.get("model_card") or "").strip() == card:
            return True
        data["model_card"] = card
        sm._save_session_data(data)
        logger.warning("[session_model] persisted sid=%s card=%s", sid, card)
        return True
    except Exception as e:
        logger.warning("[session_model] persist failed sid=%s: %s", sid, e)
        return False


def set_session_card(runner: Any, sid: str, card_name: str) -> None:
    """Memory + disk (best-effort)."""
    set_memory(runner, sid, card_name)
    persist(sid, card_name)


async def bind_for_turn(
    runner: Any,
    sid: str,
    *,
    preferred_card: str | None = None,
) -> Any:
    """Ensure *sid* has a ChatAPI bound to its session model (or agent default).

    Returns the ChatAPI instance to use for this turn. Never silently keeps a
    stale provider when a session/preferred card is set.
    """
    from opensquad.model_switch import apply_model_reload, resolve_card
    from opensquad.session_dispatcher import _clone_chat_api

    sid = (sid or "").strip()
    if not sid:
        return getattr(runner, "chat_api", None)

    preferred = (preferred_card or "").strip() or None

    # Explicit chat payload always wins. (Previously we ignored preferred when it
    # equalled the agent default so a stale UI closure would not clobber a pane
    # override — but Agent Web now persists the last UI pick as the agent
    # default, so that guard would keep old session cards after refresh.)
    # Short-circuit when memory/disk already carry this card so the per-message
    # persist() (full session serialize + double-file write) is skipped entirely.
    if preferred and get(runner, sid) != preferred:
        set_session_card(runner, sid, preferred)

    apis = session_api_map(runner)
    api = apis.get(sid)
    if api is None:
        root = getattr(runner, "_root_chat_api", None) or getattr(runner, "chat_api", None)
        api = _clone_chat_api(root)
        api._sid_provider = lambda s=sid: s
        api._user_id_provider = lambda: getattr(runner, "_current_user_id", "")
        apis[sid] = api

    desired = preferred or get(runner, sid)
    if not desired:
        return api

    if current_api_card(api) == desired:
        return api

    try:
        new_cfg = resolve_card(desired)
    except (FileNotFoundError, ValueError) as e:
        logger.warning("[session_model] bind resolve failed sid=%s: %s", sid, e)
        return api

    # Preserve live reasoning_effort when the card omits it.
    try:
        from opensquad import model_switch as ms

        ms._preserve_effort(new_cfg, api)
    except Exception:
        pass

    try:
        refreshed = await apply_model_reload(runner, new_cfg, chat_api=api)
        if refreshed is not None:
            apis[sid] = refreshed
            api = refreshed
        logger.warning(
            "[session_model] bound sid=%s card=%s model=%s base=%s",
            sid,
            desired,
            new_cfg.get("model_name"),
            (new_cfg.get("base_url") or "")[:60],
        )
    except Exception as e:
        logger.warning("[session_model] bind reload failed sid=%s: %s", sid, e)
    return api
