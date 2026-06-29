# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from opensquad.gateway_adapter import GatewayAdapter
from opensquad.registry import ToolRegistry


@dataclass
class BootPhaseArtifacts:
    config: dict[str, Any]
    agent_dir: str
    registry: ToolRegistry
    chat_api: Any
    model_name: str
    vision_config: dict[str, Any]
    memory_manager: Any | None
    adapter: GatewayAdapter | None


class RunnerBootPhases:
    """Explicit bootstrap phases for `runner_bootstrap`."""

    def __init__(self, config_path: str, loader: Callable[[str], dict[str, Any]]):
        self.config_path = config_path
        self.loader = loader

    def resolve_config(self) -> tuple[dict[str, Any], str]:
        config = self.loader(self.config_path)
        from opensquad.runner_bootstrap import resolve_agent_dir

        agent_dir = resolve_agent_dir(self.config_path)
        return config, agent_dir

    def build_runtime_artifacts(self, config: dict[str, Any], agent_dir: str) -> BootPhaseArtifacts:
        from opensquad.runner_bootstrap import (
            create_chat_api,
            create_gateway_adapter,
            create_memory_manager,
            create_tool_registry,
        )

        registry = create_tool_registry(config)
        chat_api, model_name = create_chat_api(config, agent_dir)
        vision_config = config.get("vision", {})
        memory_manager = create_memory_manager(config, agent_dir)
        adapter = create_gateway_adapter(config)
        return BootPhaseArtifacts(
            config=config,
            agent_dir=agent_dir,
            registry=registry,
            chat_api=chat_api,
            model_name=model_name,
            vision_config=vision_config,
            memory_manager=memory_manager,
            adapter=adapter,
        )
