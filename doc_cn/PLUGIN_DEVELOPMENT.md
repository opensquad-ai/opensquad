# 插件系统与开发指南

## 概览

OpenSquad 的插件系统允许开发者扩展 Agent 的能力。插件可以注册新的工具、拦截生命周期事件、集成外部平台，或启动嵌入式 HTTP 服务。

所有插件位于 `src/plugins/` 目录下，由 `PluginManager`（`src/plugins/plugin_manager.py`）在 Agent 启动时自动扫描和加载。

---

## 插件类型

| 类型 | 用途 | 示例 |
|------|------|------|
| `tool` | 为 Agent 注册可调用的工具 | websearch、vision、git_core |
| `hook` | 拦截生命周期事件，被动收集数据 | token_analytics、task_watch |
| `platform` | 平台集成适配器，附带消息发送工具 | feishu、telegram、qq |
| `service` | 嵌入式 HTTP / 后台服务 | whisper、hello_service |

---

## 目录结构

每个插件是 `src/plugins/` 下的独立子目录，最少需要两个文件：

```
src/plugins/
└── my_plugin/
    ├── __init__.py      # 空文件，标记 Python 包
    └── plugin.py        # 插件主体（必需）
```

`plugin.json` 不需要手动创建，PluginManager 会在首次加载时自动生成。**不要手动编辑 `plugin.json`**，它会在每次 Agent 启动或热重载时被覆盖。所有配置（包括 `config_schema`）都写在 `plugin.py` 的 `@register` 装饰器中。

带服务子进程的插件（如 websearch）还有额外的文件：

```
src/plugins/
└── websearch/
    ├── __init__.py
    ├── plugin.py            # 插件入口，继承 ServicePlugin
    ├── plugin.json          # 自动生成（不要手动编辑）
    ├── websearch.py         # 工具实现模块
    └── service/
        ├── main.py          # FastAPI 服务入口
        ├── websearch_api.py # 服务逻辑
        └── web_crawler.py   # 爬虫实现
```

---

## 插件生命周期

每个插件的完整生命周期如下：

```
1. PluginManager.discover_and_load()
     ↓
2. 发现 plugin.py → 找到 @register 装饰的类
     ↓
3. 读取 config_schema 默认值 → 合并 data/plugins/{name}/config.json 持久化覆盖
     ↓
4. 构建 Context 对象（agent_id, project_root, event_bus, config, data_dir, plugin_dir）
     ↓
5. 实例化插件：PluginClass(context)
     ↓
6. 调用 plugin.on_load()
     ↓
7. 扫描 @tool 方法 → 构建 ToolModuleWrapper → 注册到 ToolRegistry
8. 扫描 @hook 方法 → 构建钩子链
9. 扫描 @on_event 方法 → 订阅到 EventBus
10. 自动生成/更新 plugin.json
     ↓
11. 插件就绪，Agent 正常使用
     ↓
12. 卸载：plugin.on_unload() → 取消 EventBus 订阅 → 从 ToolRegistry 注销
```

---

## 快速开始：Hello World 插件

```python
# -*- coding: utf-8 -*-
from opensquad.plugin_api import register, tool, Plugin, Context

@register(
    name="my_plugin",
    author="OpenSquad",
    description="插件描述",
    version="1.0.0",
    plugin_type="tool",
    display_name="My Plugin",
    config_schema={
        "api_key": {
            "type": "string",
            "default": "",
            "description": "服务 API Key",
            "secret": True,
        },
        "max_retries": {
            "type": "integer",
            "default": 3,
            "description": "最大重试次数",
        },
        "model": {
            "type": "string",
            "default": "gpt-4",
            "enum": ["gpt-4", "gpt-3.5-turbo"],
            "description": "模型选择",
        },
    },
)
class MyPlugin(Plugin):

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        api_key = self.context.config.get("api_key", "")

    def on_unload(self) -> None:
        pass

    @tool(name="my_plugin", level="extended", auto_register=False)
    def do_something(self, param: str) -> dict:
        """
        工具方法描述（Agent 看到的 docstring）。

        Args:
            param: 参数说明
        Returns:
            {"success": True, "result": "..."}
        """
        return {"success": True, "result": param}
```

