"""delegate_task model-card override: sub-agents may run on a named model card."""

import pytest

from opensquad.tools import delegate


@pytest.fixture()
def _init_delegate(monkeypatch):
    monkeypatch.setattr(delegate, "_chat_api_cfg", {"model": "parent-model", "api_key": "sk-parent"})
    monkeypatch.setattr(delegate, "_tool_registry", object())
    monkeypatch.setattr(delegate, "_parent_sid", "s-test")


def _fake_card(monkeypatch, card):
    import opensquad.model_switch as ms

    monkeypatch.setattr(ms, "resolve_card", lambda name: dict(card, _card=name))


def test_empty_model_inherits_parent(_init_delegate):
    sub_cfg = {"model": "parent-model", "api_key": "sk-parent"}
    assert delegate._apply_model_override(sub_cfg, "") is None
    assert sub_cfg["model"] == "parent-model"
    assert "model_name" not in sub_cfg


def test_override_copies_card_fields(_init_delegate, monkeypatch):
    _fake_card(
        monkeypatch,
        {
            "model_name": "glm-5.3-flash",
            "api_key": "sk-card",
            "base_url": "https://api.example.com/v1",
            "api_protocol": "openai_compat",
        },
    )
    sub_cfg = {"model": "parent-model", "api_key": "sk-parent"}
    assert delegate._apply_model_override(sub_cfg, "glm-5.3-flash") is None
    assert sub_cfg["model"] == "glm-5.3-flash"
    assert sub_cfg["model_name"] == "glm-5.3-flash"
    assert sub_cfg["api_key"] == "sk-card"
    assert sub_cfg["base_url"] == "https://api.example.com/v1"
    assert sub_cfg["api_protocol"] == "openai_compat"


def test_unknown_card_returns_error_and_keeps_cfg(_init_delegate, monkeypatch):
    import opensquad.model_switch as ms

    def _boom(name):
        raise FileNotFoundError(f"model card not found: {name}")

    monkeypatch.setattr(ms, "resolve_card", _boom)
    sub_cfg = {"model": "parent-model"}
    err = delegate._apply_model_override(sub_cfg, "no-such-card")
    assert err is not None and "no-such-card" in err
    assert sub_cfg["model"] == "parent-model"


def test_build_runner_propagates_override_error(_init_delegate, monkeypatch):
    import opensquad.model_switch as ms

    def _boom(name):
        raise ValueError(f"model card {name!r} has no model_name")

    monkeypatch.setattr(ms, "resolve_card", _boom)
    runner, err = delegate._build_runner(0, "preview", model="bad-card")
    assert runner is None
    assert err is not None and "bad-card" in err
