"""事件通知块在 LLM 请求历史里的行为。

背景：DM/群聊等外部事件以 `--- External Events ---` 块的形式挂在一个合成的
`system__event_pipeline` tool 结果上（`chat_api.add_pipeline_events`）。块里的事件
是**通知**，不是新一轮对话；历史里堆着多块时，模型可能把旧块当成"刚到的消息"
再回一遍 —— 这就是重复私信的另一半原因。这里锁住两条：

  1. 追加新块时，旧的块（合成 assistant tool_call + 对应 role=tool）被清掉；
  2. 块首带一句"已送达通知，读一次、回一次"的提醒。
"""

from __future__ import annotations

from opensquad.chat_api import (
    _PIPELINE_EVENTS_NOTE,
    _PIPELINE_TOOL_NAME,
    strip_previous_pipeline_events,
)


def _assistant_with_pipeline_call(call_id: str, *, also_real: bool = False) -> dict:
    tool_calls = [{"id": call_id, "type": "function", "function": {"name": _PIPELINE_TOOL_NAME, "arguments": "{}"}}]
    if also_real:
        tool_calls.insert(
            0,
            {"id": f"real_{call_id}", "type": "function", "function": {"name": "system__run", "arguments": "{}"}},
        )
    return {"role": "assistant", "content": None, "tool_calls": tool_calls}


def test_older_blocks_are_dropped_and_the_newest_survives():
    req = [
        {"role": "system", "content": "prompt"},
        _assistant_with_pipeline_call("p1"),
        {"role": "tool", "tool_call_id": "p1", "name": _PIPELINE_TOOL_NAME, "content": "[DM] hi"},
        {"role": "assistant", "content": "answered"},
    ]

    dropped = strip_previous_pipeline_events(req)

    assert dropped == 1
    assert [m["role"] for m in req] == ["system", "assistant"]
    assert req[-1]["content"] == "answered"


def test_a_real_tool_call_on_the_same_message_is_kept():
    # 合成块会和真实工具调用共用一个 assistant 消息，只允许摘掉前者。
    req = [
        _assistant_with_pipeline_call("p1", also_real=True),
        {"role": "tool", "tool_call_id": "p1", "name": _PIPELINE_TOOL_NAME, "content": "[DM] hi"},
        {"role": "tool", "tool_call_id": "real_p1", "name": "system__run", "content": "ok"},
    ]

    assert strip_previous_pipeline_events(req) == 1
    assert [tc["id"] for tc in req[0]["tool_calls"]] == ["real_p1"]
    assert [m.get("tool_call_id") for m in req[1:]] == ["real_p1"]


def test_history_without_blocks_is_untouched():
    req = [{"role": "system", "content": "p"}, {"role": "user", "content": "hi"}]
    assert strip_previous_pipeline_events(req) == 0
    assert req == [{"role": "system", "content": "p"}, {"role": "user", "content": "hi"}]
    assert strip_previous_pipeline_events([]) == 0


def test_the_block_says_it_is_a_notification():
    assert "already arrived" in _PIPELINE_EVENTS_NOTE
    assert "once" in _PIPELINE_EVENTS_NOTE