### 关键规则

- `@register(name=...)` 中的 `name` 是工具**命名空间**
- **插件名不需要与目录名一致**：例如 `plugins/whisper/plugin.py` 使用 `@register(name="whisper_transcribe")`，Launcher 会自动匹配
- 每个 `@tool(name=...)` 的 `name` **必须**与 `@register` 中的一致（同一命名空间下的多个方法共享该 name）
- 一个类可以有多个 `@tool` 方法，Agent 调用格式为 `namespace.method_name()`
- `auto_register=False` 表示需要在 Agent 的 `config.json` 的 `tools` 列表中显式启用

---

## `@register` 装饰器 — 完整参数参考

```python
@register(
    name: str,                              # 插件唯一标识（如 "websearch"）
    author: str = "",                       # 作者名
    description: str = "",                  # 描述文本
    version: str = "1.0.0",                 # 语义化版本
    plugin_type: str = "tool",              # "platform" | "tool" | "hook"
    display_name: str = "",                 # UI 显示名（默认从 name 自动生成）
    config_schema: dict = None,             # JSON-Schema 风格配置定义
    config_section: str = "",               # 桥接到 system_config.json 的 section
    dependencies: dict = None,              # {"pip": ["requests", ...]}
    contributes: dict = None,               # 前端贡献点（视图注册）
    tags: list = None,                      # 分类标签（如 ["search"]）
    node_scope: str = "all",               # "all" | "single" 多节点部署提示
)
```

### 参数详解

| 参数 | 说明 |
|------|------|
| `name` | 插件唯一标识。tool 类型插件的 name 同时也是工具命名空间 |
| `plugin_type` | `"tool"` — 注册工具；`"hook"` — 纯钩子/事件监听；`"platform"` — 平台集成适配器 |
| `node_scope` | `"all"` — 所有节点加载（默认）；`"single"` — 仅单节点运行，生成的 plugin.json 默认 `enabled=false`，需手动启用 |
| `contributes` | 前端贡献点。格式见下方「前端视图」章节 |
| `config_section` | 将插件配置桥接到 `system_config.json` 的某个 section（平台插件常用） |
| `tags` | 分类标签列表，用于 UI 筛选 |
| `dependencies` | `{"pip": ["package1", "package2"]}` 声明 pip 依赖 |

---

## `Context` 对象

`Context` 在插件实例化时注入，通过 `self.context` 访问。完整属性：

| 属性 | 类型 | 说明 |
|------|------|------|
| `agent_id` | `str` | 当前 Agent 的 ID（如 `"agent301-001"`） |
| `project_root` | `str` | 项目根目录绝对路径 |
| `event_bus` | `EventBus` | EventBus 单例，可用于 `subscribe()` / `emit()` 自定义事件 |
| `config` | `dict` | 合并后的插件配置（schema 默认值 + 用户持久化覆盖） |
| `data_dir` | `str` | `data/plugins/{name}/` 绝对路径，用于存放插件持久化数据 |
| `plugin_dir` | `str` | `src/plugins/{name}/` 绝对路径，用于读取插件自身文件 |

```python
# 使用示例
def on_load(self):
    db_path = os.path.join(self.context.data_dir, "analytics.db")
    agent_id = self.context.agent_id
    port = self.context.config.get("port", 9000)
```

---

## 配置系统

### config_schema 完整字段

```python
config_schema={
    "api_key": {
        "type": "string",          # "string" | "integer" | "number" | "boolean"
        "default": "",              # 默认值
        "description": "API 密钥",  # UI 提示文本
        "secret": True,             # 渲染为密码输入框
    },
    "max_results": {
        "type": "integer",
        "default": 10,
        "description": "最大返回结果数",
    },
    "enabled": {
        "type": "boolean",
        "default": True,
        "description": "是否启用",
    },
    "model": {
        "type": "string",
        "default": "gpt-4",
        "enum": ["gpt-4", "gpt-3.5-turbo"],   # 渲染为下拉选择框
        "description": "模型选择",
    },
}
```

