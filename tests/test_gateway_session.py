"""
E2E tests for Gateway session persistence and WebSocket communication.
Covers the critical path bugs found in production:
- Session dedup logic (datetime UnboundLocalError)
- In-memory cache behavior
- Cache invalidation on current_session events
"""

import ast
import importlib.util
import os
import time

import pytest

# ── Direct import of sessions.py (bypass __init__.py chain) ──
# The gateway backend __init__.py eagerly imports routes which triggers
# a cascade of module loads (app.api, app.bot_api, etc.) that may
# require database connections or complex dependencies. Since we only
# need GatewaySessionCache (which has no such dependencies), import the
# source file directly.
_SESSIONS_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "opensquad",
        "gateway",
        "backend",
        "app",
        "ai_web",
        "sessions.py",
    )
)
_spec = importlib.util.spec_from_file_location("gateway_session_test", _SESSIONS_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
GatewaySessionCache = _mod.GatewaySessionCache


@pytest.fixture
def gateway_session_manager():
    """Create an isolated GatewaySessionCache for each test."""
    sm = GatewaySessionCache()
    yield sm


class TestGatewaySessionCache:
    """Tests for Gateway-side session caching (sessions.py).

    GatewaySessionCache is now a pure in-memory cache with NO file persistence.
    Runner disk is the authoritative durable source.
    """

    def test_add_message_creates_session(self, gateway_session_manager):
        """Adding a message auto-creates a session."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "Hello")
        session = sm.get_session("user1", "agent1")
        assert session is not None
        assert session["message_count"] == 1
        assert session["messages"][0]["content"] == "Hello"
        assert session["messages"][0]["role"] == "user"

    def test_add_message_with_message_id(self, gateway_session_manager):
        """Adding a message with message_id stores it properly."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "assistant", "Hi!", message_id="msg_001")
        msgs = sm.get_history("user1", "agent1")
        assert msgs[0]["message_id"] == "msg_001"

    def test_dedup_exact_message_id(self, gateway_session_manager):
        """Strong dedup: same message_id as the LAST message should be skipped.
        Note: dedup only checks against the most recent message (prevents
        rapid double-saves), not against the full history."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "assistant", "Hi!", message_id="msg_001")
        # Same message_id as last → deduped (prevent double-save)
        sm.add_message("user1", "agent1", "assistant", "Hi!", message_id="msg_001")
        history = sm.get_history("user1", "agent1")
        assert len(history) == 1  # msg_001 deduped against last message

    def test_dedup_same_content_within_window(self, gateway_session_manager):
        """Weak dedup: same role+content within 3s should be skipped."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "assistant", "Same reply")
        sm.add_message("user1", "agent1", "assistant", "Same reply")
        history = sm.get_history("user1", "agent1")
        assert len(history) == 1  # Deduped (within time window)

    def test_dedup_different_content_same_role(self, gateway_session_manager):
        """Different content with same role should NOT be deduped."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "assistant", "Reply one")
        time.sleep(0.1)
        sm.add_message("user1", "agent1", "assistant", "Reply two")
        history = sm.get_history("user1", "agent1")
        assert len(history) == 2  # Different content

    def test_history_limit(self, gateway_session_manager):
        """get_history with limit returns only the last N messages."""
        sm = gateway_session_manager
        for i in range(10):
            sm.add_message("user1", "agent1", "user", f"Message {i}")
        history = sm.get_history("user1", "agent1", limit=3)
        assert len(history) == 3
        assert history[-1]["content"] == "Message 9"

    def test_cache_volatile_after_restart(self, gateway_session_manager):
        """Cache-only: messages do NOT persist in a new GatewaySessionCache instance.
        This validates the decoupling — Runner disk is the only durable store."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "Hello")
        sm.add_message("user1", "agent1", "assistant", "Hi back")

        # Create a NEW GatewaySessionCache (simulates Gateway restart)
        sm2 = GatewaySessionCache()
        session = sm2.get_session("user1", "agent1")
        assert session is None  # No persistence across restarts

    def test_invalidate_clears_messages_keeps_session(self, gateway_session_manager):
        """invalidate() clears messages but retains session metadata."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "Hello")
        sm.add_message("user1", "agent1", "assistant", "Hi back")
        assert sm.get_history("user1", "agent1") != []

        sm.invalidate("user1", "agent1")
        history = sm.get_history("user1", "agent1")
        assert len(history) == 0
        # Session metadata should still exist
        session = sm.get_session("user1", "agent1")
        assert session is not None
        assert session["message_count"] == 0

    def test_invalidate_non_existent_session(self, gateway_session_manager):
        """invalidate() on a non-existent session should not raise."""
        sm = gateway_session_manager
        # Should not raise
        sm.invalidate("nonexistent", "agent1")

    def test_clear_session(self, gateway_session_manager):
        """clear_session removes messages and can be confirmed."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "Hello")
        sm.clear_session("user1", "agent1")
        assert sm.get_history("user1", "agent1") == []

    def test_delete_session(self, gateway_session_manager):
        """delete_session removes the entire session entry."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "Hello")
        sm.delete_session("user1", "agent1")
        assert sm.get_session("user1", "agent1") is None

    def test_multiple_sessions_isolation(self, gateway_session_manager):
        """Sessions for different user:agent pairs are isolated."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "U1A1 msg")
        sm.add_message("user2", "agent1", "user", "U2A1 msg")
        sm.add_message("user1", "agent2", "user", "U1A2 msg")

        h1 = sm.get_history("user1", "agent1")
        h2 = sm.get_history("user2", "agent1")
        h3 = sm.get_history("user1", "agent2")

        assert len(h1) == 1 and h1[0]["content"] == "U1A1 msg"
        assert len(h2) == 1 and h2[0]["content"] == "U2A1 msg"
        assert len(h3) == 1 and h3[0]["content"] == "U1A2 msg"

    def test_add_message_with_media(self, gateway_session_manager):
        """Messages with images/attachments store structured metadata."""
        sm = gateway_session_manager
        images = [{"path": "/tmp/img.png", "original_name": "img.png"}]
        attachments = [{"path": "/tmp/doc.pdf", "original_name": "doc.pdf"}]
        sm.add_message(
            "user1",
            "agent1",
            "user",
            "Check these",
            images=images,
            attachments=attachments,
        )
        msg = sm.get_history("user1", "agent1")[0]
        assert msg["images"] == images
        assert msg["attachments"] == attachments

    def test_session_key_consistency(self, gateway_session_manager):
        """gateway_session_key should be consistent and always available."""
        sm = gateway_session_manager
        sm.get_or_create_session("user1", "agent1")
        assert sm._get_session_key("user1", "agent1") == "user1:agent1"

    def test_get_user_sessions_returns_sorted(self, gateway_session_manager):
        """get_user_sessions returns sessions sorted by updated_at descending."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "First")
        time.sleep(0.05)
        sm.add_message("user1", "agent2", "user", "Second")
        sessions = sm.get_user_sessions("user1")
        assert len(sessions) == 2
        # Most recently updated first
        assert sessions[0]["agent_id"] == "agent2"
        assert sessions[1]["agent_id"] == "agent1"

    def test_get_stats(self, gateway_session_manager):
        """get_stats returns meaningful metrics."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "Hello")
        sm.add_message("user1", "agent1", "assistant", "Hi")
        stats = sm.get_stats()
        assert stats["total_sessions"] >= 1
        assert stats["total_messages"] >= 1

    def test_flush_now_is_noop(self, gateway_session_manager):
        """flush_now() is a no-op in cache-only mode (backward compat)."""
        sm = gateway_session_manager
        sm.add_message("user1", "agent1", "user", "Hello")
        sm.flush_now()  # Should not raise
        assert sm.get_history("user1", "agent1")[0]["content"] == "Hello"


class TestGatewayWebSocketSessionFlow:
    """Tests for the websocket.py session integration (critical bugs)."""

    def test_datetime_import_not_in_function_body(self):
        """
        Regression test: `from datetime import datetime` must NOT appear inside
        any function body in sessions.py. Python scoping rules cause
        UnboundLocalError when a conditional branch doesn't execute the import.
        """
        import os

        sessions_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "opensquad",
            "gateway",
            "backend",
            "app",
            "ai_web",
            "sessions.py",
        )
        with open(sessions_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, ast.Import | ast.ImportFrom):
                        for alias in child.names:
                            if alias.name == "datetime":
                                pytest.fail(
                                    f"Function '{node.name}' contains local import of 'datetime' "
                                    f"at line {child.lineno}. Always import at module level."
                                )

    def test_connected_event_has_session_id(self):
        """
        The connected event must always include a gateway_session_key.
        This was the root cause of the 'can't have continuous conversation' bug.
        """
        import os

        ws_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "opensquad",
            "gateway",
            "backend",
            "app",
            "ai_web",
            "websocket.py",
        )
        with open(ws_path, encoding="utf-8") as f:
            source = f.read()

        # The connected event payload must include gateway_session_key
        assert '"gateway_session_key"' in source, (
            "Connected event MUST include gateway_session_key for frontend session routing"
        )

    def test_no_deprecated_apis_in_session_modules(self):
        """Session-related modules must not use deprecated datetime APIs."""
        import os

        files = [
            "src/opensquad/gateway/backend/app/ai_web/sessions.py",
            "src/opensquad/gateway/backend/app/ai_web/websocket.py",
        ]
        project_root = os.path.join(os.path.dirname(__file__), "..")

        for rel_path in files:
            abs_path = os.path.join(project_root, rel_path)
            with open(abs_path, encoding="utf-8") as f:
                source = f.read()
            assert "utcnow()" not in source, f"{rel_path}: deprecated utcnow() still present"
