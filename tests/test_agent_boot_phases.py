import asyncio
import sys
import types

import opensquad.agent_boot_phases as agent_boot_phases_module
from opensquad.agent_boot_phases import AgentBootPhases


class DummyRegistry:
    def __init__(self):
        self.registered = []
        self.lazy_registered = []
        self.mcp_adapters = []

    def register(self, module, name, level="extended"):
        self.registered.append((module, name, level))

    def register_lazy(self, module_path, name, level="extended", on_loaded=None):
        self.lazy_registered.append((module_path, name, level, on_loaded))

    def register_mcp_adapter(self, adapter, level="extended"):
        self.mcp_adapters.append((adapter, level))


class DummySessionManager:
    def __init__(self, messages=None):
        self._messages = messages or []
        self.async_writer_started = False

    def start_async_writer(self):
        self.async_writer_started = True

    def get_messages(self):
        return list(self._messages)


class DummyStateManager:
    def __init__(self):
        self.states = []
        self.wake_modes = []

    async def set_state(self, state):
        self.states.append(state)

    async def set_wake_mode(self, wake_mode):
        self.wake_modes.append(wake_mode)


class DummyInputHub:
    def __init__(self):
        self.contexts = []

    def set_agent_context(self, agent_dir):
        self.contexts.append(agent_dir)

    def _check_session_cwd(self):
        return None


class DummyLogger:
    def __init__(self):
        self.logs = []

    def info(self, message, *args):
        if args:
            message = message % args
        self.logs.append(message)

    def error(self, message, *args):
        if args:
            message = message % args
        self.logs.append(message)

    def warning(self, message, *args):
        if args:
            message = message % args
        self.logs.append(message)


def test_register_builtin_tools_wires_levels_and_filesystem(monkeypatch, tmp_path):
    calls = []

    class FilesystemModule:
        def __init__(self):
            self.agent_ids = []
            self.config_paths = []
            self.allowed_dirs = []

        def set_agent_id(self, agent_id):
            self.agent_ids.append(agent_id)

        def set_config_path(self, config_path):
            self.config_paths.append(config_path)

        def set_allowed_dirs(self, allowed_dirs):
            self.allowed_dirs.append(allowed_dirs)

    fs_module = FilesystemModule()

    class SystemModule:
        def __init__(self):
            self.agent_id = None

        def set_agent_id(self, agent_id):
            self.agent_id = agent_id

    system_module = SystemModule()

    def fake_import(name):
        calls.append(name)
        if name == "mod.system":
            return system_module
        if name == "mod.filesystem":
            return fs_module
        raise ImportError(name)

    monkeypatch.setattr(agent_boot_phases_module.importlib, "import_module", fake_import)
    monkeypatch.setattr(agent_boot_phases_module.syscfg, "get_workspace", lambda: str(tmp_path / "ws"))
    monkeypatch.setattr(
        agent_boot_phases_module.syscfg, "filesystem_workspace_dirs", lambda: [str(tmp_path / "global")]
    )

    phases = AgentBootPhases(
        tool_modules={"system": "mod.system", "filesystem": "mod.filesystem"},
        mandatory_tools={"system", "filesystem"},
        core_tools={"system", "filesystem"},
    )
    registry = DummyRegistry()
    config = {
        "agent_id": "agent-1",
        "tools": ["plugin_only"],
        "tool_levels": {"filesystem": "extended"},
        "filesystem": {"workspace_dirs": ["relative-dir"]},
    }

    phases.register_builtin_tools(config, registry, str(tmp_path / "agent"))

    # filesystem stays eager (needs configure); system is deferred (lazy).
    assert sorted(calls) == ["mod.filesystem"]
    assert [(n, l) for _, n, l in registry.registered] == [("filesystem", "extended")]
    assert registry.lazy_registered == [("mod.system", "system", "core", registry.lazy_registered[0][3])]
    # on_loaded must wire set_agent_id when the lazy module is finally imported
    lazy_on_loaded = registry.lazy_registered[0][3]
    lazy_on_loaded(system_module)
    assert system_module.agent_id == "agent-1"
    assert fs_module.agent_ids == ["agent-1"]
    assert fs_module.config_paths == [str(tmp_path / "agent" / "config.json")]
    assert len(fs_module.allowed_dirs) == 1
    assert str(tmp_path / "ws") in fs_module.allowed_dirs[0]
    assert str(tmp_path / "global") in fs_module.allowed_dirs[0]
    assert str((tmp_path / "ws" / "relative-dir").resolve()) in fs_module.allowed_dirs[0]


