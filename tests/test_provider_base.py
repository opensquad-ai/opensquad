"""Locks for the provider-API single source of truth.

``ChatAPI`` / ``ClaudeAPI`` / ``GoogleAPI`` used to carry three drifting copies
of the same session-history, token-accounting and context-compression code.
This module is the anti-regression guard for that consolidation:

* ``test_hoisted_methods_live_only_in_the_base`` — fails the moment anyone
  re-adds a private copy of a shared method to one provider.
* the compression invariants that actually broke in production (degenerate
  range returning uncompressed history, orphan ``role:"tool"`` retention,
  missing auto-compress stats) are asserted across **all three** providers,
  so a provider-specific fork is caught even if it only regresses one of them.
"""

from __future__ import annotations

import pytest

from opensquad import _provider_base as pb
from opensquad._provider_base import ProviderAPIBase
from opensquad.chat_api import ChatAPI
from opensquad.claude_api import ClaudeAPI
from opensquad.google_api import GoogleAPI

ALL_PROVIDERS = (ChatAPI, ClaudeAPI, GoogleAPI)

#: Everything that must exist exactly once, on ProviderAPIBase.
HOISTED = (
    "_emit_with_sid",
    "_prepare_messages",
    "_tail_msgs_for_rounds",
    "_is_tool_result_msg",
    "_build_conv_text",
    "_generate_summary",
    "_count_tokens",
    "_count_message_tokens",
    "_count_tools_tokens",
    "get_current_token_count",
    "invalidate_token_cache",
    "_ensure_history_dir",
    "_initialize_history",
    "save_history",
    "get_cumulative_stats",
    "list_sessions",
    "_first_real_user_idx",
    "_summary_system_prompt",
    "_summary_user_prompt",
)


# ── single source of truth ───────────────────────────────────────────────


def test_hoisted_methods_live_only_in_the_base():
    """No provider may re-declare shared logic in its own ``__dict__``."""
    for name in HOISTED:
        assert name in vars(ProviderAPIBase), f"ProviderAPIBase is missing {name}"
    for cls in ALL_PROVIDERS:
        for name in HOISTED:
            assert name not in vars(cls), (
                f"{cls.__name__}.{name} re-implements logic that belongs to ProviderAPIBase — "
                "the three providers will drift apart again"
            )


def test_all_providers_share_one_compression_entry_point():
    for cls in ALL_PROVIDERS:
        assert cls._prepare_messages is ProviderAPIBase._prepare_messages
        assert cls._count_message_tokens is ProviderAPIBase._count_message_tokens


def test_summariser_transport_is_the_only_provider_specific_piece():
    """Each provider must override exactly the transport, not the prompt."""
    for cls in ALL_PROVIDERS:
        assert "_summarizer_request" in vars(cls), f"{cls.__name__} must implement _summarizer_request"
        assert cls._generate_summary is ProviderAPIBase._generate_summary


# ── helpers ──────────────────────────────────────────────────────────────


def _bare(cls):
    """A provider instance with no SDK client and no tiktoken (deterministic)."""
    api = object.__new__(cls)
    api.req = [{"role": "system", "content": "sys"}]
    api.model = "test-model"
    api.token_max = 100
    api.encoding = None  # -> len(str(x)) // 4 fallback, no tokenizer needed
    api._init_provider_base()
    # Compression must never try to reach a real summariser.
    api._generate_summary = lambda msgs: "SUMMARY"
    return api


@pytest.fixture
def compression_params(monkeypatch):
    """Pin the compression knobs so boundary maths is reproducible."""

    def _apply(trigger=0.5, keep_frac=0.1, hard_cap=0.3, rounds=2):
        monkeypatch.setattr(pb.syscfg, "ctx_trigger_threshold", lambda: trigger)
        monkeypatch.setattr(pb.syscfg, "ctx_keep_recent_fraction", lambda: keep_frac)
        monkeypatch.setattr(pb.syscfg, "ctx_recent_hard_cap_frac", lambda: hard_cap)
        monkeypatch.setattr(pb.syscfg, "ctx_keep_recent_rounds", lambda: rounds)

    return _apply


