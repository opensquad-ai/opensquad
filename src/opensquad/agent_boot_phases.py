from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

# Strong references to background bridge tasks so they are not garbage-collected
# before completing. asyncio.create_task() does not hold a strong ref in all
# Python versions; without this the bridge task can silently vanish.
_bridge_bg_tasks: set[asyncio.Task] = set()
_bridge_ws_tasks: set[asyncio.Task] = set()

from opensquad import AgentRunner, bus
from opensquad.chat_api import ChatAPI
from opensquad.claude_api import ClaudeAPI
from opensquad.google_api import GoogleAPI
from opensquad.skill_loader import init_skill_runtime, load_skills_from_config, register_skill_tools
from opensquad.system_config import syscfg
from opensquad.xml_parser import StreamingTagParser


@dataclass
class BootRuntimeArtifacts:
    data_dir: str
    history_dir: str
    session_manager: Any
    state_manager: Any
    default_wake_mode: str


@dataclass
class ChatRuntimeArtifacts:
    chat_api: Any
    provider: str
    model_config: Any
    model_cfg: dict[str, Any]
    parser: Any
    vision_config: dict[str, Any]


@dataclass
class EarlyRunnerArtifacts:
    runner: AgentRunner
    runner_task: Any


@dataclass
class PluginRuntimeArtifacts:
    plugin_manager: Any
    skills: list[Any]
    memory_manager: Any | None


@dataclass
class ContextRuntimeArtifacts:
    context_module: Any
    hooks: dict[str, Any]


