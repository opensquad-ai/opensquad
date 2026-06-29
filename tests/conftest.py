# -*- coding: utf-8 -*-
"""Shared fixtures for OpenSquad unit tests."""
import json
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock
import pytest

from opensquad._context import AgentContext, set_current_context


# ── P0.5: AgentContext fixture (DI for testability) ─────────────────────

@pytest.fixture
def application_context():
    """Create a standalone AgentContext with mocked dependencies.

    The fixture sets the context via ``set_current_context()`` so that
    ``AgentRunner.__init__`` can discover it via ``get_current_context()``
    when no explicit ``agent_context`` arg is passed.

    Tests can override specific fields by assigning to ``ctx.field``
    before creating the object under test::

        def test_something(application_context):
            ctx = application_context
            ctx.chat_api = my_mock_api
            runner = AgentRunner(ctx.tool_registry, ctx.chat_api, agent_context=ctx)
    """
    # Mock all required AsyncContext services
    mock_bus = MagicMock()
    mock_bus.emit_async = AsyncMock()
    mock_bus.emit = MagicMock()
    mock_bus.subscribe = MagicMock()

    mock_input_hub = MagicMock()
    mock_input_hub.get_user_response = AsyncMock(return_value={"source": "test", "content": ""})
    mock_input_hub.wait_for_input = AsyncMock(return_value=False)
    mock_input_hub.get_input_event = MagicMock()
    mock_input_hub.check_urgent_commands = MagicMock(return_value=[])

    mock_message_queue = MagicMock()
    mock_message_queue.get_all = MagicMock(return_value=[])
    mock_message_queue.get_message_event = MagicMock()
    mock_message_queue.size = 0

    mock_state_manager = MagicMock()
    mock_state_manager.get_state = AsyncMock(return_value="idle")
    mock_state_manager.set_state = AsyncMock()

    mock_session_manager = MagicMock()
    mock_session_manager.get_current_session_id = MagicMock(return_value="test-session")
    mock_session_manager.session_data = MagicMock()
    mock_session_manager.session_data.get = MagicMock(return_value="")

    mock_chat_api = MagicMock()
    mock_chat_api.req = []
    mock_chat_api.total_input_tokens = 0
    mock_chat_api.total_output_tokens = 0
    mock_chat_api.total_requests = 0
    mock_chat_api.total_cache_read_tokens = 0

    mock_tool_registry = MagicMock()
    mock_tool_registry.call = AsyncMock(return_value="mock result")

    ctx = AgentContext(
        event_bus=mock_bus,
        input_hub=mock_input_hub,
        message_queue=mock_message_queue,
        state_manager=mock_state_manager,
        session_manager=mock_session_manager,
        chat_api=mock_chat_api,
        tool_registry=mock_tool_registry,
        agent_id="test-agent",
        agent_name="Test Agent",
        config_path="",
    )

    set_current_context(ctx)
    return ctx


# ── Existing fixtures ───────────────────────────────────────────────────


@pytest.fixture
def tmp_file(tmp_path):
    """Return a helper that writes content to a file and returns its path."""
    def _make(content: str, filename: str = "test.json"):
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")
        return str(path)
    return _make


@pytest.fixture
def sample_agent_config():
    """Standard agent config.json content."""
    return {
        "agent_id": "test-agent",
        "agent_name": "Test Agent",
        "model": {
            "provider": "openai",
            "model_name": "gpt-4",
        },
        "web_server": {"port": 8001},
        "tools": ["system", "filesystem"],
        "prompt": {"role": "role.md"},
    }


@pytest.fixture
def sample_dynamic_parts():
    """Standard dynamic context parts for _build_context_prefix."""
    return {
        "RUNTIME_STATE": "idle",
        "TASK_STATE": "No active task",
        "MEMORY_CONTEXT": "User mentioned Python project",
        "custom_key": "custom_value",
    }
