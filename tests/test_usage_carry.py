"""Locks for the mid-task model switch that erased the cache hit rate.

Regression this file exists for
------------------------------
Agent Web, 2026-09-25 (agent305): ``deepseek-v4.1-flash`` was switched to
``glm-5.3-flash`` in the middle of a task, and the context panel stopped showing
a cache hit rate at all.

The session had been billing against the **root** client.  Only
``_runner._parallel_session_turn`` calls ``session_model.bind_for_turn``, so a
serial session runs on ``runner.chat_api``.  ``switch_to_card`` then found no
per-session client for that pane and minted one with ``_clone_chat_api(root)``,
which copies model config but **starts every usage counter at 0**:

    root  : input=1000000 cache_read=800000 requests=50
    clone : input=0       cache_read=0      requests=0

``runner._chat_api_for_token_stats(sid)`` prefers the per-session client, so the
panel switched to reading the zeroed one.  With ``hit + miss == 0`` and no
output, ``SoloContextFooter`` returns ``null`` for the cache split and renders
no block at all — the hit rate does not read "0%", it stops existing.

The fix hands the baseline over to the new client.  It **moves** rather than
copies: the root's counters are the current session's (New Session rolls them
into ``_hist_*`` and zeroes them), so a copy left behind would bill those tokens
a second time on the next archive.
"""

from __future__ import annotations

import ast
from pathlib import Path

from opensquad._provider_base import USAGE_COUNTER_FIELDS, transfer_usage_counters
from opensquad.session_dispatcher import make_session_chat_api

SRC = Path(__file__).resolve().parents[1] / "src" / "opensquad"


class _FakeChatAPI:
    """Constructor surface ``_clone_chat_api`` drives (``type(base)`` + kwargs)."""

    def __init__(self, config=None, stream_parser=None, **kw):
        self.config = config
        self.stream_parser = stream_parser
        self.provider = "openai"
        self._sid_provider = None
        self._user_id_provider = None
        for field in USAGE_COUNTER_FIELDS:
            setattr(self, field, 0)


class _FakeRunner:
    def __init__(self, root):
        self._root_chat_api = root
        self.chat_api = root
        self._session_chat_apis = {}
        self._current_user_id = "u1"


HIST_FIELDS = (
    "_hist_input_tokens",
    "_hist_output_tokens",
    "_hist_requests",
    "_hist_cache_read_tokens",
    "_hist_cache_creation_tokens",
)


def _seed(root: _FakeChatAPI) -> _FakeChatAPI:
    """An hour of serial traffic on the root client."""
    root.total_input_tokens = 1_000_000
    root.total_output_tokens = 42_000
    root.total_requests = 50
    root.total_cache_read_tokens = 800_000
    root.usage_reported_turns = 50
    root.usage_estimated_turns = 0
    return root


def _billed(api) -> tuple[int, int, int]:
    """(hit, miss, output) exactly as ``runner._broadcast_token_stats`` computes."""
    hit = int(getattr(api, "total_cache_read_tokens", 0) or 0)
    inp = int(getattr(api, "total_input_tokens", 0) or 0)
    return hit, max(0, inp - hit), int(getattr(api, "total_output_tokens", 0) or 0)


# ── the pure transfer ──────────────────────────────────────────────────────


def test_every_billed_counter_moves():
    src, dst = _seed(_FakeChatAPI()), _FakeChatAPI()
    moved = transfer_usage_counters(src, dst)
    assert set(moved) == set(USAGE_COUNTER_FIELDS)
    for field in USAGE_COUNTER_FIELDS:
        assert getattr(dst, field) == getattr(src, field)


def test_copy_leaves_the_source_intact():
    """A cross-provider reload discards the old client, so copying is enough."""
    src, dst = _seed(_FakeChatAPI()), _FakeChatAPI()
    transfer_usage_counters(src, dst, clear_source=False)
    assert dst.total_cache_read_tokens == 800_000
    assert src.total_cache_read_tokens == 800_000


def test_move_zeroes_the_source():
    src, dst = _seed(_FakeChatAPI()), _FakeChatAPI()
    transfer_usage_counters(src, dst, clear_source=True)
    assert dst.total_input_tokens == 1_000_000
    assert src.total_input_tokens == 0
    assert src.total_cache_read_tokens == 0
    assert src.total_requests == 0


