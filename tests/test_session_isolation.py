"""多会话隔离：执行独立 + 状态隔离。

这里锁住两条本该成立、但此前并不成立的保证：

1. **执行独立** —— 一个会话在跑，不能阻止另一个会话启动。
   调度器过去会 park 在正在跑的会话上
   （``hub.push(...)`` + ``await wait_session_free(sid, 5.0)``），
   于是单个忙 pane 会拖住整个调度器（其它 pane 在它整轮期间都无法启动）。
2. **状态隔离** —— 一个会话不能改掉另一个会话的工作目录，
   per-turn 计数器也不能共享。

除显式标注为「结构性」的断言外，其余全部是行为断言（驱动真实对象）。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import opensquad.tools.filesystem as filesystem
import opensquad.tools.system as system_mod
import opensquad.utils.path_utils as path_utils
from opensquad.input_hub import InputHub
from opensquad.session_parallel import (
    ParallelTurnScheduler,
    TurnLocal,
    reset_turn_local,
    set_turn_local,
)
from opensquad.utils.session_cwd import (
    read_session_cwd,
    session_cwd_path,
    write_session_cwd,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "src" / "opensquad"


class _FakeShell:
    """Minimal ``ShellSession`` surface used by the recycle loop."""

    def __init__(self, working_directory: str = "", ui_sid: str = ""):
        self.working_directory = working_directory
        self.ui_sid = ui_sid
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_process_wide_cwd():
    """No test may inherit — or leak — another test's process-wide cwd."""
    path_utils.set_session_cwd_override(None)
    path_utils._SESSION_CWD_BY_SID.clear()
    saved_allowed = list(path_utils._EXTRA_ALLOWED_DIRS)
    filesystem._CONFIG_PATH = None
    filesystem._EXTRA_ALLOWED_DIRS = []
    yield
    path_utils.set_session_cwd_override(None)
    path_utils._SESSION_CWD_BY_SID.clear()
    path_utils._EXTRA_ALLOWED_DIRS = saved_allowed
    filesystem._EXTRA_ALLOWED_DIRS = []


@pytest.fixture()
def projects(tmp_path: Path):
    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    a.mkdir()
    b.mkdir()
    return a, b


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


# ---------------------------------------------------------------------------
# 1. 会话级 cwd
# ---------------------------------------------------------------------------


def test_each_session_resolves_its_own_workspace_root(projects, monkeypatch):
    """Two panes, two projects, one process → two roots, no bleed."""
    a, b = projects
    monkeypatch.setattr(system_mod, "_SESSIONS", {})

    assert filesystem.set_session_cwd(str(a), session_id="sess-a")["status"] == "success"
    assert filesystem.set_session_cwd(str(b), session_id="sess-b")["status"] == "success"

    tok_a = set_turn_local(TurnLocal(sid="sess-a"))
    try:
        assert path_utils.get_workspace_root() == _norm(a)
    finally:
        reset_turn_local(tok_a)

    tok_b = set_turn_local(TurnLocal(sid="sess-b"))
    try:
        assert path_utils.get_workspace_root() == _norm(b)
    finally:
        reset_turn_local(tok_b)

    # And the first session is still itself afterwards — no last-writer-wins.
    tok_a2 = set_turn_local(TurnLocal(sid="sess-a"))
    try:
        assert path_utils.get_workspace_root() == _norm(a)
    finally:
        reset_turn_local(tok_a2)


def test_a_sessions_project_is_invisible_without_its_turn(projects, monkeypatch):
    """Backward compatibility: the sid-less (CLI / serial) view is unchanged.

    Without a turn-local, ``get_workspace_root()`` must fall back to the legacy
    process-wide value — a per-session project must not leak into it.
    """
    a, b = projects
    monkeypatch.setattr(system_mod, "_SESSIONS", {})

    filesystem.set_session_cwd(str(a))  # legacy, no session id
    filesystem.set_session_cwd(str(b), session_id="sess-b")

    assert path_utils.get_workspace_root() == _norm(a)
    assert path_utils.get_session_cwd_for("sess-b") == _norm(b)


