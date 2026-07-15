"""TUI reply dedup must not treat truncated stream as equal to final text."""

from opensquad.cli.tui.app import _is_truncated_prefix, _same_reply


def test_same_reply_exact_only():
    assert _same_reply("hello", "hello")
    assert _same_reply("hello  world", "hello world")
    # Truncated stream vs complete final must NOT match
    assert not _same_reply("你好呀！有什么需要", "你好呀！有什么需要帮忙的吗？ 😊")
    assert not _same_reply("你想让我帮你做什么？直接说", "你想让我帮你做什么？直接说就行～ 😊")


def test_truncated_prefix():
    assert _is_truncated_prefix("你好呀！有什么需要", "你好呀！有什么需要帮忙的吗？ 😊")
    assert not _is_truncated_prefix("你好呀！有什么需要帮忙的吗？ 😊", "你好呀！有什么需要")
    assert not _is_truncated_prefix("abc", "abc")
    assert not _is_truncated_prefix("", "abc")
