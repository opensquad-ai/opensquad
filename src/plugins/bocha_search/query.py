"""Bocha search plugin actions — the wizard's "test connection" probe.

A key is only real if the provider accepts it, so the check is one tiny search request:
``POST {base_url}/v1/web-search`` with ``Authorization: Bearer <key>``. The response body is
read either way, because Bocha reports "invalid key" in its own words.
"""

from __future__ import annotations

try:  # loaded by path (launcher) or as a package (tests)
    from plugins import setup_check as sc
except ImportError:  # pragma: no cover - path-loaded fallback
    import setup_check as sc  # type: ignore[no-redef]

DEFAULT_BASE = "https://api.bocha.cn"
NETWORK_HINT = "连接博查接口失败：确认这台机器能访问外网，且「接口地址」没有写错（默认 https://api.bocha.cn）。"


def handle_action(project_root: str, action: str, data: dict) -> dict:
    """
    Supported actions:
        test_connection — data: {config?} — verify the API key without saving it
    """
    if action == "test_connection":
        return test_connection(project_root, data or {})
    return {"error": f"Unsupported action: {action}"}


def test_connection(project_root: str, data: dict) -> dict:
    """One minimal search request with the configured key."""
    import os

    cfg = sc.read_config(project_root, "bocha_search", overrides=(data or {}).get("config"))
    api_key = str((data or {}).get("api_key") or cfg.get("api_key") or os.environ.get("BOCHA_API_KEY") or "").strip()
    base_url = str(cfg.get("base_url") or DEFAULT_BASE).strip().rstrip("/") or DEFAULT_BASE
    if not api_key:
        return sc.missing(
            names=["api_key"],
            hint="到博查控制台（https://open.bochaai.com/）创建 API KEY，然后粘贴到这里。",
        )

    try:
        status, body = sc.http_json(
            f"{base_url}/v1/web-search",
            method="POST",
            payload={"query": "opensquad", "count": 1, "summary": False},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
        return sc.result(checks=[sc.failed("web-search", exc, NETWORK_HINT)])

    # Bocha answers 200 inside the body on success; a non-200 code (or an `error`) is a
    # refusal. Verified against the provider's own responses, not assumed.
    if status == 200 and not body.get("error") and body.get("code") in (None, 0, 200):
        return sc.result(checks=[sc.check("web-search", True, "已连接：API Key 可用")])
    reason = str(body.get("message") or body.get("error") or body.get("_text") or f"HTTP {status}")
    hint = (
        "API Key 无效：博查控制台 → API KEY 管理，确认 key 未删除、未过期，并注意别复制多余空格。"
        if status in (401, 403)
        else "确认「接口地址」正确（默认 https://api.bocha.cn）且该 key 有可用额度。"
    )
    return sc.result(checks=[sc.check("web-search", False, reason, hint)])