def test_a_field_the_source_lacks_is_left_alone_not_zeroed():
    """Duck-typed clients in tests/plugins must not be zeroed by accident."""
    src, dst = _FakeChatAPI(), _FakeChatAPI()
    del src.total_cache_read_tokens
    dst.total_cache_read_tokens = 123
    moved = transfer_usage_counters(src, dst)
    assert "total_cache_read_tokens" not in moved
    assert dst.total_cache_read_tokens == 123


def test_provenance_travels_with_the_numbers():
    """A session with estimated turns must keep reporting the rate as unknown."""
    src, dst = _FakeChatAPI(), _FakeChatAPI()
    src.usage_estimated_turns = 3
    transfer_usage_counters(src, dst)
    assert dst.usage_estimated_turns == 3


# ── re-homing the session onto its own client ──────────────────────────────


def test_a_serial_session_keeps_its_cache_split_when_it_gets_its_own_client():
    """The incident: the panel must still be able to compute a hit rate."""
    root = _seed(_FakeChatAPI())
    root._sid_provider = lambda: "sess-A"
    runner = _FakeRunner(root)

    clone = make_session_chat_api(runner, "sess-A")
    runner._session_chat_apis["sess-A"] = clone

    assert _billed(clone) == (800_000, 200_000, 42_000)
    # The baseline was handed over, not duplicated onto the root.
    assert root.total_cache_read_tokens == 0
    assert root.total_input_tokens == 0
    assert root.total_requests == 0


def test_the_minted_client_is_bound_to_its_own_session():
    root = _seed(_FakeChatAPI())
    root._sid_provider = lambda: "sess-A"
    api = make_session_chat_api(_FakeRunner(root), "sess-A")
    assert api._sid_provider() == "sess-A"


def test_the_root_keeps_its_counters_while_another_session_bills_on_it():
    """Do not rob a *different* session that is still using the root client."""
    root = _seed(_FakeChatAPI())
    root._sid_provider = lambda: "sess-OTHER"
    api = make_session_chat_api(_FakeRunner(root), "sess-A")
    assert api.total_cache_read_tokens == 0
    assert root.total_cache_read_tokens == 800_000


def test_the_baseline_is_not_counted_twice_once_the_root_is_archived():
    """Move, don't copy: New Session rolls the root into ``_hist_*``."""
    from opensquad.runner import AgentRunner

    root = _seed(_FakeChatAPI())
    root._sid_provider = lambda: "sess-A"
    runner = _FakeRunner(root)
    for field in HIST_FIELDS:
        setattr(runner, field, 0)

    clone = make_session_chat_api(runner, "sess-A")
    AgentRunner._reset_session_stats(runner)

    cumulative_read = runner._hist_cache_read_tokens + clone.total_cache_read_tokens
    assert cumulative_read == 800_000, "the same prompt tokens were billed twice"


# ── structural fences ──────────────────────────────────────────────────────


def _functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _calls_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _call_sites_of(func_name: str) -> set[tuple[str, str]]:
    """(module, enclosing function) for every call to *func_name* under src/."""
    sites: set[tuple[str, str]] = set()
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and func_name in _calls_in(node):
                sites.add((rel, node.name))
    return sites


def _body_text(path: Path, func_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    fn = _functions(path)[func_name]
    return "\n".join(text.splitlines()[fn.lineno - 1 : fn.end_lineno])


def test_only_the_single_factory_mints_a_session_client():
    """A second open-coded clone is precisely how this bug happened."""
    assert _call_sites_of("_clone_chat_api") == {("session_dispatcher.py", "make_session_chat_api")}


def test_the_factory_hands_the_baseline_over():
    assert "transfer_usage_counters" in _calls_in(_functions(SRC / "session_dispatcher.py")["make_session_chat_api"])


def test_both_re_homing_paths_go_through_the_factory():
    for rel, fn in (("model_switch.py", "switch_to_card"), ("session_model.py", "bind_for_turn")):
        calls = _calls_in(_functions(SRC / rel)[fn])
        assert "make_session_chat_api" in calls, f"{rel}::{fn} must mint the pane client via the factory"
        assert "_clone_chat_api" not in calls, f"{rel}::{fn} must not clone the root by hand"


def test_the_cross_provider_reload_uses_the_shared_transfer():
    body = _body_text(SRC / "model_switch.py", "apply_model_reload")
    assert "transfer_usage_counters(" in body
    # No hand-rolled per-field copying — that list is what drifted out of sync.
    assert "total_input_tokens =" not in body
    assert "usage_estimated_turns =" not in body
