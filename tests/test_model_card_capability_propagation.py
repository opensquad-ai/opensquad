"""Editing a model card must reach the agents that use it.

Reported from the live deployment: the card editor showed 「支持多模态图片
(is_image)」 switched ON, the card file on disk agreed, and the agent still answered
"当前模型不支持看图".  The card is a *template*: each agent keeps its own copy of
the model block in ``agents/<id>/config.json``, and ``model.is_image`` there was
still ``false``.  Saving the card never touched the copies, and no UI path pushed a
card into an agent (``modelCardAPI.assignToAgent`` has no callers), so the switch
was inert for every agent that referenced it.

What is pinned here is the propagation added to ``_handle_put_model_card`` -- and,
just as importantly, its *narrowness*: the card's capability switches and the fields
that say which model it is travel, nothing else.  An agent's own ``temperature`` /
``top_k`` / ``render_mode`` tuning must survive a card edit, or fixing one switch
would silently rewrite every agent's sampling.

Mutations verified (applied, run, reverted):
  M1 propagate the whole card body instead of the card-owned keys -> R1 fails
  M2 ignore which card the agent references                       -> R2 fails
  M3 skip ``reload_config`` after rewriting the config            -> R3 fails
  M4 rewrite + reload even when nothing changed                   -> R4 fails
  M5 drop the model-field loop                                    -> R5 fails
  M6 let model fields overwrite the agent's sampling              -> R5 fails
  M7 write absent keys out as their defaults                      -> R6 fails
  M8 list a per-agent field (render_mode) as card-owned            -> R7 fails
"""

from __future__ import annotations

import json
import os

import pytest

from opensquad.launcher.management_api import _cards as cards

CARD = "ark__glm-5.3-flash"

# What the card file holds before the tests run; the agent copies are seeded from
# it, so a save that changes nothing is a true no-op.  ``model_name`` deliberately
# differs from the card defaults, which is the case the old code got wrong.
CARD_BASE = {
    "api_protocol": "openai_compat",
    "provider": "volc",
    "api_key": "sk-live-1",
    "base_url": "https://ark.example/v1",
    "model_name": "glm-5.3-flash",
    "token_max": 128000,
}


class _Stub(cards.CardsMixin):
    """The mixin itself (so the real helper runs) with ``_send_json`` captured."""

    def __init__(self) -> None:
        self.sent: tuple[dict, int] | None = None

    def _send_json(self, payload: dict, status: int = 200):
        self.sent = (payload, status)
        return payload


class _FakeProcess:
    def __init__(self) -> None:
        self.reloads = 0

    def reload_config(self) -> None:
        self.reloads += 1


def _write_agent(agents_dir: str, name: str, card: str, model_extra: dict) -> str:
    os.makedirs(os.path.join(agents_dir, name), exist_ok=True)
    path = os.path.join(agents_dir, name, "config.json")
    # Connection fields mirror the card (so "save nothing" is a true no-op); the
    # tuning below is deliberately the agent's own, unlike the card's defaults.
    model = {
        "_card": card,
        **CARD_BASE,
        "temperature": 0.7,
        "top_k": 5,
        "tool_output_max_chars": 900,
        "render_mode": "relaxed",
        "audio_output_voice": "nova",
        "tool_call_mode": "required",
    }
    model.update(model_extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"agent_id": name, "model": model}, f)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    cards_dir = tmp_path / "model_cards"
    agents_dir.mkdir()
    cards_dir.mkdir()
    monkeypatch.setattr(cards, "AGENTS_DIR", str(agents_dir))
    monkeypatch.setattr(cards, "MODEL_CARDS_DIR", str(cards_dir))
    with open(cards_dir / f"{CARD}.json", "w", encoding="utf-8") as f:
        json.dump({"name": CARD, "title": "GLM 5.3 Flash", **CARD_BASE}, f)

    using = _write_agent(str(agents_dir), "agent305", CARD, {"is_image": False, "is_image_output": False})
    other = _write_agent(str(agents_dir), "agent999", "some-other-card", {"is_image": False})
    proc = _FakeProcess()
    monkeypatch.setitem(cards._processes, "agent305", proc)

    def payload(card_name=CARD):
        with open(os.path.join(str(cards_dir), f"{card_name}.json"), encoding="utf-8") as f:
            return json.load(f)

    return {
        "agents_dir": str(agents_dir),
        "using": using,
        "other": other,
        "proc": proc,
        "payload": payload,
        "stub": _Stub(),
    }