def test_build_tool_name_list_respects_disabled_tools():
    phases = AgentBootPhases(
        tool_modules={},
        mandatory_tools={"system", "filesystem", "im", "workspace"},
        core_tools=set(),
    )
    config = {
        "tools": ["im", "websearch", "workspace"],
        "disabled_tools": ["im", "workspace"],
    }
    assert sorted(phases.build_tool_name_list(config)) == ["filesystem", "system", "websearch"]


def test_initialize_runtime_infrastructure_registers_mcp_adapter(monkeypatch, tmp_path):
    registry = DummyRegistry()
    phases = AgentBootPhases(tool_modules={}, mandatory_tools=set(), core_tools=set())

    monkeypatch.setattr(agent_boot_phases_module.syscfg, "workspace_data_dir", lambda name: str(tmp_path / name))
    monkeypatch.setattr(agent_boot_phases_module.os.path, "isfile", lambda _: False)

    async def fake_init_mcp_adapter(agent_dir, global_disabled_servers, registry=None):
        adapter = {"agent_dir": agent_dir, "disabled": global_disabled_servers}
        if registry is not None:
            registry.register_mcp_adapter(adapter, level="extended")
        return adapter

    fake_module = types.ModuleType("opensquad.tools.mcp_adapter")
    fake_module.init_mcp_adapter = fake_init_mcp_adapter
    monkeypatch.setitem(sys.modules, "opensquad.tools.mcp_adapter", fake_module)

    asyncio.run(
        phases.initialize_runtime_infrastructure(
            config={"mcp": {"enabled": True}},
            registry=registry,
            agent_dir=str(tmp_path / "agent"),
        )
    )

    assert registry.mcp_adapters == [({"agent_dir": str(tmp_path / "agent"), "disabled": set()}, "extended")]