### bot_list 类型（平台插件专用）

平台插件（feishu/telegram/qq）使用 `bot_list` 管理多机器人列表，UI 会渲染带增删改功能的列表编辑器：

```python
config_schema={
    "service_enabled": {
        "type": "boolean",
        "default": False,
        "description": "启用平台服务",
    },
    "bots": {
        "type": "bot_list",
        "default": [],
        "description": "机器人列表",
        "item_schema": {
            "name":     {"type": "string",  "default": "",   "description": "显示名称"},
            "token":    {"type": "string",  "default": "",   "description": "Bot Token", "secret": True},
            "agent_id": {"type": "string",  "default": "",   "description": "绑定的 Agent ID"},
            "enabled":  {"type": "boolean", "default": True, "description": "启用"},
        },
    },
}
```

标记 `"secret": True` 的字段在 UI 中渲染为密码输入框。平台插件的配置会自动桥接到 `system_config.json`，无需手动处理。

### 配置解析优先级

```
schema default  →  data/plugins/{name}/config.json 持久化覆盖
```

1. PluginManager 先读取 `config_schema` 中每个字段的 `default`
2. 如果 `data/plugins/{name}/config.json` 存在，其中的值会覆盖默认值
3. 合并后的结果存入 `self.context.config`

用户在 Web UI 的 Plugin Manager 中修改配置后，值会写入 `config.json`。Runner 每 5 秒检查 `config.json` 的 mtime，变化时自动触发插件热重载（重新实例化插件，传入新配置）。

---

## `@tool` 装饰器 — 完整参数

```python
@tool(
    name: str = "",                    # 工具命名空间名（空则默认方法名）
    description: str = "",             # 工具级描述（core 模式下显示在 system prompt）
    level: str = "extended",           # "core" | "extended"
    auto_register: bool = False,       # True = 自动注册到所有 Agent
    requires_agent_id: bool = False,   # True = ToolRegistry 会调用 set_agent_id() 注入
)
```

### level 的含义

| level | system prompt 中的表现 |
|-------|----------------------|
| `"core"` | 展开完整 docstring 和参数说明 |
| `"extended"` | 只显示工具名和一行摘要 |

### auto_register 和工具注册规则

| 规则 | 说明 |
|------|------|
| `auto_register: true` | 自动为所有 Agent 注册，无需在 `config.json` 中列出 |
| `auto_register: false` | 必须在 Agent 的 `config.json > tools[]` 中列出才会注册 |
| `MANDATORY_TOOLS` | system、filesystem、agent_setup、im、collaboration、delegate_task、workspace、task_watch 始终注册，不受 `tools` 列表限制 |

### ToolModuleWrapper（内部机制）

`@tool` 装饰的方法会被 `ToolModuleWrapper` 包装。Wrapper 会将绑定方法（含 `self`）转换为无 `self` 参数的普通函数，并重建签名，以便 `ToolRegistry` 的 `inspect` 机制正确发现和调用。

---

## 自定义工具开发

工具（Tool）是 Agent 可以调用的函数。OpenSquad 支持两条自定义工具的打包路径——**通过技能**（推荐用于项目内辅助工具）和**通过插件**（适合跨项目分发、管理员可开关的工具）。上一节 `@tool` 装饰器讲的是插件路径的契约；本节是两条路径的实操指南，以及所有工具必须遵守的约定。

### 工具调用模式

OpenSquad 支持两种工具调用格式：

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| Native FC | LLM 厂商原生函数调用（OpenAI / Anthropic / Google） | 支持 FC 的模型 |
| XML | 提示词里的 XML `<tool>` 标签，由 Agent 解析 | 不支持 FC 的模型；兼容性最好 |

运行时会根据模型能力和 `tool_call_mode` 配置自动选择。使用 XML 模式时，调用格式如下：

```xml
<tool name="filesystem_read_file">
  <parameter name="path">/workspace/main.py</parameter>
</tool>
```

Agent 解析 XML、执行工具、注入结果。

### 内置工具

以下是 OpenSquad 自带的工具（受下面的 level 规则约束）：

