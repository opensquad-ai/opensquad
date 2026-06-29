# -*- coding: utf-8 -*-
from opensquad.agent_config_schema import apply_config_defaults, validate_agent_config


def test_group_chat_defaults_enabled():
    cfg = {
        "agent_id": "a1",
        "agent_name": "A",
        "model": {"api_protocol": "openai_compat", "provider": "OpenAI", "model_name": "gpt-4", "base_url": "http://x"},
    }
    apply_config_defaults(cfg)
    assert cfg["group_chat"]["enabled"] is True
    assert cfg["group_chat"]["email"] == "ai@ai"
    assert cfg["group_chat"]["password"] == "aaaaaa"
    assert cfg["group_chat"]["groups"] == []
    assert validate_agent_config(cfg) == []


def test_group_chat_enabled_without_groups_is_valid():
    cfg = {
        "agent_id": "a1",
        "agent_name": "A",
        "model": {"api_protocol": "openai_compat", "provider": "OpenAI", "model_name": "gpt-4", "base_url": "http://x"},
        "group_chat": {"enabled": True, "email": "bot@ai", "password": "secret", "groups": []},
    }
    assert validate_agent_config(cfg) == []
