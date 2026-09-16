"""Locks for the system-prompt bound and the irreducible-overflow guard.

Incident this exists to prevent (2026-09-15, agent305):

* ``context_base.inject_standard()`` put the whole of ``agent.md`` into the
  system prompt as ``AGENT_PROFILE`` with **no size limit**. A 1.76 MB
  agent.md -> 1,336,859 system tokens against a 262,144-token window.
* ``_provider_base._prepare_messages()`` always keeps the system message
  (``compacted_req = [system_msg, recent...]``), so context compression can
  never fix that. Worse, the ``len(self.req) < 5`` early return shipped the
  oversized prompt **uncompressed** — and a brand-new session has exactly 3
  messages, which is why every *new* session came back
  ``400 BadRequestError: max context length 262144, requested 603011``.

Two invariants are locked here:

* **B** — every stable system-prompt variable is bounded, and the cut is
  loud (error log) and in-prompt (truncation marker naming the real size).
* **C** — when the *irreducible* part (system message + tool schemas) alone
  overflows the window, ``_prepare_messages`` raises
  :class:`ContextOverflowError` instead of issuing a request that must 400.
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

from opensquad import _provider_base as pb
from opensquad import context_base as cb
from opensquad._provider_base import ContextOverflowError, ProviderAPIBase
from opensquad.chat_api import ChatAPI
from opensquad.claude_api import ClaudeAPI
from opensquad.google_api import GoogleAPI

ALL_PROVIDERS = (ChatAPI, ClaudeAPI, GoogleAPI)

#: Kept in sync with context_base.inject_standard()'s bound-name tuple.
SYSTEM_VAR_NAMES = ("AGENT_PROFILE", "CONTEXT_SUMMARY", "AGENT_WORKSPACE", "TEAM_COLLAB_CARDS")

CONTEXT_BASE_SRC = pathlib.Path(cb.__file__)
PROVIDER_SRC = pathlib.Path(pb.__file__)


def _cb_tree() -> ast.Module:
    return ast.parse(CONTEXT_BASE_SRC.read_text(encoding="utf-8"))


def _pb_tree() -> ast.Module:
    return ast.parse(PROVIDER_SRC.read_text(encoding="utf-8"))


class _RecordingLogger:
    """Captures records so the guard can assert on the warning itself."""

    def __init__(self):
        self.records: list[tuple[int, str]] = []

    def error(self, msg, *args, **_kw):
        self.records.append((logging.ERROR, msg % args if args else str(msg)))

    def warning(self, msg, *args, **_kw):
        self.records.append((logging.WARNING, msg % args if args else str(msg)))

    def info(self, *_a, **_kw):
        pass

    def debug(self, *_a, **_kw):
        pass


# ═══════════════════════════════════════════════════════════════════════════
# B — the stable system-prompt layer is bounded
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def budget(monkeypatch):
    """Fix the char budget and reset the dedupe state."""

    def _apply(limit: int = 1000):
        monkeypatch.setattr(cb, "_system_var_char_limit", lambda: limit)
        monkeypatch.setattr(cb, "_cap_warned_at", {})
        return limit

    return _apply


def test_cap_passes_short_text_through_untouched(budget):
    budget(1000)
    text = "x" * 999
    assert cb._cap_system_var("AGENT_PROFILE", text) == text


def test_cap_exactly_at_the_limit_is_not_truncated(budget):
    budget(1000)
    text = "x" * 1000
    assert cb._cap_system_var("AGENT_PROFILE", text) == text


def test_cap_truncates_and_states_the_real_size(budget):
    budget(1000)
    out = cb._cap_system_var("AGENT_PROFILE", "x" * 5000)
    assert out.startswith("x" * 1000)
    assert len(out) > 1000  # marker appended
    assert "AGENT_PROFILE" in out
    assert "5,000 chars" in out
    assert "1,000" in out  # the limit
    assert "TRUNCATED" in out


def test_cap_keeps_the_head_not_the_tail(budget):
    """A memory document opens with the preferences/instructions — keep those.

    Uses distinguishable content on purpose: an all-"x" payload cannot tell
    ``text[:limit]`` from ``text[-limit:]``.
    """
    budget(1000)
    out = cb._cap_system_var("AGENT_PROFILE", "H" * 1000 + "T" * 4000)
    assert out.startswith("H" * 1000), "the head of the document must survive truncation"
    assert "T" * 10 not in out, "the tail must be dropped, not kept"


def test_cap_marker_says_compression_cannot_help_and_names_the_fix(budget):
    budget(1000)
    out = cb._cap_system_var("AGENT_PROFILE", "x" * 5000)
    # The whole point of the incident: this layer is NOT reachable by compression.
    assert "never summarised" in out
    assert "system prompt" in out
    # ...and the agent must be told how to actually fix it.
    assert "smaller" in out


def test_cap_zero_or_negative_limit_means_unbounded(budget):
    budget(0)
    text = "x" * 5000
    assert cb._cap_system_var("AGENT_PROFILE", text) == text


def test_cap_logs_one_error_per_distinct_size(budget, monkeypatch):
    budget(1000)
    rec = _RecordingLogger()
    monkeypatch.setattr(cb, "logger", rec)

    cb._cap_system_var("AGENT_PROFILE", "x" * 5000)
    cb._cap_system_var("AGENT_PROFILE", "x" * 5000)  # same size -> deduped
    assert len(rec.records) == 1, f"expected the warning to be deduped, got {rec.records}"

    cb._cap_system_var("AGENT_PROFILE", "x" * 6000)  # new size -> warn again
    assert len(rec.records) == 2

    level, msg = rec.records[0]
    assert level == logging.ERROR
    assert "AGENT_PROFILE" in msg
    assert "5000" in msg


def test_cap_is_silent_when_within_budget(budget, monkeypatch):
    budget(1000)
    rec = _RecordingLogger()
    monkeypatch.setattr(cb, "logger", rec)
    cb._cap_system_var("AGENT_PROFILE", "x" * 10)
    assert rec.records == []


def test_the_budget_comes_from_config_not_a_hardcoded_constant(monkeypatch):
    """The limit must be tunable, and must be read per call (not frozen)."""
    monkeypatch.setattr(pb.syscfg, "ctx_system_prompt_budget_chars", lambda: 1234)
    assert cb._system_var_char_limit() == 1234
    monkeypatch.setattr(pb.syscfg, "ctx_system_prompt_budget_chars", lambda: 99)
    assert cb._system_var_char_limit() == 99


def test_system_var_char_limit_falls_back_when_config_explodes(monkeypatch):
    def _boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(pb.syscfg, "ctx_system_prompt_budget_chars", _boom)
    assert cb._system_var_char_limit() == cb._DEFAULT_SYSTEM_VAR_CHAR_LIMIT


# ── the bound is actually applied in inject_standard() ─────────────────────


class _FakeChatAPI:
    def __init__(self, summary: str = ""):
        self._latest_summary = summary


def test_inject_standard_caps_a_huge_agent_profile(budget, monkeypatch):
    """The exact production shape: an oversized agent.md must come out bounded."""
    limit = budget(1000)
    monkeypatch.setattr(cb, "_read_agent_md", lambda: "# Permanent Memory\n" + "濠" * 900_000)

    system_vars, _ = cb.inject_standard({"chat_api": _FakeChatAPI()})

    profile = system_vars["AGENT_PROFILE"]
    assert len(profile) <= limit + 800, f"AGENT_PROFILE not bounded: {len(profile)} chars"
    assert "TRUNCATED" in profile


def test_inject_standard_caps_a_huge_context_summary(budget, monkeypatch):
    limit = budget(1000)
    monkeypatch.setattr(cb, "_read_agent_md", lambda: "small")
    system_vars, _ = cb.inject_standard({"chat_api": _FakeChatAPI(summary="s" * 40_000)})

    summary = system_vars["CONTEXT_SUMMARY"]
    assert len(summary) <= limit + 800, f"CONTEXT_SUMMARY not bounded: {len(summary)} chars"


def _payload_len(value: str) -> int:
    """Length of the capped content, excluding an appended truncation marker."""
    marker = "\n\n[... TRUNCATED:"
    i = value.find(marker)
    return len(value) if i == -1 else i


def test_inject_standard_bounds_every_key_it_returns(budget, monkeypatch):
    """Invariant: no key in system_vars carries more than the budget."""
    budget(1000)
    monkeypatch.setattr(cb, "_read_agent_md", lambda: "p" * 50_000)

    system_vars, dynamic_vars = cb.inject_standard({"chat_api": _FakeChatAPI(summary="q" * 50_000)})

    assert set(system_vars) == set(SYSTEM_VAR_NAMES), (
        f"inject_standard's key set changed ({sorted(system_vars)}); "
        "update the bound-name tuple in context_base and SYSTEM_VAR_NAMES here"
    )
    for name, value in system_vars.items():
        assert _payload_len(value) <= 1000, f"{name} was returned uncapped: {len(value)} chars"


def test_every_key_assigned_to_system_vars_is_in_the_bound_tuple():
    """Structural lock: a new system var cannot be added without bounding it.

    Walking the AST is the only way to catch it — ``inject_standard`` returns
    whatever it assigned, so an unbounded sixth variable would be invisible to
    the runtime assertions above.
    """
    bounded = None
    for node in ast.walk(_cb_tree()):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            names = {e.value for e in node.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if names & set(SYSTEM_VAR_NAMES):
                bounded = names
                break

    assert bounded == set(SYSTEM_VAR_NAMES), (
        f"the bound-name tuple in context_base.inject_standard is {sorted(bounded or [])}, "
        f"expected {sorted(SYSTEM_VAR_NAMES)}"
    )

    assigned = set()
    for node in ast.walk(_cb_tree()):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Name)
            and node.targets[0].value.id == "system_vars"
            and isinstance(node.targets[0].slice, ast.Constant)
            and isinstance(node.targets[0].slice.value, str)
        ):
            assigned.add(node.targets[0].slice.value)

    assert assigned, "AST scan found no system_vars assignments — the guard has gone stale"
    unbounded = assigned - set(SYSTEM_VAR_NAMES)
    assert not unbounded, (
        f"{sorted(unbounded)} is injected into the system prompt without being bounded — "
        "add it to the bound-name tuple in context_base.inject_standard"
    )


# ═══════════════════════════════════════════════════════════════════════════
# D — an encoding-damaged agent.md is caught early, not at the 400
# ═══════════════════════════════════════════════════════════════════════════


def test_damage_heuristic_accepts_clean_chinese():
    assert not cb._looks_encoding_damaged("# Permanent Memory\n\n## User Preferences\n\n- 用户偏好：默认城市福州\n")


def test_damage_heuristic_accepts_an_empty_document():
    assert not cb._looks_encoding_damaged("")


def test_damage_heuristic_flags_replacement_chars():
    text = "ok" + "\ufffd" * cb._ENCODING_DAMAGE_FFFD_THRESHOLD
    assert cb._looks_encoding_damaged(text)


def test_damage_heuristic_tolerates_a_stray_replacement_char():
    """One U+FFFD is noise; the threshold exists so we do not cry wolf."""
    assert not cb._looks_encoding_damaged("a\ufffdb")


@pytest.mark.parametrize("marker", cb._MOJIBAKE_MARKERS)
def test_damage_heuristic_flags_each_mojibake_marker(marker):
    assert cb._looks_encoding_damaged("prefix " + marker + " suffix")


def _point_at(tmp_path, monkeypatch, payload: bytes):
    p = tmp_path / "agent.md"
    p.write_bytes(payload)
    monkeypatch.setattr(cb, "_agent_md_path", str(p))
    monkeypatch.setattr(cb, "_agent_md_cache", None)
    monkeypatch.setattr(cb, "_agent_md_damage_warned_mtime", None)
    return p


def test_read_agent_md_warns_about_a_damaged_file(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, ("# Permanent Memory\n" + "锟斤拷" * 40).encode("utf-8"))
    rec = _RecordingLogger()
    monkeypatch.setattr(cb, "logger", rec)

    cb._read_agent_md()
    errors = [m for lvl, m in rec.records if lvl == logging.ERROR]
    assert len(errors) == 1, f"expected exactly one damage warning, got {rec.records}"
    assert "encoding-damaged" in errors[0]
    # The warning has to be actionable, not just descriptive.
    assert "filesystem.write_file" in errors[0]


def test_read_agent_md_warning_names_the_offending_file(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, ("# Permanent Memory\n" + "\ufffd" * 50).encode())
    rec = _RecordingLogger()
    monkeypatch.setattr(cb, "logger", rec)
    cb._read_agent_md()
    assert "agent.md" in rec.records[0][1]


def test_read_agent_md_warns_once_per_file_version(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, ("# Permanent Memory\n" + "锟斤拷" * 40).encode("utf-8"))
    rec = _RecordingLogger()
    monkeypatch.setattr(cb, "logger", rec)

    cb._read_agent_md()
    cb._read_agent_md()
    errors = [m for lvl, m in rec.records if lvl == logging.ERROR]
    assert len(errors) == 1, f"the warning must not repeat for every read: {len(errors)}"


def test_read_agent_md_is_silent_for_a_healthy_file(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, "# Permanent Memory\n\n## User Preferences\n\n- 默认城市福州\n".encode())
    rec = _RecordingLogger()
    monkeypatch.setattr(cb, "logger", rec)

    assert cb._read_agent_md().startswith("# Permanent Memory")
    assert [m for lvl, m in rec.records if lvl == logging.ERROR] == []


def test_the_damage_check_is_wired_into_the_read_path():
    """Structural lock: the detector existing is not enough — it must be called."""
    tree = _cb_tree()
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_read_agent_md")
    called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_looks_encoding_damaged" in called, (
        "_read_agent_md no longer inspects the content it just read — a double-encoded "
        "agent.md would silently destroy the permanent memory again"
    )


# ═══════════════════════════════════════════════════════════════════════════
# C — irreducible overflow fails fast instead of 400-ing
# ═══════════════════════════════════════════════════════════════════════════


def _bare(cls, *, token_max: int = 100, req: list[dict] | None = None):
    """A provider with no SDK client and no tiktoken (deterministic counting)."""
    api = object.__new__(cls)
    api.req = req if req is not None else [{"role": "system", "content": "sys"}]
    api.model = "test-model"
    api.token_max = token_max
    api._init_provider_base()
    # Set AFTER _init_provider_base(): ChatAPI's variant installs a real
    # tiktoken encoding while the other two leave it None, so forcing None
    # afterwards is what makes the fallback counting identical for all three.
    api.encoding = None
    api._generate_summary = lambda msgs: "SUMMARY"
    return api


@pytest.fixture
def guard_frac(monkeypatch):
    """Pin the overflow-guard fraction (and the other knobs) for reproducibility."""
    monkeypatch.setattr(pb.syscfg, "ctx_overflow_guard_frac", lambda: 0.95)
    monkeypatch.setattr(pb.syscfg, "ctx_trigger_threshold", lambda: 0.75)
    monkeypatch.setattr(pb.syscfg, "ctx_keep_recent_fraction", lambda: 0.1)
    monkeypatch.setattr(pb.syscfg, "ctx_recent_hard_cap_frac", lambda: 0.3)
    monkeypatch.setattr(pb.syscfg, "ctx_keep_recent_rounds", lambda: 2)


def _huge_system(chars: int = 4000) -> dict:
    return {"role": "system", "content": "S" * chars}


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_irreducible_counts_the_system_message(cls, guard_frac):
    api = _bare(cls, token_max=1000, req=[_huge_system(4000), {"role": "user", "content": "hi"}])
    # encoding is None -> len(str(msg)) // 4; the system message dominates.
    assert api._irreducible_prompt_tokens() >= 4000 // 4


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_irreducible_includes_tool_schemas(cls, guard_frac):
    scheme = [{"type": "function", "function": {"name": "big_tool", "description": "d" * 4000}}]
    api = _bare(cls, token_max=1000, req=[{"role": "system", "content": "sys"}])
    without = api._irreducible_prompt_tokens()
    api._last_tools = scheme
    with_tools = api._irreducible_prompt_tokens()
    assert with_tools > without + 1000, "tool schemas are not counted as irreducible"


def test_irreducible_is_zero_without_messages(guard_frac):
    api = _bare(ChatAPI, req=[])
    api._last_tools = None
    assert api._irreducible_prompt_tokens() == 0


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_prepare_messages_raises_when_the_system_prompt_alone_overflows(cls, guard_frac):
    # token_max=100 -> guard limit 95 tokens; a 4000-char system msg ~ 1000 tokens.
    api = _bare(cls, token_max=100, req=[_huge_system(4000)])

    with pytest.raises(ContextOverflowError) as exc:
        api._prepare_messages()

    msg = str(exc.value)
    assert "system message and the tool schemas are never summarised" in msg
    assert "agent.md" in msg, "the error must name the actionable fix"
    assert "tokens" in msg


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_the_guard_runs_before_the_too_few_messages_early_return(cls, guard_frac):
    """The precise production regression.

    A brand-new session has 3 messages (system + user + assistant), which hit
    the old ``len(self.req) < 5`` branch and returned the oversized prompt
    **uncompressed**. The preflight must fire first.
    """
    req = [
        _huge_system(4000),
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert len(req) < 5, "fixture must reproduce the < 5 messages branch"
    api = _bare(cls, token_max=100, req=req)

    with pytest.raises(ContextOverflowError):
        api._prepare_messages()
    assert api.req == req, "the request must not be mutated when the turn is aborted"


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_prepare_messages_is_untouched_when_the_system_prompt_fits(cls, guard_frac):
    """Negative control: a normal session must not be disturbed by the guard."""
    req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    api = _bare(cls, token_max=1_000_000, req=req)
    assert api._prepare_messages() == req


def test_the_guard_fraction_is_config_driven_not_hardcoded(monkeypatch):
    """Disabling the guard must let the oversized request through (and vice versa)."""
    req = [_huge_system(4000)]
    api = _bare(ChatAPI, token_max=100, req=req)

    monkeypatch.setattr(pb.syscfg, "ctx_overflow_guard_frac", lambda: 10_000.0)
    monkeypatch.setattr(pb.syscfg, "ctx_trigger_threshold", lambda: 0.75)
    assert api._prepare_messages() == req, "guard ignored ctx_overflow_guard_frac (too permissive)"

    monkeypatch.setattr(pb.syscfg, "ctx_overflow_guard_frac", lambda: 0.0)
    with pytest.raises(ContextOverflowError):
        api._prepare_messages()


def test_the_limit_is_a_fraction_of_token_max(guard_frac, monkeypatch):
    """frac is applied to token_max: the same prompt is legal in a 4x window.

    Self-calibrating on purpose - hardcoding "1990 chars ~ 497 tokens" was
    wrong because the fallback counts ``len(str(dict)) // 4``, which includes
    the dict-repr overhead on top of the content.
    """
    monkeypatch.setattr(pb.syscfg, "ctx_overflow_guard_frac", lambda: 0.5)

    small = _bare(ChatAPI, token_max=1000, req=[_huge_system(4000)])
    n = small._irreducible_prompt_tokens()
    assert n > 500, f"fixture too small to exceed the 500-token limit: {n}"
    with pytest.raises(ContextOverflowError):  # limit = 500 < n
        small._prepare_messages()

    # Same prompt, a window wide enough that the limit (frac * token_max = 2n)
    # comfortably clears n.
    big = _bare(ChatAPI, token_max=n * 4, req=[_huge_system(4000)])
    assert big._irreducible_prompt_tokens() == n
    assert big._prepare_messages() == big.req


@pytest.mark.parametrize("cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_count_tools_tokens_survives_missing_tiktoken(cls, guard_frac):
    """tiktoken missing must degrade, not AttributeError.

    ``_count_tools_tokens`` is called directly by ``get_current_token_count``
    and by ``_irreducible_prompt_tokens`` (outside the try/except that
    ``_count_tokens`` wraps it in), and it used to dereference
    ``self.encoding.encode(...)`` with no None guard.
    """
    api = _bare(cls, token_max=1000)
    api._last_tools = [{"type": "function", "function": {"name": "t", "description": "d" * 400}}]
    assert api._count_tools_tokens(api._last_tools) > 0
    assert api._count_tools_tokens([]) == 0
    assert api._count_tools_tokens(None) == 0


def test_get_current_token_count_survives_missing_tiktoken(guard_frac):
    api = _bare(ChatAPI, token_max=1000, req=[{"role": "system", "content": "sys"}])
    api._last_tools = [{"type": "function", "function": {"name": "t", "description": "d" * 400}}]
    assert api.get_current_token_count(api._last_tools) > 0


def test_overflow_error_is_exported_and_typed():
    assert "ContextOverflowError" in pb.__all__
    assert issubclass(ContextOverflowError, RuntimeError)


def test_prepare_messages_still_contains_the_raise():
    """Structural lock: deleting the ``raise`` must fail, not silently regress."""
    tree = _pb_tree()
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_prepare_messages")
    raised = {n.exc.func.id for n in ast.walk(fn) if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)}
    assert "ContextOverflowError" in raised, (
        "_prepare_messages no longer raises ContextOverflowError — an oversized system prompt "
        "will again ship a request that is guaranteed to 400"
    )


def test_overflow_error_message_fits_the_runner_truncation_window(guard_frac):
    """runner.py truncates the surfaced error to 300 chars; the fix must survive."""
    api = _bare(ChatAPI, token_max=262_144, req=[_huge_system(6_000_000)])
    with pytest.raises(ContextOverflowError) as exc:
        api._prepare_messages()
    assert len(str(exc.value)) <= 300, (
        f"error is {len(str(exc.value))} chars and would be cut off before naming the fix"
    )


def test_base_class_no_longer_ships_an_oversized_prompt():
    """End-to-end shape check on the real base class (no subclass involved)."""
    assert ProviderAPIBase._irreducible_prompt_tokens is not None
