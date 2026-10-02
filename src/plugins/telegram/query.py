"""Telegram plugin actions — the wizard's "test connection" probe.

The check is the provider's own: ``getMe`` with the bot token. Nothing else can tell you
whether a token is real, and Telegram answers with the bot's username, which is exactly
what the user needs to see ("yes, this is my bot").
"""

from __future__ import annotations

import re
from typing import Any

try:  # loaded by path (launcher) or as a package (tests)
    from plugins import setup_check as sc
except ImportError:  # pragma: no cover - path-loaded fallback
    import setup_check as sc  # type: ignore[no-redef]

API_BASE = "https://api.telegram.org"
# 123456789:AAH… — digits, a colon, then 30+ url-safe characters.
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
NETWORK_HINT = (
    "连接 api.telegram.org 失败：国内网络通常需要代理，填「代理」后再试"
    "（例如 http://127.0.0.1:7890），或确认这台机器能访问外网。"
)


def handle_action(project_root: str, action: str, data: dict) -> dict:
    """
    Supported actions:
        test_connection — data: {config?} — verify the bot tokens without saving them
    """
    if action == "test_connection":
        return test_connection(project_root, data or {})
    return {"error": f"Unsupported action: {action}"}


def test_connection(project_root: str, data: dict) -> dict:
    """Call ``getMe`` for each configured bot, using the form's values when given."""
    cfg = sc.read_config(project_root, "telegram", section="telegram", overrides=(data or {}).get("config"))
    bots = [b for b in (cfg.get("bots") or []) if isinstance(b, dict)]
    proxy = str(cfg.get("proxy") or "").strip()
    if not bots:
        return sc.missing(
            names=["bots[].bot_token"],
            hint="先在 Telegram 里给 @BotFather 发 /newbot，把返回的 token 填进上面的机器人配置。",
        )

    checks: list[dict[str, Any]] = []
    for bot in bots:
        label = str(bot.get("name") or bot.get("agent_id") or "bot").strip() or "bot"
        token = str(bot.get("bot_token") or "").strip()
        if not token:
            checks.append(sc.check(f"{label} / token", False, "未填写", "向 @BotFather 发 /newbot 获取 token。"))
            continue
        if not TOKEN_RE.match(token):
            checks.append(
                sc.check(
                    f"{label} / token",
                    False,
                    "格式不像 Telegram token",
                    "形如 123456789:AAH…（数字 + 冒号 + 35 位字符）；别把空格、引号或整句消息复制进来。",
                )
            )
            continue
        try:
            status, body = sc.http_json(f"{API_BASE}/bot{token}/getMe", proxy=proxy)
        except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
            checks.append(sc.failed(f"{label} / getMe", exc, NETWORK_HINT))
            continue
        if status == 200 and body.get("ok"):
            username = str(
                ((body.get("result") or {}) if isinstance(body.get("result"), dict) else {}).get("username") or ""
            )
            checks.append(sc.check(f"{label} / getMe", True, f"已连接：@{username}" if username else "已连接"))
            continue
        reason = str(body.get("description") or body.get("_text") or f"HTTP {status}")
        hint = (
            "Token 无效或已被撤销：在 @BotFather 里 /mybots → 选机器人 → API Token 重新取一个。"
            if status in (401, 403, 404)
            else NETWORK_HINT
        )
        checks.append(sc.check(f"{label} / getMe", False, reason, hint))

    return sc.result(checks=checks)