def test_cwd_change_recycles_only_the_owning_sessions_shells(projects, monkeypatch):
    """One pane's folder change must not kill another pane's running terminal."""
    a, b = projects
    mine = _FakeShell(working_directory=str(b), ui_sid="sess-a")  # stale for A
    theirs = _FakeShell(working_directory=str(b), ui_sid="sess-b")  # B's own
    unattributed = _FakeShell(working_directory=str(b), ui_sid="")  # CLI / serial
    monkeypatch.setattr(
        system_mod,
        "_SESSIONS",
        {"mine": mine, "theirs": theirs, "unattributed": unattributed},
    )

    filesystem.set_session_cwd(str(a), session_id="sess-a")

    assert mine.closed is True
    assert theirs.closed is False, "a sibling session's shell was killed by another session's cwd change"
    assert unattributed.closed is True, "an unattributed (CLI/serial) shell must follow the cwd"
    assert list(system_mod._SESSIONS) == ["theirs"]


def test_legacy_cwd_change_still_ignores_ownership(projects, monkeypatch):
    """The sid-less call keeps its old semantics: nothing is attributed."""
    a, b = projects
    s1 = _FakeShell(working_directory=str(b), ui_sid="pane-a")
    s2 = _FakeShell(working_directory=str(b), ui_sid="pane-b")
    monkeypatch.setattr(system_mod, "_SESSIONS", {"s1": s1, "s2": s2})

    filesystem.set_session_cwd(str(a))

    assert s1.closed is True and s2.closed is True


def test_signal_file_is_per_session_with_agent_level_fallback(tmp_path: Path):
    """The folder-picker signal must be per session; the agent file is a fallback."""
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    assert session_cwd_path(str(agent_dir), "sess-a") != session_cwd_path(str(agent_dir))

    write_session_cwd(str(agent_dir), str(a), session_id="sess-a")
    write_session_cwd(str(agent_dir), str(b), session_id="sess-b")

    assert read_session_cwd(str(agent_dir), "sess-a")["path"] == os.path.abspath(str(a))
    assert read_session_cwd(str(agent_dir), "sess-b")["path"] == os.path.abspath(str(b))
    # No file of its own and no agent-level file → nothing to apply.
    assert read_session_cwd(str(agent_dir), "sess-c") is None

    # Agent-level file is the fallback, and never overrides a session's own.
    write_session_cwd(str(agent_dir), str(b))
    assert read_session_cwd(str(agent_dir), "sess-a")["path"] == os.path.abspath(str(a))
    assert read_session_cwd(str(agent_dir), "sess-c")["path"] == os.path.abspath(str(b))


def test_session_ids_with_path_separators_do_not_collide(tmp_path: Path):
    """Sanitising must not let two real session ids share one signal file."""
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)

    p1 = session_cwd_path(str(agent_dir), "team/a")
    p2 = session_cwd_path(str(agent_dir), "team:a")
    assert p1 != p2
    assert os.path.basename(p1) != os.path.basename(p2)


# ---------------------------------------------------------------------------
# 2. 调度：不因为一个会话在跑就挡住别的会话
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_any_excludes_a_busy_sessions_normal_inbox_only():
    """Exclusion skips a running session's queue — and only its normal one."""
    hub = InputHub()
    hub.push("a-queued", source="test", session_id="sess-a")
    hub.push("b-now", source="test", session_id="sess-b")

    got = await hub.wait_any(timeout=0.2, exclude_sids={"sess-a"})
    assert got is not None and got[0] == "sess-b", "the free session had to wait behind the busy one"
    assert got[1]["content"] == "b-now"

    # Its urgent inbox is never excluded — a Stop cannot be starved.
    hub.push_urgent("__STOP__", source="test", session_id="sess-a")
    urgent = await hub.wait_any(timeout=0.2, exclude_sids={"sess-a"})
    assert urgent is not None and urgent[0] == "sess-a"
    assert urgent[1]["content"] == "__STOP__"

    # Once unexcluded, the deferred message is served.
    later = await hub.wait_any(timeout=0.2, exclude_sids=set())
    assert later is not None and later[0] == "sess-a"
    assert later[1]["content"] == "a-queued"


@pytest.mark.asyncio
async def test_an_excluded_session_alone_does_not_spin():
    """With nothing else to serve, the wait must block, not busy-loop."""
    hub = InputHub()
    hub.push("a-queued", source="test", session_id="sess-a")

    assert await hub.wait_any(timeout=0.05, exclude_sids={"sess-a"}) is None
    # Still queued (not dropped) — the exclusion is a delay, not a discard.
    assert hub.peek_session_pending("sess-a") is True


