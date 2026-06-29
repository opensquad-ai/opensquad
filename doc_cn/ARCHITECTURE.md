# OpenSquad 架构说明

## 概览

OpenSquad 是一个多智能体框架，每个 Agent 作为独立进程运行，拥有各自的 LLM 连接、工具集和会话状态。Agent 之间通过共享群聊（ChatPro）进行通信，并通过协作卡片（Collab Card）定义的工作流进行协调。

```
                   Telegram / Feishu / 外部 API
                              |
                        Gateway (9555)
                       /      |      \
                  WebSocket  HTTP   代理
                  (实时)    (REST)  (-> Launcher)
                      |       |        |
                 Frontend  Backend   Launcher (9600)
                 (9530)    (FastAPI)   |
                                    Agent 进程
                                   /    |    \
                               PM    Coder   QA
                              (8003) (8002) (8006)
                                \     |     /
                             ChatPro 群聊
```

---

## 核心模块（`src/opensquad/`）

### Agent 运行时

| 模块 | 职责 |
|------|------|
| `runner.py` | Agent 主循环。接收输入、构建 Prompt（含占位符）、调用 LLM、解析响应、分发工具调用、处理流式输出。核心函数：`_setup_prompt()`（约第 2731 行）。 |
| `parser.py` | 将 LLM 输出解析为结构化事件：`<tool_call>`、`<thought>`、`<plan>`、`<state>`、`<to_user>`、`<sleep>` 等。 |
| `registry.py` | 工具注册表。`register()` 添加工具，`dispatch()` 调用工具，`generate_tool_descriptions()` 将工具文档渲染到 Prompt 中。 |
| `input_hub.py` | 统一输入队列。所有来源（Web、CLI、群聊、定时器）均汇入单一异步队列，Agent 从此队列读取输入。 |
| `state_manager.py` | Agent 状态机：`idle` / `working` / `sleeping`。控制 Agent 何时接受新输入。 |
| `sleep_controller.py` | 可中断的睡眠。Agent 可执行 `<sleep seconds="120">`，并在收到新消息时被唤醒。 |

### 会话与记忆

| 模块 | 职责 |
|------|------|
| `session_manager.py` | 管理对话会话。将会话历史保存/加载为 `agents/{name}/data/sessions/` 目录下的 JSON 文件。 |
| `context_base.py` | 标准上下文注入：`inject_standard()` 提供 AGENT_PROFILE、CONTEXT_SUMMARY、AGENT_WORKSPACE、TEAM_COLLAB_CARDS、RUNTIME_STATE、MEMORY_CONTEXT 等变量。 |
| `memory_manager.py` | 长期记忆抽象层。封装 `agent_memory_tool`，提供跨会话的语义搜索与召回。 |
| `task_logger.py` | 任务生命周期日志。将任务的开始/结束/轮次记录到 `agents/{name}/data/tasks/` 目录下的 JSON 文件。 |

### 通信

| 模块 | 职责 |
|------|------|
| `bridge.py` | `ChatProBridge` 类。处理登录、加入群组、连接 ChatPro WebSocket 以实现实时群消息收发。 |
| `message_router.py` | 路由传入的群聊消息。过滤自身消息、检测 @提及、路由到输入队列。 |
| `message_queue.py` | 用于跨组件通信的内部消息队列。 |
| `gateway_adapter.py` | Agent 侧 Gateway WebSocket 连接适配器（AI Web 通道）。 |

### 技能与插件

| 模块 | 职责 |
|------|------|
| `skill_loader.py` | 加载 SKILL.md 文件（YAML frontmatter + Markdown）。管理任务板（`_task_board` 字典），自动同步到 `workspace/collab/`。 |
| `plugin_api.py` | 插件装饰器 API：`@register`、`@on_event`、`@tool` 等。定义 `ToolPlugin`、`HookPlugin`、`AdapterPlugin` 基础概念。 |
| `sdk.py` | 插件与 Agent 运行时交互的公开 SDK 接口。 |

---

## 内置工具（`src/opensquad/tools/`）

| 模块 | 工具 | 注册方式 |
|------|------|----------|
| `filesystem.py` | `read_file`、`write_file`、`list_directory`、`search_files` 等 | 在 Agent `config.json > tools` 中列出 |
| `im.py` | `send_message`、`get_history`、`list_groups`、`join_group` | 在 config 中列出 |
| `collaboration.py` | `start_collaboration`、`join_collaboration`、`end_collaboration`、`leave_collaboration`、`get_team_status`、`list_active_collaborations` 及 Board/Task 工具共 20+ 函数 | 在 config 中列出 |
| `agent_setup.py` | `load_task_context`、`unload_task_context`、`list_installed`、`install_skill`、`get_skill_info` | 在 config 中列出 |
| `system.py` | `get_time`、`run_command`、`get_env_info`、`wait`（支持可中断模式） | 在 config 中列出 |
| `delegate.py` | `delegate_task`、`delegate_task_submit`、`delegate_task_result`、`delegate_task_list` | 在 config 中列出 |
| `workspace.py` | 工作区管理工具 | 在 config 中列出 |
| `task_watch.py` | 任务监控工具 | 在 config 中列出 |
| `long_memory.py` | `query_memory`、`store_memory`（需要 agent_memory_tool） | 在 config 中列出 |
| `mcp_adapter.py` | 动态 MCP 工具代理 | 从 MCP 配置自动发现 |

