"""Default model card — the workspace fallback for agents with no model.

The Model Cards UI marks exactly one card ``is_default`` (the admin PUT keeps it
unique). It is the model a turn falls back to when neither the chat payload, the
session override, nor the agent's own config names one. Without it the turn
built a client with an empty ``model_name`` and only failed at request time.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

import opensquad.model_switch as model_switch
import opensquad.session_model as session_model
from opensquad.launcher.management_api import _cards as cards_mod
from opensquad.launcher.management_api._cards import CardsMixin
from opensquad.system_config import syscfg


class _Recorder(CardsMixin):
    """Minimal ``self`` for the unbound handler: the mixin plus ``_send_json``."""

    def __init__(self):
        self.sent = []

    def _send_json(self, payload, status: int = 200):
        self.sent.append((payload, status))
        return payload


@pytest.fixture
def cards_dir(tmp_path, monkeypatch):
    """Point the card directory + workspace resolver at a throwaway folder."""
    d = tmp_path / "model_cards"
    d.mkdir()
    agents = tmp_path / "agents"
    agents.mkdir()
    monkeypatch.setattr(cards_mod, "MODEL_CARDS_DIR", str(d))
    monkeypatch.setattr(cards_mod, "AGENTS_DIR", str(agents))
    monkeypatch.setattr(syscfg, "workspace_model_cards_dir", lambda: str(d))
    return d


def _write(cards_dir, name: str, **over) -> dict:
    card = {"model_name": f"m-{name}", "api_key": "k", "base_url": f"https://{name}.example/v1"}
    card.update(over)
    (cards_dir / f"{name}.json").write_text(json.dumps(card), encoding="utf-8")
    return card


def _read(cards_dir, name: str) -> dict:
    return json.loads((cards_dir / f"{name}.json").read_text(encoding="utf-8"))


# ── default_model_card() finds the flag ──


def test_none_when_no_card_is_flagged(cards_dir):
    _write(cards_dir, "a")
    _write(cards_dir, "b")
    assert model_switch.default_model_card() is None


def test_returns_the_flagged_card(cards_dir):
    _write(cards_dir, "a")
    _write(cards_dir, "b", is_default=True)
    assert model_switch.default_model_card() == "b"


def test_missing_directory_is_none(monkeypatch):
    monkeypatch.setattr(
        syscfg,
        "workspace_model_cards_dir",
        lambda: os.path.join(os.sep, "definitely-not-here", "model_cards"),
    )
    assert model_switch.default_model_card() is None


# ── the PUT keeps the flag unique ──


def test_put_default_clears_the_previous_default(cards_dir):
    _write(cards_dir, "a", is_default=True)
    _write(cards_dir, "b")

    handler = _Recorder()
    CardsMixin._handle_put_model_card(handler, "b", {"is_default": True})
    assert handler.sent[-1][0]["ok"] is True

    assert _read(cards_dir, "b")["is_default"] is True
    assert _read(cards_dir, "a")["is_default"] is False
    assert model_switch.default_model_card() == "b"


def test_put_without_the_flag_leaves_default_alone(cards_dir):
    _write(cards_dir, "a", is_default=True)
    _write(cards_dir, "b")

    handler = _Recorder()
    CardsMixin._handle_put_model_card(handler, "b", {"api_key": "new"})
    assert _read(cards_dir, "a")["is_default"] is True
    assert model_switch.default_model_card() == "a"


# ── bind_for_turn falls back to the workspace default ──


@pytest.mark.asyncio
async def test_bind_falls_back_to_workspace_default(cards_dir, monkeypatch):
    _write(cards_dir, "fallback", is_default=True)

    api = SimpleNamespace(model_config={}, config={}, base_url="", reasoning_effort="high", req=[])
    runner = SimpleNamespace(
        chat_api=api,
        _root_chat_api=api,
        _session_chat_apis={"sid-x": api},
        _session_model_cards={},
        _model_config={},
        _current_user_id="",
    )

    # No session override: the fallback must come from the workspace default.
    monkeypatch.setattr(session_model, "get", lambda r, sid: None)

    async def fake_apply(r, new_model, *, chat_api=None):
        chat_api.model_config = dict(new_model)
        chat_api.config = dict(new_model)
        chat_api.base_url = new_model.get("base_url")
        return chat_api

    monkeypatch.setattr(model_switch, "apply_model_reload", fake_apply)

    out = await session_model.bind_for_turn(runner, "sid-x")
    assert session_model.current_api_card(out) == "fallback"
    assert out.base_url.startswith("https://fallback.example")
