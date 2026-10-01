"""事件管道按 id 去重。

背景：同一条 DM 会经两条路径到达 runner（message_queue 与 input_hub 的补充
drain），管道只按 sid 入队、不做去重，于是模型读到两遍「hi」并回复两遍 ——
用户看到两条一模一样的私信。这里锁住：同 id 只投递一次、不同 id 照常投递、
没有 id 的事件不参与去重、撤回后重发仍会投递。
"""

from __future__ import annotations

from opensquad.event_pipeline import EventPipeline, PipelineEvent


def _push(pipe: EventPipeline, content: str, msg_id: str = "", sid: str = "s1", source: str = "dm"):
    metadata = {"raw_data": {"id": msg_id}} if msg_id else {}
    pipe.push_nowait(source, content, metadata, session_id=sid)


def test_same_message_id_is_delivered_once():
    pipe = EventPipeline()
    _push(pipe, "hi", "dm_1")
    _push(pipe, "hi", "dm_1")

    events = pipe.drain_sync("s1")
    assert [e.content for e in events] == ["hi"]
    assert pipe.stats["deduped"] == 1


def test_the_same_text_twice_is_still_two_messages():
    # 用户真的连发两条一样的话 —— 两条都得到达（id 不同）。
    pipe = EventPipeline()
    _push(pipe, "hi", "dm_1")
    _push(pipe, "hi", "dm_2")

    assert [e.content for e in pipe.drain_sync("s1")] == ["hi", "hi"]
    assert pipe.stats["deduped"] == 0


def test_events_without_an_id_are_never_deduped():
    pipe = EventPipeline()
    _push(pipe, "timer tick")
    _push(pipe, "timer tick")

    assert len(pipe.drain_sync("s1")) == 2
    assert pipe.stats["deduped"] == 0


def test_duplicate_still_pending_is_collapsed():
    # 第一条还没被消费时又来一条同 id 的：只留一条在队列里。
    pipe = EventPipeline()
    _push(pipe, "hi", "dm_1")
    _push(pipe, "hi", "dm_1")
    assert pipe.size_for("s1") == 1


def test_buckets_do_not_cross_dedupe():
    pipe = EventPipeline()
    _push(pipe, "hi", "dm_1", sid="s1")
    _push(pipe, "hi", "dm_1", sid="s2")

    assert len(pipe.drain_sync("s1")) == 1
    assert len(pipe.drain_sync("s2")) == 1


def test_cancel_then_resend_is_delivered_again():
    # 引导注入撤回后重发同一条：不能被"已投递"记录挡住。
    pipe = EventPipeline()
    md = {"client_id": "c1", "raw_data": {"id": "dm_1"}}
    pipe.push_nowait("web", "please stop", md, session_id="s1")
    assert pipe.cancel_user_event("s1", "c1") is True

    pipe.push_nowait("web", "please stop", md, session_id="s1")
    events = pipe.drain_sync("s1")
    assert [e.content for e in events] == ["please stop"], events
    assert isinstance(events[0], PipelineEvent)
