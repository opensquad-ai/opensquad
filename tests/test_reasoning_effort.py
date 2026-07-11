"""Tests for Cursor-style reasoning_effort mapping."""

from opensquad.reasoning_effort import (
    apply_openai_compat_thinking_params,
    effort_to_claude_budget,
    map_openai_compat_effort,
    normalize_effort,
)


def test_normalize_effort():
    assert normalize_effort("low") == "low"
    assert normalize_effort("HIGH") == "high"
    assert normalize_effort(None) == "high"
    assert normalize_effort("weird") == "high"


def test_claude_budget_map():
    assert effort_to_claude_budget("low") == 2048
    assert effort_to_claude_budget("medium") == 8000
    assert effort_to_claude_budget("high") == 16000


def test_deepseek_effort_map():
    assert map_openai_compat_effort("low", model="deepseek-v4-flash") == "high"
    assert map_openai_compat_effort("medium", model="deepseek-v4-flash") == "high"
    assert map_openai_compat_effort("high", model="deepseek-v4-flash") == "max"
    assert map_openai_compat_effort("high", base_url="https://api.deepseek.com") == "max"


def test_openai_native_effort_map():
    assert map_openai_compat_effort("low", model="o3") == "low"
    assert map_openai_compat_effort("medium", model="gpt-5") == "medium"
    assert map_openai_compat_effort("high", model="o4-mini") == "high"


def test_apply_params_deepseek():
    params: dict = {"model": "deepseek-v4-flash"}
    apply_openai_compat_thinking_params(
        params,
        is_think=True,
        effort="high",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )
    assert params["reasoning_effort"] == "max"
    assert params["extra_body"]["thinking"] == {"type": "enabled"}


def test_apply_params_non_think_noop():
    params: dict = {"model": "gpt-4o"}
    apply_openai_compat_thinking_params(
        params,
        is_think=False,
        effort="high",
        model="gpt-4o",
        base_url="https://api.openai.com",
    )
    assert "reasoning_effort" not in params
    assert "extra_body" not in params


def test_apply_params_openai_native():
    params: dict = {"model": "o3"}
    apply_openai_compat_thinking_params(
        params,
        is_think=True,
        effort="medium",
        model="o3",
        base_url="https://api.openai.com/v1",
    )
    assert params["reasoning_effort"] == "medium"
    assert "extra_body" not in params