def _bulky_history(n_turns: int = 4, filler: int = 400) -> list[dict]:
    """system + first user + n_turns of (assistant tool_calls, tool, assistant)."""
    req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "U0 " + "a" * filler},
    ]
    for i in range(n_turns):
        req.append(
            {
                "role": "assistant",
                "content": f"thinking {i} " + "b" * filler,
                "tool_calls": [{"id": f"call_{i}", "function": {"name": "read", "arguments": "{}"}}],
            }
        )
        req.append({"role": "tool", "tool_call_id": f"call_{i}", "name": "read", "content": f"out {i} " + "c" * filler})
        req.append({"role": "assistant", "content": f"reply {i} " + "d" * filler})
    return req


# ── invariant: degenerate range must still compact ───────────────────────


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_degenerate_range_still_compacts_instead_of_returning_uncompressed(cls, compression_params):
    """The old ClaudeAPI branch returned ``self.req`` untouched here.

    That is the "compression is a no-op and tokens keep climbing" failure the
    surrounding comments warn about: a triggered compression that silently
    does nothing.  The unified algorithm must always drop the middle.
    """
    compression_params(keep_frac=1.0, hard_cap=1.0, rounds=0)
    api = _bare(cls)
    # Only user turn is the very last message -> the summarise range collapses.
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "a" * 400},
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "b" * 400},
        {"role": "assistant", "content": "c" * 400},
        {"role": "user", "content": "final question " + "d" * 400},
    ]
    before_messages = len(api.req)

    result = api._prepare_messages()

    assert len(result) < before_messages, "degenerate range must not return the history uncompressed"
    assert result[0] == {"role": "system", "content": "sys"}
    assert api._auto_compressed is True
    assert api._auto_compress_stats.get("summary_empty") is True


# ── invariant: never retain an orphan tool message ───────────────────────


def _tool_pair_history(n_steps: int = 6, filler: int = 400) -> list[dict]:
    """system + user + repeated (assistant w/ tool_calls, tool result, assistant)."""
    req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do the thing " + "u" * 120},
    ]
    for i in range(n_steps):
        req.append(
            {
                "role": "assistant",
                "content": f"step {i} " + "a" * 120,
                "tool_calls": [{"id": f"call_{i}", "function": {"name": "read", "arguments": "{}"}}],
            }
        )
        req.append(
            {"role": "tool", "tool_call_id": f"call_{i}", "name": "read", "content": f"result {i} " + "t" * filler}
        )
        req.append({"role": "assistant", "content": f"after {i} " + "b" * 120})
    return req


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_retained_section_never_starts_with_a_tool_message(cls, compression_params):
    """Splitting a tool_calls/tool pair makes the provider reject the request.

    Sweeps the retention fraction so the boundary lands on every message type
    (including the large ``tool`` results), rather than checking one fixed
    boundary — a single hand-picked case does not exercise the guards.

    Two complementary guards are in play: a backwards walk while the boundary
    sits on a ``tool`` message, and a short forward scan for orphaned tool
    messages in the first few retained positions.  ``hard_cap`` is pinned tiny
    on purpose so the rounds/anchor pullbacks are refused and the boundary
    stays where the token budget put it — otherwise the degenerate-range
    rescue path runs instead, which has its own (unmutated) copy of the walk.

    With this history the raw boundary lands on a ``tool`` message at
    keep_frac 0.15 / 0.31 / 0.47, so the sweep does exercise the guards.
    """
    for step in range(3, 61, 2):
        frac = step / 100
        compression_params(keep_frac=frac, hard_cap=0.001, rounds=0)

        api = _bare(cls)
        api.req = _tool_pair_history()
        api._prepare_messages()

        # req[0] is the system message, req[1] the preserved first user message.
        retained = api.req[2:]
        if not retained:
            continue
        assert retained[0].get("role") != "tool", (
            f"{cls.__name__} retained context starting on an orphan tool message (keep_frac={frac})"
        )
        assert api.req[1].get("role") == "user", "the preserved first message must stay a real user turn"


# ── invariant: auto-compress stats are emitted by every provider ─────────


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_auto_compress_stats_present_for_every_provider(cls, compression_params):
    compression_params(keep_frac=0.1, hard_cap=1.0, rounds=0)
    api = _bare(cls)
    api.req = _bulky_history()

    api._prepare_messages()

    assert api._auto_compressed is True
    stats = api._auto_compress_stats
    for key in (
        "tokens_before",
        "tokens_after",
        "messages_before",
        "messages_after",
        "dropped_count",
        "recent_start",
        "previous_summary",
        "first_kept_role",
        "first_kept_content",
    ):
        assert key in stats, f"{cls.__name__} auto-compress stats missing {key!r}"
    assert stats["tokens_after"] <= stats["tokens_before"]


