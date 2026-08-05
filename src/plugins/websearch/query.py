"""
WebSearch plugin query / action module for Launcher admin UI.

Entry points:
  query_data(project_root, params) -> dict
  handle_action(project_root, action, data) -> dict

This file is invoked by the Launcher's ``/api/plugins/{name}/data`` and
``/api/plugins/{name}/action`` endpoints. It is the central place where
the admin UI talks to:

- ``setup_status`` — Bing/browser-profile first-deploy status (legacy)
- ``reranker_model_store`` — Qwen3-Reranker model download / readiness

Note: reranker weights are large (~1.2GB) and may need to be downloaded
on first run.  The UI surfaces a "Download" button (mirroring the
SenseVoice pattern) instead of relying on the silent background
auto-download that ``service/reranker_sidecar.py`` performs.
"""

from __future__ import annotations

from typing import Any

try:
    from plugins.websearch.reranker_model_store import (
        get_status as reranker_status,
    )
    from plugins.websearch.reranker_model_store import (
        start_download as reranker_start_download,
    )
except ImportError:
    from reranker_model_store import (  # type: ignore[no-redef]
        get_status as reranker_status,
    )
    from reranker_model_store import (
        start_download as reranker_start_download,
    )

try:
    from plugins.websearch.setup_status import (
        dismiss_setup_banner,
        get_browser_config,
        get_setup_status,
        mark_login_done,
        set_browser,
        spawn_login_setup,
        write_plugin_status,
    )
except ImportError:
    from setup_status import (  # type: ignore[no-redef]
        dismiss_setup_banner,
        get_browser_config,
        get_setup_status,
        mark_login_done,
        set_browser,
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
        # Reranker model status is included so the UI can show both
        # Bing-login readiness and the reranker download state in one
        # panel without an extra round-trip.
        "reranker": reranker_status(),
        "browser_config": get_browser_config(),
        **status,
    }


def handle_action(project_root: str, action: str, data: dict | None = None) -> dict[str, Any]:
    _ = project_root, data
    action = (action or "").strip()
    data = data or {}

    # ── Reranker model actions ───────────────────────────────────────
    if action in ("download_reranker", "download_reranker_model", "reranker_download"):
        result = reranker_start_download(force=bool(data.get("force", False)))
        return {"ok": True, "action": action, **result}

    if action in ("reranker_status", "reranker_refresh"):
        return {"ok": True, "action": action, **reranker_status()}

    # ── Bing login actions (legacy) ──────────────────────────────────
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

    if action in ("set_browser", "choose_browser"):
        result = set_browser(str(data.get("browser", "")).strip())
        return {"action": action, "browser_config": get_browser_config(), **result}

    if action in ("browser_config", "get_browser"):
        return {"ok": True, "action": action, "browser_config": get_browser_config()}

    return {"ok": False, "error": f"Unknown action: {action}"}
