"""Feishu/Lark plugin actions — the wizard's "test connection" probe.

The check is the credential exchange the adapter itself performs: ``app_id`` + ``app_secret``
→ ``tenant_access_token``. If that call succeeds the credentials are right; if it fails,
Feishu says why (invalid app_secret, app not published…), which is the guidance to pass on.
"""

from __future__ import annotations

from typing import Any

try:  # loaded by path (launcher) or as a package (tests)
    from plugins import setup_check as sc
except ImportError:  # pragma: no cover - path-loaded fallback
    import setup_check as sc  # type: ignore[no-redef]

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
NETWORK_HINT = "连接 open.feishu.cn 失败：确认这台机器能访问飞书开放平台（企业网络可能需要代理/放行）。"


def handle_action(project_root: str, action: str, data: dict) -> dict:
    """
    Supported actions:
        test_connection — data: {config?} — verify app credentials without saving them
    """
    if action == "test_connection":
        return test_connection(project_root, data or {})
    return {"error": f"Unsupported action: {action}"}


def test_connection(project_root: str, data: dict) -> dict:
    """Exchange ``app_id``/``app_secret`` for a tenant token, once per bot."""
    cfg = sc.read_config(project_root, "feishu", section="feishu", overrides=(data or {}).get("config"))
    bots = [b for b in (cfg.get("bots") or []) if isinstance(b, dict)]
    if not bots:
        return sc.missing(
            names=["app_id", "app_secret"],
            hint="先到飞书开放平台创建企业自建应用，复制凭证与基础信息里的 App ID / App Secret。",
        )

    checks: list[dict[str, Any]] = []
    for bot in bots:
        label = str(bot.get("name") or bot.get("agent_id") or "app").strip() or "app"
        app_id = str(bot.get("app_id") or "").strip()
        app_secret = str(bot.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            lacking = [n for n, v in (("app_id", app_id), ("app_secret", app_secret)) if not v]
            checks.append(
                sc.check(
                    f"{label} / 凭据",
                    False,
                    f"缺少 {'、'.join(lacking)}",
                    "开放平台 → 你的应用 → 凭证与基础信息：App ID 与 App Secret 都在那一页。",
                )
            )
            continue
        if not app_id.startswith("cli_"):
            checks.append(
                sc.check(
                    f"{label} / app_id",
                    False,
                    "看起来不是 App ID",
                    "飞书的 App ID 形如 cli_a1b2c3d4e5f6g7h8（以 cli_ 开头）；这里可能填成了别的值。",
                )
            )
            continue
        try:
            status, body = sc.http_json(TOKEN_URL, method="POST", payload={"app_id": app_id, "app_secret": app_secret})
        except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
            checks.append(sc.failed(f"{label} / tenant_access_token", exc, NETWORK_HINT))
            continue
        if status == 200 and int(body.get("code") or 0) == 0:
            checks.append(sc.check(f"{label} / 凭据", True, "已连接：凭据可换取 tenant_access_token"))
            continue
        reason = str(body.get("msg") or body.get("_text") or f"HTTP {status}")
        code = body.get("code")
        hint = (
            "App Secret 不对：开放平台 → 凭证与基础信息 → 重新获取（旧的会立即失效）。"
            if code in (10003, 10014) or status in (401, 403)
            else "确认应用已创建、凭据属于该应用，且这台机器能访问 open.feishu.cn。"
        )
        checks.append(sc.check(f"{label} / 凭据", False, f"{reason}（code={code}）", hint))

    return sc.result(checks=checks)
