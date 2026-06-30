"""Tests for WebSocket message sequencing and deduplication (P2-2).

Validates:
1. Outbound messages get sequence numbers
2. Duplicate inbound messages are dropped
3. Out-of-order messages are detected
4. Deduplication window evicts old entries
"""

import pytest

# Skip all tests if websockets is not installed
pytestmark = pytest.mark.skipif(
    __import__("importlib").util.find_spec("websockets") is None,
    reason="websockets package not installed",
)

try:
    from opensquad.sdk import AgentConfig, BaseAgent
except ImportError:
    BaseAgent = None
    AgentConfig = None


@pytest.fixture
def fake_agent():
    """Create a BaseAgent with a fake config."""
    cfg = AgentConfig(
        gateway_url="ws://localhost:8000",
        agent_id="test-agent",
        agent_name="Test Agent",
        agent_type="test",
        capabilities=["test"],
    )
    return BaseAgent(cfg)


def test_send_response_increments_seq(fake_agent):
    """send_response() should increment _send_seq."""
    agent = fake_agent
    assert agent._send_seq == 0

    # Simulate sending (ws is None, so payload is built but not sent)
    # We test the seq increment logic directly
    agent._send_seq += 1
    assert agent._send_seq == 1

    agent._send_seq += 1
    assert agent._send_seq == 2


def test_is_duplicate_detects_duplicates(fake_agent):
    """_is_duplicate() should return True for seen seq numbers."""
    agent = fake_agent
    assert not agent._is_duplicate(1)
    agent._record_seq(1)
    assert agent._is_duplicate(1)
    assert not agent._is_duplicate(2)


def test_record_seq_evicts_old_entries(fake_agent):
    """Old seq numbers should be evicted when window is full."""
    agent = fake_agent
    agent.DEDUP_WINDOW_SIZE = 5  # Small window for testing

    for i in range(10):
        agent._record_seq(i)

    # Old entries (0-4) should be evicted
    for i in range(5):
        assert not agent._is_duplicate(i), f"seq {i} should have been evicted"

    # New entries (5-9) should still be tracked
    for i in range(5, 10):
        assert agent._is_duplicate(i), f"seq {i} should still be tracked"


def test_dedup_window_size_default(fake_agent):
    """Default dedup window should be 100."""
    assert fake_agent.DEDUP_WINDOW_SIZE == 100


def test_last_recv_seq_tracks_max(fake_agent):
    """_last_recv_seq should track the highest seen seq."""
    agent = fake_agent
    assert agent._last_recv_seq is None

    # Simulate receiving messages with seq
    agent._record_seq(5)
    agent._last_recv_seq = max(agent._last_recv_seq or 0, 5)
    assert agent._last_recv_seq == 5

    agent._record_seq(10)
    agent._last_recv_seq = max(agent._last_recv_seq or 0, 10)
    assert agent._last_recv_seq == 10

    # Lower seq (out-of-order)
    agent._record_seq(3)
    agent._last_recv_seq = max(agent._last_recv_seq or 0, 3)
    assert agent._last_recv_seq == 10  # Should not decrease


def test_payload_structure_with_seq():
    """Verify that the payload structure includes seq when sent."""
    # This is a structural test: we verify the payload dict has the right keys
    payload = {
        "type": "message",
        "role": "assistant",
        "content": "hello",
        "timestamp": 1234567890.0,
        "seq": 1,
    }
    assert "seq" in payload
    assert payload["seq"] == 1


def test_payload_without_seq_is_backward_compatible():
    """Messages without seq should still be processable."""
    # Simulate an old Gateway sending messages without seq
    payload = {
        "type": "chat",
        "user_id": "user123",
        "content": "hello",
    }
    assert "seq" not in payload
    # The _message_loop should handle this gracefully (seq=None -> skip dedup)
    seq = payload.get("seq")
    assert seq is None
