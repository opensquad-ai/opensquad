"""
SenseVoice plugin query / action module for Launcher admin UI.

Entry points:
  query_data(project_root, params) -> dict
  handle_action(project_root, action, data) -> dict
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.sensevoice.model_store import get_status, start_download

logger = logging.getLogger("plugins.sensevoice.query")


def query_data(project_root: str, params: dict | None = None) -> dict[str, Any]:
    """Return model download / readiness status for the SenseVoice panel."""
    _ = project_root, params
    status = get_status()
    return {
        "ok": True,
        "plugin": "sensevoice",
        "title": "系统内置 SenseVoice ASR",
        "description": (
            "SenseVoice-Small INT8 ONNX 本地语音转文本。"
            "首次使用请先下载模型（约 150MB），再在「服务管理」中启动 SenseVoice 服务，"
            "然后在 Agent Web 语音配置的 ASR 输入中选择「系统内置 SenseVoice ASR」。"
        ),
        "model_source": "https://www.modelscope.cn/models/iic/SenseVoiceSmall",
        **status,
    }


def handle_action(project_root: str, action: str, data: dict | None = None) -> dict[str, Any]:
    """Execute SenseVoice plugin actions (download_model / refresh_status)."""
    _ = project_root
    data = data or {}
    action = (action or "").strip()

    if action in ("download_model", "download"):
        force = bool(data.get("force", False))
        result = start_download(force=force)
        return {"ok": True, "action": action, **result}

    if action in ("status", "refresh_status", "refresh"):
        return {"ok": True, "action": action, **get_status()}

    return {"ok": False, "error": f"Unknown action: {action}"}
