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

import tokenize
from pathlib import Path

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
    history = _tool_pair_history()
    opening = history[1]
    for step in range(3, 61, 2):
        frac = step / 100
        compression_params(keep_frac=frac, hard_cap=0.001, rounds=0)

        api = _bare(cls)
        api.req = list(history)
        api._prepare_messages()

        # req[0] is the system message; the retained window follows it.
        assert api.req[0].get("role") == "system"
        retained = api.req[1:]
        if not retained:
            continue
        assert retained[0].get("role") != "tool", (
            f"{cls.__name__} retained context starting on an orphan tool message (keep_frac={frac})"
        )
        # The session's opening request must not survive as a live message — it
        # is summarised instead (see test_first_user_request_is_summarised...).
        assert opening not in retained, f"{cls.__name__} re-pinned the session's first user request (keep_frac={frac})"


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_first_user_request_is_summarised_not_pinned(cls, compression_params):
    """The op's root cause: after compaction the model re-ran the original task.

    ``compacted_req`` used to be ``[system_msg, first_user, *recent]``, so the
    session's very first user request stayed in the request as if the user had
    just sent it (session 20260922_113451_zh08). It must instead reach the model
    through the summary — which means the summariser has to *see* it, or the
    information is lost rather than moved.
    """
    compression_params(keep_frac=0.1, hard_cap=1.0, rounds=0)
    api = _bare(cls)
    api.req = _bulky_history()
    opening = api.req[1]
    summarised: list[list[dict]] = []
    api._generate_summary = lambda msgs: summarised.append(list(msgs)) or "SUMMARY"

    result = api._prepare_messages()

    assert result[0] == {"role": "system", "content": "sys"}
    assert opening not in result[1:], "the opening request was pinned back into the request"
    assert any(opening in batch for batch in summarised), (
        "the opening request was neither pinned nor summarised — the task description is simply gone"
    )


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_compaction_never_returns_a_request_without_a_turn(cls, compression_params):
    """A request of `[system]` alone has nothing to answer.

    The retention budget is a fraction of the current tokens, so a single
    oversized message (a big tool result, say) can be refused by the budget
    entirely. The re-pinned first-user message used to cover for that; with it
    gone the request must still keep a real turn.
    """
    compression_params(keep_frac=0.1, hard_cap=0.1, rounds=0)
    api = _bare(cls)
    # ≥5 messages or _prepare_messages returns early without compressing.
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": "working"},
        {"role": "assistant", "content": "still working"},
        {"role": "user", "content": "huge payload " + "z" * 4000},
    ]
    last = api.req[-1]

    result = api._prepare_messages()

    assert result[0] == {"role": "system", "content": "sys"}
    assert len(result) > 1, "compaction returned a request with only the system message"
    assert result[-1] is last, "the live turn was summarised away instead of retained"


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_auto_compaction_carries_the_previous_summary_forward(cls, compression_params):
    """The superseded summary must reach the summariser, not just be replaced.

    Every compaction overwrites ``_latest_summary``, and the messages the old
    summary described are by then out of context — so a summariser that never
    sees it drops that knowledge permanently (the manual path has always passed
    it through ``build_summary_payload``; the automatic path did not).
    """
    compression_params(keep_frac=0.1, hard_cap=1.0, rounds=0)
    prompts: list[str] = []
    api = object.__new__(cls)
    api.req = _bulky_history()
    api.model = "test-model"
    api.token_max = 100
    api.encoding = None
    api._init_provider_base()

    def _transport(system_prompt, user_prompt, max_tokens):
        prompts.append(user_prompt)
        return "NEW SUMMARY"

    api._summarizer_request = _transport  # keep the real _generate_summary/prompt assembly

    api._latest_summary = "OLD SUMMARY: port 8080, edited src/a/b.py"
    api._prepare_messages()

    assert prompts, "the summariser was never called"
    assert "OLD SUMMARY: port 8080, edited src/a/b.py" in prompts[0], (
        "the previous summary was dropped — only directly re-read messages reach the summariser"
    )
    assert "[Previous Context Summary" in prompts[0]

    # ...and nothing is invented when there is no previous summary.
    prompts.clear()
    api2 = object.__new__(cls)
    api2.req = _bulky_history()
    api2.model = "test-model"
    api2.token_max = 100
    api2.encoding = None
    api2._init_provider_base()
    api2._summarizer_request = _transport
    api2._prepare_messages()
    assert prompts and "[Previous Context Summary" not in prompts[0]


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_short_history_with_one_oversized_message_still_compacts(cls, compression_params):
    """`len(self.req) < 5` used to return the history whole, whatever its size.

    The irreducible preflight only covers the system message + tool schemas, so
    a 4-message session carrying one enormous tool result was never compacted and
    went straight to the provider to 400.
    """
    compression_params(keep_frac=0.1, hard_cap=0.1, rounds=0)
    api = _bare(cls)
    api.token_max = 100  # overflow_limit = 100 * ctx_overflow_guard_frac()
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "summarise this dump"},
        {"role": "assistant", "content": "reading"},
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "x" * 20000},
    ]
    before = len(api.req)

    result = api._prepare_messages()

    assert api._auto_compressed is True, "a 4-message history with a huge tool result was not compacted"
    assert len(result) < before or result is not api.req


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_short_small_history_is_still_left_untouched(cls, compression_params):
    """The guard must not start compacting healthy short sessions."""
    compression_params(keep_frac=0.1, hard_cap=1.0, rounds=0)
    api = _bare(cls)
    api.token_max = 100000
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    result = api._prepare_messages()

    assert result is api.req
    assert api._auto_compressed is False