| 工具 | 模块 | 说明 | 级别 |
|------|------|------|------|
| `system` | `tools/system.py` | 系统命令、环境变量、时间 | core |
| `filesystem` | `tools/filesystem.py` | 文件读写、目录操作 | core |
| `agent_setup` | `tools/agent_setup.py` | 技能、会话、Profile 管理 | core |
| `im` | `tools/im.py` | 即时通讯、消息历史 | core |
| `collaboration` | `tools/collaboration.py` | 多 Agent 协作 | core |
| `delegate_task` | `tools/delegate.py` | 任务委托 | core |
| `workspace` | `tools/workspace.py` | 工作区管理 | core |
| `task_watch` | `tools/task_watch.py` | 任务监控 | core |
| `web` | `tools/web.py` | HTTP 请求 | extended |
| `websearch` | — | 实时联网搜索 | extended |
| `vision` | — | 图像分析 | extended |
| `mcp_query` | — | 查询 MCP 服务 | extended |
| `long_memory` | `tools/long_memory.py` | 长期记忆与语义检索 | extended |

### 方式一：通过技能（推荐）

在技能目录下加一个 `tools.py`，框架会自动发现并注册其中所有顶层函数：

```
skills/my-skill/
├── SKILL.md
├── skill.json
└── tools.py         # <-- 工具函数放这里
```

示例 `tools.py`：

```python
def my_custom_tool(param1: str, param2: int = 10) -> dict:
    """
    自定义工具的简短描述。

    Args:
        param1: 参数1的描述
        param2: 参数2的描述，默认 10

    Returns:
        {"result": str, "status": str}
    """
    result = f"处理了 {param1}，参数值: {param2}"
    return {"result": result, "status": "success"}
```

**关键规则：**
- 函数名即工具名
- docstring 会被解析为工具描述和参数说明
- 返回值必须是 `dict`（或任何可 JSON 序列化的对象）

### 方式二：通过插件

需要打包、分发或由管理员开关的工具，走插件路径。`@tool` 装饰器一节已经列出了完整参数表，下面是最小示例：

```python
# plugins/my-plugin/plugin.py
from opensquad.plugin_api import register, tool, Context

@register(
    name="my-plugin",
    display_name="My Plugin",
    config_schema={}
)
class MyPlugin:
    def __init__(self, context: Context):
        self.context = context

    @tool(name="my_tool", description="工具描述", level="extended")
    def my_tool_function(self, param1: str) -> dict:
        """工具描述 — param1: 参数说明"""
        return {"result": param1}
```

### 工具级别

| 级别 | 说明 |
|------|------|
| `core` | 始终可用，不可关闭（如 `system`、`filesystem`） |
| `extended` | 默认可用，可按 Agent 关闭 |
| `premium` | 必须在 Agent 配置中显式启用 |

### 编写高质量的工具描述

LLM 靠描述来决定何时调用哪个工具，写清楚。

✅ **好的描述：**

```python
def search_files(pattern: str, directory: str = ".") -> dict:
    """在指定目录里按 glob 模式搜索文件。

    pattern: glob 模式，如 '*.py' 或 'test_*.ts'
    directory: 目标目录路径，默认当前目录
    """
```

❌ **差的描述：**

```python
def search_files(pattern: str, directory: str = ".") -> dict:
    """搜索文件。"""
```

### 安全：文件系统白名单

文件系统工具只能访问白名单内的路径，在 `system_config.json` 中配置：

```json
{
  "filesystem": {
    "workspace_dirs": ["/data/projects", "../shared"]
  }
}
```

白名单之外的请求会被拒绝。

### 工具调用调试日志

在 `system_config.json` 中启用：

```json
{
  "logging": {
    "tool_call_debug": true,
    "tool_call_debug_max_size_mb": 5,
    "tool_call_debug_backup_count": 3
  }
}
```

或通过环境变量：

```bash
export TOOL_CALL_DEBUG=1
```

查看日志：

```bash
opensquad logs -s gateway --grep "tool_call"
```

调试日志会记录每次工具调用的完整参数、返回结果和耗时。

---

