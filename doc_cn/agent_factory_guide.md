# Agent Factory 使用指南

## 概述

Agent Factory 是一个强大的插件，允许你通过编程方式动态创建、配置和管理 OpenSquad Agent。

## 前置条件

### 1. 启用插件

确保在 agent 的 `config.json` 中包含这两个插件：

```json
{
  "tools": [
    "chat_account",
    "agent_factory"
  ],
  "tool_levels": {
    "chat_account": "core",
    "agent_factory": "core"
  }
}
```

### 2. 确认 Launcher 服务运行

Launcher 默认运行在 `http://127.0.0.1:9600`

## 完整工作流

### 步骤 1: 注册 OpenSquad 账号

每个 Agent 需要一个唯一的 OpenSquad 账号（像身份证一样）。

```python
# 使用 chat_account 插件注册账号
result = chat_account.register_account(
    email="mybot@ai",
    password="MyBot@123",
    name="我的机器人"
)

# 返回示例:
# {
#   "success": true,
#   "user_id": "u_12345",
#   "email": "mybot@ai",
#   "name": "我的机器人"
# }
```

### 步骤 2: 创建 Agent 目录结构

```python
# 创建 Agent 基础目录和默认配置文件
result = agent_factory.create_agent(
    dir_name="my_bot",          # 目录名（只允许字母/数字/下划线）
    agent_name="我的机器人",     # 显示名称
    agent_type="general",        # Agent 类型
    description="一个通用助手"   # 描述（可选）
)

# 返回示例:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "message": "Agent created successfully"
# }
```

这将在 `agents/my_bot/` 创建以下文件：
- `config.json` - Agent 配置
- `role.md` - 角色设定
- `mcp_config.json` - MCP 配置

### 步骤 3: 配置 Agent

```python
# 写入完整的 Agent 配置（会覆盖默认配置）
config = {
    "agent_id": "mybot-001",
    "agent_name": "我的机器人",
    "agent_type": "general",
    "description": "一个通用助手",

    # 模型配置
    "model": {
        "provider": "openai",
        "api_key": "sk-xxx",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "token_max": 128000,
        "temperature": 0.3,
        "tool_call_mode": "auto",  # auto | native | xml
        "tool_filter": "high"      # high (97个工具) | all (124个) | baseline (57个)
    },

    # 工具集
    "tools": ["system", "filesystem", "websearch", "im", "agent_setup"],

    # 群聊配置（绑定注册的账号）
    "group_chat": {
        "enabled": true,
        "email": "mybot@ai",      # 步骤1注册的邮箱
        "password": "MyBot@123",   # 步骤1注册的密码
        "groups": ["g813q4"]       # 要加入的群组ID
    },

    # Web 服务器
    "web_server": {
        "enabled": true,
        "port": 8010
    },

    # Gateway 连接
    "gateway": {
        "enabled": true,
        "url": "ws://127.0.0.1:9555/ai-ws/register"
    },

    # 协作模式
    "collaboration": {
        "enabled": true,
        "role": "worker"
    }
}

result = agent_factory.configure_agent(
    dir_name="my_bot",
    config=config
)

# 返回示例:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "message": "Configuration saved"
# }
```

### 步骤 4: 设置 Agent 角色

```python
# 定义 Agent 的身份、专长和行为方式
role_content = """
# 我的机器人

## 身份
你是一个通用助手，专注于帮助用户完成日常任务。

## 专长
- 信息查询
- 任务管理
- 问题解答

## 行为准则
- 友好、专业
- 回答简洁明确
- 主动询问不清楚的地方
"""

result = agent_factory.set_agent_role(
    dir_name="my_bot",
    role_content=role_content
)

# 返回示例:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "message": "Role file saved"
# }
```

### 步骤 5: 启动 Agent

```python
# 启动 Agent 进程
result = agent_factory.start_agent(dir_name="my_bot")

# 返回示例:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "pid": 12345,
#   "port": 8010,
#   "message": "Agent started"
# }
```

