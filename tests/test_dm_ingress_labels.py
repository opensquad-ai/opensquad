"""A direct message is ONE turn, and it says it is a DM.

Regression guard for 2026-10-06 (agent305): `bridge` delivered every DM twice —
once through the message queue (source ``dm``) and once straight onto the primary
session (source ``chatpro``) — so the model answered the same DM twice and, asked
which channel the message had come in on, answered "Agent Web" for one of them.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import opensquad.bridge as bridge_module
import opensquad.ingress_policy as ingress_module
import opensquad.message_queue as mq_module
import opensquad.sleep_controller as sleep_module
import opensquad.state_manager as state_module
from opensquad.ingress_policy import chatpro_ingress_labels


class _FakeQueue:
    """Stands in for the module-level message_queue singleton."""

    def __init__(self):
        self.puts: list = []

    async def put(self, msg) -> bool:
        self.puts.append(msg)
        return True


def _dm_payload(text: str = "ping") -> dict:
    return {
        "id": "dm-1",
        "sender_id": "human-1",
        "sender_name": "ss",
        "content": text,
        "timestamp": 1.0,
        "attachments": [],
    }


def _run_dm(monkeypatch, state: str, text: str = "ping"):
    """Feed one DM through the bridge; return (pushed, sentinels, queued)."""
    pushed: list = []
    sentinels: list = []
    queue = _FakeQueue()

    monkeypatch.setattr(state_module.state_manager, "get_state", AsyncMock(return_value=state))
    monkeypatch.setattr(sleep_module.sleep_controller, "wake_up", lambda *a, **k: None)
    monkeypatch.setattr(mq_module, "message_queue", queue)
    monkeypatch.setattr(ingress_module, "push_ingress", lambda content, **kw: pushed.append((content, kw)) or "sid")
    monkeypatch.setattr(ingress_module, "trigger_process_queue", lambda **kw: sentinels.append(kw) or "sid")

    b = bridge_module.ChatProBridge(base_url="http://127.0.0.1:9", agent_name="t")
    b.user_id = "ai-1"  # not the sender, so the DM is not filtered as our own
    asyncio.run(b._handle_message({"type": "new_direct_message", "data": _dm_payload(text)}))
    return pushed, sentinels, queue.puts


# ── bridge: exactly one delivery, labelled dm ────────────────────────────────


def test_awake_dm_is_delivered_once_and_labelled_dm(monkeypatch):
    pushed, sentinels, queued = _run_dm(monkeypatch, "working")

    assert len(queued) == 0, "an awake agent must not also get the DM through the queue"
    assert len(sentinels) == 0
    assert len(pushed) == 1
    content, kw = pushed[0]
    assert kw["source"] == "dm", "a DM must not be labelled as the group source"
    assert kw["channel"] == "chatpro_dm"
    assert content == "[DM] ss: ping"


def test_sleeping_dm_is_queued_then_drained_by_the_sentinel(monkeypatch):
    pushed, sentinels, queued = _run_dm(monkeypatch, "sleeping")

    assert len(queued) == 1 and queued[0].type == "dm"
    assert len(pushed) == 0, "the queue carries the content; the hub must not carry it too"
    assert len(sentinels) == 1 and sentinels[0]["channel"] == "chatpro_dm"


# ── the shared label helper ──────────────────────────────────────────────────


def test_chatpro_labels_pure_dm_is_a_dm():
    assert chatpro_ingress_labels(["dm", "dm"]) == ("dm", "chatpro_dm")


def test_chatpro_labels_group_and_mixed_keep_the_group_label():
    assert chatpro_ingress_labels(["group"]) == ("chatpro", "chatpro_group")
    # A batch holding both cannot be described by one label; the reply targets
    # default to the group, so the group label wins.
    assert chatpro_ingress_labels(["group", "dm"]) == ("chatpro", "chatpro_group")
    assert chatpro_ingress_labels([]) == ("chatpro", "chatpro_group")


# ── the model must be told the channel ───────────────────────────────────────


def test_runtime_state_names_the_channel():
    from opensquad.context_base import inject_standard

    _, dynamic = inject_standard(
        {
            "query": "which channel is this?",
            "source": "dm",
            "channel": "chatpro_dm",
            "current_state": "idle",
            "current_wake": "strict",
        }
    )
    assert "Input source: dm (channel: chatpro_dm)" in dynamic["RUNTIME_STATE"]


def test_runtime_state_without_a_channel_is_unchanged():
    from opensquad.context_base import inject_standard

    _, dynamic = inject_standard({"query": "q", "source": "web", "current_state": "idle", "current_wake": "strict"})
    assert "Input source: web\n" in dynamic["RUNTIME_STATE"]
    assert "channel:" not in dynamic["RUNTIME_STATE"]
