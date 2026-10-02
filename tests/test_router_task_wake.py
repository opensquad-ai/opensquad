"""A message the gateway addressed to an agent must reach it in strict mode.

The task window writes to a board; its messages carried no @mention of their own, so a
strict-mode agent (coder/qa keep strict by design) discarded them — the user typed in the
window and the agent only found out when it happened to poll the board.
"""

from __future__ import annotations

import asyncio

import pytest

import opensquad.message_router as mr


class _State:
    def __init__(self, state: str, mode: str):
        self._state, self._mode = state, mode

    async def get_state(self):
        return self._state

    async def get_wake_mode(self):
        return self._mode


class _Queue:
    async def put(self, msg):
        return True

    def has_pending(self):
        return False


class _Sleep:
    def is_sleeping(self):
        return False

    async def wake_up(self, *a, **k):
        return True


@pytest.fixture()
def router(monkeypatch):
    monkeypatch.setattr(mr, "get_state_manager", lambda: _State("working", "strict"))
    monkeypatch.setattr(mr, "get_message_queue", lambda: _Queue())
    monkeypatch.setattr(mr, "get_sleep_controller", lambda: _Sleep())
    monkeypatch.setattr(mr, "trigger_process_queue", lambda *a, **k: None)
    return mr.message_router


def _frame(**over):
    msg = {
        "id": "m1",
        "group_id": "g-1",
        "sender_name": "ss",
        "content": "[System] Message from the user in a collaboration task",
    }
    msg.update(over)
    return msg


def test_a_strict_agent_still_discards_a_plain_frame(router):
    res = asyncio.run(router.route_group_message(_frame(id="m-plain")))

    assert res["action"] == "filtered"
    assert "not @-mentioned" in res["reason"]


def test_a_frame_the_gateway_addressed_to_the_agent_is_kept(router):
    """`wake` is the gateway saying "this one is for you" (a task-window message)."""
    res = asyncio.run(
        router.route_group_message(
            _frame(id="m-wake", content="[System] task message: 现在能看到吗", wake=True, channel="task")
        )
    )

    assert res["action"] != "filtered"
    assert res["queued"] is True