## `@hook` 装饰器 — 完整参考

钩子装饰器通过 `hook` 命名空间访问，支持可选 `priority` 参数（默认 0，数值越大越先执行）：

```python
from opensquad.plugin_api import hook

@hook.on_after_tool                    # priority=0（默认）
@hook.on_after_tool(priority=100)      # 显式优先级
```

### 所有可用钩子

#### 输入 / LLM 管道

| 钩子 | 触发时机 | context 关键字段 | 可用操作 |
|------|---------|-----------------|---------|
| `on_message_received` | 收到消息，处理前 | `message, channel, sender_name, chat_name, source_chat_id, input_source` | 设 `context['__stop__'] = True` 丢弃消息 |
| `on_before_llm` | LLM 调用前 | `messages (list), model, agent_id` | 设 `context['__stop__'] = True` 跳过调用 |
| `on_after_llm` | LLM 响应后 | `response, agent_id` | 修改 `context['response']` 改写输出 |

#### 工具执行

| 钩子 | 触发时机 | context 关键字段 | 可用操作 |
|------|---------|-----------------|---------|
| `on_before_tool` | 工具调用前 | `tool_name, arguments, agent_id` | 设 `context['skip'] = True` + `context['result']` 替代执行 |
| `on_after_tool` | 工具调用后（成功或错误） | `tool_name, arguments, result, agent_id, model` | 修改 `context['result']` 改写工具结果 |
| `on_tool_error` | 工具返回 Error: 后 | `tool_name, arguments, error, agent_id` | 修改 `context['error']` 改写错误消息 |

> `on_tool_error` 在 `on_after_tool` **之后**触发，仅当 `result` 仍以 `Error:` 开头。

#### 输出

| 钩子 | 触发时机 | context 关键字段 | 可用操作 |
|------|---------|-----------------|---------|
| `on_before_send` | 回复持久化和发送前 | `message, agent_id` | 修改 `context['message']` 改写回复；设 `__stop__` 取消发送 |
| `on_after_send` | 回复已发送后 | `message, agent_id` | 只读；用于日志、分析、副作用 |

#### 系统提示词

| 钩子 | 触发时机 | 可用操作 |
|------|---------|---------|
| `on_before_prompt` | 每轮提示词最终确定前 | 修改 `context['prompt']` 动态注入/改写 |

#### 任务生命周期

| 钩子 | 触发时机 | context 关键字段 |
|------|---------|-----------------|
| `on_task_start` | 任务开始（状态 → working） | `task_id, requirement, source, agent_id` |
| `on_task_complete` | 任务结束（task_complete/task_failed） | `task_id, completion_status, tools_used (list), turns, agent_id` |

#### 状态机

| 钩子 | 触发时机 | context 关键字段 |
|------|---------|-----------------|
| `on_state_change` | Agent 状态切换 | `old_state, new_state, agent_id`（状态：`idle`/`working`/`sleeping`） |

> **注意**：`on_state_change` 以 asyncio task 异步触发（在状态锁外部），不要在此钩子中调用 `state_manager.get_state()`。

### 钩子链执行机制

1. 所有插件的同一钩子处理器按 `(-priority, plugin_name)` 排序后**链式执行**
2. 前一个处理器的返回值（`context`）传给下一个
3. 任何处理器可设 `context['__stop__'] = True` 中止后续处理器
4. 处理器抛异常会被捕获日志，链继续执行

### 堆叠多个钩子

允许在同一方法上堆叠多个钩子装饰器：

```python
@hook.on_before_tool
@hook.on_after_tool(priority=50)
async def my_handler(self, context):
    ...
```

---

## `@on_event` 装饰器

订阅 EventBus 事件，PluginManager 在加载时自动调用 `event_bus.subscribe()`。

```python
from opensquad.plugin_api import on_event

@on_event("token_stats")
def handle_token_stats(self, event_data: dict):
    """收到 token_stats 事件时自动调用"""
    ...
```

### 可用事件

插件可以订阅任意 EventBus 事件。常见事件：

