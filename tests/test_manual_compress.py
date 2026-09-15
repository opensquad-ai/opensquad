"""Locks for manual context compression ("压缩上下文") in BOTH run modes.

Regression this file exists for
------------------------------
The Agent Web UI runs ``run_parallel_dispatcher`` (multi-pane). Its
``_handle_agent_level_command`` answered ``__COMPRESS_CONTEXT__`` with

    "[Runner] __COMPRESS_CONTEXT__ ignored on parallel dispatcher"

so clicking the button produced no summary, no error and -- the part that made
it look broken -- **no terminal frame**. The frontend only clears its optimistic
"Generating context summary..." block on ``summary_stream {done:true}`` /
``compression_progress {is_final:true}``, so the spinner stayed up forever.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from opensquad import runner as runner_mod
from opensquad._runner import _manual_compress
from opensquad.session_manager import SessionManager

# ── fixtures / fakes ───────────────────────────────────────────────────────


@pytest.fixture
def sm(tmp_path):
    return SessionManager(
        save_dir=str(tmp_path / "sessions"),
        history_dir=str(tmp_path / "history"),
    )


@pytest.fixture
def use_sm(monkeypatch, sm):
    """Make the module-level singleton resolver hand back our temp manager."""
    monkeypatch.setattr("opensquad.session_manager.get_session_manager", lambda: sm)
    return sm


class _FakeScheduler:
    def __init__(self, busy: set[str] | None = None):
        self._busy = set(busy or ())

    def is_session_busy(self, sid: str) -> bool:
        return sid in self._busy


class _FakeChatAPI:
    def __init__(self):
        self.base_url = "http://summarizer.invalid/v1"
        self.api_key = "sk-test"
        self.model = "test-model"
        self._latest_summary = ""


class _FakeRunner:
    """Only the surface ``compress_session_context`` touches."""

    def __init__(self, *, busy: set[str] | None = None):
        self.chat_api = _FakeChatAPI()
        self._parallel_scheduler = _FakeScheduler(busy)
        self._current_round = 3
        self._agent_id = "agent-test"
        self._agent_dir = None
        self._workspace_dir = None
        self.emitted: list[tuple[str, Any, str | None]] = []

    async def _emit(self, etype, data, *, sid=None):
        self.emitted.append((etype, data, sid))

    async def _broadcast_token_stats(self, sid=None):
        self.emitted.append(("token_stats", {"sid": sid}, sid))

    # helpers for assertions
    def types(self) -> list[str]:
        return [t for t, _d, _s in self.emitted]

    def frames(self, etype: str) -> list[dict]:
        return [d for t, d, _s in self.emitted if t == etype]


def _seed_session(sm: SessionManager, sid: str, *, messages: int = 60) -> None:
    """Give *sid* enough history that compression actually trims something."""
    for i in range(messages):
        sm.add_message("user" if i % 2 == 0 else "assistant", f"{sid} message {i} " + "x" * 200, sid=sid)


# ── SessionManager.compress_current_session(sid=...) ──────────────────────


class TestCompressTargetsOneSession:
    async def test_sid_none_still_targets_focused_session(self, sm):
        sm.add_message("user", "focused hello")
        result = sm.compress_current_session(external_summary="SUMMARY")

        assert result["compressed"] is True
        assert result["sid"] == sm.get_current_session_id()
        assert sm.session_data["latest_summary"] == "SUMMARY"
        assert os.path.exists(sm.current_session_file)

    async def test_sid_compresses_only_that_session(self, sm, tmp_path):
        sm.add_message("user", "focused hello")
        focused_before = json.dumps(sm.session_data["messages"], ensure_ascii=False)
        _seed_session(sm, "pane-B")

        result = sm.compress_current_session(external_summary="B SUMMARY", sid="pane-B")

        assert result["compressed"] is True
        assert result["sid"] == "pane-B"

        # Focused session is untouched — no cross-pane bleed.
        assert json.dumps(sm.session_data["messages"], ensure_ascii=False) == focused_before
        assert sm.session_data["latest_summary"] != "B SUMMARY"

        # The target pane's history file carries the summary + archived tail.
        hist = tmp_path / "history" / "pane-B.json"
        assert hist.exists()
        data = json.loads(hist.read_text(encoding="utf-8"))
        assert data["latest_summary"] == "B SUMMARY"
        assert data["archived_messages"], "tail should be archived, not deleted"

    async def test_non_focused_compress_does_not_rewrite_current_session_file(self, sm, tmp_path):
        sm.add_message("user", "focused hello")
        sm._save_session()
        current_before = (tmp_path / "sessions" / "current_session.json").read_text(encoding="utf-8")

        _seed_session(sm, "pane-C")
        sm.compress_current_session(external_summary="C SUMMARY", sid="pane-C")

        current_after = (tmp_path / "sessions" / "current_session.json").read_text(encoding="utf-8")
        assert current_before == current_after
        assert "C SUMMARY" not in current_after


# ── compress_session_context(...) ─────────────────────────────────────────


class TestCompressSessionContext:
    async def test_success_streams_and_terminates(self, use_sm, monkeypatch):
        _seed_session(use_sm, "pane-A")
        runner = _FakeRunner()

        async def _fake_summarizer(payload, *, base_url, api_key, model, on_chunk=None):
            for part in ("## Current Task\n", "doing things"):
                if on_chunk:
                    await on_chunk(part)
            return "## Current Task\ndoing things"

        monkeypatch.setattr(_manual_compress, "run_external_summarizer", _fake_summarizer)

        result = await _manual_compress.compress_session_context(runner, sid="pane-A", emit=runner._emit)

        assert result["compressed"] is True
        assert result["sid"] == "pane-A"

        # Deltas then exactly one terminal done frame (the frontend's clear signal).
        streams = runner.frames("summary_stream")
        assert any(d.get("delta") for d in streams), "no streamed deltas"
        assert streams[-1].get("done") is True

        progress = runner.frames("compression_progress")
        assert progress and progress[-1]["is_final"] is True

        syncs = runner.frames("history_sync")
        assert syncs and syncs[-1]["reason"] == "compression"
        assert syncs[-1]["session_id"] == "pane-A"

        # Every frame is scoped to the target pane so parallel panes stay isolated.
        assert {s for _t, _d, s in runner.emitted} == {"pane-A"}
        assert "token_stats" in runner.types()

    async def test_empty_session_terminates_without_touching_disk(self, use_sm, monkeypatch):
        runner = _FakeRunner()
        monkeypatch.setattr(
            _manual_compress,
            "run_external_summarizer",
            lambda *a, **k: pytest.fail("summarizer must not run for an empty session"),
        )

        result = await _manual_compress.compress_session_context(runner, sid="pane-empty", emit=runner._emit)

        assert result == {"compressed": False, "sid": "pane-empty", "reason": "empty"}
        assert runner.frames("summary_stream")[-1].get("done") is True
        assert runner.frames("compression_progress")[-1]["is_final"] is True

    async def test_busy_session_skips_and_says_so(self, use_sm, monkeypatch):
        _seed_session(use_sm, "pane-busy")
        runner = _FakeRunner(busy={"pane-busy"})
        monkeypatch.setattr(
            _manual_compress,
            "run_external_summarizer",
            lambda *a, **k: pytest.fail("must not summarize a busy pane"),
        )

        result = await _manual_compress.compress_session_context(runner, sid="pane-busy", emit=runner._emit)

        assert result["reason"] == "busy"
        assert runner.frames("summary_stream")[-1].get("done") is True
        skipped = [d for d in runner.frames("info") if d.get("event") == "context_compress_skipped"]
        assert skipped, "a refused compression must explain itself"

    async def test_summarizer_failure_never_stays_silent(self, use_sm, monkeypatch):
        _seed_session(use_sm, "pane-D")
        runner = _FakeRunner()

        async def _boom(*a, **k):
            raise RuntimeError("upstream 402")

        monkeypatch.setattr(_manual_compress, "run_external_summarizer", _boom)

        result = await _manual_compress.compress_session_context(runner, sid="pane-D", emit=runner._emit)

        assert result["compressed"] is False
        assert "upstream 402" in result["error"]
        assert runner.frames("summary_stream")[-1].get("done") is True
        assert runner.frames("summary_stream")[-1].get("error")
        assert runner.frames("compression_progress")[-1]["is_final"] is True

    async def test_falls_back_to_focused_session_when_sid_missing(self, use_sm, monkeypatch):
        _seed_session(use_sm, use_sm.get_current_session_id())
        runner = _FakeRunner()
        monkeypatch.setattr(_manual_compress, "run_external_summarizer", _async_return("## Current Task\nfocused"))

        result = await _manual_compress.compress_session_context(runner, sid="", emit=runner._emit)

        assert result["sid"] == use_sm.get_current_session_id()


def _async_return(value):
    async def _inner(*a, **k):
        return value

    return _inner


# ── runner wiring: the actual regression ──────────────────────────────────


class TestParallelDispatcherWiring:
    async def test_compress_context_is_not_dropped(self, monkeypatch):
        """The parallel branch must CALL the compressor, not warn and return."""
        calls: list[dict] = []

        async def _fake_compress(runner, *, sid=None, emit=None):
            calls.append({"runner": runner, "sid": sid, "emit": emit})
            return {"compressed": True}

        monkeypatch.setattr(_manual_compress, "compress_session_context", _fake_compress)

        r = runner_mod.AgentRunner.__new__(runner_mod.AgentRunner)

        async def _emit(etype, data, *, sid=None):
            pass

        r._emit = _emit
        await r._handle_agent_level_command({"content": "__COMPRESS_CONTEXT__", "session_id": "sid-abc"})

        assert len(calls) == 1
        assert calls[0]["sid"] == "sid-abc"
        assert calls[0]["emit"] is _emit

    async def test_compress_context_without_sid_uses_focused_session(self, monkeypatch, sm):
        # runner._get_session_manager() resolves `opensquad.session_manager.session_manager`
        # (the module attribute), not get_session_manager() — patch the former, and
        # clear any runner injected by a sibling test so the singleton path is used.
        monkeypatch.setattr("opensquad.session_manager.session_manager", sm)
        monkeypatch.setattr(runner_mod, "_active_runner", None, raising=False)
        sm.load_history_session(sm.get_current_session_id())

        seen: list[str] = []

        async def _fake_compress(runner, *, sid=None, emit=None):
            seen.append(sid)
            return {"compressed": False}

        monkeypatch.setattr(_manual_compress, "compress_session_context", _fake_compress)

        r = runner_mod.AgentRunner.__new__(runner_mod.AgentRunner)

        async def _emit(etype, data, *, sid=None):
            pass

        r._emit = _emit
        await r._handle_agent_level_command({"content": "__COMPRESS_CONTEXT__"})

        assert seen == [sm.get_current_session_id()]

    async def test_old_drop_path_is_gone(self):
        """Guard against a revert to 'ignore on parallel dispatcher'."""
        import inspect

        src = inspect.getsource(runner_mod.AgentRunner._handle_agent_level_command)
        assert "ignored on parallel dispatcher" not in src
        assert "compress_session_context" in src
