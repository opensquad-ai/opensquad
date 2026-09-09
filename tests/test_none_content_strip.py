"""Regression: tool-only assistant content=None must not crash repetition check."""

from __future__ import annotations

from opensquad._runner._validation import is_repeated_content
from opensquad.runner import AgentRunner


def test_is_repeated_content_tolerates_none_assistant_content(monkeypatch):
    runner = object.__new__(AgentRunner)

    class _SM:
        def get_messages(self, limit=200):
            # Mimic Native FC / tool-call turn persisted with content=None
            return [
                {"role": "user", "content": "open deepseek"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
            ]

    monkeypatch.setattr("opensquad.runner._get_session_manager", lambda: _SM())
    # Should not raise AttributeError: 'NoneType' object has no attribute 'strip'
    assert runner._is_repeated_content("please wait for login") is False


def test_validation_is_repeated_content_tolerates_none_assistant():
    def get_messages():
        return [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}]

    assert is_repeated_content("hello world this is fine text", get_messages=get_messages) is False