### 工具集合定义（`agents_boot.py`）

| 集合 | 成员 | 作用 |
|------|------|------|
| `TOOL_MODULES` | system, filesystem, im, agent_setup, long_memory, collaboration, delegate_task, workspace, task_watch | 工具模块名→模块路径的映射表 |
| `CORE_TOOLS` | system, filesystem, im, long_memory, collaboration | 控制 Prompt 中的详细程度（完整签名 vs 单行摘要） |
| `MANDATORY_TOOLS` | system, filesystem, agent_setup, im, collaboration, delegate_task, workspace, task_watch | 始终注册的工具，不受 `config.json > tools[]` 限制 |

### 工具注册规则
- 内置工具：Agent 必须在 `config.json > tools[]` 中列出工具模块名称，未列出则不注册（`MANDATORY_TOOLS` 中的除外）。
- 插件工具中 `auto_register: true`（`plugin.json` 中设置）：自动为所有 Agent 注册。
- 插件工具中 `auto_register: false`：Agent 必须在 `config.json > tools[]` 中列出工具名称。
- `CORE_TOOLS` 集合仅控制 Prompt 中的详细程度（完整签名 vs 单行摘要），不影响工具的可用性。

---

## Prompt 架构

系统提示词模板分为两种模式，由 `runner._setup_prompt()` 动态选择：

- **Native FC 模式**：`src/opensquad/base_fc.md`
- **XML 模式**：`src/opensquad/base_xml.md`

关键占位符由 `runner._setup_prompt()` 注入：

| 占位符 | 来源 | 内容 |
|--------|------|------|
| `{{EXPERT_ROLE_CARD}}` | `agents/{name}/role.md` | Agent 的角色定义 |
| `{{TOOL_DESCRIPTIONS}}` | `registry.generate_tool_descriptions()` | 所有已注册工具的签名/文档 |
| `{{MCP_GUIDE}}` | `runner._setup_prompt()` | MCP 服务器使用指南 |
| `{{SKILLS_INSTRUCTIONS}}` | `skill_loader` | 已加载的技能指令 |

上下文变量由 `context_base.inject_standard()` 注入：

| 变量 | 来源 | 内容 |
|------|------|------|
| `AGENT_PROFILE` | `agents/{name}/agent.md` | Agent 的长期记忆 |
| `CONTEXT_SUMMARY` | 会话管理器 | 当前会话上下文摘要 |
| `AGENT_WORKSPACE` | 目录扫描 | 工作区目录结构 |
| `TEAM_COLLAB_CARDS` | 协作管理器 | 当前活跃的协作卡片 |
| `RUNTIME_STATE` | 状态管理器 | Agent 运行时状态 |
| `MEMORY_CONTEXT` | 长期记忆 | 相关记忆召回内容 |

---

## Gateway 层（`gateway/`）

### 后端（`gateway/backend/`）
- FastAPI 应用，端口 9555
- 路由：`/api/ai-web/admin/...` 代理到 Launcher（9600）的 `/api/...`
- WebSocket：`/ai-web/ws/{agentId}?token={token}`，用于实时流式传输
- 鉴权：基于 Token 的会话管理
- 文件上传：存储在 `gateway/backend/uploads/`

### 前端（`gateway/nexuschat-pro/`）
- React + TypeScript + Vite，端口 9530
- 核心组件：`AIChatPage.tsx`（主聊天界面）、`AgentManagerPage.tsx`（Agent 管理）
- Vite 代理：`/api` 和 `/uploads` -> Gateway 后端（仅 HTTP）
- WebSocket：直接连接 Gateway :9555（不经过代理）

---

## 插件系统（`plugins/`）

共 **20** 个插件，分为 Tool（14）、Hook（3）、Platform（3）三类。`auto_register` 列含义按类型而异：Tool 插件为 `tools[0].auto_register`（是否随所有 Agent 自动注册），Platform 插件为 `service.auto_start`（平台适配器是否随 Launcher 自动启动），Hook 插件为 `—`（钩子随插件加载，无此概念）。

### Tool 插件（14）

| 插件 | auto_register | 说明 |
|------|---------------|------|
| `websearch` | true | Web 搜索与网页抓取（通过外部 WebSearch 服务） |
| `vision` | true | 图像识别（将图片路径写入 `img_path.txt` 供视觉模型处理） |
| `media` | false | 媒体格式转换（基于 ffmpeg 的音频转码） |
| `whisper` | false | 语音转文字（通过 Whisper 服务，支持中英文） |
| `mcp_query` | true | MCP 服务器管理（list/add/remove/reconnect/reload） |
| `sequential_think` | false | 结构化思维与摘要生成 |
| `git_core` | true | 本地 Git 版本控制操作（带自动身份） |
| `agent_factory` | false | 通过 Launcher API 动态创建/配置/启动 Agent |
| `chat_account` | false | ChatPro 账号与群组管理 |
| `email_assistant` | false | 通用 IMAP/SMTP 邮件（IMAP IDLE 收件 + SMTP SSL 发件） |
| `plugin_admin` | false | 插件管理（列出/启停/读写配置/热重载） |
| `quick_note` | false | 快速笔记（标签 + 搜索） |
| `reminder` | true | 定时提醒（支持延时与绝对时间触发） |
| `vcs_remote` | true | 远程 VCS 操作（通过 `gh` CLI 处理 GitHub Issues / PRs） |