# ── invariant: cumulative-stats key names ────────────────────────────────


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_cumulative_stats_uses_the_keys_the_consumers_read(cls):
    """``runner.py`` and the token_analytics plugin read ``cache_read_tokens``.

    GoogleAPI used to return ``total_cache_read_tokens``, so Gemini cache
    statistics were silently dropped on the floor.
    """
    stats = _bare(cls).get_cumulative_stats()
    assert set(stats) == {
        "total_input_tokens",
        "total_output_tokens",
        "total_tokens",
        "total_requests",
        "cache_read_tokens",
        "cache_creation_tokens",
    }


# ── invariant: rounds-based retention stays wired and correct ────────────


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_tail_msgs_for_rounds_starts_at_the_last_user_turn_and_drops_tool_results(cls):
    """Shared helper: tool payloads must be summarised, never carried verbatim."""
    api = _bare(cls)
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first turn " + "u" * 200},
        {"role": "assistant", "content": "a" * 200},
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "t" * 200},
        {"role": "assistant", "content": "b" * 200},
        {"role": "user", "content": "second turn " + "v" * 200},
        {"role": "assistant", "content": "c" * 200},
        {"role": "tool", "tool_call_id": "c2", "name": "read", "content": "t" * 200},
    ]

    tail = api._tail_msgs_for_rounds(1)

    assert tail, "expected a non-empty tail"
    assert tail[0]["content"].startswith("second turn"), "tail must start at the last real user turn"
    assert all(m.get("role") != "tool" for m in tail), "tool results must be left for the summariser"


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_rounds_config_is_still_consumed_by_the_unified_algorithm(cls, compression_params):
    """``ctx_keep_recent_rounds`` must not become dead config.

    Two providers used a rounds-based retention pass; the third did not. The
    merge has to keep consulting the knob, or a documented tunable would
    silently stop working.
    """
    compression_params(keep_frac=0.1, hard_cap=1.0, rounds=2)
    api = _bare(cls)
    api.req = _bulky_history()

    seen: list[int] = []
    original = ProviderAPIBase._tail_msgs_for_rounds

    def spy(n_rounds):
        seen.append(n_rounds)
        return original(api, n_rounds)

    api._tail_msgs_for_rounds = spy
    api._prepare_messages()

    assert seen, "compression no longer consults ctx_keep_recent_rounds()"
    assert seen == [2]


# ── invariant: token accounting covers reasoning_content ─────────────────


@pytest.fixture
def chat():
    return ChatAPI(
        api_key="test-key",
        model="gpt-4",
        base_url="https://api.openai.com/v1",
        prompt="You are a helpful assistant.",
        reduction_strategy="start",
    )


def test_count_message_tokens_counts_reasoning_content(chat):
    """Two providers silently ignored reasoning_content (~2.5% undercount)."""
    if not chat.encoding:
        pytest.skip("tiktoken unavailable")
    plain = chat._count_message_tokens({"role": "assistant", "content": "x"})
    with_reasoning = chat._count_message_tokens(
        {"role": "assistant", "content": "x", "reasoning_content": "deep thought " * 60}
    )
    assert with_reasoning > plain


def test_count_tokens_includes_tool_schema_overhead(chat):
    if not chat.encoding:
        pytest.skip("tiktoken unavailable")
    msgs = [{"role": "user", "content": "hi"}]
    tools = [
        {
            "function": {
                "name": "read_file",
                "description": "Read a file from disk",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        }
    ]
    assert chat._count_tokens(msgs, tools) > chat._count_tokens(msgs)


# ── invariant: the summariser prompt carries the current-task section ────


def test_summary_prompt_asks_for_the_current_task():
    """Prompt assembly is shared, so every provider gets the same template."""
    prompt = ProviderAPIBase._summary_user_prompt("CONV-TEXT")
    assert "## Current Task" in prompt
    assert "## Original Goal" in prompt
    assert "CONV-TEXT" in prompt
