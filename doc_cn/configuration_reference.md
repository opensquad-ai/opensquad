# 配置参考手册

本文档列出 OpenSquad 所有配置项及其说明。

---

## 配置文件位置

| 文件 | 用途 |
|------|------|
| `system_config.json` | 系统级配置（工作区根目录） |
| `src/agents/<name>/config.json` | Agent 配置 |
| `src/model_cards/*.json` | 模型卡配置 |

> 配置优先级：**环境变量 > system_config.json > 硬编码默认值**

---

## system_config.json

配置文件位于工作区根目录。首次启动时会自动从 `system_config.example.json` 创建。

### ports — 服务端口

```json
{
  "ports": {
    "gateway": 9555,
    "launcher": 9600,
    "external_adapter": 9700,
    "whisper": 5001,
    "websearch": 9001,
    "agent_web_server": 8001,
    "legacy_server": 8000,
    "frontend": 9530
  }
}
```

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|----------|------|
| `gateway` | int | 9555 | `GATEWAY_PORT` | Gateway 后端端口 |
| `launcher` | int | 9600 | `LAUNCHER_PORT` | Agent 管理端口 |
| `external_adapter` | int | 9700 | `EXTERNAL_ADAPTER_PORT` | 外部适配器端口 |
| `whisper` | int | 5001 | `WHISPER_PORT` | 语音转文字服务端口 |
| `websearch` | int | 9001 | `WEBSEARCH_PORT` | 网页搜索服务端口 |
| `agent_web_server` | int | 8001 | `AGENT_WEB_SERVER_PORT` | Agent Web 服务端口 |
| `legacy_server` | int | 8000 | `LEGACY_SERVER_PORT` | 旧版服务器端口 |
| `frontend` | int | 9530 | `FRONTEND_PORT` | 前端开发服务器端口 |

### hosts — 服务绑定地址

```json
{
  "hosts": {
    "gateway": "127.0.0.1",
    "launcher": "127.0.0.1",
    "external_adapter": "0.0.0.0",
    "whisper": "0.0.0.0",
    "legacy_server": "0.0.0.0",
    "frontend": "0.0.0.0"
  }
}
```

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|----------|------|
| `gateway` | string | 127.0.0.1 | `GATEWAY_HOST` | Gateway 绑定地址 |
| `launcher` | string | 127.0.0.1 | `LAUNCHER_HOST` | Launcher 绑定地址 |
| `external_adapter` | string | 0.0.0.0 | `EXTERNAL_ADAPTER_HOST` | 外部适配器地址 |

> **注意**：Docker 部署时 `gateway` 会自动设为 `0.0.0.0` 以便外部访问。

### auth — 认证配置

```json
{
  "auth": {
    "gateway_token": "",
    "external_api_key": "",
    "node_secret": ""
  }
}
```

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|----------|------|
| `gateway_token` | string | "" | `GATEWAY_TOKEN` | Gateway 访问令牌 |
| `external_api_key` | string | 自动生成 | `EXTERNAL_API_KEY` | 外部 API 访问密钥 |
| `node_secret` | string | "" | `NODE_SECRET` | 多节点通信密钥 |

### node — 节点标识

```json
{
  "node": {
    "id": "node-local",
    "label": "",
    "register_to_gateway": false,
    "launcher_url": ""
  }
}
```

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|----------|------|
| `id` | string | 自动生成 | `NODE_ID` | 节点唯一 ID |
| `label` | string | "" | `NODE_LABEL` | 节点显示名称 |
| `register_to_gateway` | bool | false | `NODE_REGISTER_TO_GATEWAY` | 是否自动注册 |
| `launcher_url` | string | "" | `NODE_LAUNCHER_URL` | 跨机器 Launcher 地址 |

### defaults — 默认参数

```json
{
  "defaults": {
    "agent_id": "default-001",
    "request_timeout": 120,
    "async_result_ttl": 600
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent_id` | string | default-001 | 默认 Agent ID |
| `request_timeout` | int | 120 | HTTP 请求超时（秒） |
| `async_result_ttl` | int | 600 | 异步结果缓存时间（秒） |

### logging — 日志配置

```json
{
  "logging": {
    "log_level": "INFO",
    "log_format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "log_date_format": "%Y-%m-%d %H:%M:%S",
    "max_size_mb": 3,
    "backup_count": 5,
    "tool_call_debug": false,
    "tool_call_debug_max_size_mb": 5,
    "tool_call_debug_backup_count": 3
  }
}
```

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|----------|------|
| `log_level` | string | INFO | `LOG_LEVEL` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `max_size_mb` | int | 3 | `LOG_MAX_SIZE_MB` | 日志文件最大大小（MB） |
| `backup_count` | int | 5 | `LOG_BACKUP_COUNT` | 日志备份数量 |
| `tool_call_debug` | bool | false | `TOOL_CALL_DEBUG` | 是否启用工具调用调试日志 |

### context_compression — 上下文压缩

