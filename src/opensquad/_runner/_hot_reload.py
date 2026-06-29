# -*- coding: utf-8 -*-
"""
Hot-reload module -- handles config.json and plugin hot-reload logic.

Extracted from runner.py. Contains ``do_plugin_reload`` which can be called
by a tool mid-workflow without waiting for the agent to be idle.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from opensquad.tool import logger

__all__ = ["HotReloadManager"]


# Module-level runner reference for tool hot-reload.
# Each agent is an independent process, so a module-level singleton is safe.
_active_runner: Any = None


def get_active_runner() -> Any:
    """Return the currently active AgentRunner instance (or None)."""
    return _active_runner


def set_active_runner(runner: Any) -> None:
    """Register the runner as the active instance."""
    global _active_runner
    _active_runner = runner


class HotReloadManager:
    """
    Manages hot-reload of config.json and plugin tools.

    Extracted from runner.py to reduce its size and isolate reload logic.
    """

    def __init__(self, runner: Any):
        self.runner = runner

    def do_plugin_reload(self) -> dict[str, Any]:
        """
        Immediately reload plugins: re-read config.json to get the latest tools list,
        register newly added tools, and unload removed tools.

        Can be called by a tool mid-workflow without waiting for the agent to be idle.
        """
        if self.runner is None:
            return {"success": False, "error": "Runner not initialized"}

        pm = self.runner._plugin_manager
        if pm is None:
            return {"success": False, "error": "Plugin manager not available"}

        config_path = self.runner._config_path
        new_tool_names = self.runner._agent_tool_names
        new_cfg: dict = {}

        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    new_cfg = json.load(f)
                new_tool_names = new_cfg.get("tools", self.runner._agent_tool_names)
                self.runner._agent_tool_names = new_tool_names
                self.runner._agent_tool_levels = new_cfg.get("tool_levels", {})
                self.runner._config_mtime = os.path.getmtime(config_path)
            except Exception as e:
                logger.warning(
                    "[HotReload] Failed to read config.json: %s", e
                )

        # Re-register built-in core tools (im, collaboration, etc.)
        if new_cfg and self.runner._agent_dir:
            try:
                from opensquad.agents_boot import (
                    register_builtin_tools_sync,
                )

                register_builtin_tools_sync(
                    new_cfg,
                    self.runner.tool_registry,
                    self.runner._agent_dir,
                )
                logger.info(
                    "[HotReload] Built-in tools re-registered"
                )
            except Exception as _e:
                logger.warning(
                    "[HotReload] Built-in tool re-registration failed: %s",
                    _e,
                )

        reload_result = pm.reload_plugins(
            registry=self.runner.tool_registry,
            agent_id=self.runner._agent_id,
            agent_tool_names=new_tool_names,
        )
        pm.register_tools_to_agent(
            registry=self.runner.tool_registry,
            agent_id=self.runner._agent_id,
            agent_tool_names=new_tool_names,
            agent_tool_levels=self.runner._agent_tool_levels,
        )
        logger.info(
            "[HotReload] Immediate plugin reload: loaded=%s, unloaded=%s",
            reload_result.get("loaded"),
            reload_result.get("unloaded"),
        )
        return {
            "success": True,
            "loaded": reload_result.get("loaded", []),
            "unloaded": reload_result.get("unloaded", []),
            "active_tools": new_tool_names,
        }

    def check_config_reload(self) -> tuple[bool, bool, dict]:
        """
        Check if config.json has changed and return what needs reloading.

        Returns:
            (tools_changed, model_changed, new_config)
        """
        config_path = self.runner._config_path
        if not config_path or not os.path.isfile(config_path):
            return False, False, {}

        try:
            mtime = os.path.getmtime(config_path)
        except OSError:
            return False, False, {}

        if mtime <= self.runner._config_mtime:
            return False, False, {}

        self.runner._config_mtime = mtime
        try:
            with open(config_path, "r", encoding="utf-8") as _f:
                new_cfg = json.load(_f)
        except Exception:
            return False, False, {}

        new_tools = new_cfg.get("tools", [])
        new_levels = new_cfg.get("tool_levels", {})
        new_model = new_cfg.get("model", {})

        tools_changed = (
            new_tools != self.runner._agent_tool_names
            or new_levels != self.runner._agent_tool_levels
        )
        model_changed = new_model != self.runner._model_config

        if tools_changed:
            self.runner._agent_tool_names = new_tools
            self.runner._agent_tool_levels = new_levels

        if model_changed:
            self.runner._model_config = new_model

        return tools_changed, model_changed, new_cfg
