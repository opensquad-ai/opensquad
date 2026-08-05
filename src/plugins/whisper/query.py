"""
Whisper plugin query / action module for Launcher admin UI.

Entry points:
  query_data(project_root, params) -> dict
  handle_action(project_root, action, data) -> dict

The admin UI calls ``/api/plugins/whisper/data`` to learn which model is
selected, what is downloaded, and where.  ``/api/plugins/whisper/action``
with ``action=download_model`` triggers a background download with
mirror fallback (hf-mirror first, then huggingface.co).
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from plugins.whisper.model_store import (
        get_status as whisper_status,
    )
    from plugins.whisper.model_store import (
        list_available_models,
    )
    from plugins.whisper.model_store import (
        start_download as whisper_start_download,
    )
except ImportError:
    from model_store import (  # type: ignore[no-redef]
        get_status as whisper_status,
    )
    from model_store import (
        list_available_models,
    )
    from model_store import (
        start_download as whisper_start_download,
    )

logger = logging.getLogger("plugins.whisper.query")


def query_data(project_root: str, params: dict | None = None) -> dict[str, Any]:
    _ = project_root, params
    status = whisper_status()
    return {
        "ok": True,
        "plugin": "whisper_transcribe",
        "title": "Whisper 语音转文本",
        "description": (
            "Whisper 离线语音转文本服务。首次使用请先选择模型大小并点击「下载模型」；"
            "下载完成后启动服务。默认从 openai-whisper 官方 Azure CDN 拉取 .pt，"
            "若失败自动切换到 HF 镜像（hf-mirror.com，国内友好）和 HF 官方。"
            "HF 仓库的 transformers 格式权重会被重新打包为 openai-whisper 兼容的 .pt。"
        ),
        "mirrors": [
            "https://openaipublic.azureedge.net/main/whisper/models",
            "https://hf-mirror.com",
            "https://huggingface.co",
        ],
        **status,
    }


def handle_action(project_root: str, action: str, data: dict | None = None) -> dict[str, Any]:
    _ = project_root
    data = data or {}
    action = (action or "").strip()

    if action in ("download_model", "download"):
        result = whisper_start_download(
            model=data.get("model"),
            force=bool(data.get("force", False)),
        )
        return {"ok": True, "action": action, **result}

    if action in ("status", "refresh_status", "refresh"):
        return {"ok": True, "action": action, **whisper_status()}

    if action in ("list_models", "models"):
        return {
            "ok": True,
            "action": action,
            "models": list_available_models(),
        }

    return {"ok": False, "error": f"Unknown action: {action}"}