```json
{
  "context_compression": {
    "trigger_threshold": 0.75,
    "keep_recent_fraction": 0.1,
    "recent_hard_cap_fraction": 0.30,
    "keep_recent_rounds": 2,
    "summary_max_tokens": 4000,
    "conv_text_budget_chars": 24000
  }
}
```

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|----------|------|
| `trigger_threshold` | float | 0.75 | `CTX_TRIGGER_THRESHOLD` | 压缩触发阈值（token_max 的比例） |
| `keep_recent_fraction` | float | 0.10 | `CTX_KEEP_RECENT_FRAC` | 压缩后保留的最近 token 比例 |
| `recent_hard_cap_fraction` | float | 0.30 | `CTX_RECENT_HARD_CAP_FRAC` | 保留区（不进摘要的最近部分）最多占当前 token 的比例；超过即放弃 user 锚点/轮次保护，把多余部分送进摘要，防止长自主工具调用场景下压缩退化为空操作 |
| `keep_recent_rounds` | int | 2 | `CTX_KEEP_RECENT_ROUNDS` | （已弃用）保留的最近轮次数 |
| `summary_max_tokens` | int | 4000 | `CTX_SUMMARY_MAX_TOKENS` | 摘要生成最大 token 数 |
| `conv_text_budget_chars` | int | 24000 | `CTX_CONV_TEXT_BUDGET_CHARS` | 对话文本摘要预算字符数 |

### vcs — 版本控制

