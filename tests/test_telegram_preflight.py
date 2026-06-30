"""Regression tests for telegram adapter preflight (issue #42).

A deployment tester reported that the telegram adapter failed at startup
with a wrapped ``telegram.error.NetworkError`` (httpx
``ConnectError: All connection attempts failed``), and that the error
message did not distinguish between *configuration* problems
(``bot_token`` is still a placeholder) and *network* problems
(``api.telegram.org`` not reachable from this environment, or a
configured proxy that doesn't work).

The fix introduces a ``_run_preflight()`` function that runs *before*
the first ``getMe`` retry and emits clear, actionable log lines:

* ``_validate_bot_configs``  -- checks for placeholder / empty values
* ``_preflight_network``      -- TCP-probes api.telegram.org:443 (direct)
                                  or the configured proxy URL

These tests pin both halves of the contract and exercise every branch
of the warning-vs-error decision tree.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

# ── helpers ──────────────────────────────────────────────────────────────


def _make_cfg(name="bot", bot_token="1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefg", agent_id="default-001", proxy=""):
    """Build a minimal TelegramBotConfig-like object for preflight tests."""
    cfg = MagicMock()
    cfg.name = name
    cfg.bot_token = bot_token
    cfg.agent_id = agent_id
    cfg.proxy = proxy
    return cfg


# ── _validate_bot_configs ────────────────────────────────────────────────


def test_validate_accepts_real_token_and_agent_id(caplog):
    from plugins.telegram.adapter import _validate_bot_configs

    cfg = _make_cfg(bot_token="1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefg", agent_id="coder-001")

    with caplog.at_level(logging.WARNING, logger="plugins.telegram.adapter"):
        ok = _validate_bot_configs([cfg])

    assert ok is True
    # Real config must not produce any warning or error records.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_validate_flags_placeholder_bot_token(caplog):
    from plugins.telegram.adapter import _validate_bot_configs

    cfg = _make_cfg(bot_token="YOUR_TELEGRAM_BOT_TOKEN")
    with caplog.at_level(logging.ERROR, logger="plugins.telegram.adapter"):
        ok = _validate_bot_configs([cfg])

    assert ok is False
    assert any("YOUR_TELEGRAM_BOT_TOKEN" in r.getMessage() for r in caplog.records)
    assert any("placeholder" in r.getMessage() for r in caplog.records)


def test_validate_flags_empty_bot_token(caplog):
    from plugins.telegram.adapter import _validate_bot_configs

    cfg = _make_cfg(bot_token="")
    with caplog.at_level(logging.ERROR, logger="plugins.telegram.adapter"):
        ok = _validate_bot_configs([cfg])

    assert ok is False
    assert any("empty bot_token" in r.getMessage() for r in caplog.records)


def test_validate_flags_placeholder_agent_id(caplog):
    from plugins.telegram.adapter import _validate_bot_configs

    cfg = _make_cfg(agent_id="your-agent-id")
    with caplog.at_level(logging.ERROR, logger="plugins.telegram.adapter"):
        ok = _validate_bot_configs([cfg])

    assert ok is False
    assert any("agent_id" in r.getMessage() for r in caplog.records)


def test_validate_warns_on_malformed_but_non_placeholder_token(caplog):
    """A short / non-matching token should warn but NOT mark ok=False.

    The bot may still work in some cases, and some tests use synthetic
    short tokens, so this is a WARNING not an ERROR.
    """
    from plugins.telegram.adapter import _validate_bot_configs

    cfg = _make_cfg(bot_token="test123", agent_id="coder-001")
    with caplog.at_level(logging.WARNING, logger="plugins.telegram.adapter"):
        ok = _validate_bot_configs([cfg])

    assert ok is True
    assert any("does not match the expected" in r.getMessage() for r in caplog.records)


def test_validate_returns_false_if_any_bot_fails(caplog):
    from plugins.telegram.adapter import _validate_bot_configs

    good = _make_cfg(name="good", agent_id="coder-001", bot_token="1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefg")
    bad = _make_cfg(name="bad", bot_token="YOUR_TELEGRAM_BOT_TOKEN")
    with caplog.at_level(logging.ERROR, logger="plugins.telegram.adapter"):
        ok = _validate_bot_configs([good, bad])

    assert ok is False
    # Only the bad bot should be flagged.
    assert any("Bot [bad]" in r.getMessage() for r in caplog.records)
    assert not any("Bot [good]" in r.getMessage() for r in caplog.records)


# ── _preflight_network ───────────────────────────────────────────────────


def test_preflight_direct_path_reachable_logs_info(caplog):
    from plugins.telegram.adapter import _preflight_network

    cfg = _make_cfg(proxy="")
    with (
        patch("socket.create_connection") as mock_conn,
        caplog.at_level(logging.INFO, logger="plugins.telegram.adapter"),
    ):
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        ok = _preflight_network([cfg])

    assert ok is True
    assert any("api.telegram.org" in r.getMessage() and "reachable" in r.getMessage() for r in caplog.records)


def test_preflight_direct_path_unreachable_warns_does_not_fail(caplog):
    """Direct path failure is a WARNING, not an ERROR -- the user may
    legitimately be in a region that needs a proxy. ok stays True so
    the adapter still starts (issue #42 explicit ask)."""
    from plugins.telegram.adapter import _preflight_network

    cfg = _make_cfg(proxy="")
    with (
        patch("socket.create_connection", side_effect=OSError("timeout")),
        caplog.at_level(logging.WARNING, logger="plugins.telegram.adapter"),
    ):
        ok = _preflight_network([cfg])

    # Issue #42: "预检测失败**不阻止** adapter 启动" for direct path
    assert ok is True
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert any("proxy" in r.getMessage().lower() for r in caplog.records)


def test_preflight_proxy_reachable_logs_info(caplog):
    from plugins.telegram.adapter import _preflight_network

    cfg = _make_cfg(proxy="socks5://proxy.example.com:1080")
    with (
        patch("socket.create_connection") as mock_conn,
        caplog.at_level(logging.INFO, logger="plugins.telegram.adapter"),
    ):
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        ok = _preflight_network([cfg])

    assert ok is True
    assert any("proxy reachable" in r.getMessage() for r in caplog.records)
    # Must have probed the proxy host, not api.telegram.org
    assert any("proxy.example.com" in r.getMessage() for r in caplog.records)


def test_preflight_proxy_unreachable_errors(caplog):
    """Proxy configured but unreachable is an ERROR -- config is wrong."""
    from plugins.telegram.adapter import _preflight_network

    cfg = _make_cfg(proxy="socks5://proxy.example.com:1080")
    with (
        patch("socket.create_connection", side_effect=OSError("connection refused")),
        caplog.at_level(logging.ERROR, logger="plugins.telegram.adapter"),
    ):
        ok = _preflight_network([cfg])

    assert ok is False
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert any("proxy.example.com" in r.getMessage() for r in caplog.records)


# ── _run_preflight (combined) ────────────────────────────────────────────


def test_run_preflight_logs_combined_summary(caplog):
    from plugins.telegram.adapter import _run_preflight

    good = _make_cfg(name="good", agent_id="coder-001", bot_token="1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefg")
    bad = _make_cfg(name="bad", bot_token="YOUR_TELEGRAM_BOT_TOKEN")

    with caplog.at_level(logging.WARNING, logger="plugins.telegram.adapter"):
        _run_preflight([good, bad])

    # Must include the summary line
    assert any("preflight detected issues" in r.getMessage() for r in caplog.records)
    # And the per-bot error
    assert any("Bot [bad]" in r.getMessage() for r in caplog.records)
