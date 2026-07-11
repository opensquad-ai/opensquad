"""Tests for Plan/Build agent mode gates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import opensquad.model_switch as model_switch
from opensquad.agent_mode import (
    MODE_BUILD,
    MODE_PLAN,
    filter_tools_for_mode,
    is_tool_blocked_in_plan,
    mode_prompt_section,
    normalize_mode,
)


def test_normalize_mode():
    assert normalize_mode("plan") == MODE_PLAN
    assert normalize_mode("BUILD") == MODE_BUILD
    assert normalize_mode(None) == MODE_BUILD
    assert normalize_mode("other") == MODE_BUILD


def test_plan_blocks_file_writes():
    assert is_tool_blocked_in_plan("filesystem__write_file")
    assert is_tool_blocked_in_plan("filesystem.write_file")
    assert is_tool_blocked_in_plan("filesystem__replace_in_file")
    assert is_tool_blocked_in_plan("filesystem__delete_file")
    assert is_tool_blocked_in_plan("filesystem__create_directory")


def test_plan_allows_reads():
    assert not is_tool_blocked_in_plan("filesystem__read_file")
    assert not is_tool_blocked_in_plan("filesystem__list_directory")
    assert not is_tool_blocked_in_plan("filesystem__search_files")
    assert not is_tool_blocked_in_plan("filesystem__find_files")


def test_plan_blocks_shell():
    assert is_tool_blocked_in_plan("system__create_shell_session")
    assert is_tool_blocked_in_plan("system__run_session_job")
    assert is_tool_blocked_in_plan("system__start_job")
    assert not is_tool_blocked_in_plan("system__get_system_info")
    assert not is_tool_blocked_in_plan("system__check_job")


def test_plan_allows_mode_switch_tool_via_registry_exception():
    # request_switch itself is not in blocked list
    assert not is_tool_blocked_in_plan("agent_mode__request_switch")


def test_filter_tools_for_mode():
    tools = [
        {"type": "function", "function": {"name": "filesystem__read_file"}},
        {"type": "function", "function": {"name": "filesystem__write_file"}},
        {"type": "function", "function": {"name": "system__run_session_job"}},
    ]
    filtered = filter_tools_for_mode(tools, MODE_PLAN)
    names = [(t.get("function") or {}).get("name") for t in filtered]
    assert names == ["filesystem__read_file"]
    assert filter_tools_for_mode(tools, MODE_BUILD) == tools


def test_mode_prompt_section():
    assert "PLAN" in mode_prompt_section("plan").upper()
    assert "BUILD" in mode_prompt_section("build").upper()
    assert "request_switch" in mode_prompt_section("plan")
    assert "request_switch" in mode_prompt_section("build")


@pytest.mark.asyncio
async def test_apply_agent_mode_nudges_only_on_approval(monkeypatch):
    nudges: list[str] = []

    async def fake_nudge(message: str) -> None:
        nudges.append(message)

    monkeypatch.setattr(model_switch, "_nudge_agent_after_mode_decision", fake_nudge)
    monkeypatch.setattr(model_switch, "_runner", SimpleNamespace(agent_mode="plan", tool_registry=None))
    monkeypatch.setattr(model_switch, "_config_path", "")
    monkeypatch.setattr(model_switch.bus, "emit_async", AsyncMock())

    # Manual ModePicker change — no resume turn
    result = await model_switch.apply_agent_mode("build")
    assert result["ok"] is True
    assert nudges == []

    # Approval card — must auto-continue
    result = await model_switch.apply_agent_mode("build", approved_request_id="req-1")
    assert result["ok"] is True
    assert len(nudges) == 1
    assert "approved" in nudges[0].lower()
    assert "build" in nudges[0].lower()


@pytest.mark.asyncio
async def test_deny_mode_switch_nudges_agent(monkeypatch):
    nudges: list[str] = []

    async def fake_nudge(message: str) -> None:
        nudges.append(message)

    monkeypatch.setattr(model_switch, "_nudge_agent_after_mode_decision", fake_nudge)
    monkeypatch.setattr(model_switch.bus, "emit_async", AsyncMock())

    result = await model_switch.deny_mode_switch("req-2", reason="Not now")
    assert result["ok"] is True
    assert len(nudges) == 1
    assert "denied" in nudges[0].lower()
    assert "Not now" in nudges[0]