| 事件名 | 触发来源 | 说明 |
|--------|---------|------|
| `token_stats` | runner._broadcast_token_stats() | 每次 LLM 调用后发送 token 用量快照 |
| `tool_stats` | runner._broadcast_token_stats() | 工具调用统计 |

### 自定义事件

插件可以通过 `self.context.event_bus` 发射自定义事件：

```python
self.context.event_bus.emit("my_custom_event", {"key": "value"})
```

---

## `get_tool_modules()` 代理模式

对于已有工具模块（如 `websearch.py` 中的独立函数），无需用 `@tool` 装饰器重写。使用 `get_tool_modules()` 代理模式即可：

```python
import importlib
from typing import Any, Dict, List

class WebSearchPlugin(ServicePlugin):

    def get_tool_modules(self) -> List[Dict[str, Any]]:
        """
        Proxy pattern: return the existing tool module for ToolRegistry.

        PluginManager 识别此方法，将返回的工具模块注册到 ToolRegistry。
        """
        tools = []
        try:
            module = importlib.import_module("plugins.websearch.websearch")
            tools.append({
                "name": "websearch",
                "module": module,
                "level": "core",
                "auto_register": True,
                "requires_agent_id": False,
            })
        except ImportError as e:
            logger.error(f"Cannot import module: {e}")
        return tools
```

返回列表的每个元素是一个 `dict`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 工具命名空间 |
| `module` | `module` | Python 模块对象（`importlib.import_module()` 返回值） |
| `level` | `str` | `"core"` 或 `"extended"` |
| `auto_register` | `bool` | 是否自动注册到所有 Agent |
| `requires_agent_id` | `bool` | 是否需要注入 agent_id |

此模式适用于 websearch、git_core、vision 等有独立工具实现文件的插件。**`@tool` 装饰器和 `get_tool_modules()` 可以共存**。

---

## ServicePlugin — 嵌入式 HTTP 服务

需要启动后台 HTTP 服务的插件继承 `ServicePlugin` 而非 `Plugin`：这是 websearch、whisper 等插件的标准模式。

### 基本用法

```python
from opensquad.plugin_api import register, Context
from opensquad.service_plugin import ServicePlugin

@register(
    name="my_service",
    plugin_type="tool",
    config_schema={
        "port": {"type": "integer", "default": 9000, "description": "服务端口"},
        "host": {"type": "string",  "default": "0.0.0.0", "description": "监听地址"},
        "auto_start": {"type": "boolean", "default": True, "description": "自动启动"},
    },
)
class MyServicePlugin(ServicePlugin):

    def __init__(self, context: Context):
        super().__init__(
            context=context,
            service_script="main.py",        # service/ 下的启动脚本
            health_endpoint="/health",       # 健康检查端点
            service_name="MyService",        # 日志中的服务名
            max_startup_wait=30,             # 最大启动等待秒数
            health_check_interval=60,        # 健康检查间隔秒数
        )

    def on_unload(self):
        super().on_unload()  # 停止服务进程和健康监控线程
```

### ServicePlugin 内部机制

| 机制 | 说明 |
|------|------|
| **全局单例注册表** | `_ServiceRegistry` 确保同一端口只有一个服务进程，多 Agent 共享引用计数 |
| **跨进程文件锁** | `_acquire_service_lock(port)` 防止多 Agent 同时启动同一服务 |
| **健康监控** | 后台线程每 `health_check_interval` 秒检查 `health_endpoint`，挂了自动重启 |
| **自动启动** | `on_load()` 中检查 `auto_start` 配置，设为后台线程不阻塞 Agent 启动 |
| **日志轮转** | 服务 stdout/stderr 写入 `data/logs/{plugin}_service.log`，超过 3MB 自动轮转 |

### service_only 标记

`plugin.json` 中设置 `"service_only": true` 的插件**仅在 Launcher 进程管理器中运行**，不会加载到 Agent 进程中：

```json
{
  "name": "whisper",
  "service_only": true,
  "service": {
    "entry": "server.py",
    "auto_start": true
  }
}
```

---

## plugin.json 自动生成

`plugin.json` **不需要手动创建**。PluginManager 在加载插件时自动生成：