### Hook 插件（3）

| 插件 | 说明 |
|------|------|
| `long_memory` | 长期记忆（语义召回 + 关键词提取 + 共现知识图谱） |
| `token_analytics` | Token 用量采集（按模型/工具拆解，写入 SQLite） |
| `task_watch` | 任务监控面板（Agent 任务生命周期、check-in、stall、工具活动） |

### Platform 插件（3）

| 插件 | auto_start | 说明 |
|------|------------|------|
| `telegram` | true | Telegram 平台适配器（入站消息 + 出站 send 工具） |
| `feishu` | true | 飞书 / Lark 平台适配器（入站消息 + 出站 send 工具） |
| `external_api` | true | 外部 API 适配器（HTTP/WebSocket 网关，桥接第三方系统） |

`plugin.json` 中的 `enabled: true` 的插件列表保存在 `src/plugins/builtin_plugins.json`，共 6 个默认启用的系统级插件：`mcp_query`、`plugin_admin`、`reminder`、`task_watch`、`vision`、`websearch`——这些随 OpenSquad 一起发布，默认开启、不可卸载，单 Agent 粒度的开关在 UI 中隐藏。

插件加载：`plugin_manager.py` 扫描 `plugins/*/plugin.json`，实例化插件类，根据 `auto_register` / `auto_start` 标志和 Agent 配置注册工具或启动服务。`plugin.json` 由每次 Agent 启动/热重载时的 `@register(...)` 装饰器**自动生成**——请勿手动编辑。

### 插件配置路由

- **标准插件**（`tool`、`hook`、`service` 类型）：配置保存到 `data/plugins/{name}/config.json`，加载时与 schema 默认值合并。
- **平台插件**（`platform` 类型 — feishu、telegram、external_api）：配置桥接到 `system_config.json`。Launcher 的 GET/PUT 配置处理器检测 `plugin.json` 中的 `config.section`，并读写 `system_config.json` 中对应的节。例如：`feishu.bots` → `system_config.json["feishu"]["bots"]`。
- **分布式广播**：当任意节点收到配置保存请求时，自动广播到所有其他在线节点，无需额外插件代码。

---

## Agent 启动流程（`src/opensquad/agents_boot.py`）

1. 加载目标 Agent 的 `config.json`
2. 初始化日志（`log_setup.py`）
3. 初始化会话管理器（会话目录：`agents/{name}/data/sessions/`）
4. 用 Agent 目录初始化 `InputHub`
5. 根据 `config.json > tools[]` 注册内置工具
6. 注册插件工具（auto_register + 显式列出的）
7. 从 `agents/{name}/mcp_config.json` 加载 MCP 服务器
8. 设置 ChatPro 桥接（登录、加入群组、WebSocket）
9. 设置 Gateway 适配器（连接 Gateway WebSocket）
10. 初始化上下文（agent.md、role.md、环境信息）
11. 加载技能（私有技能从 `agents/{name}/skills/`，公共技能从 `skills/`）
12. 启动 runner 主循环

---

## 数据流

### 用户消息 -> Agent 响应（Web UI）
```
浏览器 -> WebSocket -> Gateway(9555) -> Agent 进程 -> LLM API
                                           |
                                        工具调用 -> filesystem / im / mcp / ...
                                           |
                                        响应流 -> WebSocket -> 浏览器
```

### 群聊消息 -> Agent
```
ChatPro 服务器 -> WebSocket -> bridge.py -> message_router -> input_hub -> runner
```

### Agent -> 群聊
```
runner -> tool_call(im.send_message) -> bridge.py -> ChatPro API
```

---

## 配置说明

### `system_config.json`
所有服务的中央配置。已知字段（参见 `src/system_config.example.json`）：
- `ports`：各服务的端口分配
- `hosts`：绑定地址
- `auth`：包含 `node_secret`（节点间认证密钥）
- `node`：节点标识与注册配置

> **注意**：`agent_registry`、`services`、`auto_start` 字段在当前版本的 `system_config.example.json` 中不存在，请以实际文件为准。

### `system_config.py`
读取类（`syscfg`），提供对 `system_config.json` 的类型化访问，并回退到环境变量和硬编码默认值。优先级：**环境变量 > system_config.json > 硬编码默认值**。

### Agent `config.json`
每个 Agent 的独立配置：
- `agent_id`：唯一标识符
- `model_name`、`base_url`、`api_key`：LLM 连接参数
- `tools`：要注册的工具模块名称列表
- `max_tokens`、`temperature`：LLM 参数
- `chatpro`：群聊连接设置
