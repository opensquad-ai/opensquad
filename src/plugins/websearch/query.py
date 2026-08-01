"""
WebSearch plugin query / action module for Launcher admin UI.

Entry points:
  query_data(project_root, params) -> dict
  handle_action(project_root, action, data) -> dict
"""

from __future__ import annotations

from typing import Any

try:
    from plugins.websearch.setup_status import (
        dismiss_setup_banner,
        get_setup_status,
        mark_login_done,
        spawn_login_setup,
        write_plugin_status,
    )
except ImportError:
    from setup_status import (  # type: ignore
        dismiss_setup_banner,
        get_setup_status,
        mark_login_done,
        spawn_login_setup,
        write_plugin_status,
    )


def query_data(project_root: str, params: dict | None = None) -> dict[str, Any]:
    _ = project_root, params
    status = get_setup_status()
    write_plugin_status(status)
    return {
        "ok": True,
        "title": "Web Search / Bing 登录",
        "description": (
            "WebSearch 用本机 Chrome 持久档案访问 Bing。"
            "首次部署建议完成一次 headed 登录，搜索质量会明显接近手动浏览器。"
        ),
        **status,
    }


def handle_action(project_root: str, action: str, data: dict | None = None) -> dict[str, Any]:
    _ = project_root, data
    action = (action or "").strip()

    if action in ("status", "refresh_status", "refresh"):
        status = get_setup_status()
        write_plugin_status(status)
        return {"ok": True, "action": action, **status}

    if action in ("mark_login_done", "mark_done"):
        status = mark_login_done(source="ui_mark")
        return {"ok": True, "action": action, **status}

    if action in ("dismiss_banner", "dismiss"):
        status = dismiss_setup_banner()
        return {"ok": True, "action": action, **status}

    if action in ("launch_login_setup", "login_setup", "open_login"):
        result = spawn_login_setup()
        return {"action": action, **result}

    return {"ok": False, "error": f"Unknown action: {action}"}
