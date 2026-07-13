from __future__ import annotations

import asyncio
import json
import logging
import os
import warnings
from dataclasses import dataclass
from typing import Any

from opensquad.chat_api import ChatAPI
from opensquad.claude_api import ClaudeAPI
from opensquad.gateway_adapter import GatewayAdapter
from opensquad.registry import ToolRegistry
from opensquad.runner_boot_phases import RunnerBootPhases
from opensquad.sdk import AgentConfig
from opensquad.system_config import syscfg
from opensquad.xml_parser import StreamingTagParser

from .runner import AgentRunner

logger = logging.getLogger(__name__)


@dataclass
class RunnerBootstrapResult:
    config: dict[str, Any]
    config_path: str
    agent_dir: str
    registry: ToolRegistry
    chat_api: ChatAPI | ClaudeAPI
    vision_config: dict[str, Any]
    memory_manager: Any | None
    runner: AgentRunner
    adapter: GatewayAdapter | None
    model_name: str


def configure_runner_process_warnings() -> None:
    warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport")
    if os.name == "nt":
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)


def load_runner_config(config_path: str) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as file_obj:
        return json.load(file_obj)


def resolve_agent_dir(config_path: str) -> str:
    return os.path.dirname(os.path.abspath(config_path))


def resolve_prompt_path(path: str, agent_dir: str) -> str:
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    agent_relative = os.path.join(agent_dir, path)
    if os.path.exists(agent_relative):
        return agent_relative
    return os.path.abspath(path)


def read_prompt_file(path: str, agent_dir: str) -> str:
    resolved_path = resolve_prompt_path(path, agent_dir)
    if resolved_path and os.path.exists(resolved_path):
        with open(resolved_path, encoding="utf-8") as file_obj:
            return file_obj.read()
    logger.warning("Prompt file not found: %s (looked in %s and cwd)", path, agent_dir)
    return ""


def build_full_prompt(config: dict[str, Any], agent_dir: str) -> str:
    prompt_conf = config.get("prompt", {})
    base_prompt = read_prompt_file(prompt_conf.get("base", "prompts/base.md"), agent_dir)
    role_prompt = read_prompt_file(prompt_conf.get("role", "role.md"), agent_dir)
    full_prompt = base_prompt + "\n\n" + role_prompt
    if not full_prompt.strip():
        return "You are a helpful assistant."
    return full_prompt


def create_tool_registry(config: dict[str, Any]) -> ToolRegistry:
    registry = ToolRegistry()
    tools_list = config.get("tools", [])

    try:
        from opensquad.tools import system

        registry.register(system, "system", level="core")
    except ImportError as exc:
        logger.warning("Failed to import system tool: %s", exc)

    try:
        from opensquad.tools import filesystem

        registry.register(filesystem, "filesystem", level="core")
    except ImportError as exc:
        logger.warning("Failed to import filesystem tool: %s", exc)

    try:
        from opensquad.tools import memory as long_memory_tool
    except ImportError as exc:
        logger.warning("Failed to import long_memory tool module: %s", exc)
        long_memory_tool = None

    if "long_memory" in tools_list and long_memory_tool is not None:
        registry.register(long_memory_tool, "memory")

    # Choice tools (propose_options) — always available for plan decision UI
    try:
        from opensquad.tools import choice_tools

        registry.register(choice_tools, "choice_tools", level="core")
    except ImportError as exc:
        logger.warning("Failed to import choice_tools: %s", exc)

    if "agent_setup" in tools_list:
        try:
            from opensquad.tools import agent_setup

            registry.register(agent_setup, "agent_setup")
        except ImportError:
            logger.warning("Failed to import agent_setup tool")

    if "im" in tools_list:
        try:
            from opensquad.tools import im

            registry.register(im, "im")
        except ImportError:
            logger.warning("Failed to import im tool")

    return registry


def _validate_api_key(config: dict[str, Any], api_key: str | None) -> None:
    if api_key not in (None, ""):
        return
    agent_name = config.get("agent_name", "unknown")
    logger.error(
        "[Boot] Agent '%s' has NO api_key configured in model card. "
        "The agent will start but cannot call any LLM. "
        "Set api_key in model_cards/*.json or via environment variable.",
        agent_name,
    )


