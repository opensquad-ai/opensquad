"""Steer（引导注入）回归锁：忙时会话的插话不打断当前工具流，下一轮进模型上下文。

链路（全部复用既有 mid-turn supplement 机制，仅补三块能力）：
1. 前端忙时发送 → 普通 ``chat`` 帧（client_id=队列条目 id）→ gateway → adapter
   → input_hub 会话队列（不打断、不开新回合）。
2. runner turn 边界 ``_drain_parallel_session_supplements`` → event_pipeline
   （metadata 带 client_id）→ per-tool drain 随工具结果进模型上下文，同时
   回发 ``steer_consumed``（前端把引导气泡挪进时间线）。
3. 撤回/删除 → ``cancel_steer`` 命令 → 从 input_hub 会话队列或 event_pipeline
   桶移除（两段都可能持有，取决于消息走到哪一步）。
4. 兜底：模型直接输出正文收尾时（无工具调用、per-tool drain 不执行），
   ``_parallel_session_turn`` finally 把残留用户插话重新入队成正常回合。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opensquad.event_pipeline import EventPipeline, event_pipeline
from opensquad.input_hub import InputHub

APP_ROOT = Path(__file__).resolve().parents[1] / "src" / "opensquad"
RUNNER_SRC = (APP_ROOT / "runner.py").read_text(encoding="utf-8")
TURN_LOOP_SRC = (APP_ROOT / "_runner" / "_turn_loop.py").read_text(encoding="utf-8")
ADAPTER_SRC = (APP_ROOT / "gateway_adapter.py").read_text(encoding="utf-8")
WS_TS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "opensquad"
    / "gateway"
    / "nexuschat-pro"
    / "services"
    / "aiWebSocket.ts"
).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Behavioural: InputHub.cancel_session_item（撤回第一段：会话队列）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_session_item_removes_only_matching_client_id():
    hub = InputHub()
    hub.push("第一条", source="gateway", session_id="s1", client_id="m1")
    hub.push("第二条", source="gateway", session_id="s1", client_id="m2")

    assert hub.cancel_session_item("s1", "m1") is True

    pending = hub.get_session_pending("s1")
    assert [it["content"] for it in pending] == ["第二条"]
    assert pending[0]["client_id"] == "m2"
    # 再撤同一条应报 not-found（幂等保护）
    assert hub.cancel_session_item("s1", "m1") is False


@pytest.mark.asyncio
async def test_cancel_session_item_is_scoped_to_one_session():
    hub = InputHub()
    hub.push("A 的消息", source="gateway", session_id="sid-a", client_id="mx")
    assert hub.cancel_session_item("sid-b", "mx") is False
    assert hub.peek_session_pending("sid-a") is True


# --------------------------------------------------------------------------
# Behavioural: EventPipeline.cancel_user_event（撤回第二段：注入管道）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_user_event_removes_matching_and_keeps_others():
    ep = EventPipeline()
    ep.push_nowait("gateway", "插话一", metadata={"client_id": "m1"}, session_id="s2")
    ep.push_nowait("gateway", "插话二", metadata={"client_id": "m2"}, session_id="s2")
    ep.push_nowait("vision_tool", "", metadata={"action": "inject_images"}, session_id="s2")

    assert ep.cancel_user_event("s2", "m1") is True

    events = ep.drain_sync(session_id="s2")
    assert [e.content for e in events if e.source == "gateway"] == ["插话二"]
    # 内部事件（vision 注入）不受撤回影响
    assert any(e.source == "vision_tool" for e in events)


@pytest.mark.asyncio
async def test_cancel_user_event_never_touches_internal_events():
    ep = EventPipeline()
    ep.push_nowait("vision_tool", "", metadata={"client_id": "m1"}, session_id="s3")
    assert ep.cancel_user_event("s3", "m1") is False
    assert ep.size_for(session_id="s3") == 1


# --------------------------------------------------------------------------
# Behavioural: supplement drain carries client_id（消费回执的对账键）
# --------------------------------------------------------------------------


def _bare_runner():
    from opensquad.runner import AgentRunner
    from opensquad.session_parallel import TurnLocal

    r = object.__new__(AgentRunner)
    r._root_tl = TurnLocal()
    r._current_images = []
    r._current_attachments = []
    return r


@pytest.mark.asyncio
async def test_supplement_drain_carries_client_id_for_steer_receipt(monkeypatch):
    import opensquad.runner as runner_mod

    hub = InputHub()
    monkeypatch.setattr(runner_mod, "input_hub", hub)
    event_pipeline.drain_sync(session_id="sid-steer")

    hub.push("引导内容", source="gateway", session_id="sid-steer", client_id="msg-42")

    runner = _bare_runner()
    assert runner._drain_parallel_session_supplements("sid-steer") == 1

    events = event_pipeline.drain_sync(session_id="sid-steer")
    assert len(events) == 1
    assert events[0].metadata.get("client_id") == "msg-42"


# --------------------------------------------------------------------------
# Behavioural: adapter cancel_steer 命令（撤回贯穿两段队列）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_cancel_steer_removes_from_input_hub(monkeypatch):
    import opensquad.gateway_adapter as adapter_mod
    from opensquad.gateway_adapter import GatewayAdapter

    hub = InputHub()
    monkeypatch.setattr(adapter_mod, "input_hub", hub)
    event_pipeline.drain_sync(session_id="sid-ack")

    hub.push("还没进管道", source="gateway", session_id="sid-ack", client_id="m9")

    adapter = object.__new__(GatewayAdapter)
    adapter._user_id_by_sid = {}
    await adapter._handle_command(
        {"command": "cancel_steer", "user_id": "u1", "data": {"session_id": "sid-ack", "message_id": "m9"}}
    )

    assert hub.get_session_pending("sid-ack") == []


@pytest.mark.asyncio
async def test_adapter_cancel_steer_falls_back_to_event_pipeline(monkeypatch):
    import opensquad.gateway_adapter as adapter_mod
    from opensquad.gateway_adapter import GatewayAdapter

    hub = InputHub()
    monkeypatch.setattr(adapter_mod, "input_hub", hub)
    event_pipeline.drain_sync(session_id="sid-ack2")

    # 已过 turn 边界：input_hub 空，事件躺在 event_pipeline 桶里
    event_pipeline.push_nowait("gateway", "已过边界", metadata={"client_id": "m7"}, session_id="sid-ack2")

    adapter = object.__new__(GatewayAdapter)
    adapter._user_id_by_sid = {}
    await adapter._handle_command(
        {"command": "cancel_steer", "user_id": "u1", "data": {"session_id": "sid-ack2", "message_id": "m7"}}
    )

    assert event_pipeline.size_for(session_id="sid-ack2") == 0


# --------------------------------------------------------------------------
# Source-level locks（协议注册 + 关键接线）
# --------------------------------------------------------------------------


def test_steer_consumed_is_registered_end_to_end():
    from opensquad.protocol_version import (
        AGENT_OUTPUT_BROADCAST_TYPES,
        EVENT_TYPES,
        relayed_event_types,
    )

    assert "steer_consumed" in EVENT_TYPES
    # 中继（adapter 泛化转发）+ 广播（每个窗格都要看到插话入列）
    assert "steer_consumed" in relayed_event_types()
    assert "steer_consumed" in AGENT_OUTPUT_BROADCAST_TYPES


def test_turn_loop_emits_steer_consumed_with_client_id():
    block = TURN_LOOP_SRC[TURN_LOOP_SRC.index("for evt in _raw_events") :]
    block = block[: block.index('if evt.source == "vision_tool"')]
    assert '"steer_consumed"' in block
    assert 'evt.metadata.get("client_id")' in block
    assert '"message_id"' in block


def test_turn_end_safety_net_requeues_leftover_user_events():
    # finally 块兜底：模型普通输出收尾时残留的用户插话必须重新入队成新回合，
    # 而不是滞留在 event_pipeline 等一次遥远的工具调用捎带。
    finally_block = RUNNER_SRC[RUNNER_SRC.index("Steer（引导注入）残留兜底") :]
    finally_block = finally_block[: finally_block.index("reset_turn_local(token)")]
    assert "input_hub.push(" in finally_block
    assert 'not _txt.startswith("__")' in finally_block
    # 非用户事件必须原样放回管道（vision 注入不能丢）
    assert "_ep.push_nowait(" in finally_block


def test_frontend_sends_steer_with_client_id_and_has_cancel_command():
    # WS 客户端：cancel_steer 命令存在
    assert "'cancel_steer'" in WS_TS
    assert "cancelSteer" in WS_TS


def test_adapter_handles_cancel_steer_command():
    assert '"cancel_steer"' in ADAPTER_SRC
    block = ADAPTER_SRC[ADAPTER_SRC.index('if command == "cancel_steer"') :]
    block = block[: block.index('if command == "set_primary_session"')]
    # 两段队列都要尝试：input_hub 会话队列 + event_pipeline 桶
    assert "cancel_session_item" in block
    assert "cancel_user_event" in block
