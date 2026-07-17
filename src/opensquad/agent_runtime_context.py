"""Process-level agent runtime context (config, ids) for plugins/tools."""

from __future__ import annotations

from typing import Any

agent_config: dict[str, Any] = {}
agent_id: str = ""
agent_dir: str = ""


def set_context(*, config: dict[str, Any] | None = None, agent_id_value: str = "", agent_dir_value: str = "") -> None:
    global agent_config, agent_id, agent_dir
    if config is not None:
        agent_config = config
    if agent_id_value:
        agent_id = agent_id_value
    if agent_dir_value:
        agent_dir = agent_dir_value
