"""Tests for config.json pydantic schema validation (Config Validation).

Validates:
1. Valid config passes validation
2. Invalid api_protocol is rejected
3. Empty agent_id/agent_name is rejected
4. Temperature out of range is rejected
5. token_max <= 0 is rejected
6. Invalid config produces human-readable error
"""

import pytest

from opensquad.config_schema import ConfigValidationError, validate_agent_config


class TestValidConfig:
    """Test that valid configs pass validation."""

    def test_minimal_valid_config(self):
        """Minimal config with required fields should pass."""
        cfg = {
            "agent_id": "test-agent",
            "agent_name": "Test Agent",
            "model": {
                "model_name": "gpt-4",
            },
        }
        result = validate_agent_config(cfg)
        assert result["agent_id"] == "test-agent"
        assert result["agent_name"] == "Test Agent"
        assert result["model"]["model_name"] == "gpt-4"
        assert result["model"]["temperature"] == 0.3  # default

    def test_full_valid_config(self):
        """Full config with all fields should pass."""
        cfg = {
            "agent_id": "ultimate",
            "agent_name": "Ultimate Agent",
            "agent_type": "assistant",
            "capabilities": ["chat", "code"],
            "model": {
                "api_protocol": "openai",
                "provider": "OpenAI",
                "model_name": "gpt-4o",
                "api_key": "sk-test",
                "base_url": "https://api.openai.com",
                "temperature": 0.7,
                "token_max": 128000,
                "timeout": 60.0,
                "tool_call_mode": "native",
                "is_image": True,
                "is_video": False,
                "is_audio_model": True,
                "use_file_api": True,
                "file_api_size_threshold": 8388608,
                "is_audio_output": True,
                "audio_output_voice": "echo",
                "frequency_penalty": 0.5,
                "presence_penalty": 0.5,
                "enable_repetition_check": True,
                "max_video_frames": 12,
                "is_think": False,
                "thinking_budget_tokens": 15000,
                "is_image_output": False,
                "top_k": 5,
            },
            "prompt": {
                "base": "base_fc.md",
                "role": "role.md",
            },
            "tools": ["filesystem", "websearch"],
            "plugins": ["quick_note"],
            "collaboration": {
                "enabled": False,
            },
            "gateway": {
                "enabled": True,
                "url": "ws://localhost:8000",
            },
            "web_server": {
                "enabled": True,
                "port": 8080,
            },
        }
        result = validate_agent_config(cfg)
        assert result["model"]["temperature"] == 0.7
        assert result["model"]["token_max"] == 128000
        assert result["web_server"]["port"] == 8080


class TestInvalidConfig:
    """Test that invalid configs are rejected with clear errors."""

    def test_empty_agent_id(self):
        """Empty agent_id should be rejected."""
        cfg = {
            "agent_id": "",
            "agent_name": "Test",
            "model": {"model_name": "gpt-4"},
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "agent_id" in str(exc_info.value)

    def test_empty_agent_name(self):
        """Empty agent_name should be rejected."""
        cfg = {
            "agent_id": "test",
            "agent_name": "",
            "model": {"model_name": "gpt-4"},
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "agent_name" in str(exc_info.value)

    def test_invalid_api_protocol(self):
        """Invalid api_protocol should be rejected."""
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {
                "api_protocol": "invalid_protocol",
                "model_name": "gpt-4",
            },
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "api_protocol" in str(exc_info.value)

    def test_temperature_too_high(self):
        """Temperature > 2.0 should be rejected."""
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {
                "model_name": "gpt-4",
                "temperature": 3.0,
            },
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "temperature" in str(exc_info.value)

    def test_temperature_negative(self):
        """Temperature < 0 should be rejected."""
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {
                "model_name": "gpt-4",
                "temperature": -0.5,
            },
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "temperature" in str(exc_info.value)

    def test_token_max_zero(self):
        """token_max = 0 should be rejected."""
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {
                "model_name": "gpt-4",
                "token_max": 0,
            },
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "token_max" in str(exc_info.value)

    def test_port_out_of_range(self):
        """Port > 65535 should be rejected."""
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {"model_name": "gpt-4"},
            "web_server": {"port": 99999},
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "port" in str(exc_info.value)

    def test_max_video_frames_too_high(self):
        """max_video_frames > 20 should be rejected."""
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {
                "model_name": "gpt-4",
                "max_video_frames": 50,
            },
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        assert "max_video_frames" in str(exc_info.value)

    def test_error_message_is_readable(self):
        """Error message should be human-readable."""
        cfg = {
            "agent_id": "",
            "agent_name": "Test",
            "model": {
                "api_protocol": "bad",
                "model_name": "gpt-4",
                "temperature": 5.0,
            },
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_agent_config(cfg)
        msg = str(exc_info.value)
        assert "config.json validation failed" in msg
        # Should list each error with location
        assert "[" in msg and "]" in msg


class TestDefaults:
    """Test that defaults are filled in correctly."""

    def test_model_defaults(self):
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {"model_name": "gpt-4"},
        }
        result = validate_agent_config(cfg)
        m = result["model"]
        assert m["api_protocol"] == "openai_compat"
        assert m["temperature"] == 0.3
        assert m["token_max"] == 100000
        assert m["timeout"] == 120.0
        assert m["tool_call_mode"] == "auto"
        assert m["is_image"] is False
        assert m["max_video_frames"] == 8
        assert m["top_k"] == 0

    def test_top_level_defaults(self):
        cfg = {
            "agent_id": "test",
            "agent_name": "Test",
            "model": {"model_name": "gpt-4"},
        }
        result = validate_agent_config(cfg)
        assert result["agent_type"] == "assistant"
        assert result["capabilities"] == []
        assert result["tools"] == []
        assert result["disabled_tools"] == []
        assert result["plugins"] == []
        assert result["gateway"]["enabled"] is True
        assert result["web_server"]["enabled"] is False
        assert result["group_chat"]["enabled"] is True
        assert result["group_chat"]["email"] == "ai@ai"
        assert result["group_chat"]["groups"] == []