```json
{
  "vcs": {
    "git_server": "",
    "default_remote": "origin",
    "default_branch": "main"
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `git_server` | string | "" | Git 服务器基础 URL |
| `default_remote` | string | origin | 默认远程仓库名 |
| `default_branch` | string | main | 默认分支名 |

### filesystem — 文件系统白名单

```json
{
  "filesystem": {
    "workspace_dirs": ["/data/projects", "../shared"]
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `workspace_dirs` | string[] | 全局文件系统白名单，所有 Agent 均可访问这些目录 |

### services — 服务开关

```json
{
  "services": {
    "feishu": { "enabled": false },
    "telegram": { "enabled": false },
    "qq": { "enabled": false }
  }
}
```

控制各平台集成插件的启用/禁用。

### github — GitHub 集成

```json
{
  "github": {
    "plugins_token": ""
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `plugins_token` | string | 用于插件市场的 GitHub Token |

---

## Agent config.json

每个 Agent 在 `src/agents/<name>/config.json` 中定义。

### 完整字段

```json
{
  "agent_id": "default-001",
  "agent_name": "Default",
  "agent_type": "general",
  "description": "General-purpose AI assistant",
  "model": {
    "provider": "openai_compat",
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model_name": "deepseek-v4-flash",
    "token_max": 128000,
    "temperature": 0.7,
    "frequency_penalty": 0,
    "presence_penalty": 0,
    "top_k": 0,
    "is_think": true,
    "is_image": false,
    "is_audio": false,
    "is_video": false,
    "tool_call_mode": "auto",
    "tool_filter": "high",
    "_card": "deepseek-v4-flash"
  },
  "tools": [
    "system", "filesystem", "agent_setup", "im",
    "collaboration", "delegate_task", "workspace", "task_watch",
    "websearch", "reminder", "vision", "mcp_query", "plugin_admin"
  ],
  "group_chat": { "enabled": false },
  "web_server": { "enabled": true },
  "gateway": { "enabled": true, "url": "" },
  "prompt": { "role": "role.md" },
  "mcp": { "enabled": true },
  "skills": {
    "enabled": true,
    "active": []
  }
}
```

### 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `agent_id` | string | 是 | Agent 唯一标识 |
| `agent_name` | string | 是 | Agent 显示名称 |
| `agent_type` | string | 否 | Agent 类型：`general`/`specialized` |
| `description` | string | 否 | Agent 描述 |

### model — 模型配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `provider` | string | 是 | 接口协议：`openai`/`openai_compat`/`anthropic`/`google` |
| `model_name` | string | 是 | 模型名称 |
| `base_url` | string | 是 | API 基础地址 |
| `api_key` | string | 是 | API 密钥 |
| `token_max` | int | 否 | 最大上下文 token，默认 128000 |
| `temperature` | float | 否 | 采样温度，默认 0.7 |
| `frequency_penalty` | float | 否 | 频率惩罚 |
| `presence_penalty` | float | 否 | 存在惩罚 |
| `top_k` | int | 否 | Top-K 采样参数 |
| `is_think` | bool | 否 | 启用思考模式 |
| `is_image` | bool | 否 | 支持图像输入 |
| `is_audio` | bool | 否 | 支持音频输入 |
| `is_video` | bool | 否 | 支持视频输入 |
| `tool_call_mode` | string | 否 | 工具调用模式：`auto`/`native`/`xml` |
| `tool_filter` | string | 否 | 工具过滤级别：`high`/`medium`/`low` |
| `_card` | string | 否 | 引用的模型卡名称 |

### tools — 启用的工具

工具列表，可用值：

| 工具名 | 说明 |
|--------|------|
| `system` | 系统命令执行 |
| `filesystem` | 文件读写 |
| `agent_setup` | Agent 配置管理 |
| `im` | 即时通讯 |
| `collaboration` | 多 Agent 协作 |
| `delegate_task` | 任务委托 |
| `workspace` | 工作区管理 |
| `task_watch` | 任务监控 |
| `websearch` | 网页搜索 |
| `reminder` | 提醒 |
| `vision` | 图像识别 |
| `mcp_query` | MCP 协议查询 |
| `plugin_admin` | 插件管理 |
| `web` | HTTP 请求 |
| `long_memory` | 长期记忆 |

### group_chat — 群聊配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用群聊 |
| `email` | string | 群聊账号邮箱 |
| `password` | string | 群聊账号密码 |
| `groups` | string[] | 群聊 ID 列表 |

### skills — 技能配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用技能系统 |
| `active` | string[] | 激活的私有技能列表 |

### prompt — 提示词配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `role` | string | 角色卡文件路径（相对于 Agent 目录） |

### gateway — 网关配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否连接到 Gateway |
| `url` | string | Gateway 注册 URL，默认自动填充 |

### web_server — Web 服务配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用 Agent Web 服务 |

### mcp — MCP 协议配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用 MCP 协议支持 |

---

## Agent 目录结构

```
<workspace>/agents/{agent_id}/
├── config.json         # 主配置文件（模型、工具、权限）
├── role.md             # 系统提示词 / 角色定义
├── agent.md            # 长期记忆文档（持久化）
├── data/               # Agent 数据存储
│   ├── sessions/       # 会话历史（JSON 日志）
│   └── profile.json    # UI 展示信息（名称、头像、描述）
└── mcp_config.json     # （可选）静态 MCP 服务配置
```

> **注意**：`MANDATORY_TOOLS`（system、filesystem、agent_setup、im、collaboration、delegate_task、workspace、task_watch）始终注册，不受 `tools` 列表限制。

### 展示信息（profile.json）

`data/profile.json` 定义 Agent 在 Web UI 中的展示方式：

```json
{
  "name": "PM Agent",
  "avatar": "https://api.dicebear.com/7.x/bottts-neutral/svg?seed=pm",
  "description": "项目协调与团队管理"
}
```

### 记忆管理

- **角色提示词（`role.md`）**：核心身份定义，会话期间保持不变
- **长期记忆（`agent.md`）**：Agent 可读写的 Markdown 文件，每轮对话都会注入
- **会话历史**：存储在 `agents/{agent_id}/data/sessions/`，JSON 格式日志

### 创建新 Agent

1. **复制**：复制现有 Agent 文件夹（如 `<workspace>/agents/coder`）
2. **重命名**：修改文件夹名和 `config.json` 中的 `agent_id`
3. **配置**：更新 `model` 和 `tools` 以适应新 Agent 的用途
4. **定义角色**：编辑 `role.md` 赋予 Agent 新的人设或专业指令
5. **重启**：通过 Launcher 启动 Agent

---

## API 参考

### 系统配置 API

通过 `opensquad.system_config` 模块获取系统配置：

```python
from opensquad.system_config import syscfg

# 端口 / 地址 / URL
gateway_port = syscfg.port("gateway")
launcher_port = syscfg.port("launcher")
gateway_http  = syscfg.gateway_http()        # "http://127.0.0.1:9555"
gateway_ws    = syscfg.gateway_ws()           # "ws://127.0.0.1:9555"
api_key       = syscfg.auth("external_api_key")

# 通用配置读取
value = syscfg.get("feishu", "app_id")

# 工作区路径
workspace = syscfg.get_workspace()
data_dir  = syscfg.workspace_data_dir("uploads")
logs_dir  = syscfg.workspace_logs_dir()
```

### WebSocket API

**连接**：`ws://127.0.0.1:9555/ai-ws/chat`

**发送聊天消息**：
```json
{
  "type": "chat",
  "agent_id": "default-001",
  "session_id": "session-xxx",
  "content": "Hello",
  "attachments": []
}
```

**系统命令**：

| 命令 | 说明 |
|------|------|
| `new_session` | 创建新会话 |
| `stop_task` | 停止当前任务 |
| `compress_context` | 手动压缩上下文 |
| `switch_and_reply` | 切换会话并回复 |
| `request_token_stats` | 请求 Token 统计 |

### 事件类型

| 事件 | 说明 |
|------|------|
| `chat_message` | 新聊天消息 |
| `tool_call` | 工具调用 |
| `tool_result` | 工具调用结果 |
| `agent_status` | Agent 状态变更 |
| `task_progress` | 任务进度更新 |
| `session_created` | 会话创建 |
| `session_deleted` | 会话删除 |

### External API（端口 9700）

```http
POST /api/chat
Content-Type: application/json
X-API-Key: your-api-key

{
  "agent_id": "default-001",
  "message": "Hello",
  "user_id": "feishu_user_xxx",
  "channel": "feishu_private"
}
```