def create_chat_api(config: dict[str, Any], agent_dir: str) -> tuple[ChatAPI | ClaudeAPI, str]:
    model_conf = config.get("model", {})
    api_key = model_conf.get("api_key")
    base_url = model_conf.get("base_url")
    model_name = model_conf.get("model_name", "gpt-3.5-turbo")
    token_max = model_conf.get("token_max", 128000)

    _validate_api_key(config, api_key)

    full_prompt = build_full_prompt(config, agent_dir)
    stream_parser = StreamingTagParser(handlers={})

    if model_name.startswith("claude"):
        chat_api = ClaudeAPI(
            api_key, model_name, base_url, full_prompt, stream_parser=stream_parser, token_max=token_max
        )
    else:
        chat_api = ChatAPI(api_key, model_name, base_url, full_prompt, stream_parser=stream_parser, token_max=token_max)

    chat_api.history_dir = os.path.join(agent_dir, "history")
    os.makedirs(chat_api.history_dir, exist_ok=True)
    return chat_api, model_name


def create_memory_manager(config: dict[str, Any], agent_dir: str) -> Any | None:
    if "long_memory" not in config.get("tools", []):
        return None
    try:
        from opensquad.memory_manager import MemoryManager
        from opensquad.tools.agent_memory_tool.memory import AgentMemory

        agent_name = config.get("agent_name", "unknown_agent")
        memory_dir = os.path.join(agent_dir, "memory")
        agent_memory = AgentMemory(data_dir=memory_dir)
        memory_manager = MemoryManager(agent_memory, agent_name)
        logger.info("Memory Manager initialized successfully")
        return memory_manager
    except ImportError:
        logger.warning("long_memory requires the external 'agent_memory_tool' package; skipping long-memory runtime.")
        return None


def create_gateway_adapter(config: dict[str, Any]) -> GatewayAdapter | None:
    gateway_conf = config.get("gateway", {})
    if not gateway_conf.get("enabled"):
        return None
    agent_config = AgentConfig(
        gateway_url=gateway_conf.get("url", syscfg.gateway_register_url()),
        agent_id=config.get("agent_id", "unknown"),
        agent_name=config.get("agent_name", "Unknown Agent"),
        agent_type=config.get("agent_type", "general"),
        capabilities=config.get("capabilities", []),
        description=config.get("description", ""),
        node_id=syscfg.node_id(),
        node_label=syscfg.node_label(),
    )
    return GatewayAdapter(agent_config)


def bootstrap_runner(config_path: str) -> RunnerBootstrapResult:
    phases = RunnerBootPhases(config_path=config_path, loader=load_runner_config)
    config, agent_dir = phases.resolve_config()
    artifacts = phases.build_runtime_artifacts(config, agent_dir)
    runner = AgentRunner(
        artifacts.chat_api,
        artifacts.registry,
        vision_config=artifacts.vision_config,
        memory_manager=artifacts.memory_manager,
        agent_id=artifacts.config.get("agent_id", "unknown"),
        config_path=config_path,
    )
    return RunnerBootstrapResult(
        config=artifacts.config,
        config_path=config_path,
        agent_dir=artifacts.agent_dir,
        registry=artifacts.registry,
        chat_api=artifacts.chat_api,
        vision_config=artifacts.vision_config,
        memory_manager=artifacts.memory_manager,
        runner=runner,
        adapter=artifacts.adapter,
        model_name=artifacts.model_name,
    )


async def run_bootstrapped_agent(bootstrap: RunnerBootstrapResult) -> None:
    if bootstrap.adapter:
        asyncio.create_task(bootstrap.adapter.start())

    logger.info("Agent '%s' started successfully.", bootstrap.config.get("agent_name"))
    logger.info("ID: %s", bootstrap.config.get("agent_id"))
    logger.info("Model: %s", bootstrap.model_name)

    await bootstrap.runner.run()
