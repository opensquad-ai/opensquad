"""P2/P3 fix verification for opensquad.chat_api (history trim pairing).

Run: python -m pytest tests/test_chat_api_p2.py -q
"""

from opensquad.chat_api import ChatAPI


def _make_api(max_history=10):
    """Build a ChatAPI with a tiny history cap so trimming triggers fast."""
    api = ChatAPI.__new__(ChatAPI)
    api.req = [{"role": "system", "content": "sys"}]
    api._MAX_HISTORY_MESSAGES = max_history
    api._cached_token_count = 42
    return api


def test_trim_does_not_split_tool_pair():
    """An assistant(tool_calls) + its tool result must be removed together."""
    api = _make_api(max_history=4)
    # system + user + assistant(tool_calls) + tool(result) + user + assistant
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "f"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    assert len(api.req) == 6
    api._trim_history_if_needed()

    # After trim, no assistant(tool_calls) should appear without a matching
    # tool message right after it.
    for i, msg in enumerate(api.req):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            nxt = api.req[i + 1] if i + 1 < len(api.req) else {}
            assert nxt.get("role") == "tool", f"orphan tool_calls at index {i}: next msg is {nxt.get('role')}"


def test_trim_keeps_system_prompt():
    api = _make_api(max_history=2)
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    api._trim_history_if_needed()
    assert api.req[0]["role"] == "system"
    assert api.req[0]["content"] == "sys"


def test_trim_noop_under_cap():
    api = _make_api(max_history=100)
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
    ]
    api._trim_history_if_needed()
    assert len(api.req) == 2  # unchanged


def test_trim_invalidates_token_cache():
    api = _make_api(max_history=2)
    api.req = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    api._cached_token_count = 42
    api._trim_history_if_needed()
    assert api._cached_token_count is None


class _FakeHttpx:
    """httpx.AsyncClient stand-in with a mutable is_closed flag."""

    def __init__(self, closed=False):
        self.is_closed = closed


class _FakeClient:
    """AsyncOpenAI stand-in wrapping an httpx client at `.client`."""

    def __init__(self, closed=False):
        self.client = _FakeHttpx(closed=closed)


def test_is_client_closed_detects_closed_underlying_httpx():
    """A closed underlying httpx pool must be reported so _ensure_client rebuilds.

    This is the regression guard for the "task stops after a tool call" bug:
    ``reload_model`` async-closes the old shared client, but ``self.client``
    still references it, so the next LLM streaming call fails with
    ``APIConnectionError ... client has been closed``.
    """
    api = _make_api()
    api.client = _FakeClient(closed=True)
    assert api._is_client_closed() is True

    api.client = _FakeClient(closed=False)
    assert api._is_client_closed() is False


def test_without_client_not_closed():
    api = _make_api()
    api.client = None
    assert api._is_client_closed() is False