def test_setup_connections_starts_gateway(monkeypatch):
    phases = AgentBootPhases(tool_modules={}, mandatory_tools=set(), core_tools=set())
    logger = DummyLogger()
    created_tasks = []

    class DummyAdapter:
        def __init__(self, config):
            self.config = config

        async def start(self):
            return None

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return types.SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr(agent_boot_phases_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(agent_boot_phases_module.syscfg, "gateway_register_url", lambda: "http://gateway")
    monkeypatch.setattr(agent_boot_phases_module.syscfg, "node_id", lambda: "node-1")
    monkeypatch.setattr(agent_boot_phases_module.syscfg, "node_label", lambda: "Node 1")
    monkeypatch.setattr(agent_boot_phases_module.syscfg, "node_secret", lambda: "secret")
    monkeypatch.setitem(
        sys.modules,
        "opensquad.gateway_adapter",
        types.SimpleNamespace(
            AgentConfig=lambda **kwargs: types.SimpleNamespace(**kwargs),
            GatewayAdapter=DummyAdapter,
        ),
    )

    asyncio.run(
        phases.setup_connections(
            config={
                "gateway": {"enabled": True},
                "agent_id": "agent-1",
                "agent_name": "Agent",
                "agent_type": "general",
            },
            logger=logger,
            data_dir="",
        )
    )

    assert created_tasks
    assert any("Gateway Adapter connected" in entry for entry in logger.logs)


def test_initialize_agent_runtime_sets_state_and_default_wake(monkeypatch, tmp_path):
    phases = AgentBootPhases(tool_modules={}, mandatory_tools=set(), core_tools=set())
    input_hub = DummyInputHub()
    logger = DummyLogger()
    session_manager = DummySessionManager(messages=[])
    state_manager = DummyStateManager()

    monkeypatch.setattr("opensquad.session_manager.reinit_session_manager", lambda save_dir: session_manager)
    monkeypatch.setattr("opensquad.state_manager.reinit_state_manager", lambda state_file: state_manager)
    monkeypatch.setattr("opensquad.session_manager.session_manager", session_manager)
    monkeypatch.setattr("opensquad.state_manager.state_manager", state_manager)

    result = asyncio.run(
        phases.initialize_agent_runtime(
            config={"default_wake_mode": "strict"},
            agent_dir=str(tmp_path / "agent"),
            input_hub=input_hub,
            agent_logger=logger,
        )
    )

    assert input_hub.contexts == [str(tmp_path / "agent")]
    assert session_manager.async_writer_started is True
    assert state_manager.states == ["idle"]
    assert state_manager.wake_modes == ["strict"]
    assert result.data_dir == str(tmp_path / "agent" / "data")
    assert result.history_dir == str(tmp_path / "agent" / "data" / "ai_his_talk")


def test_initialize_chat_runtime_builds_chat_api(monkeypatch, tmp_path):
    phases = AgentBootPhases(tool_modules={}, mandatory_tools=set(), core_tools=set())
    logger = DummyLogger()

    class DummyModelConfig:
        def __init__(self, model, token_max=1000, timeout=60.0):
            self.model = model
            self.token_max = token_max
            self.timeout = timeout
            self.reasoning_effort = "high"
            self.is_think = False

    class DummyChatApi:
        def __init__(self, config, stream_parser):
            self.config = config
            self.stream_parser = stream_parser
            self.history_dir = None
            self.output_media_dir = None

    monkeypatch.setattr(
        "opensquad.model_config.ModelConfig.from_dict",
        lambda model_cfg, prompt, provider: DummyModelConfig(model=model_cfg.get("model_name", "")),
    )
    monkeypatch.setattr("opensquad.chat_api.ChatAPI", DummyChatApi)
    monkeypatch.setattr(agent_boot_phases_module.syscfg, "workspace_uploads_dir", lambda: str(tmp_path / "uploads"))

    result = phases.initialize_chat_runtime(
        config={"model": {"provider": "openai", "model_name": "gpt-test", "is_image": True}},
        system_prompt="system prompt",
        history_dir=str(tmp_path / "history"),
        agent_logger=logger,
    )

    assert isinstance(result.chat_api, DummyChatApi)
    assert result.provider == "openai"
    assert result.chat_api.history_dir == str(tmp_path / "history")
    assert result.chat_api.output_media_dir == str(tmp_path / "uploads")
    assert result.vision_config == {"is_img_mode": True}


def test_finalize_runner_runtime_and_shutdown(monkeypatch):
    phases = AgentBootPhases(tool_modules={}, mandatory_tools=set(), core_tools=set())
    logger = DummyLogger()
    emitted = []
    monkeypatch.setattr(agent_boot_phases_module.bus, "emit", lambda event, payload: emitted.append((event, payload)))

    class DummyRunner:
        def __init__(self):
            self._hooks = None
            self._memory_manager = None
            self.run_calls = 0

        async def run(self):
            self.run_calls += 1
            return None

    class DummySessionWriter:
        def __init__(self):
            self.stopped = []

        async def stop_async_writer(self, timeout):
            self.stopped.append(timeout)

    runner = DummyRunner()
    session_manager = DummySessionWriter()
    hooks = {"before_input": object()}
    memory_manager = object()

    phases.finalize_runner_runtime(
        early_runner=runner,
        hooks=hooks,
        memory_manager=memory_manager,
        boot_main_t0=0.0,
        agent_id="agent-1",
        agent_logger=logger,
    )

    assert runner._hooks is hooks
    assert runner._memory_manager is memory_manager
    assert emitted == [("agent_ready", {"agent_id": "agent-1"})]

    asyncio.run(
        phases.await_runner_shutdown(
            early_runner=runner,
            runner_task=asyncio.sleep(0),
            session_manager=session_manager,
        )
    )

    assert session_manager.stopped == [5.0]