1. 从 `@register` 装饰器元数据生成基础内容（name、tools、hooks、config_schema 等）
2. 从 `get_tool_modules()` 合并代理工具
3. **保留**已存在 plugin.json 中的非装饰器字段：
   - `enabled` — 启/禁用标志
   - `service` — 进程管理配置（entry、auto_start）
   - `config.section` — system_config.json 桥接
4. 写入 `plugins/{name}/plugin.json`

因此，如果你需要设置 `service` 字段，应当**手动创建一个仅含 `service` 字段的 plugin.json**，PluginManager 会自动保留它：

```json
{
  "service": {
    "entry": "adapter.py",
    "auto_start": true
  }
}
```

---

## 启用插件

在目标 Agent 的 `agents/{agent_id}/config.json` 中，将插件命名空间添加到 `tools` 列表：

```json
{
  "tools": [
    "system",
    "filesystem",
    "my_plugin"
  ]
}
```

也可以在 Web UI 的 Plugin Manager 中点击插件的开关来启用/禁用。

---

## 热重载

### 机制

热重载通过哨兵文件 `plugins/.reload_ts` 触发。Runner 每 5 秒调用 `check_reload_needed()`，检查：

1. `.reload_ts` 文件的 mtime 是否变化
2. 已加载插件的 `data/plugins/{name}/config.json` mtime 是否变化

任一变化即触发 `reload_plugins()`：卸载禁用的插件 + 加载新增/启用的插件。

### 触发方式

```
skill_plugin_dev.trigger_reload()
```

### 各场景操作

| 场景 | 操作 |
|------|------|
| 全新插件（首次创建） | `trigger_reload()` |
| 修改 `config_schema` 的值（用户配置） | `trigger_reload()`（自动检测 config.json 变化） |
| `plugin.json` 改为 `"enabled": false`（禁用） | `trigger_reload()` |
| **修改已有插件的 `.py` 代码** | **必须重启 Agent 进程**（Python 模块已导入，无法重载） |
| **在 `config.json` 的 `tools` 列表中增删插件** | **必须重启 Agent 进程** |

### 重启方式

通过 Launcher 管理端口 9600：

```
POST http://127.0.0.1:9600/api/agents/{dir_name}/restart
POST http://127.0.0.1:9600/api/agents/{dir_name}/stop
POST http://127.0.0.1:9600/api/agents/{dir_name}/start
```

> `dir_name` 是 `agents/` 下的**目录名**（不是 `config.json` 中的 `agent_id`）。

也可以在 Web UI 的 Admin → Agents 页面找到对应 Agent，点击重启按钮。

---

## 前端视图（可选）

如果需要自定义仪表盘，除了后端 `plugin.py`，还需要完成前端部分。

### 需要创建/修改的文件

1. **前端组件**：`gateway/nexuschat-pro/components/plugin-views/{name}/MyDashboard.tsx`
2. **注册到 registry**：`gateway/nexuschat-pro/components/plugin-views/registry.ts`

   在 `PLUGIN_VIEW_LOADERS` 对象中添加新条目：
   ```ts
   'my_plugin:my_view': () =>
     import('./my-plugin/MyDashboard').then(m => ({ default: m.MyDashboard })),
   ```
   使用动态导入（`() => import(...)`）。**不要**使用静态 `import`，组件语法错误会导致整个构建失败。

3. **在 `@register` 中声明 `contributes`**（`plugin.py`）：
   ```python
   contributes={
       "views": [
           {
               "name": "my_view",
               "title": "My Dashboard",
               "icon": "BarChart3",
               "data_endpoint": "/api/plugins/my_plugin/data",
           }
       ]
   },
   ```

### 调试前端视图

前端视图组件运行时崩溃不影响主应用，错误会自动写入日志文件。

**查看错误日志：**
```
filesystem.read_file("plugins/{name}/view_errors.log")
```

**调试循环：**
1. 编写/修改组件代码
2. Vite 开发服务器自动热重载
3. 在管理 UI 中打开插件视图
4. 如果崩溃 → 读取 `view_errors.log` → 修复 → 重复
5. 视图正确渲染 → 完成