# ── invariant: the trigger runs on measured, not assumed, token sizes ────


def test_providers_feed_reported_usage_into_the_calibration():
    """The base-class maths is useless if no provider ever reports a sample."""
    root = Path(__file__).resolve().parents[1] / "src" / "opensquad"
    for name in ("chat_api.py", "claude_api.py"):
        assert "record_token_calibration(" in (root / name).read_text(encoding="utf-8"), (
            f"{name} no longer feeds provider-reported usage into the token calibration"
        )


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_calibration_learns_the_real_to_estimate_ratio(cls):
    api = _bare(cls)
    sent = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "word " * 100},
    ]
    assert api._calibrated_token_scale() == (1.0, False)

    api.record_token_calibration(400, sent)

    scale, calibrated = api._calibrated_token_scale()
    assert calibrated is True
    estimated = api._count_tokens(sent, api._last_tools)
    assert scale == pytest.approx(400 / estimated, rel=0.01)


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_calibration_ignores_unusable_samples(cls):
    """A wild or missing sample must not become compression policy."""
    api = _bare(cls)
    sent = [{"role": "user", "content": "hello"}]

    api.record_token_calibration(0, sent)
    api.record_token_calibration(None, sent)
    api.record_token_calibration("nonsense", sent)
    assert api._calibrated_token_scale() == (1.0, False), "an empty sample calibrated the estimate"

    # Clamped, smoothed, and never zero/negative.
    for _ in range(20):
        api.record_token_calibration(10**9, sent)
    scale, calibrated = api._calibrated_token_scale()
    assert calibrated and 1.0 < scale <= 5.0


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_calibrated_scale_drives_the_compression_trigger(cls, compression_params):
    """Under-counted tokens (CJK case) must still trigger compaction.

    The trigger compares the local estimate against the window, so a measured
    scale of 4x (roughly the Chinese `len//4` error) has to be enough on its own:
    the fixture is sized to pass both uncalibrated checks and fail only once the
    measured ratio is applied.
    """
    compression_params(trigger=0.75, keep_frac=0.1, hard_cap=1.0, rounds=0)
    api = _bare(cls)
    api.token_max = 1000
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u" * 160},
        {"role": "assistant", "content": "a" * 160},
        {"role": "user", "content": "v" * 160},
        {"role": "assistant", "content": "b" * 160},
    ]
    soft = int(api.token_max * 0.75)
    guard = int(api.token_max * 0.85)
    raw = api.get_current_token_count(api._last_tools)
    assert raw <= soft and raw * 3 <= guard, "fixture already compacts while uncalibrated"
    assert raw * 4 > soft, "fixture does not cross the soft threshold at 4x"

    api._prepare_messages()
    assert api._auto_compressed is False, "sanity: the uncalibrated estimate looks fine"

    api.record_token_calibration(raw * 4, list(api.req))
    api._prepare_messages()

    assert api._auto_compressed is True, "a 4x under-count did not trigger compaction"


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