class AgentBootPhases:
    """Explicit boot phases for `agents_boot.py`."""

    def __init__(self, tool_modules: dict[str, str], mandatory_tools: set[str], core_tools: set[str]):
        self.tool_modules = tool_modules
        self.mandatory_tools = mandatory_tools
        self.core_tools = core_tools

    def build_tool_name_list(self, config: dict[str, Any]) -> list[str]:
        configured_tools = config.get("tools", [])
        return list(self.mandatory_tools) + [name for name in configured_tools if name not in self.mandatory_tools]

    def register_builtin_tools(self, config: dict[str, Any], registry: Any, agent_dir: str) -> None:
        t0 = __import__("time").perf_counter()
        tool_names = self.build_tool_name_list(config)
        agent_id = config.get("agent_id", "")
        tool_levels = config.get("tool_levels", {})
        for name in tool_names:
            module_path = self.tool_modules.get(name)
            if not module_path:
                continue
            try:
                module = importlib.import_module(module_path)
                default_level = "core" if name in self.core_tools else "extended"
                level = tool_levels.get(name, default_level)
                registry.register(module, name, level=level)
                if hasattr(module, "set_agent_id") and agent_id:
                    module.set_agent_id(agent_id)
                if name == "filesystem" and hasattr(module, "set_allowed_dirs"):
                    self._configure_filesystem_module(module, config, agent_dir)
            except Exception as exc:
                logging.error(f"[Boot] Failed to register built-in tool '{name}': {exc}")
        from opensquad.structured_log import perf_event

        perf_event(
            "boot",
            "builtin_tools_ready",
            agent_id=agent_id,
            elapsed_ms=int((__import__("time").perf_counter() - t0) * 1000),
            tool_count=len(tool_names),
        )

    async def initialize_runtime_infrastructure(self, config: dict[str, Any], registry: Any, agent_dir: str) -> None:
        from opensquad.structured_log import perf_event

        t0 = __import__("time").perf_counter()
        await self._initialize_mcp_runtime(config, registry, agent_dir)
        perf_event(
            "boot",
            "mcp_runtime_ready",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((__import__("time").perf_counter() - t0) * 1000),
        )

    async def setup_connections(self, config: dict[str, Any], logger: Any, data_dir: str = "") -> None:
        boot_t0 = __import__("time").perf_counter()
        from opensquad.structured_log import perf_event

        await self._setup_web_server(config, logger)
        await self._setup_gateway_adapter(config, logger, boot_t0)
        self._setup_group_chat_bridge(config, logger, data_dir)
        perf_event(
            "boot",
            "connections_scheduled",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((__import__("time").perf_counter() - boot_t0) * 1000),
        )

    async def initialize_agent_runtime(
        self, config: dict[str, Any], agent_dir: str, input_hub: Any, agent_logger: Any
    ) -> BootRuntimeArtifacts:
        from opensquad._context import get_current_context
        from opensquad.structured_log import perf_event

        t0 = __import__("time").perf_counter()
        input_hub.set_agent_context(agent_dir)
        input_hub._check_session_cwd()
        data_dir = os.path.join(agent_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        import opensquad.session_manager as _sm
        import opensquad.state_manager as _sm_state
        from opensquad.state_manager import reinit_state_manager

        _reinit_result = _sm.reinit_session_manager(save_dir=os.path.join(data_dir, "sessions"))
        reinit_state_manager(state_file=os.path.join(data_dir, "ai_state.json"))
        agent_logger.info(f"[Boot] Data isolation: {data_dir}")

        # IMPORTANT: Use _sm.session_manager (module variable) instead of the local
        # reference from 'from ... import session_manager'.  reinit_session_manager()
        # assigns a NEW instance to the module variable, but a 'from' import creates
        # a local reference that still points to the OLD instance.  Using the module
        # variable guarantees we get the post-reinit instance.
        # Same trap applies to state_manager: reinit_state_manager() rebinds the module
        # global, so we MUST read _sm_state.state_manager (module variable) rather than
        # a `from ... import state_manager` local — otherwise ctx.state_manager and the
        # system.set_wake_mode tool (which reads the module global) would point at
        # DIFFERENT instances, and runtime wake-mode changes would never reach the
        # message_router (which reads ctx.state_manager via get_state_manager()).
        state_manager = _sm_state.state_manager
        _sm.session_manager.start_async_writer()
        agent_logger.info("[Boot] Session async writer started")

        await state_manager.set_state("idle")
        has_history = len(_sm.session_manager.get_messages()) > 0
        # Respect the agent's configured default_wake_mode. The previous
        # "if not has_history: normal" override silently turned every
        # freshly-deployed agent into normal mode, defeating a config that
        # explicitly asked for strict (e.g. coder/qa, which should only
        # wake on @mention/delegation). Now config wins; pm keeps normal,
        # coder/qa keep strict, regardless of session history.
        default_wake_mode = config.get("default_wake_mode", "strict")
        if default_wake_mode in ("strict", "normal"):
            await state_manager.set_wake_mode(default_wake_mode)
        agent_logger.info(f"[Boot] State reset to idle, wake_mode={default_wake_mode}, has_history={has_history}")
        perf_event(
            "boot",
            "agent_runtime_ready",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((__import__("time").perf_counter() - t0) * 1000),
            has_history=has_history,
        )

        # ── Phase 1b: Populate AgentContext with runtime managers ──
        ctx = get_current_context()
        if ctx is not None:
            ctx.session_manager = _sm.session_manager
            ctx.state_manager = _sm_state.state_manager  # post-reinit module instance
            from opensquad.event_pipeline import event_pipeline
            from opensquad.message_router import message_router
            from opensquad.sleep_controller import sleep_controller

            ctx.event_pipeline = event_pipeline
            ctx.message_router = message_router
            ctx.sleep_controller = sleep_controller

        return BootRuntimeArtifacts(
            data_dir=data_dir,
            history_dir=os.path.join(data_dir, "ai_his_talk"),
            session_manager=_sm.session_manager,
            state_manager=state_manager,
            default_wake_mode=default_wake_mode,
        )

    def initialize_chat_runtime(
        self, config: dict[str, Any], system_prompt: str, history_dir: str, agent_logger: Any
    ) -> ChatRuntimeArtifacts:
        from opensquad.structured_log import perf_event

        t0 = __import__("time").perf_counter()
        model_cfg = config.get("model", {})
        parser = StreamingTagParser({})
        provider = model_cfg.get("api_protocol", "openai")
        model_name = model_cfg.get("model_name", "").lower()

        if provider == "openai" and ("claude" in model_name or "anthropic" in model_name):
            provider = "claude"
            agent_logger.info(f"[Boot] Auto-detected Claude model: {model_name}, switching provider to 'claude'")
        if provider == "openai" and "gemini" in model_name:
            provider = "google"
            agent_logger.info(f"[Boot] Auto-detected Gemini model: {model_name}, switching provider to 'google'")

        from opensquad.model_config import ModelConfig

        model_config = ModelConfig.from_dict(model_cfg, prompt=system_prompt, provider=provider)
        if provider in ["claude", "anthropic"]:
            model_config.timeout = 1200.0
        elif provider in ["google", "gemini"]:
            model_config.token_max = 1_000_000

        if provider in ["claude", "anthropic"]:
            agent_logger.info("[Boot] ENGINE SWITCH: Using ClaudeAPI (Anthropic Native Protocol)")
            agent_logger.info(f"   Model: {model_config.model}, Max Tokens: {model_config.token_max}")
            chat_api = ClaudeAPI(config=model_config, stream_parser=parser)
        elif provider in ["google", "gemini"]:
            agent_logger.info("[Boot] ENGINE SWITCH: Using GoogleAPI (Google Gemini Native Protocol)")
            agent_logger.info(f"   Model: {model_config.model}, Max Tokens: {model_config.token_max}")
            chat_api = GoogleAPI(config=model_config, stream_parser=parser)
        else:
            agent_logger.info("[Boot] ENGINE SWITCH: Using ChatAPI (OpenAI Compatible Protocol)")
            agent_logger.info(f"   Model: {model_config.model}, Max Tokens: {model_config.token_max}")
            chat_api = ChatAPI(config=model_config, stream_parser=parser)

        os.makedirs(history_dir, exist_ok=True)
        chat_api.history_dir = history_dir
        chat_api.output_media_dir = syscfg.workspace_uploads_dir()
        perf_event(
            "boot",
            "chat_runtime_ready",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((__import__("time").perf_counter() - t0) * 1000),
            provider=provider,
        )
        return ChatRuntimeArtifacts(
            chat_api=chat_api,
            provider=provider,
            model_config=model_config,
            model_cfg=model_cfg,
            parser=parser,
            vision_config={"is_img_mode": model_cfg.get("is_image", False)},
        )

    def initialize_delegate_tool(
        self, provider: str, model_cfg: dict[str, Any], system_prompt: str, tool_registry: Any, agent_logger: Any
    ) -> None:
        try:
            from opensquad.tools.delegate import init_delegate_tool

            delegate_config = {
                "provider": provider,
                "api_key": model_cfg.get("api_key", ""),
                "base_url": model_cfg.get("base_url", ""),
                "model": model_cfg.get("model_name", ""),
                "token_max": model_cfg.get("token_max", 32000),
                "temperature": model_cfg.get("temperature", 0.3),
                "timeout": model_cfg.get("timeout", 60.0),
                "is_img_model": model_cfg.get("is_image", False),
                "is_audio_model": model_cfg.get("is_audio_model", False),
                "is_video_model": model_cfg.get("is_video", False),
                "use_file_api": model_cfg.get("use_file_api", False),
                "file_api_size_threshold": model_cfg.get("file_api_size_threshold", 4 * 1024 * 1024),
                "tool_call_mode": model_cfg.get("tool_call_mode", "auto"),
                "tool_filter": model_cfg.get("tool_filter", "all"),
                "parent_prompt": system_prompt,
            }
            init_delegate_tool(delegate_config, tool_registry)
            agent_logger.info("[Boot] delegate_task tool initialized")
        except Exception as exc:
            agent_logger.error(f"[Boot] delegate_task init failed: {exc}")

    def start_early_runner(
        self,
        chat_api: Any,
        tool_registry: Any,
        agent_id: str,
        config: dict[str, Any],
        agent_dir: str,
        vision_config: dict[str, Any],
        boot_main_t0: float,
        agent_logger: Any,
        agent_context: Any = None,
        session_manager: Any = None,
        state_manager: Any = None,
    ) -> EarlyRunnerArtifacts:
        early_runner = AgentRunner(
            chat_api,
            tool_registry,
            hooks={},
            vision_config=vision_config,
            plugin_manager=None,
            agent_id=agent_id,
            agent_tool_names=config.get("tools", []),
            config_path=os.path.join(agent_dir, "config.json"),
            agent_context=agent_context,
            session_manager=session_manager,
            state_manager=state_manager,
        )

        async def _runner_with_crash_handler():
            try:
                await early_runner.run()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                agent_logger.exception(
                    "[Runner] CRASHED (agent=%s): %s. The runner task has died.",
                    agent_id,
                    exc,
                )

        runner_task = asyncio.create_task(_runner_with_crash_handler())
        agent_logger.info(
            "[BootPerf] phase_runner_started=%dms agent_id=%s",
            int((__import__("time").perf_counter() - boot_main_t0) * 1000),
            agent_id,
        )
        return EarlyRunnerArtifacts(runner=early_runner, runner_task=runner_task)

    def initialize_plugin_runtime(
        self,
        config: dict[str, Any],
        agent_dir: str,
        project_root: str,
        data_dir: str,
        tool_registry: Any,
        early_runner: AgentRunner,
        agent_name: str,
        agent_logger: Any,
        boot_main_t0: float,
    ) -> PluginRuntimeArtifacts:
        from opensquad.plugin_manager import PluginManager
        from opensquad.structured_log import perf_event

        t0 = __import__("time").perf_counter()
        plugin_manager = PluginManager(agent_id=config.get("agent_id", ""))
        t_plugin_discovery = __import__("time").perf_counter()
        plugin_manager.discover_and_load()
        t_plugin_register = __import__("time").perf_counter()
        plugin_tool_count = plugin_manager.register_tools_to_agent(
            registry=tool_registry,
            agent_id=config.get("agent_id", ""),
            agent_tool_names=config.get("tools", []),
            agent_tool_levels=config.get("tool_levels", {}),
        )
        if plugin_tool_count:
            agent_logger.info(f"[Boot] Plugin system registered {plugin_tool_count} tool(s)")
        perf_event(
            "boot",
            "plugins_ready",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((t_plugin_register - t_plugin_discovery) * 1000),
            discovery_ms=int((t_plugin_discovery - t0) * 1000),
            plugin_tool_count=plugin_tool_count,
        )
        early_runner._plugin_manager = plugin_manager
        # Phase 1d: sync plugin_manager back to AgentContext so downstream
        # consumers (AgentRunner, tests) can access it consistently.
        if getattr(early_runner, "_ctx", None) is not None:
            early_runner._ctx.plugin_manager = plugin_manager

        t_skills = __import__("time").perf_counter()
        skills = load_skills_from_config(config, agent_dir, project_root)
        if skills:
            register_skill_tools(skills, tool_registry)
            skill_names = [skill.display_name for skill in skills]
            agent_logger.info(f"[Boot] Skills loaded: {skill_names}")
        else:
            skills = []
        init_skill_runtime(skills, tool_registry)
        # Log tool inventory after plugin+skill registration so smoke tests
        # and crash logs can verify no namespace was silently lost.
        tool_registry.log_inventory(agent_logger)
        perf_event(
            "boot",
            "skills_ready",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((__import__("time").perf_counter() - t_skills) * 1000),
            skill_count=len(skills),
        )

        t_mem = __import__("time").perf_counter()
        memory_manager = self._initialize_long_memory_runtime(
            config=config,
            data_dir=data_dir,
            project_root=project_root,
            agent_name=agent_name,
            agent_logger=agent_logger,
        )
        perf_event(
            "boot",
            "long_memory_ready",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((__import__("time").perf_counter() - t_mem) * 1000),
            has_memory=memory_manager is not None,
        )
        perf_event(
            "boot",
            "plugin_runtime_total",
            agent_id=config.get("agent_id", ""),
            elapsed_ms=int((__import__("time").perf_counter() - t0) * 1000),
        )
        return PluginRuntimeArtifacts(
            plugin_manager=plugin_manager,
            skills=skills,
            memory_manager=memory_manager,
        )

    async def initialize_context_runtime(
        self,
        config: dict[str, Any],
        agent_dir: str,
        agent_id: str,
        agent_name: str,
        chat_api: Any,
        tool_registry: Any,
        input_hub: Any,
        project_root: str,
        memory_manager: Any | None,
        load_context_module: Any,
        agent_logger: Any,
    ) -> ContextRuntimeArtifacts:
        from opensquad.structured_log import perf_event

        t0 = __import__("time").perf_counter()
        from opensquad.context_base import init_standard_context

        init_standard_context(
            agent_md_path=os.path.join(agent_dir, "agent.md"),
            memory_manager=memory_manager,
            agent_config=config,
            agents_dir=os.path.dirname(agent_dir),
        )
        agent_logger.info("[Boot] Standard context base initialized")

        context_module = load_context_module(agent_dir)
        hooks: dict[str, Any] = {}
        if context_module:
            agent_logger.info(f"[Boot] context.py loaded from {agent_dir}")
            agent_context = {
                "config": config,
                "agent_dir": agent_dir,
                "agent_id": agent_id,
                "agent_name": agent_name,
                "chat_api": chat_api,
                "tool_registry": tool_registry,
                "bus": bus,
                "input_hub": input_hub,
                "project_root": project_root,
                "agent_memory": memory_manager,
                "memory_manager": memory_manager,
                "agent_md_path": os.path.join(agent_dir, "agent.md"),
            }
            if hasattr(context_module, "init"):
                try:
                    init_fn = context_module.init
                    if asyncio.iscoroutinefunction(init_fn):
                        await init_fn(agent_context)
                    else:
                        init_fn(agent_context)
                    agent_logger.info("[Boot] context.init() executed")
                except Exception as exc:
                    agent_logger.error(f"[Boot] context.init() failed: {exc}")
            if hasattr(context_module, "before_input"):
                hooks["before_input"] = context_module.before_input
                agent_logger.info("[Boot] context.before_input() registered as hook")
        else:
            agent_logger.info("[Boot] No context.py found, using defaults")
        perf_event(
            "boot",
            "context_runtime_ready",
            agent_id=agent_id,
            elapsed_ms=int((__import__("time").perf_counter() - t0) * 1000),
        )
        return ContextRuntimeArtifacts(context_module=context_module, hooks=hooks)

    def finalize_runner_runtime(
        self,
        early_runner: AgentRunner,
        hooks: dict[str, Any],
        memory_manager: Any | None,
        boot_main_t0: float,
        agent_id: str,
        agent_logger: Any,
    ) -> None:
        from opensquad.structured_log import perf_event

        early_runner._hooks = hooks
        early_runner._memory_manager = memory_manager
        # Phase 1d: sync memory_manager back to AgentContext
        if getattr(early_runner, "_ctx", None) is not None:
            early_runner._ctx.memory_manager = memory_manager
        total_ms = int((__import__("time").perf_counter() - boot_main_t0) * 1000)
        perf_event("boot", "agent_ready", agent_id=agent_id, elapsed_ms=total_ms)
        agent_logger.info(f"[BootPerf] phase_ready_full={total_ms}ms agent_id={agent_id}")
        bus.emit("agent_ready", {"agent_id": agent_id})

    async def await_runner_shutdown(self, early_runner: AgentRunner, runner_task: Any, session_manager: Any) -> None:
        try:
            while True:
                try:
                    await runner_task
                    break
                except asyncio.CancelledError:
                    logging.warning("[Boot] Runner task interrupted, restarting...")
                    runner_task = asyncio.create_task(early_runner.run())
                    continue
                except Exception:
                    raise
        finally:
            try:
                await session_manager.stop_async_writer(timeout=5.0)
            except Exception as exc:
                logging.warning(f"[Boot] Session writer stop failed: {exc}")

    def _configure_filesystem_module(self, module: Any, config: dict[str, Any], agent_dir: str) -> None:
        if hasattr(module, "set_config_path"):
            module.set_config_path(os.path.join(agent_dir, "config.json"))
        workspace_root = syscfg.get_workspace()
        global_dirs = syscfg.filesystem_workspace_dirs()
        fs_cfg = config.get("filesystem", {})
        agent_dirs = fs_cfg.get("workspace_dirs", [])
        merged: list[str] = []
        seen: set[str] = set()
        for path in [workspace_root, *global_dirs, *agent_dirs]:
            resolved = path if os.path.isabs(path) else os.path.abspath(os.path.join(workspace_root, path))
            if resolved not in seen:
                seen.add(resolved)
                merged.append(resolved)
        if merged:
            module.set_allowed_dirs(merged)
            logging.info(f"[Boot] filesystem workspace_dirs ({len(merged)}): {merged}")

    async def _initialize_mcp_runtime(self, config: dict[str, Any], registry: Any, agent_dir: str) -> None:
        mcp_cfg = config.get("mcp", {})
        if not mcp_cfg.get("enabled", True):
            return

        global_disabled: set[str] = set()
        try:
            from opensquad.json_cache import load_json_cached

            global_mcp_path = syscfg.workspace_data_dir("mcp_global.json")
            global_data = load_json_cached(global_mcp_path)
            for server_name, server_cfg in global_data.get("servers", {}).items():
                if not server_cfg.get("enabled", True):
                    global_disabled.add(server_name)
        except Exception as exc:
            logging.warning(f"[Boot] Failed to read mcp_global.json: {exc}")

        try:
            from opensquad.tools.mcp_adapter import init_mcp_adapter

            mcp_adapter = await init_mcp_adapter(
                agent_dir=agent_dir,
                global_disabled_servers=global_disabled,
            )
            registry.register_mcp_adapter(mcp_adapter, level="extended")
        except ImportError as exc:
            # ImportError here means the MCP SDK itself is missing from the
            # runtime (e.g. frozen bundle didn't bundle `mcp` package). This
            # is a BUILD BUG, not a runtime issue — escalate to error so it
            # is visible in crash logs instead of silently disabling MCP tools.
            logging.error(
                f"[Boot] MCP SDK import failed: {exc}. "
                f"This is a build/packaging bug — MCP tools will be unavailable. "
                f"In frozen builds, check PyInstaller spec includes the `mcp` package."
            )
        except (Exception, asyncio.CancelledError) as exc:
            # Runtime errors (MCP server connection failures, config issues,
            # etc.) are non-fatal — MCP tools are optional.
            logging.warning(f"[Boot] MCP adapter not available: {exc}")

    async def _setup_web_server(self, config: dict[str, Any], logger: Any) -> None:
        web_cfg = config.get("web_server", {})
        if not web_cfg.get("enabled", False):
            return
        start_port = web_cfg.get("port", syscfg.port("agent_web_server"))
        max_retries = 10
        try:
            from server import app as web_app
        except ModuleNotFoundError:
            logger.warning("[Boot] Web Server disabled: 'server.py' not found in agent directory")
            return

        import socket

        import uvicorn

        for attempt in range(max_retries):
            current_port = start_port + attempt
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    if sock.connect_ex(("localhost", current_port)) == 0:
                        logger.warning(f"[Boot] Port {current_port} is busy, trying {current_port + 1}...")
                        continue
                web_config = uvicorn.Config(web_app, host="0.0.0.0", port=current_port, log_level="error")
                web_server = uvicorn.Server(web_config)
                asyncio.create_task(web_server.serve())
                logger.info(f"[Boot] Web Server running on port {current_port}")
                if current_port != start_port:
                    config.setdefault("web_server", {})["port"] = current_port
                return
            except Exception as exc:
                logger.error(f"[Boot] Web Server start failed on {current_port}: {exc}")
                if attempt == max_retries - 1:
                    logger.error("[Boot] Could not find any available port for Web Server!")

    async def _setup_gateway_adapter(self, config: dict[str, Any], logger: Any, boot_t0: float) -> None:
        gw_cfg = config.get("gateway", {})
        logger.info(f"[Boot] _setup_gateway_adapter: enabled={gw_cfg.get('enabled')}, url={gw_cfg.get('url')}")
        if not gw_cfg.get("enabled", False):
            logger.info("[Boot] _setup_gateway_adapter: gateway not enabled, skipping")
            return
        try:
            from opensquad.gateway_adapter import AgentConfig, GatewayAdapter

            gw_agent_config = AgentConfig(
                gateway_url=gw_cfg.get("url", syscfg.gateway_register_url()),
                agent_id=config.get("agent_id", "unnamed-agent"),
                agent_name=config.get("agent_name", "Unnamed Agent"),
                agent_type=config.get("agent_type", "general"),
                capabilities=config.get("capabilities", []),
                description=config.get("description", ""),
                node_id=syscfg.node_id(),
                node_label=syscfg.node_label(),
                node_secret=syscfg.node_secret(),
            )
            adapter = GatewayAdapter(gw_agent_config)
            asyncio.create_task(adapter.start())
            dt_ms = int((__import__("time").perf_counter() - boot_t0) * 1000)
            logger.info(f"[BootPerf] gateway_adapter_task_scheduled={dt_ms}ms agent_id={gw_agent_config.agent_id}")
            logger.info(f"[Boot] Gateway Adapter connected (ID: {gw_agent_config.agent_id})")
        except Exception as exc:
            logger.error(f"[Boot] Gateway Adapter failed: {exc}")

    def _setup_group_chat_bridge(self, config: dict[str, Any], logger: Any, data_dir: str) -> None:
        group_cfg = config.get("group_chat", {})
        if not group_cfg.get("enabled", False):
            return

        async def bridge_connect_bg() -> None:
            from opensquad.bridge import create_bridge

            agent_bridge = create_bridge(config)
            max_retries = 3
            for retry in range(max_retries):
                try:
                    login_ok = False
                    for attempt in range(5):
                        # requests.* is synchronous — must not run on the asyncio loop
                        # or it blocks Gateway WS registration and the whole boot sequence.
                        if await asyncio.to_thread(agent_bridge.login):
                            login_ok = True
                            break
                        logger.warning(f"[Boot] ChatPro Bridge login attempt {attempt + 1}/5 failed, retrying in 3s...")
                        await asyncio.sleep(3)
                    if login_ok:
                        logger.info("[Boot] ChatPro Bridge login ok, writing profile + joining groups...")
                        self._write_chat_profile(data_dir, agent_bridge, config)
                        for gid in group_cfg.get("groups", []):
                            logger.info(f"[Boot] ChatPro Bridge joining group {gid}...")
                            result = await asyncio.to_thread(agent_bridge.join_group_api, gid)
                            logger.info(f"[Boot] ChatPro Bridge join {gid}: {result}")
                        ws_task = asyncio.create_task(agent_bridge.connect_ws())
                        _bridge_ws_tasks.add(ws_task)
                        ws_task.add_done_callback(_bridge_ws_tasks.discard)
                        logger.info("[Boot] ChatPro Bridge connected")
                        import opensquad.bridge as bridge_module

                        bridge_module.bridge = agent_bridge
                        return  # success — exit retry loop
                    else:
                        logger.error("[Boot] ChatPro Bridge login failed after 5 attempts")
                        return
                except asyncio.CancelledError:
                    # During boot, an anyio CancelScope cancellation can fire across
                    # all tasks (SDK, Runner, Bridge). The SDK/Runner recover via
                    # uncancel(); the Bridge must too — re-login and reconnect.
                    logger.warning(
                        f"[Boot] ChatPro Bridge cancelled during setup (retry {retry + 1}/{max_retries}), recovering..."
                    )
                    if retry < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    # Last retry failed — try one final shielded connect
                    logger.warning("[Boot] ChatPro Bridge retries exhausted, attempting shielded connect")
                    try:
                        if await asyncio.shield(asyncio.to_thread(agent_bridge.login)):
                            ws_task = asyncio.create_task(agent_bridge.connect_ws())
                            _bridge_ws_tasks.add(ws_task)
                            ws_task.add_done_callback(_bridge_ws_tasks.discard)
                            import opensquad.bridge as bridge_module

                            bridge_module.bridge = agent_bridge
                            logger.info("[Boot] ChatPro Bridge connected (shielded recovery)")
                    except Exception as exc:
                        logger.error(f"[Boot] ChatPro Bridge shielded recovery failed: {exc}")
                    return
                except Exception as exc:
                    logger.error(f"[Boot] ChatPro Bridge failed: {exc}", exc_info=True)
                    return

        task = asyncio.create_task(bridge_connect_bg())
        _bridge_bg_tasks.add(task)
        task.add_done_callback(_bridge_bg_tasks.discard)

    def _write_chat_profile(self, data_dir: str, agent_bridge: Any, config: dict[str, Any]) -> None:
        """Persist group-chat display name/avatar for launcher + web UI.

        Canonical path: ``data/profile.json`` (what launcher ``_read_chat_profile`` reads).
        Also mirrors to legacy ``data/group_chat/profile.json``.
        """
        from opensquad.avatar_utils import ensure_agent_avatar
        from opensquad.json_cache import invalidate_json_cache, load_json_cached

        display_name = (
            getattr(agent_bridge, "user_name", None)
            or getattr(agent_bridge, "nickname", None)
            or config.get("agent_name", "")
        )
        seed = str(getattr(agent_bridge, "user_id", None) or config.get("agent_name", "") or "agent")
        avatar_url = ensure_agent_avatar(
            getattr(agent_bridge, "user_avatar", None) or getattr(agent_bridge, "avatar", None) or "",
            seed,
        )
        # Keep bridge in sync so later callers see the resolved avatar.
        try:
            agent_bridge.user_avatar = avatar_url
        except Exception:
            pass

        payload = {"name": display_name, "avatar": avatar_url}
        paths = [
            os.path.join(data_dir, "profile.json"),
            os.path.join(data_dir, "group_chat", "profile.json"),
        ]
        try:
            for profile_path in paths:
                os.makedirs(os.path.dirname(profile_path), exist_ok=True)
                old = load_json_cached(profile_path, default=None)
                if old is not None and old.get("name") == display_name and old.get("avatar") == avatar_url:
                    continue
                invalidate_json_cache(profile_path)
                with open(profile_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
            # Backfill ChatPro User.avatar when the DB still has empty/Dicebear.
            self._sync_chat_user_avatar(agent_bridge, avatar_url)
        except Exception as exc:
            logging.getLogger("Boot").warning(f"[Boot] Failed to write profile.json: {exc}")

    def _sync_chat_user_avatar(self, agent_bridge: Any, avatar_url: str) -> None:
        """PUT /api/users/me so group member lists show the agent avatar."""
        if not avatar_url or not agent_bridge:
            return
        token = getattr(agent_bridge, "token", None)
        base_url = getattr(agent_bridge, "base_url", None)
        if not token or not base_url:
            return
        current = getattr(agent_bridge, "user_avatar", None) or ""
        # Always push when we just resolved a local bot avatar for an empty/Dicebear account.
        try:
            import requests

            requests.put(
                f"{base_url}/api/users/me",
                headers={"Authorization": f"Bearer {token}"},
                json={"avatar": avatar_url},
                timeout=5,
            )
            agent_bridge.user_avatar = avatar_url or current
        except Exception as exc:
            logging.getLogger("Boot").debug(f"[Boot] Avatar sync skipped: {exc}")

    def _initialize_long_memory_runtime(
        self,
        config: dict[str, Any],
        data_dir: str,
        project_root: str,
        agent_name: str,
        agent_logger: Any,
    ) -> Any | None:
        if "long_memory" not in config.get("tools", []):
            return None
        try:
            from opensquad.memory_manager import MemoryManager
            from opensquad.tools.agent_memory_tool.memory import AgentMemory
            from opensquad.tools.long_memory import init_memory_tools

            memory_data_dir = os.path.join(data_dir, "long_memory")
            os.makedirs(memory_data_dir, exist_ok=True)
            from opensquad.json_cache import load_json_cached

            plugin_cfg_path = os.path.join(project_root, "data", "plugins", "long_memory", "config.json")
            plugin_cfg: dict[str, Any] = {}
            plugin_cfg = load_json_cached(plugin_cfg_path)

            agent_memory = AgentMemory(
                data_dir=memory_data_dir,
                min_cooccurrence=int(plugin_cfg.get("min_cooccurrence", 5)),
                decay_rate=float(plugin_cfg.get("decay_rate", 0.005)),
                decay_interval=int(plugin_cfg.get("decay_interval", 500)),
                time_decay_lambda=float(plugin_cfg.get("time_decay_lambda", 0.1)),
                max_dim=int(plugin_cfg.get("max_dim", 100000)),
            )
            try:
                agent_memory.load(memory_data_dir)
                agent_logger.info(f"[Boot] Long memory loaded from {memory_data_dir}")
            except Exception:
                agent_logger.info(f"[Boot] Long memory initialized fresh at {memory_data_dir}")

            memory_manager = MemoryManager(
                agent_memory=agent_memory,
                agent_name=agent_name,
                config={
                    "token_budget": int(plugin_cfg.get("token_budget", 3000)),
                    "window_size": int(plugin_cfg.get("window_size", 5)),
                    "context_depth": int(plugin_cfg.get("context_depth", 4)),
                    "cache_ttl": int(plugin_cfg.get("cache_ttl", 8)),
                },
            )
            init_memory_tools(memory_manager)
            agent_logger.info("[Boot] Long-term memory system initialized (MemoryManager)")
            return memory_manager
        except Exception as exc:
            agent_logger.error(f"[Boot] Long-term memory init failed: {exc}")
            return None
