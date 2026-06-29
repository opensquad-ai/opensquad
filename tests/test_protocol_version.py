# -*- coding: utf-8 -*-
"""Tests for protocol_version — API version control."""
import pytest

from opensquad.protocol_version import (
    CURRENT_VERSION,
    MIN_SUPPORTED_VERSION,
    version_string,
    negotiate_version,
    wrap_message,
    unwrap_message,
    get_message_version,
    normalize_v1_message,
    downgrade_message,
)


class TestVersionString:
    def test_format(self):
        assert version_string(2) == "v2"
        assert version_string(1) == "v1"


class TestNegotiateVersion:
    def test_exact_match(self):
        assert negotiate_version([CURRENT_VERSION]) == CURRENT_VERSION

    def test_peer_behind(self):
        assert negotiate_version([1]) == 1

    def test_peer_ahead(self):
        # Peer claims v99 but we only support up to CURRENT_VERSION
        assert negotiate_version([99, CURRENT_VERSION]) == CURRENT_VERSION

    def test_no_overlap_fallback(self):
        # Peer only supports v0 (below minimum)
        assert negotiate_version([0]) == MIN_SUPPORTED_VERSION

    def test_multiple_options(self):
        assert negotiate_version([1, 2, 3]) == CURRENT_VERSION


class TestWrapMessage:
    def test_adds_version(self):
        msg = {"type": "chat", "content": "hi"}
        wrapped = wrap_message(msg)
        assert wrapped["v"] == CURRENT_VERSION
        assert wrapped["type"] == "chat"

    def test_respects_existing_version(self):
        msg = {"type": "chat", "v": 1}
        wrapped = wrap_message(msg)
        assert wrapped["v"] == 1

    def test_explicit_version_override(self):
        msg = {"type": "chat"}
        wrapped = wrap_message(msg, version=1)
        assert wrapped["v"] == 1


class TestUnwrapMessage:
    def test_preserves_existing(self):
        msg = {"type": "chat", "v": 2}
        unwrapped = unwrap_message(msg)
        assert unwrapped["v"] == 2

    def test_adds_default_for_legacy(self):
        msg = {"type": "chat"}
        unwrapped = unwrap_message(msg)
        assert unwrapped["v"] == 1


class TestGetMessageVersion:
    def test_int_version(self):
        assert get_message_version({"v": 2}) == 2
        assert get_message_version({"v": 1}) == 1

    def test_string_version(self):
        assert get_message_version({"v": "v2"}) == 2
        assert get_message_version({"v": "v10"}) == 10

    def test_missing_defaults_to_1(self):
        assert get_message_version({"type": "chat"}) == 1

    def test_invalid_defaults_to_1(self):
        assert get_message_version({"v": "invalid"}) == 1
        assert get_message_version({"v": -1}) == 1


class TestNormalizeV1Message:
    def test_adds_seq(self):
        msg = {"type": "chat"}
        norm = normalize_v1_message(msg)
        assert norm["seq"] == 0

    def test_adds_timestamp(self):
        msg = {"type": "chat"}
        norm = normalize_v1_message(msg)
        assert "timestamp" in norm
        assert isinstance(norm["timestamp"], str)

    def test_preserves_existing_fields(self):
        msg = {"type": "chat", "seq": 42, "timestamp": "2024-01-01T00:00:00Z"}
        norm = normalize_v1_message(msg)
        assert norm["seq"] == 42
        assert norm["timestamp"] == "2024-01-01T00:00:00Z"


class TestDowngradeMessage:
    def test_no_change_for_current(self):
        msg = {"type": "chat", "seq": 5, "v": CURRENT_VERSION}
        down = downgrade_message(msg, CURRENT_VERSION)
        assert down["seq"] == 5
        assert down["v"] == CURRENT_VERSION

    def test_strips_v2_fields_for_v1(self):
        msg = {"type": "chat", "seq": 5, "v": 2}
        down = downgrade_message(msg, 1)
        assert "seq" not in down
        assert "v" not in down
        assert down["type"] == "chat"

    def test_does_not_mutate_original(self):
        msg = {"type": "chat", "seq": 5, "v": 2}
        downgrade_message(msg, 1)
        assert "seq" in msg
        assert "v" in msg
