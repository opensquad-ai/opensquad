"""Golden tests: session model store + switch isolation + not-ready error."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import opensquad.model_switch as model_switch
import opensquad.session_model as session_model


@pytest.mark.asyncio
async def test_switch_session_scoped_also_promotes_default(monkeypatch):
    """Pane switch still binds the session, and promotes last pick to agent default."""
    session_api = SimpleNamespace(
        model_config={"_card": "old"},
        config={"_card": "old"},
        reasoning_effort="high",
        req=[],
    )
    root = SimpleNamespace(
        model_config={"_card": "default-card"},
        config={"_card": "default-card"},
        reasoning_effort="high",
    )
    runner = SimpleNamespace(
        chat_api=root,
        _root_chat_api=root,
        _session_chat_apis={"sid-a": session_api},
        _session_model_cards={},
        _model_config={"_card": "default-card"},
        _current_user_id="",
    )
    monkeypatch.setattr(model_switch, "_runner", runner)
    monkeypatch.setattr(model_switch, "_config_path", "/tmp/cfg.json")
    monkeypatch.setattr(
        model_switch,
        "_resolve_card",
        lambda name: {
            "_card": name,
            "model_name": "step",
            "base_url": "https://api.stepfun.com/v1",
            "api_protocol": "openai",
            "api_key": "k",
        },
    )
    persist_cfg = MagicMock()
    monkeypatch.setattr(model_switch, "_persist_config", persist_cfg)
    monkeypatch.setattr(model_switch.bus, "emit_async", AsyncMock())
    monkeypatch.setattr(session_model, "persist", lambda *a, **k: True)

    async def fake_apply(r, new_model, *, chat_api=None):
        if chat_api is not None:
            chat_api.model_config = dict(new_model)
            chat_api.config = dict(new_model)
            chat_api.base_url = new_model.get("base_url")
            return chat_api
        r.chat_api.model_config = dict(new_model)
        r.chat_api.config = dict(new_model)
        r._model_config = dict(new_model)
        return None

    monkeypatch.setattr(model_switch, "apply_model_reload", fake_apply)

    result = await model_switch.switch_to_card("stepaudio-2.5-chat", session_id="sid-a")
    assert result["ok"] is True
    assert result["scope"] == "session"
    assert persist_cfg.called
    assert runner._model_config.get("_card") == "stepaudio-2.5-chat"
    assert runner._session_model_cards["sid-a"] == "stepaudio-2.5-chat"
    assert session_api.base_url.startswith("https://api.stepfun.com")


@pytest.mark.asyncio
async def test_bind_preferred_card_overrides_stale_api(monkeypatch):
    api = SimpleNamespace(
        model_config={"_card": "opencode-card"},
        config={"_card": "opencode-card", "base_url": "https://opencode.ai/x"},
        base_url="https://opencode.ai/x",
        reasoning_effort="high",
        req=[],
    )
    runner = SimpleNamespace(
        chat_api=api,
        _root_chat_api=api,
        _session_chat_apis={"sid-b": api},
        _session_model_cards={},
        _current_user_id="",
    )
    monkeypatch.setattr(
        model_switch,
        "resolve_card",
        lambda name: {
            "_card": name,
            "model_name": "step",
            "base_url": "https://api.stepfun.com/v1",
            "api_key": "k",
        },
    )
    monkeypatch.setattr(session_model, "persist", lambda *a, **k: True)

    async def fake_apply(r, new_model, *, chat_api=None):
        chat_api.model_config = dict(new_model)
        chat_api.config = dict(new_model)
        chat_api.base_url = new_model.get("base_url")
        return chat_api

    monkeypatch.setattr(model_switch, "apply_model_reload", fake_apply)
    out = await session_model.bind_for_turn(runner, "sid-b", preferred_card="stepaudio-2.5-chat")
    assert session_model.current_api_card(out) == "stepaudio-2.5-chat"
    assert out.base_url.startswith("https://api.stepfun.com")
    assert runner._session_model_cards["sid-b"] == "stepaudio-2.5-chat"


@pytest.mark.asyncio
async def test_bind_preferred_wins_over_session_override(monkeypatch):
    """Explicit chat preferred card always wins (last UI pick / refresh default)."""
    api = SimpleNamespace(
        model_config={"_card": "deepseek-deepseek-chat"},
        config={"_card": "deepseek-deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
        base_url="https://api.deepseek.com/v1",
        reasoning_effort="high",
        req=[],
    )
    runner = SimpleNamespace(
        chat_api=api,
        _root_chat_api=api,
        _session_chat_apis={"sid-z": api},
        _session_model_cards={"sid-z": "deepseek-deepseek-chat"},
        _model_config={"_card": "deepseek-v4-pro"},
        _current_user_id="",
    )

    async def fake_apply(r, new_model, *, chat_api=None):
        chat_api.model_config = dict(new_model)
        chat_api.config = dict(new_model)
        chat_api.base_url = new_model.get("base_url", chat_api.base_url)
        return chat_api

    monkeypatch.setattr(
        model_switch,
        "resolve_card",
        lambda name: {
            "_card": name,
            "model_name": name,
            "base_url": "https://api.deepseek.com/v4",
            "api_key": "k",
        },
    )
    monkeypatch.setattr(model_switch, "apply_model_reload", fake_apply)
    monkeypatch.setattr(session_model, "persist", lambda *a, **k: True)

    out = await session_model.bind_for_turn(runner, "sid-z", preferred_card="deepseek-v4-pro")
    assert session_model.get(runner, "sid-z") == "deepseek-v4-pro"
    assert session_model.current_api_card(out) == "deepseek-v4-pro"

    monkeypatch.setattr(model_switch, "_runner", None)
    monkeypatch.setattr(model_switch, "_ensure_runner", lambda: None)
    monkeypatch.setattr(model_switch.bus, "emit_async", AsyncMock())
    result = await model_switch.switch_to_card("any-card", session_id="sid-x")
    assert result["ok"] is False
    assert "not ready" in result["error"] or "not initialised" in result["error"]


@pytest.mark.asyncio
async def test_session_switch_promotes_default_for_other_panes(monkeypatch):
    """Last UI pick becomes agent default; panes without override follow it."""

    def make_api(card, base):
        return SimpleNamespace(
            model_config={"_card": card},
            config={"_card": card, "base_url": base},
            base_url=base,
            reasoning_effort="high",
            req=[],
        )

    root = make_api("deepseek-v4-pro", "https://opencode.ai/zen/go/v1")
    api_a = make_api("deepseek-v4-pro", "https://opencode.ai/zen/go/v1")
    runner = SimpleNamespace(
        chat_api=root,
        _root_chat_api=root,
        _session_chat_apis={"a": api_a},
        _session_model_cards={},
        _model_config={"_card": "deepseek-v4-pro"},
        _current_user_id="",
    )
    monkeypatch.setattr(model_switch, "_runner", runner)
    monkeypatch.setattr(model_switch, "_config_path", "")
    monkeypatch.setattr(
        model_switch,
        "_resolve_card",
        lambda name: {
            "_card": name,
            "model_name": name,
            "base_url": "https://api.stepfun.com/v1" if "step" in name else "https://opencode.ai/x",
            "api_protocol": "openai",
            "api_key": "k",
        },
    )
    monkeypatch.setattr(model_switch, "_persist_config", MagicMock())
    monkeypatch.setattr(model_switch.bus, "emit_async", AsyncMock())
    monkeypatch.setattr(session_model, "persist", lambda *a, **k: True)

    async def fake_apply(r, new_model, *, chat_api=None):
        target = chat_api or r.chat_api
        target.model_config = dict(new_model)
        target.config = dict(new_model)
        target.base_url = new_model.get("base_url")
        if chat_api is None:
            r._model_config = dict(new_model)
        return chat_api

    monkeypatch.setattr(model_switch, "apply_model_reload", fake_apply)

    def fake_clone(base):
        return make_api(
            (getattr(base, "model_config", {}) or {}).get("_card") or "deepseek-v4-pro",
            getattr(base, "base_url", None) or "https://opencode.ai/zen/go/v1",
        )

    monkeypatch.setattr("opensquad.session_dispatcher._clone_chat_api", fake_clone)

    r = await model_switch.switch_to_card("stepaudio-2.5-chat", session_id="a")
    assert r["ok"]
    assert runner._model_config.get("_card") == "stepaudio-2.5-chat"
    api_b = await session_model.bind_for_turn(runner, "b")
    assert session_model.get(runner, "a") == "stepaudio-2.5-chat"
    assert session_model.get(runner, "b") is None
    assert api_a.base_url.startswith("https://api.stepfun.com")
    # New pane clones root which was promoted to the last UI pick.
    assert session_model.current_api_card(api_b) == "stepaudio-2.5-chat"