# ── invariant: cache-hit prompt tokens are read the same way everywhere ──
#
# The context panel's "cache hit rate" is only as good as the counter behind
# it.  Each backend spells the field differently and none of them errors when
# it is absent, so a provider that forgets one spelling reports "0% cache"
# forever with no visible failure — the pre-refactor code did exactly that for
# DeepSeek (`prompt_cache_hit_tokens` was never read) and for Gemini
# (`cached_content_token_count` was never read).


class _OpenAIUsage:
    """``usage.prompt_tokens_details.cached_tokens`` — OpenAI, Ark, Gemini-compat."""

    def __init__(self, prompt: int, cached: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = 1
        self.prompt_tokens_details = type("_D", (), {"cached_tokens": cached})()


class _DeepSeekUsage:
    """``prompt_cache_hit_tokens`` is undeclared, so pydantic parks it in model_extra."""

    def __init__(self, prompt: int, hit: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = 1
        self.model_extra = {"prompt_cache_hit_tokens": hit}


class _GeminiUsage:
    """Native ``usage_metadata.cached_content_token_count``."""

    def __init__(self, prompt: int, cached: int) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = 1
        self.cached_content_token_count = cached


@pytest.mark.parametrize(
    "usage",
    [
        _OpenAIUsage(1000, 800),
        _DeepSeekUsage(1000, 800),
        _GeminiUsage(1000, 800),
        {"prompt_tokens_details": {"cached_tokens": 800}},
        {"prompt_cache_hit_tokens": 800},
    ],
    ids=["openai-sdk", "deepseek-extra", "gemini-native", "openai-dict", "deepseek-dict"],
)
def test_extract_cached_tokens_reads_every_provider_spelling(usage):
    assert pb.extract_cached_tokens(usage) == 800


@pytest.mark.parametrize(
    "usage",
    [None, {}, {"prompt_tokens_details": None}, {"prompt_cache_hit_tokens": "n/a"}],
    ids=["none", "empty-dict", "null-details", "garbage"],
)
def test_extract_cached_tokens_is_zero_not_an_exception(usage):
    """A provider that omits or mangles the field must not take the turn down."""
    assert pb.extract_cached_tokens(usage) == 0


def test_cache_miss_is_input_minus_hit_floored_at_zero():
    assert pb.cache_miss_tokens(1000, 800) == 200
    assert pb.cache_miss_tokens(1000, 0) == 1000
    # Stale counter after a provider-side eviction: never negative.
    assert pb.cache_miss_tokens(100, 500) == 0
    assert pb.cache_miss_tokens(None, None) == 0
    assert pb.cache_miss_tokens("n/a", 1) == 0


def _code_without_comments(path: Path) -> str:
    """Source with ``#`` comments dropped, string literals kept.

    Comments must go: they legitimately *name* the provider fields (e.g. the
    note above ``extract_cached_tokens``), and a scan that matched them would
    fail for the right fix and pass for the wrong one.
    """
    with path.open(encoding="utf-8") as fh:
        return " ".join(tok.string for tok in tokenize.generate_tokens(fh.readline) if tok.type != tokenize.COMMENT)


def test_only_the_base_knows_the_provider_cache_field_names():
    """No provider may re-implement the field lookup — that is how it drifted."""
    src_dir = Path(pb.__file__).resolve().parent
    base_src = _code_without_comments(src_dir / "_provider_base.py")
    assert "prompt_tokens_details" in base_src, "the extractor lost its OpenAI spelling"

    for name in ("chat_api.py", "claude_api.py", "google_api.py"):
        provider_src = _code_without_comments(src_dir / name)
        assert "prompt_tokens_details" not in provider_src, (
            f"{name} reads the provider-specific cache field directly — "
            "route it through _provider_base.extract_cached_tokens instead"
        )

    # The two OpenAI-compatible paths must actually *call* the extractor and
    # feed the counter.  Asserting the bare name would pass on the import line
    # alone, so match the call and the accumulation.
    for name, arg in (("chat_api.py", "stream_usage"), ("google_api.py", "usage")):
        compact = _code_without_comments(src_dir / name).replace(" ", "")
        assert f"extract_cached_tokens({arg})" in compact, (
            f"{name} stopped counting cache hits (extractor imported but unused)"
        )
        assert ".total_cache_read_tokens+=" in compact, f"{name} computes cache hits but never accumulates them"
    # Claude has its own pair (read + creation) but must keep feeding both counters.
    claude_src = _code_without_comments(src_dir / "claude_api.py")
    assert "total_cache_read_tokens += _cache_read" in claude_src


def test_every_token_stats_emitter_publishes_the_cache_split():
    """The panel reads input/cached/miss + provenance off `token_stats.session`.

    Both emitters must carry them: `runner._broadcast_token_stats` is the live
    one, `_runner/_output_handler.OutputHandler` is the extracted copy a future
    refactor will swap in — and wiring up a payload without provenance is
    exactly how "estimated 0" comes back as a confident 0.0%.
    """
    src_dir = Path(pb.__file__).resolve().parent
    for rel in ("runner.py", "_runner/_output_handler.py"):
        src = _code_without_comments(src_dir / rel)
        # `_code_without_comments` re-joins tokens with spaces, so compare compacted.
        compact = src.replace(" ", "")
        assert '"cache_read_tokens"' in src, rel
        assert '"cache_miss_tokens"' in src, rel
        assert "cache_miss_tokens(" in compact, f"{rel}: the split must use the shared clamp helper"
        # Provenance travels to the UI: without it a session whose usage was
        # estimated renders a confident 0.0% hit rate instead of "unavailable".
        assert '"usage_estimated"' in src, rel
        assert "has_estimated_usage(getattr(" in compact, rel


def test_streaming_requests_ask_the_provider_for_usage():
    """A stream only carries `usage` when the request opts in.

    Measured against the Ark endpoint this project is configured with
    (``glm-5.3-flash``): the same completion returns 66 chunks and
    ``usage=None`` without ``stream_options.include_usage``, and
    ``prompt_tokens_details.cached_tokens = 4800`` with it.  Without the
    opt-in every counter takes the local-tokenizer fallback, so
    ``total_cache_read_tokens`` can never leave 0 and the panel prints a
    fabricated 0% — the reported bug.
    """
    api, client = _run_one_turn()

    assert client.calls[0]["stream_options"] == {"include_usage": True}
    assert api.total_cache_read_tokens == 4800, "the usage chunk must be counted"
    assert api.total_input_tokens == 4827
    assert api.usage_reported_turns == 1
    assert api.usage_is_estimated() is False


def test_a_stream_without_usage_is_marked_estimated_not_zero():
    """The fallback still counts tokens, but must admit it cannot know the split."""
    api, _client = _run_one_turn(emit_usage=False)

    assert api.total_cache_read_tokens == 0
    assert api.usage_estimated_turns == 1
    assert api.usage_reported_turns == 0
    assert api.usage_is_estimated() is True, "a 0 here is 'unknown', not 'no cache hits'"


def test_endpoints_that_reject_stream_options_still_complete_the_turn():
    """A strict proxy must cost us usage stats, never the answer.

    The stub 400s while ``stream_options`` is present, so the turn only
    succeeds if the parameter is dropped and the request retried.  Such an
    endpoint never emits usage either, hence the estimate that follows.
    """
    api, client = _run_one_turn(emit_usage=False, reject_stream_options=True)

    turn1 = client.calls
    assert len(turn1) >= 2, "the request must be retried without the parameter"
    assert "stream_options" in turn1[0]
    assert "stream_options" not in turn1[-1], "the parameter must be dropped on retry"
    assert api._stream_options_rejected is True
    assert api.usage_is_estimated() is True, "retrying is not free: no usage came back"

    # And the decision sticks: a later turn must not pay the 400 again.
    before = len(client.calls)
    _run_one_turn(api=api, client=client)
    assert "stream_options" not in client.calls[before], "the rejection must be remembered across turns"


def test_model_switch_keeps_the_usage_provenance():
    """Swapping the model mid-session must not launder estimated turns.

    Fence on the intent, not on the wording: the reload has to carry the
    counters through the one shared transfer (a hand-rolled field list is what
    drifts), and the provenance fields have to be **on** that list — a session
    that already contains estimated turns must keep reporting the hit rate as
    unavailable instead of silently starting to print one.
    """
    src = _code_without_comments(Path(pb.__file__).resolve().parent / "model_switch.py").replace(" ", "")
    assert "transfer_usage_counters(chat_api,new_api)" in src
    assert "usage_reported_turns" in pb.USAGE_COUNTER_FIELDS
    assert "usage_estimated_turns" in pb.USAGE_COUNTER_FIELDS


def test_a_session_with_an_estimated_turn_cannot_claim_a_hit_rate():
    """`usage_is_estimated` / `has_estimated_usage` is the gate the UI keys off.

    The pure helper is what `runner` applies to a duck-typed chat client's
    counters, so it must survive junk the same way `cache_miss_tokens` does.
    """
    api = ProviderAPIBase()
    api._init_provider_base()
    assert api.usage_is_estimated() is False, "a fresh client has nothing estimated yet"

    api.usage_reported_turns = 3
    api.usage_estimated_turns = 1
    assert api.usage_is_estimated() is True

    api.usage_estimated_turns = 0
    assert api.usage_is_estimated() is False

    assert pb.has_estimated_usage(1) is True
    assert pb.has_estimated_usage(0) is False
    assert pb.has_estimated_usage(None) is False
    assert pb.has_estimated_usage("n/a") is False
    assert pb.has_estimated_usage("2") is True


def test_ark_usage_shape_yields_a_real_hit_rate():
    """Numbers captured from the live Ark endpoint (cached 4800 of 4827)."""
    usage = _OpenAIUsage(4827, 4800)
    hit = pb.extract_cached_tokens(usage)
    assert hit == 4800
    assert pb.cache_miss_tokens(4827, hit) == 27
    # The panel's denominator is hit+miss, which must reconstruct the prompt.
    assert hit + pb.cache_miss_tokens(4827, hit) == 4827


# ─────────────────── streaming usage: a real turn, stubbed transport ──────────
#
# The counters above are only reachable if the request asks for usage, so these
# drive the actual `ChatAPI.chat()` against a stub client rather than scanning
# source.  A source scan cannot tell "the parameter is sent" apart from "the
# parameter was deleted from a branch nothing executes".


class _StubDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.reasoning_content = None
        self.tool_calls = None
        self.audio = None


class _StubChoice:
    def __init__(self, content: str | None, finish: str | None) -> None:
        self.delta = _StubDelta(content)
        self.finish_reason = finish
        self.index = 0


class _StubChunk:
    def __init__(self, content: str | None = None, usage: object = None, finish: str | None = None) -> None:
        # A usage-only chunk carries an EMPTY choices list, exactly like the
        # OpenAI-compatible streaming protocol.
        self.choices = [_StubChoice(content, finish)] if (content is not None or finish) else []
        self.usage = usage


class _StubUsage:
    """The Ark shape captured live: cached 4800 of a 4827-token prompt."""

    def __init__(self) -> None:
        self.prompt_tokens = 4827
        self.completion_tokens = 5
        self.total_tokens = 4832
        self.prompt_tokens_details = type("_D", (), {"cached_tokens": 4800})()
        self.model_extra: dict = {}


class _BadRequestError(Exception):
    """400 from a strict proxy: the parameter name it did not recognise."""


class _StubClient:
    def __init__(self, emit_usage: bool, reject_stream_options: bool) -> None:
        self.calls: list[dict] = []
        self.emit_usage = emit_usage
        self.reject_stream_options = reject_stream_options
        # Mimic the SDK's `client.chat.completions.create` nesting.
        self.chat = type("_Chat", (), {"completions": self})()

    async def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.reject_stream_options and "stream_options" in kwargs:
            # Bounded: after three rejections the stub plays along, so a
            # regression that never drops the parameter fails the assertions
            # instead of hanging the suite in a retry loop.
            if sum("stream_options" in c for c in self.calls) <= 3:
                raise _BadRequestError("400 Bad Request - Unknown parameter: 'stream_options'")

        emit_usage = self.emit_usage

        async def gen():
            yield _StubChunk("Hello", finish="stop")
            if emit_usage:
                yield _StubChunk(usage=_StubUsage())

        return gen()


def _run_one_turn(*, emit_usage: bool = True, reject_stream_options: bool = False, api=None, client=None):
    """Drive one real ChatAPI turn over the stub transport.

    Runs its own event loop, so it stays a plain sync test under
    ``asyncio_mode = auto`` without leaking a loop into the shared fixtures.
    Pass *api*/*client* to run a second turn against the same pair.
    """
    import asyncio

    if api is None:
        api = ChatAPI(api_key="sk-test", model="glm-5.3-flash", prompt="You are a test.")
        api.base_url = "https://ark.cn-beijing.volces.com/api/coding/v3"
    if client is None:
        client = _StubClient(emit_usage=emit_usage, reject_stream_options=reject_stream_options)
    # Bypass the real SDK entirely — no network, no credentials.
    api._ensure_client = lambda: client

    result = asyncio.run(api.chat("hi"))
    assert result["text"] == "Hello", result
    return api, client