def _save(env, body: dict) -> dict:
    return env["stub"]._handle_put_model_card(CARD, dict(body))


def _model(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["model"]


def test_capability_switches_reach_the_agent_that_uses_the_card(env):
    """R1 — the switches land, and the agent's own tuning does not move."""
    saved = _save(
        env,
        {
            "is_think": True,
            "is_image": True,
            "is_video": True,
            "is_audio": True,
            "is_audio_output": True,
            "is_image_output": True,
            "is_builtin": True,
            "temperature": 0.1,
            "top_k": 99,
        },
    )
    model = _model(env["using"])
    assert model["is_think"] is True
    assert model["is_image"] is True
    assert model["is_video"] is True
    assert model["is_audio_model"] is True  # card's is_audio -> agent's is_audio_model
    assert model["is_audio_output"] is True
    assert model["is_image_output"] is True
    assert model["is_builtin"] is True
    assert model["temperature"] == 0.7, "a card edit must not rewrite the agent's sampling"
    assert model["top_k"] == 5
    assert saved["agents_updated"] == ["agent305"]


def test_model_fields_follow_the_card(env):
    """R5 — moving the card's endpoint/model/key moves every agent that uses it."""
    _save(
        env,
        {
            **CARD_BASE,
            "provider": "custom",
            "api_key": "sk-live-2",
            "base_url": "https://ark2.example/v1",
            "model_name": "glm-5.4-flash",
            "token_max": 64000,
            "image_size": "768x768",
            "image_steps": 20,
            "image_cfg_scale": 4.5,
            "asr_protocol": "openai_compat",
            "builtin_service": "step-image-edit-2",
            "temperature": 0.1,
            "top_k": 99,
        },
    )
    model = _model(env["using"])
    assert model["provider"] == "custom"
    assert model["api_key"] == "sk-live-2"
    assert model["base_url"] == "https://ark2.example/v1"
    assert model["model_name"] == "glm-5.4-flash"
    assert model["token_max"] == 64000
    assert model["image_size"] == "768x768"
    assert model["image_steps"] == 20
    assert model["image_cfg_scale"] == 4.5
    assert model["asr_protocol"] == "openai_compat"
    assert model["builtin_service"] == "step-image-edit-2"
    assert model["temperature"] == 0.7, "a card edit must not rewrite the agent's sampling"
    assert model["top_k"] == 5
    assert _model(env["other"])["base_url"] == CARD_BASE["base_url"]


def test_per_agent_fields_are_not_synced(env):
    """R7 — the card records UX/sampling knobs an agent may have overridden itself."""
    _save(
        env,
        {
            "render_mode": "strict",
            "audio_output_voice": "alloy",
            "tool_call_mode": "auto",
            "tool_output_max_chars": 1234,
        },
    )
    model = _model(env["using"])
    assert model["render_mode"] == "relaxed"
    assert model["audio_output_voice"] == "nova"
    assert model["tool_call_mode"] == "required"
    assert model["tool_output_max_chars"] == 900
    # The card still records what was saved — it is the agent copy that is the override.
    card = env["payload"]()
    assert card["render_mode"] == "strict"
    assert card["audio_output_voice"] == "alloy"


def test_agents_on_other_cards_are_untouched(env):
    """R2 — matching is by the card the agent references."""
    _save(env, {"is_image": True})
    assert _model(env["other"])["is_image"] is False


def test_the_running_agent_is_told_to_reload(env):
    """R3 — config.json alone is not enough; the launcher must re-read it."""
    _save(env, {"is_image": True})
    assert env["proc"].reloads == 1


def test_an_unchanged_card_writes_nothing(env):
    """R4 — saving without touching the switches is a no-op for agents."""
    before = os.path.getmtime(env["using"])
    saved = _save(env, {"is_image": False, "temperature": 0.1})
    assert saved["agents_updated"] == []
    assert os.path.getmtime(env["using"]) == before
    assert env["proc"].reloads == 0
    assert _model(env["using"])["temperature"] == 0.7


def test_an_agent_missing_a_default_valued_key_is_left_alone(env):
    """R6 — an absent key already reads as the field default, so do not materialise it."""
    path = env["using"]
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    del cfg["model"]["api_key"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    before = os.path.getmtime(path)
    saved = _save(env, {"is_image": False, "api_key": ""})
    assert saved["agents_updated"] == []
    assert os.path.getmtime(path) == before
    assert "api_key" not in _model(path)
