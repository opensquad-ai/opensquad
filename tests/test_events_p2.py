# -*- coding: utf-8 -*-
"""P2/P3 fix verification for event pipeline & message routing.

Run: python -m pytest tests/test_events_p2.py -q
"""
import pytest

from opensquad.message_queue import MessageQueue, QueueMessage
from opensquad.event_pipeline import EventPipeline


# ── MessageQueue.peek ─────────────────────────────────────────────────────

def test_peek_returns_head_without_removing():
    q = MessageQueue()
    msg = QueueMessage(
        id="m1", type="group", source_id="g1", source_name="g",
        sender_id="a1", sender_name="a", content="hello",
        timestamp=0.0, mentions=[], raw_data={},
    )
    q._queue.put_nowait(msg)
    peeked = q.peek()
    assert peeked is not None
    assert peeked.id == "m1"
    # Message must still be in the queue
    assert q.size == 1


def test_peek_empty_returns_none():
    q = MessageQueue()
    assert q.peek() is None


def test_get_all_empty_queue_no_crash():
    """Regression: get_all() on an empty asyncio.Queue must not raise.

    asyncio.Queue.get_nowait() raises asyncio.QueueEmpty (not queue.Empty);
    get_all must catch both.
    """
    q = MessageQueue()
    assert q.get_all() == []


def test_get_all_drains_all():
    q = MessageQueue()
    for i in range(3):
        q._queue.put_nowait(QueueMessage(
            id=f"m{i}", type="group", source_id="g", source_name="g",
            sender_id="a", sender_name="a", content=f"msg{i}",
            timestamp=0.0, mentions=[], raw_data={},
        ))
    result = q.get_all()
    assert len(result) == 3
    assert q.get_all() == []  # drained


# ── EventPipeline no longer has _has_events ───────────────────────────────

def test_event_pipeline_has_no_asyncio_event():
    ep = EventPipeline()
    assert not hasattr(ep, "_has_events"), "_has_events dead code should be removed"


def test_event_pipeline_push_drain():
    ep = EventPipeline()
    ep.push_nowait("group", "hello", {"sender_name": "a"})
    events = ep.drain_sync()
    assert len(events) == 1
    assert events[0].content == "hello"
    # Second drain is empty
    assert ep.drain_sync() == []


# ── _check_mention does not text-match user_id ────────────────────────────

class _FakeBridge:
    """Minimal stand-in for opensquad.bridge.bridge"""
    user_id = "123456"
    user_name = "alice"


def test_check_mention_matches_name(monkeypatch):
    from opensquad import message_router as mr
    monkeypatch.setattr(mr, "__bridge_ref__", _FakeBridge(), raising=False)
    # Patch the internal import inside _check_mention
    import opensquad.bridge as bmod
    monkeypatch.setattr(bmod, "bridge", _FakeBridge())

    msg = {"mentions": [], "content": "hey @alice can you help?"}
    assert mr.message_router._check_mention(msg) is True


def test_check_mention_does_not_match_user_id_in_text(monkeypatch):
    """A message discussing 'user 123456' must NOT trigger a mention."""
    from opensquad import message_router as mr
    import opensquad.bridge as bmod
    monkeypatch.setattr(bmod, "bridge", _FakeBridge())

    msg = {"mentions": [], "content": "I saw user 123456 in the logs"}
    assert mr.message_router._check_mention(msg) is False


def test_check_mention_matches_via_mentions_list(monkeypatch):
    """user_id in the structured mentions list SHOULD match (exact)."""
    import opensquad.bridge as bmod
    monkeypatch.setattr(bmod, "bridge", _FakeBridge())

    from opensquad import message_router as mr
    msg = {"mentions": ["123456"], "content": "check this"}
    assert mr.message_router._check_mention(msg) is True