@pytest.mark.asyncio
async def test_scheduler_wakes_waiters_when_a_turn_finishes():
    """A finishing turn must free a slot and wake the dispatcher immediately."""
    sched = ParallelTurnScheduler(max_parallel=1)
    woke: list[int] = []
    sched.set_on_free(lambda: woke.append(1))
    hold = asyncio.Event()

    async def linger():
        await hold.wait()

    assert sched.has_capacity() is True
    assert await sched.acquire_slot("sess-a") is True
    sched.start("sess-a", linger())
    assert sched.has_capacity() is False, "a running turn must consume the only slot"
    assert woke == [], "nothing finished yet"

    hold.set()
    await asyncio.sleep(0.05)
    assert woke, "the dispatcher was never told the slot freed"
    assert sched.has_capacity() is True


def test_parallel_turn_applies_its_own_session_cwd():
    """结构性：per-turn 的 cwd 应用必须带上该 turn 的 sid。

    并且 ``wait_any`` 不能再替**所有**会话轮询信号文件 —— 那正是
    「另一个 pane 动一下文件夹选择器，本 pane 就被换根」的来源。
    """
    runner_src = (APP_ROOT / "runner.py").read_text(encoding="utf-8", errors="replace")
    block = runner_src[runner_src.index("async def _parallel_session_turn") :][:20000]
    assert "input_hub._check_session_cwd(sid)" in block

    hub_src = (APP_ROOT / "input_hub.py").read_text(encoding="utf-8", errors="replace")
    wait_block = hub_src[hub_src.index("async def wait_any") : hub_src.index("def peek_session_pending")]
    assert "_check_session_cwd()" not in wait_block
    assert "exclude_sids" in wait_block


# ---------------------------------------------------------------------------
# 3. per-turn 状态不再共享
# ---------------------------------------------------------------------------


def test_per_turn_counters_are_not_shared_between_sessions(application_context):
    """These three counters were bare instance attributes → cross-pane clobber.

    They are *per turn*, not per session: each turn builds a fresh ``TurnLocal``,
    so the invariant is that two turns running at the same time cannot read or
    overwrite each other's value — the classic lost-update at an ``await``.
    """
    from opensquad.runner import AgentRunner

    runner = AgentRunner(
        chat_api=application_context.chat_api,
        tool_registry=application_context.tool_registry,
        agent_context=application_context,
    )

    tl_a = TurnLocal(sid="sess-a")
    tl_b = TurnLocal(sid="sess-b")

    tok_a = set_turn_local(tl_a)
    try:
        runner._format_error_streak = 3
        runner._repetition_rewind_count = 2
        runner._auth_fallback_used = True

        # The sibling turn, interleaved.
        tok_b = set_turn_local(tl_b)
        try:
            assert runner._format_error_streak == 0, "session B inherited session A's format-error streak"
            assert runner._repetition_rewind_count == 0
            assert runner._auth_fallback_used is False
            runner._format_error_streak = 7
        finally:
            reset_turn_local(tok_b)

        # Back in A: its own values survived B's write untouched.
        assert runner._format_error_streak == 3, "session A's counter was clobbered by a concurrent session"
        assert runner._repetition_rewind_count == 2
        assert runner._auth_fallback_used is True
        assert tl_b.format_error_streak == 7
    finally:
        reset_turn_local(tok_a)


# ---------------------------------------------------------------------------
# 4. Stop 的作用域
# ---------------------------------------------------------------------------


def test_a_session_started_after_a_stop_is_not_born_stopped(monkeypatch):
    """A global Stop must not black-hole the next session's first message."""
    import opensquad.sub_agent_runner as sub_agent_runner

    class _JM:
        def cancel_all(self, reason: str = "aborted") -> int:
            return 0

    class _Sched:
        busy_sessions = {"sess-a"}

        def request_stop_session(self, sid: str) -> None:
            pass

    class _Runner:
        _parallel_scheduler = _Sched()

    monkeypatch.setattr(sub_agent_runner, "job_manager", _JM())
    monkeypatch.setattr(system_mod, "abort_all_tool_processes", lambda *a, **k: {})
    monkeypatch.setattr("opensquad.runner._active_runner", _Runner())

    hub = InputHub()
    hub.request_stop()

    # The session that was running when Stop was raised is stopped …
    assert hub.is_session_stop_requested("sess-a") is True
    # … a session created afterwards is not.
    assert hub.is_session_stop_requested("sess-new") is False
    # A sid-less caller keeps the old "any Stop means stopped" answer.
    assert hub.is_session_stop_requested("") is True

    hub.clear_stop_request()
    assert hub.is_session_stop_requested("sess-a") is True, "per-session latch must survive"
    assert hub.is_session_stop_requested("sess-new") is False
    hub.clear_session_stop("sess-a")
    assert hub.is_session_stop_requested("sess-a") is False
