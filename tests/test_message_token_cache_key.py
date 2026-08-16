"""Identity+shape token cache keys must not json.dumps the whole message."""

from __future__ import annotations

import json

from opensquad.token_breakdown import message_token_cache_key


def test_same_object_is_stable():
    msg = {"role": "user", "content": "hello"}
    assert message_token_cache_key(msg) == message_token_cache_key(msg)


def test_list_append_changes_key():
    msg = {"role": "user", "content": [{"type": "text", "text": "a"}]}
    first = message_token_cache_key(msg)
    msg["content"].append({"type": "text", "text": "b"})
    assert message_token_cache_key(msg) != first


def test_replace_first_text_changes_key():
    msg = {"role": "user", "content": [{"type": "text", "text": "a"}]}
    first = message_token_cache_key(msg)
    msg["content"][0]["text"] = "changed"
    assert message_token_cache_key(msg) != first


def test_role_change_changes_key():
    msg = {"role": "user", "content": "hello"}
    first = message_token_cache_key(msg)
    msg["role"] = "assistant"
    assert message_token_cache_key(msg) != first


def test_key_does_not_json_dumps(monkeypatch):
    calls = {"n": 0}
    original = json.dumps

    def spy(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("opensquad.token_breakdown.json.dumps", spy)
    message_token_cache_key({"role": "user", "content": "hello", "tool_calls": [{"id": "1"}]})
    assert calls["n"] == 0
