"""Tests for ContextBuilder (P1-1: extracted from AgentRunner).

Validates:
1. build_context_prefix() assembles sections in correct order
2. build_context_prefix() skips empty values
3. ContextBuilder instantiation works without full runner
4. build() returns expected tuple shape
"""

import pytest

from opensquad.context_builder import (
    ContextBuilder,
    build_context_prefix,
    build_dynamic_mcp_state,
    collect_on_demand_prompt_parts,
)


def test_build_context_prefix_orders_sections():
    """Sections must appear in fixed order: RUNTIME_STATE, TASK_STATE, MEMORY_CONTEXT."""
    parts = {
        "MEMORY_CONTEXT": "some memory",
        "RUNTIME_STATE": "runtime info",
        "TASK_STATE": "task plan",
    }
    result = build_context_prefix(parts)
    # Order in output should match RUNTIME_STATE -> TASK_STATE -> MEMORY_CONTEXT
    rt_pos = result.find("Runtime State")
    task_pos = result.find("Task Plan")
    mem_pos = result.find("Long-term Memory")
    assert rt_pos < task_pos < mem_pos


def test_build_context_prefix_skips_empty():
    """Empty or whitespace-only values should not produce sections."""
    parts = {
        "RUNTIME_STATE": "",
        "TASK_STATE": "   ",
        "MEMORY_CONTEXT": "valid memory",
    }
    result = build_context_prefix(parts)
    assert "Runtime State" not in result
    assert "Task Plan" not in result
    assert "valid memory" in result


def test_build_context_prefix_custom_keys():
    """Unknown custom keys should be appended after known keys."""
    parts = {
        "RUNTIME_STATE": "runtime",
        "CUSTOM_VAR": "custom value",
    }
    result = build_context_prefix(parts)
    assert "Runtime State" in result
    assert "CUSTOM_VAR" in result
    # Custom should come after known
    assert result.find("Runtime State") < result.find("CUSTOM_VAR")


def test_build_context_prefix_empty_dict():
    """Empty dict should return empty string."""
    assert build_context_prefix({}) == ""


def test_build_dynamic_mcp_state_with_no_adapter():
    """When mcp_adapter is None, should return empty string."""
    assert build_dynamic_mcp_state(None) == ""


def test_context_builder_init_minimal():
    """ContextBuilder can be instantiated with minimal dependencies."""

    class FakeChatAPI:
        def get_template(self):
            return "{{SKILLS_INSTRUCTIONS}}"

        def get_system_prompt(self):
            return ""

        def update_system_prompt(self, p):
            pass

    class FakeStrategy:
        def prepare_llm_call(self, base):
            return {"system_prompt": base, "tools": None, "tool_choice": "auto"}

    class FakeTaskManager:
        def render(self):
            return ""

    cb = ContextBuilder(
        chat_api=FakeChatAPI(),
        tool_call_strategy=FakeStrategy(),
        task_manager=FakeTaskManager(),
    )
    assert cb is not None
    assert not cb.has_prompt_snapshot


def test_prompt_snapshot_state_is_per_session():
    """Parallel sessions must not share prompt snapshot emission state."""

    class FakeChatAPI:
        _sid_provider = None

        def set_sid(self, sid):
            self._sid_provider = lambda: sid

    class FakeStrategy:
        def prepare_llm_call(self, base):
            return {"system_prompt": base, "tools": None, "tool_choice": "auto"}

    class FakeTaskManager:
        def render(self):
            return ""

    cb = ContextBuilder(
        chat_api=FakeChatAPI(),
        tool_call_strategy=FakeStrategy(),
        task_manager=FakeTaskManager(),
    )
    cb.chat_api.set_sid("session-a")
    cb.mark_snapshot_emitted()
    assert cb.has_prompt_snapshot

    cb.chat_api.set_sid("session-b")
    assert not cb.has_prompt_snapshot
    cb.mark_snapshot_emitted()
    assert cb.has_prompt_snapshot

    cb.chat_api.set_sid("session-a")
    assert cb.has_prompt_snapshot
    cb.reset_prompt_snapshot()
    assert not cb.has_prompt_snapshot

    cb.chat_api.set_sid("session-b")
    assert cb.has_prompt_snapshot


@pytest.mark.asyncio
async def test_context_builder_build_returns_tuple():
    """build() must return (system_prompt, dynamic_prefix, llm_params, is_changed)."""

    class FakeChatAPI:
        req = []

        def get_template(self):
            return "template"

        def get_system_prompt(self):
            return ""

        def update_system_prompt(self, p):
            pass

    class FakeStrategy:
        def prepare_llm_call(self, base):
            return {"system_prompt": base, "tools": None, "tool_choice": "auto"}

    class FakeTaskManager:
        def render(self):
            return ""

    cb = ContextBuilder(
        chat_api=FakeChatAPI(),
        tool_call_strategy=FakeStrategy(),
        task_manager=FakeTaskManager(),
    )
    result = await cb.build(
        last_user_input="hello",
        current_input_source="web",
        current_turn=1,
        current_round=1,
    )
    assert len(result) == 4
    system_prompt, dynamic_prefix, llm_params, is_changed = result
    assert isinstance(system_prompt, str)
    assert isinstance(dynamic_prefix, str)
    assert isinstance(llm_params, dict)
    assert "tools" in llm_params
    assert "tool_choice" in llm_params
    assert isinstance(is_changed, bool)


def test_on_demand_parts_empty_when_idle():
    parts = collect_on_demand_prompt_parts()
    assert parts == {}


def test_on_demand_parts_load_only_active_modes():
    goal = collect_on_demand_prompt_parts(goal_active=True)
    assert "GOAL_MODE_RULES" in goal
    assert "verifiable completion contract" in goal["GOAL_MODE_RULES"]
    assert "PLAN_WORKFLOW_RULES" not in goal

    plan = collect_on_demand_prompt_parts(mode="plan")
    assert "PLAN_WORKFLOW_RULES" in plan
    assert "Cursor-style design before coding" in plan["PLAN_WORKFLOW_RULES"]

    sched = collect_on_demand_prompt_parts(scheduled_turn=True)
    assert "SCHEDULED_TASK_RULES" in sched
    assert "HARD RULE" in sched["SCHEDULED_TASK_RULES"]

    mcp = collect_on_demand_prompt_parts(mcp_connected=True)
    assert "MCP_USAGE_GUIDE" in mcp
    assert "mcp__{server_name}__{tool_name}" in mcp["MCP_USAGE_GUIDE"]
