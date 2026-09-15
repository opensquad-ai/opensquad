"""Shared fixtures for OpenSquad unit tests."""

# ---------------------------------------------------------------------------
# Workspace isolation — MUST execute before the first `opensquad` import below.
#
# opensquad._syscfg._workspace resolves the workspace root from
# OPENSQUAD_WORKSPACE / OPENSQUAD_USER_DATA / OPENSQUAD_APP_DATA **at import
# time** and caches it in a module global (`_WORKSPACE_ROOT`). Without an
# override it falls back to the real runtime workspace
# (`<...>/opensquad_runtime_deploy`), so the module-level singletons created on
# import — `session_manager = SessionManager()`, `state_manager = ...` — point
# their save/history dirs at the developer's LIVE data.
#
# The consequences were both real and reproducible:
#   * running pytest rewrote real runtime data, notably
#     `data/sessions/current_session.json`, `data/ai_his_talk/*.json` and
#     `data/collab_board/{plan_history,snapshots}/*`;
#   * tests therefore read whatever that state happened to contain, so the same
#     file could pass in isolation and fail in a full run, or pass on one run
#     and fail on the next (disk state is not reset between runs).
#
# Pointing the workspace at a throwaway directory fixes both. This mirrors the
# mechanism the runtime itself uses (see `cli/runtime_boot.py`), and is why
# `_resolve_initial_workspace()` documents that it honours these vars "so
# import-time side effects (e.g. session_manager creating data/sessions/) never
# touch the install dir".
# ---------------------------------------------------------------------------
import os as _os
import tempfile as _tempfile

TEST_WORKSPACE = _tempfile.mkdtemp(prefix="opensquad-pytest-")
_os.environ["OPENSQUAD_WORKSPACE"] = TEST_WORKSPACE

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from opensquad._context import AgentContext, reset_current_context, set_current_context

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

    # Make the context visible to the code under test (AgentRunner discovers it
    # via get_current_context()), but never leak it.
    #
    # This fixture used to call set_current_context(ctx) and return, with no
    # teardown. A ContextVar.set() persists for the whole lifetime of the test
    # context, so the first test to use this fixture left its MagicMock bundle
    # installed for the remainder of the session. Every later test that resolved
    # a service through the context -- get_message_queue(), get_input_hub(),
    # session/state managers -- silently received those stale mocks instead of
    # the real singletons. That is why test_parallel_sessions,
    # test_propose_options and test_chat_api passed in isolation yet failed in a
    # full-suite run.
    token = set_current_context(ctx)
    try:
        yield ctx
    finally:
        reset_current_context(token)


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


def _known_failure_ids() -> set[str]:
    path = os.path.join(os.path.dirname(__file__), "known_failures.txt")
    if not os.path.isfile(path):
        return set()
    out: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if line:
                out.add(line.replace("\\", "/"))
    return out


def pytest_collection_modifyitems(config, items):
    """Xfail documented legacy failures so new regressions fail the gate."""
    known = _known_failure_ids()
    if not known:
        return
    mark = pytest.mark.xfail(
        reason="listed in tests/known_failures.txt",
        strict=True,
        run=True,
    )
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if nodeid in known or nodeid.split("::", 1)[0] in known:
            item.add_marker(mark)


def pytest_sessionfinish(session, exitstatus):
    """Delete the throwaway workspace created at import time.

    Best-effort: a sandboxed filesystem may refuse the recursive delete (its
    guard raises before returning), and failing teardown must never turn a green
    run red. The path is printed so it can be inspected when that happens.
    """
    import shutil

    try:
        shutil.rmtree(TEST_WORKSPACE, ignore_errors=True)
    except BaseException:  # noqa: BLE001 - teardown must not mask the test result
        print(f"[conftest] could not remove test workspace: {TEST_WORKSPACE}")