---

## 真实插件案例

### 案例一：websearch — tool 插件 + ServicePlugin + 代理模式

[完整代码](../src/plugins/websearch/plugin.py)

特点：
- 继承 `ServicePlugin` 自动管理 FastAPI 子进程
- 使用 `get_tool_modules()` 代理现有 `websearch.py` 工具模块
- 工具函数（search、fetch）定义在独立的 `websearch.py` 中

### 案例二：token_analytics — hook 插件 + @event 事件监听

[完整代码](../src/plugins/token_analytics/plugin.py)

特点：
- `plugin_type="hook"` — 纯数据收集，不注册工具
- `@on_event("token_stats")` 监听每轮 LLM 调用后的 token 快照
- `@hook.on_after_tool` 记录每个工具调用的 token 估算
- 数据持久化到 `data/plugins/token_analytics/analytics.db` SQLite
- `contributes.views` 注册前端 Token Analytics 面板

```python
@register(
    name="token_analytics",
    plugin_type="hook",
    config_schema={
        "db_path": {"type": "string", "default": "data/.../analytics.db", "description": "数据库路径"},
        "buffer_size": {"type": "integer", "default": 10, "description": "缓冲记录数"},
        "flush_interval_sec": {"type": "integer", "default": 30, "description": "刷新间隔秒数"},
    },
    contributes={
        "views": [{
            "name": "token_dashboard",
            "title": "Token Analytics",
            "icon": "BarChart3",
            "data_endpoint": "/api/plugins/token_analytics/data",
        }]
    },
)
class TokenAnalyticsPlugin(Plugin):
    def on_load(self):
        # 初始化 SQLite 存储
        ...

    @on_event("token_stats")
    def handle_token_stats(self, event_data: dict):
        # 记录 token 快照
        ...

    @hook.on_after_tool
    async def track_tool_usage(self, context):
        # 记录工具调用 token
        ...
        return context  # 必须返回 context
```

### 案例三：external_api — platform 插件（无工具）

[完整代码](../src/plugins/external_api/plugin.py)

特点：
- `plugin_type="platform"` — 平台适配器
- 无 `@tool` 方法，无出站工具
- 纯入站适配器，由 Launcher 管理 adapter.py 进程

---

## 标准开发工作流

### 场景 A：全新插件（首次创建）

1. 在 `src/plugins/` 下创建目录和 `plugin.py`
2. 编写插件代码，使用 `@register` 和 `@tool` / `@hook` / `@on_event` 装饰器
3. 在目标 Agent 的 `config.json` 的 `tools` 列表中添加插件名（或设 `auto_register=True`）
4. 重启 Agent 进程（`config.json` 的 `tools` 列表变更需要重启）
5. 验证插件已加载

### 场景 B：修改已有插件代码

1. 编辑 `src/plugins/{name}/plugin.py`
2. 重启 Agent 进程（Python 模块已导入，热重载无效）
3. 验证变更生效

### 场景 C：修改插件配置（不改变代码）

1. 在 Web UI 的 Plugin Manager 中修改配置
2. 自动保存并触发热重载
3. 无需重启

---

## 内置插件一览

### Tool 插件

| 插件 | 说明 |
|------|------|
| websearch | 网络搜索 |
| vision | 图像分析 |
| media | 音频格式转换 |
| whisper | 语音转文字 |
| mcp_query | MCP 服务器管理 |
| sequential_think | 结构化思维 |
| git_core | Git 版本控制 |
| agent_factory | 动态创建 Agent |
| chat_account | 聊天账号管理 |
| quick_note | 快速笔记 |
| plugin_admin | 插件管理与配置 |

### Hook 插件

| 插件 | 说明 |
|------|------|
| token_analytics | Token 用量追踪（被动，无工具） |
| task_watch | 任务监控 |

### Platform 插件

| 插件 | 说明 |
|------|------|
| telegram | Telegram 机器人适配器 |
| feishu | 飞书机器人适配器 |
| qq | QQ 机器人适配器 |
| vcs_remote | 远程 VCS 操作（GitHub PR 等） |
