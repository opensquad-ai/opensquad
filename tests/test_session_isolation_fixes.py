"""Regression tests for parallel session isolation fixes."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from opensquad.context_builder import ContextBuilder
from opensquad.sub_agent_runner import SubAgentJobManager


class _FakeChatAPI:
    def __init__(self, sid: str, label: str):
        self._sid = sid
        self.label = label
        self.req: list = []
        self._system = f"system-{label}"

    def get_template(self) -> str:
        return f"template-{self.label}"

    def get_system_prompt(self) -> str:
        return self._system

    def update_system_prompt(self, prompt: str) -> None:
        self._system = prompt

    def _sid_provider(self):
        return self._sid


@pytest.mark.asyncio
async def test_context_builder_uses_explicit_chat_api_per_turn():
    """Parallel turns must not mutate shared ContextBuilder.chat_api."""
    strategy = MagicMock()
    strategy.prepare_llm_call.return_value = {
        "system_prompt": "base",
        "tools": None,
        "tool_choice": "auto",
    }
    task_manager = MagicMock()
    cb = ContextBuilder(
        chat_api=_FakeChatAPI("root", "root"),
        tool_call_strategy=strategy,
        task_manager=task_manager,
    )

    api_a = _FakeChatAPI("sid-a", "a")
    api_b = _FakeChatAPI("sid-b", "b")

    async def _build(api):
        return await cb.build(
            last_user_input="hello",
            current_input_source="web",
            current_turn=1,
            current_round=1,
            chat_api=api,
        )

    res_a, res_b = await asyncio.gather(_build(api_a), _build(api_b))
    assert api_a.get_system_prompt().startswith("base")
    assert api_b.get_system_prompt().startswith("base")
    assert cb.chat_api.label == "root"
    assert res_a[3] in (True, False)
    assert res_b[3] in (True, False)


def test_job_manager_cancel_by_sid_only_targets_matching_runner():
    mgr = SubAgentJobManager()

    class _Runner:
        def __init__(self, sid: str):
            self._sid = sid
            self._job_id = None
            self.aborted = False
            self.reason = ""

        def abort(self, reason: str) -> None:
            self.aborted = True
            self.reason = reason

    runner_a = _Runner("sid-a")
    runner_b = _Runner("sid-b")
    mgr.register_runner(runner_a)  # type: ignore[arg-type]
    mgr.register_runner(runner_b)  # type: ignore[arg-type]

    n = mgr.cancel_by_sid("sid-a", "stop_session")
    assert n == 1
    assert runner_a.aborted is True
    assert runner_b.aborted is False