### 管理 Agent

#### 列出所有 Agent

```python
result = agent_factory.list_agents()

# 返回示例:
# {
#   "success": true,
#   "count": 3,
#   "agents": [
#     {
#       "dir_name": "my_bot",
#       "agent_id": "mybot-001",
#       "agent_name": "我的机器人",
#       "alive": true,
#       "pid": 12345,
#       "port": 8010
#     },
#     ...
#   ]
# }
```

#### 停止 Agent

```python
result = agent_factory.stop_agent(dir_name="my_bot")
```

#### 重启 Agent

```python
# 修改配置后重启使新配置生效
result = agent_factory.restart_agent(dir_name="my_bot")
```

## 配置说明

### 模型配置

#### tool_call_mode（工具调用模式）
- `auto` - 自动检测（推荐）
- `native` - 强制使用 Native Function Calling
- `xml` - 强制使用 XML 格式

#### tool_filter（工具过滤级别）
- `high` - 97 个高频工具（推荐）
- `all` - 124 个所有工具
- `baseline` - 57 个基础工具

### 群聊配置

#### 创建新群组

```python
# 使用 chat_account 插件创建群组
result = chat_account.create_group(
    name="我的团队",
    description="团队协作群",
    is_private=False,
    email="mybot@ai",      # 创建者账号
    password="MyBot@123"
)

# 返回群组ID，用于 group_chat.groups 配置
```

#### 加入现有群组

```python
result = chat_account.join_group(
    group_id="g813q4",
    email="mybot@ai",
    password="MyBot@123"
)
```

## 完整示例

```python
# 1. 注册账号
register_result = chat_account.register_account(
    email="assistant@ai",
    password="Assistant@123",
    name="智能助手"
)

# 2. 创建 Agent
create_result = agent_factory.create_agent(
    dir_name="assistant",
    agent_name="智能助手"
)

# 3. 配置 Agent
config = {
    "agent_id": "assistant-001",
    "agent_name": "智能助手",
    "model": {
        "provider": "openai",
        "api_key": "sk-xxx",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "token_max": 128000,
        "temperature": 0.3,
        "tool_call_mode": "auto",
        "tool_filter": "high"
    },
    "tools": ["system", "filesystem", "websearch", "im"],
    "group_chat": {
        "enabled": true,
        "email": "assistant@ai",
        "password": "Assistant@123",
        "groups": ["g813q4"]
    },
    "web_server": {"enabled": true, "port": 8011},
    "gateway": {
        "enabled": true,
        "url": "ws://127.0.0.1:9555/ai-ws/register"
    }
}

configure_result = agent_factory.configure_agent(
    dir_name="assistant",
    config=config
)

# 4. 设置角色
role = """
# 智能助手

你是一个友好的通用助手，帮助用户完成各种任务。
"""

role_result = agent_factory.set_agent_role(
    dir_name="assistant",
    role_content=role
)

# 5. 启动 Agent
start_result = agent_factory.start_agent(dir_name="assistant")

print(f"Agent 已启动: PID {start_result['pid']}, Port {start_result['port']}")
```

## 故障排查

### 插件未加载

确保在 agent 的 `config.json` 中：
1. `tools` 数组包含 `"chat_account"` 和 `"agent_factory"`
2. `tool_levels` 中设置了对应的级别

重启 agent 后生效：
```bash
curl -X POST http://127.0.0.1:9600/api/agents/{agent_dir}/restart
```

### 账号注册失败

- 检查邮箱格式是否正确（建议使用 `xxx@ai` 格式）
- 确保邮箱未被占用
- 密码需符合安全要求

### Agent 启动失败

- 检查端口是否被占用
- 确保 config.json 格式正确
- 查看 agent 日志: `agents/{dir_name}/logs/`

## API 参考

详细的 API 文档请查看：
- `plugins/agent_factory/plugin.py`
- `plugins/chat_account/plugin.py`
