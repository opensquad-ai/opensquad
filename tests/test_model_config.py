"""Tests for ModelConfig dataclass (P2-1).

Validates:
1. from_dict() builds correct config from raw dict
2. Default values are sensible
3. Provider-specific overrides work
4. to_dict() serializes correctly
5. ChatAPI/ClaudeAPI/GoogleAPI accept ModelConfig
"""

import pytest

from opensquad.model_config import ModelConfig


class TestModelConfigFromDict:
    """Test ModelConfig.from_dict() factory method."""

    def test_minimal_dict(self):
        """Minimal config dict should produce valid ModelConfig."""
        cfg = ModelConfig.from_dict({})
        assert cfg.api_key == ""
        assert cfg.model == ""
        assert cfg.base_url == ""
        assert cfg.temperature == 0.3
        assert cfg.token_max == 100_000
        assert cfg.provider == "openai"

    def test_full_dict(self):
        """Full config dict should populate all fields."""
        raw = {
            "api_key": "sk-test",
            "model_name": "gpt-4",
            "base_url": "https://api.example.com",
            "temperature": 0.7,
            "token_max": 200_000,
            "timeout": 60.0,
            "is_image": True,
            "is_audio_model": True,
            "is_video": True,
            "use_file_api": True,
            "file_api_size_threshold": 8 * 1024 * 1024,
            "is_audio_output": True,
            "audio_output_voice": "echo",
            "frequency_penalty": 0.5,
            "presence_penalty": 0.5,
            "enable_repetition_check": True,
            "max_video_frames": 12,
            "is_think": True,
            "thinking_budget_tokens": 20_000,
            "is_image_output": True,
            "top_k": 5,
        }
        cfg = ModelConfig.from_dict(raw, prompt="system prompt", provider="openai")
        assert cfg.api_key == "sk-test"
        assert cfg.model == "gpt-4"
        assert cfg.base_url == "https://api.example.com"
        assert cfg.temperature == 0.7
        assert cfg.token_max == 200_000
        assert cfg.timeout == 60.0
        assert cfg.is_img_model is True
        assert cfg.is_audio_model is True
        assert cfg.is_video_model is True
        assert cfg.use_file_api is True
        assert cfg.file_api_size_threshold == 8 * 1024 * 1024
        assert cfg.is_audio_output is True
        assert cfg.audio_output_voice == "echo"
        assert cfg.frequency_penalty == 0.5
        assert cfg.presence_penalty == 0.5
        assert cfg.enable_repetition_check is True
        assert cfg.max_video_frames == 12
        assert cfg.is_think is True
        assert cfg.thinking_budget_tokens == 20_000
        assert cfg.is_image_output is True
        assert cfg.top_k == 5
        assert cfg.prompt == "system prompt"
        assert cfg.provider == "openai"

    def test_claude_provider(self):
        """Claude provider should set correct defaults."""
        cfg = ModelConfig.from_dict({}, provider="claude")
        assert cfg.provider == "claude"

    def test_google_provider(self):
        """Google provider should set correct defaults."""
        cfg = ModelConfig.from_dict({}, provider="google")
        assert cfg.provider == "google"

    def test_video_frames_capped(self):
        """max_video_frames should be capped at 20."""
        cfg = ModelConfig.from_dict({"max_video_frames": 50})
        assert cfg.max_video_frames == 20


class TestModelConfigDefaults:
    """Test default values."""

    def test_default_temperature(self):
        assert ModelConfig().temperature == 0.3

    def test_default_timeout(self):
        assert ModelConfig().timeout == 120.0

    def test_default_token_max(self):
        assert ModelConfig().token_max == 100_000

    def test_default_file_threshold(self):
        assert ModelConfig().file_api_size_threshold == 4 * 1024 * 1024

    def test_default_booleans_are_false(self):
        cfg = ModelConfig()
        assert cfg.is_img_model is False
        assert cfg.is_audio_model is False
        assert cfg.is_video_model is False
        assert cfg.use_file_api is False
        assert cfg.is_audio_output is False
        assert cfg.is_think is False
        assert cfg.is_image_output is False
        assert cfg.enable_repetition_check is False


class TestModelConfigToDict:
    """Test serialization."""

    def test_to_dict_has_required_keys(self):
        cfg = ModelConfig(api_key="sk-test", model="gpt-4")
        d = cfg.to_dict()
        assert d["provider"] == "openai"
        assert d["api_key"] == "sk-test"
        assert d["model"] == "gpt-4"
        assert "token_max" in d
        assert "temperature" in d


class TestModelConfigWithChatAPI:
    """Test that ChatAPI accepts ModelConfig."""

    @pytest.mark.skipif(
        __import__("importlib").util.find_spec("openai") is None,
        reason="openai package not installed",
    )
    def test_chat_api_with_config(self):
        from opensquad.chat_api import ChatAPI

        cfg = ModelConfig(api_key="sk-test", model="gpt-4", prompt="hello")
        api = ChatAPI(config=cfg)
        assert api.api_key == "sk-test"
        assert api.model == "gpt-4"
        assert api.config is cfg

    @pytest.mark.skipif(
        __import__("importlib").util.find_spec("openai") is None,
        reason="openai package not installed",
    )
    def test_chat_api_backward_compat(self):
        """Legacy kwargs should still work."""
        from opensquad.chat_api import ChatAPI

        api = ChatAPI(api_key="sk-test", model="gpt-4", prompt="hello")
        assert api.api_key == "sk-test"
        assert api.model == "gpt-4"
        assert api.config is not None


class TestModelConfigWithClaudeAPI:
    """Test that ClaudeAPI accepts ModelConfig."""

    @pytest.mark.skipif(
        __import__("importlib").util.find_spec("tiktoken") is None,
        reason="tiktoken package not installed",
    )
    def test_claude_api_with_config(self):
        from opensquad.claude_api import ClaudeAPI

        cfg = ModelConfig(api_key="sk-test", model="claude-3", prompt="hello")
        api = ClaudeAPI(config=cfg)
        assert api.api_key == "sk-test"
        assert api.model == "claude-3"
        assert api.config is cfg


class TestModelConfigWithGoogleAPI:
    """Test that GoogleAPI accepts ModelConfig."""

    def test_google_api_with_config(self):
        from opensquad.google_api import GoogleAPI

        cfg = ModelConfig(api_key="sk-test", model="gemini-pro", prompt="hello")
        api = GoogleAPI(config=cfg)
        assert api.api_key == "sk-test"
        assert api.model == "gemini-pro"
        assert api.config is cfg
