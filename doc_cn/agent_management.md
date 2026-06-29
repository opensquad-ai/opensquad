# Agent 管理完全指南：角色、模型、协作卡配置

## 概述

在 OpenSquad 系统中，Agent 可以通过两种方式管理自己或其他 Agent 的配置：

| 方式 | 适用场景 | 优势 | 工具/方法 |
|------|---------|------|----------|
| **通过插件 API** | 创建新 Agent、完整配置管理 | 安全、标准化、有校验、支持热重载 | `agent_factory.*` + `chat_account.*` |
| **直接文件操作** | 快速修改、批量操作、复杂场景 | 灵活、直接、支持任意编辑 | `filesystem.*` |

**推荐优先级**：
1. **新建 Agent**: 必须使用 `agent_factory` 插件（自动生成结构、注册账号、启动进程）
2. **修改现有配置**: 优先使用 `agent_factory`，需要复杂操作时使用 `filesystem`
3. **修改自己**: 两种方式都可以，`filesystem` 更简单直接

---

## 1. Agent 配置结构

### 1.1 目录结构

```
agents/
├── your_agent/
│   ├── config.json          # 主配置文件（必需）
│   ├── role.md              # 角色设定文件（必需）
│   ├── mcp_config.json      # MCP 服务器配置（可选）
│   └── data/
│       ├── sessions/        # 会话历史
│       └── logs/            # 运行日志
```

### 1.2 config.json 完整结构

```json
{
  "agent_id": "100001",                    // Agent 唯一 ID
  "agent_name": "my-agent-001",           // Agent 名称
  "agent_type": "general",                // Agent 类型
  "description": "Agent 描述",

  // === 模型配置 ===
  "model": {
    "provider": "openai_compat",          // 提供商：openai、openai_compat、anthropic 等
    "api_key": "sk-xxxx",                 // API 密钥
    "base_url": "https://api.example.com", // API 基础 URL
    "model_name": "gpt-4",                // 模型名称
    "token_max": 128000,                  // 最大 token 数
    "temperature": 0.3,                   // 温度参数 (0-1)
    "is_think": false,                    // 是否支持 CoT 思考
    "is_image": false,                    // 是否支持图像输入
    "is_video": false,                    // 是否支持视频输入
    "_card": "GPT-4",                     // 显示名称（前端用）
    "tool_call_mode": "auto",             // 工具调用模式：auto | native | xml
    "tool_filter": "high"                 // 工具过滤级别：high (97) | all (124) | baseline (57)
  },

  // === 工具配置 ===
  "tools": [
    "system",                             // 系统工具
    "filesystem",                         // 文件系统
    "im",                                 // 群聊消息
    "agent_setup",                        // Agent 自我管理
    "collaboration",                      // 协作工具
    "agent_factory",                      // Agent 工厂（创建其他 Agent）
    "chat_account"                        // 聊天账号管理
  ],

  // === 协作配置 ===
  "collaboration": {
    "enabled": true,                      // 是否启用协作
    "role": "developer"                   // 协作角色：pm、developer、qa、worker 等
  },

  // === 群聊配置 ===
  "group_chat": {
    "enabled": true,                      // 是否启用群聊
    "email": "myagent@ai",                // 登录邮箱（必须先注册）
    "password": "123456",                 // 登录密码
    "groups": ["gXXXXX"]                  // 加入的群组 ID 列表
  },

  // === Web 服务器配置 ===
  "web_server": {
    "enabled": true,                      // 是否启用 Web 界面
    "port": 8010                          // 监听端口
  },

  // === Gateway 配置 ===
  "gateway": {
    "enabled": true,                      // 是否注册到 Gateway
    "url": "ws://127.0.0.1:9555/ai-ws/register"
  },

  // === 其他配置 ===
  "prompt": {
    "role": "role.md"                     // 角色文件路径
  },
  "default_wake_mode": "strict",          // 唤醒模式：strict | relaxed
  "mcp": {
    "enabled": true                       // 是否启用 MCP
  },
  "skills": {
    "enabled": true,                      // 是否启用 Skills
    "active": ["skill_name"]              // 激活的 Skill 列表
  },
  "filesystem": {
    "workspace_dirs": [                   // 文件系统白名单
      "/path/to/workspace"
    ]
  },
  "tool_levels": {                        // 工具优先级覆盖
    "websearch": "core",
    "filesystem": "core"
  }
}
```

### 1.3 role.md 结构

`role.md` 是 Agent 的"人格设定"，在每次对话时注入系统提示。

```markdown
# Role: Agent 名称

你的群聊昵称是 xxx。
你是一个专业的 XXX，擅长 YYY。

## 核心能力
- 能力 1
- 能力 2

## 工作流程
1. 步骤 1
2. 步骤 2

## 行为准则
- 准则 1
- 准则 2

## 交互风格
- 简洁专业
- 主动思考
```

---

## 2. 通过 agent_factory 插件管理（推荐）

### 2.1 创建新 Agent 完整流程

```xml
<!-- 步骤 1: 注册聊天账号 -->
<tool name="chat_account">
  <function>register_account</function>
  <parameters>
    <email>newagent@ai</email>
    <password>123456</password>
    <name>新 Agent</name>
  </parameters>
</tool>

<!-- 步骤 2: 创建 Agent 目录结构 -->
<tool name="agent_factory">
  <function>create_agent</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
    <agent_name>我的新助手</agent_name>
    <agent_type>general</agent_type>
    <description>这是一个通用助手</description>
  </parameters>
</tool>

<!-- 步骤 3: 配置完整 config.json -->
<tool name="agent_factory">
  <function>configure_agent</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
    <config>{
      "agent_id": "100002",
      "agent_name": "my-new-agent",
      "agent_type": "general",
      "description": "我的新助手",
      "model": {
        "provider": "openai_compat",
        "api_key": "sk-xxxx",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "token_max": 128000,
        "temperature": 0.3,
        "tool_call_mode": "auto",
        "tool_filter": "high"
      },
      "tools": ["system", "filesystem", "im", "collaboration"],
      "group_chat": {
        "enabled": true,
        "email": "newagent@ai",
        "password": "123456",
        "groups": ["gXXXXX"]
      },
      "collaboration": {
        "enabled": true,
        "role": "developer"
      },
      "web_server": {"enabled": true, "port": 8011},
      "gateway": {"enabled": true, "url": "ws://127.0.0.1:9555/ai-ws/register"}
    }</config>
  </parameters>
</tool>

<!-- 步骤 4: 设置角色 -->
<tool name="agent_factory">
  <function>set_agent_role</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
    <role_content># Role: Python 专家

你的群聊昵称是 newagent。
你是一个专业的 Python 开发专家，擅长代码审查和性能优化。

## 核心能力
- Python 编程
- 代码审查
- 性能分析

## 工作原则
- 代码简洁优雅
- 注重性能
- 遵循 PEP 8 规范</role_content>
  </parameters>
</tool>

<!-- 步骤 5: 启动 Agent -->
<tool name="agent_factory">
  <function>start_agent</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
  </parameters>
</tool>
```

### 2.2 修改现有 Agent

#### 修改模型配置

```xml
<!-- 1. 先读取当前配置 -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>

<!-- 2. 修改后写回（使用 configure_agent） -->
<tool name="agent_factory">
  <function>configure_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
    <config>{
      ... 完整配置（包含修改后的 model 部分）...
    }</config>
  </parameters>
</tool>

<!-- 3. 重启 Agent 使配置生效 -->
<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
  </parameters>
</tool>
```

#### 修改角色设定

```xml
<tool name="agent_factory">
  <function>set_agent_role</function>
  <parameters>
    <dir_name>my_agent</dir_name>
    <role_content># Role: 新角色设定

新的角色描述...</role_content>
  </parameters>
</tool>

<!-- 重启生效 -->
<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
  </parameters>
</tool>
```

#### 修改协作角色

```xml
<!-- 读取 → 修改 collaboration.role → 写回 → 重启 -->
<tool name="agent_factory">
  <function>configure_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
    <config>{
      ...,
      "collaboration": {
        "enabled": true,
        "role": "qa"  // 修改为 QA 角色
      },
      ...
    }</config>
  </parameters>
</tool>

<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
  </parameters>
</tool>
```

---

## 3. 通过 filesystem 直接修改（灵活方式）

### 3.1 修改自己的配置（最常用）

```xml
<!-- 示例：给自己添加新工具 -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/coder/config.json</path>
  </parameters>
</tool>

<!-- 在 tools 数组中添加新工具，然后写回 -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/coder/config.json</path>
    <content>{
  "agent_id": "100001",
  ...,
  "tools": [
    "system",
    "filesystem",
    "new_tool"  // 新增的工具
  ],
  ...
}</content>
  </parameters>
</tool>
```

**重要提示**：
- 修改 `config.json` 后必须重启 Agent 才能生效
- 如果是修改自己，需要请求其他 Agent 或用户重启你
- 如果是修改其他 Agent，可以使用 `agent_factory.restart_agent()` 重启

### 3.2 修改模型配置

```xml
<!-- 直接编辑 config.json 的 model 部分 -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>

<!-- 修改 model 对象后写回 -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
    <content>{
  ...,
  "model": {
    "provider": "openai_compat",
    "api_key": "new-api-key",
    "base_url": "https://new-endpoint.com",
    "model_name": "new-model",
    "token_max": 200000,
    "temperature": 0.5,
    "tool_call_mode": "native",
    "tool_filter": "all"
  },
  ...
}</content>
  </parameters>
</tool>
```

### 3.3 修改角色设定

```xml
<!-- 直接覆盖 role.md -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/my_agent/role.md</path>
    <content># Role: 新角色

你是一个专业的 XXX...

## 能力
- 能力 1
- 能力 2

## 原则
- 原则 1
- 原则 2</content>
  </parameters>
</tool>
```

### 3.4 修改协作配置

```xml
<!-- 方法 1: 修改 config.json 中的 collaboration 对象 -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>

<!-- 修改后写回 -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
    <content>{
  ...,
  "collaboration": {
    "enabled": true,
    "role": "pm"  // 修改为 PM 角色
  },
  ...
}</content>
  </parameters>
</tool>
```

---

## 4. 协作卡（Collab Cards）管理

### 4.1 什么是协作卡？

协作卡是预定义的多 Agent 协作模式，存储在 `collab_cards/*.md`，定义了：
- 协作流程和阶段
- 角色分工和职责
- 消息格式和规范
- 行为约束

### 4.2 查看可用协作卡

```xml
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>
```

返回示例：
```json
{
  "status": "success",
  "cards": [
    {
      "name": "software_dev_team",
      "display_name": "软件开发团队",
      "description": "适用于软件开发类完整项目的多 agent 协作协议",
      "suggested_roles": ["pm", "developer", "qa"],
      "tags": "software, team, pm, dev, qa"
    },
    {
      "name": "code_review",
      "display_name": "代码审查",
      "description": "专注于代码审查的协作模式",
      "suggested_roles": ["reviewer", "author"],
      "tags": "code, review"
    }
  ]
}
```

### 4.3 启动协作会话（PM 专用）

```xml
<tool name="collaboration">
  <function>start_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
    <members>["coder", "qa_agent"]</members>
    <group_id>gXXXXX</group_id>
    <project_name>新功能开发</project_name>
    <project_description>实现用户登录模块</project_description>
  </parameters>
</tool>
```

**流程**：
1. 协作卡内容会被加载到 PM 的系统提示中
2. 系统自动在群聊中 @提及建议的成员，邀请他们加入
3. PM 负责分配任务和管理进度

### 4.4 加入协作会话（Worker 专用）

```xml
<tool name="collaboration">
  <function>join_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
  </parameters>
</tool>
```

**效果**：
- 协作卡内容被加载到 Worker 的系统提示中
- Worker 开始按协作卡规范工作

### 4.5 结束协作会话

```xml
<!-- PM 结束会话 -->
<tool name="collaboration">
  <function>end_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
    <group_id>gXXXXX</group_id>
  </parameters>
</tool>

<!-- Worker 离开会话 -->
<tool name="collaboration">
  <function>leave_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
  </parameters>
</tool>
```

### 4.6 创建自定义协作卡

```xml
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>collab_cards/my_custom_card.md</path>
    <content>---
name: my_custom_card
description: 我的自定义协作模式
tags: custom, workflow
suggested_roles: leader, worker
min_members: 2
---

## 协作流程

1. 阶段 1: 规划
2. 阶段 2: 执行
3. 阶段 3: 验收

## 角色职责

### Leader
- 制定计划
- 分配任务

### Worker
- 执行任务
- 汇报进度

## 消息规范

**任务分配**
```
@Worker [TASK] 任务名称
描述: ...
验收标准: ...
```

**状态汇报**
```
[STATUS] 已完成 XXX，遇到问题 YYY
```

## 行为约束

- 不越权
- 及时沟通
- 文档化决策</content>
  </parameters>
</tool>
```

---

## 5. 最佳实践

### 5.1 配置修改检查清单

在修改配置前，确认：
- [ ] 是否需要备份现有配置？
- [ ] 修改后的 JSON 格式是否正确？
- [ ] 新配置中的端口、群组 ID 是否有效？
- [ ] API Key 是否正确？
- [ ] 修改后是否需要重启 Agent？
- [ ] 是否会影响其他正在运行的协作会话？

### 5.2 模型配置建议

| 模型类型 | tool_call_mode | tool_filter | temperature | 适用场景 |
|---------|----------------|-------------|-------------|----------|
| GPT-4 / Claude | `auto` | `high` | 0.3 | 通用编程、复杂推理 |
| DeepSeek-V3 | `native` | `all` | 0.0 | 严格工具调用、精确执行 |
| GLM-4/5 | `auto` | `high` | 0.0 | 中文优先、稳定输出 |
| Qwen | `native` | `baseline` | 0.5 | 创意生成、对话 |

### 5.3 协作角色选择

| 角色 | 适用场景 | 必需工具 | 协作卡示例 |
|------|---------|---------|-----------|
| `pm` | 项目管理、任务分配 | `collaboration`, `im`, `delegate_task` | `software_dev_team` |
| `developer` | 代码编写 | `filesystem`, `vcs_remote`, `api_browser` | `software_dev_team` |
| `qa` | 测试验证 | `filesystem`, `api_browser`, `im` | `software_dev_team` |
| `reviewer` | 代码审查 | `vcs_remote`, `filesystem` | `code_review` |
| `worker` | 通用执行者 | 根据任务需求 | 任意 |

### 5.4 安全注意事项

1. **API Key 保护**：
   - 不要在群聊中明文发送 API Key
   - 使用环境变量或安全存储

2. **权限隔离**：
   - `filesystem.workspace_dirs` 限制文件访问范围
   - 不同 Agent 使用不同的工作目录

3. **密码管理**：
   - 群聊密码应使用强密码
   - 定期更换密码

4. **配置备份**：
   - 修改前备份 `config.json`
   - 使用版本控制管理配置文件

---

## 6. 故障排查

### 6.1 配置修改后不生效

**原因**：没有重启 Agent

**解决**：
```xml
<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>目标agent目录名</dir_name>
  </parameters>
</tool>
```

### 6.2 Agent 无法加入群聊

**检查**：
1. `group_chat.email` 和 `password` 是否已注册
2. `group_chat.groups` 中的群组 ID 是否正确
3. Gateway 是否正常运行

**解决**：
```xml
<!-- 1. 确认账号已注册 -->
<tool name="chat_account">
  <function>register_account</function>
  <parameters>
    <email>agent@ai</email>
    <password>123456</password>
    <name>Agent</name>
  </parameters>
</tool>

<!-- 2. 检查 config.json 配置 -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>
```

### 6.3 协作卡加载失败

**检查**：
1. 协作卡文件是否存在于 `collab_cards/` 目录
2. 协作卡格式是否正确（YAML front matter + Markdown）
3. `collaboration` 工具是否在 Agent 的 `tools` 列表中

**解决**：
```xml
<!-- 1. 列出所有可用协作卡 -->
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>

<!-- 2. 检查 Agent 是否启用协作 -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>
<!-- 确认 "collaboration" 在 tools 数组中，且 collaboration.enabled = true -->
```

### 6.4 修改自己的配置后无法重启

**问题**：Agent 修改了自己的 `config.json`，但无法自己重启自己

**解决方案**：
1. **请求用户手动重启**：
   ```
   我已经修改了配置，请在 Launcher 界面重启我，或者使用以下命令：
   重启 Agent: coder
   ```

2. **请求其他 Agent 帮忙重启**：
   ```xml
   <!-- 在群聊中 @另一个有 agent_factory 工具的 Agent -->
   @admin_agent 请帮我重启，我刚修改了配置

   <!-- admin_agent 执行 -->
   <tool name="agent_factory">
     <function>restart_agent</function>
     <parameters>
       <dir_name>coder</dir_name>
     </parameters>
   </tool>
   ```

---

## 7. 快速参考

### 7.1 常用命令速查

| 操作 | 推荐工具 | 备选方案 |
|------|---------|---------|
| 创建新 Agent | `agent_factory.create_agent` | - |
| 修改模型配置 | `agent_factory.configure_agent` | `filesystem.write_file` |
| 修改角色设定 | `agent_factory.set_agent_role` | `filesystem.write_file` |
| 修改协作角色 | `agent_factory.configure_agent` | `filesystem.write_file` |
| 添加工具 | `filesystem.write_file` (config.json) | - |
| 启动协作 | `collaboration.start_collaboration` | - |
| 加入协作 | `collaboration.join_collaboration` | - |
| 重启 Agent | `agent_factory.restart_agent` | 请求用户/其他 Agent |

### 7.2 配置模板

#### 最小化配置
```json
{
  "agent_id": "100xxx",
  "agent_name": "my-agent",
  "model": {
    "provider": "openai_compat",
    "api_key": "sk-xxx",
    "base_url": "https://api.example.com",
    "model_name": "model-name",
    "token_max": 128000,
    "temperature": 0.3
  },
  "tools": ["system", "filesystem"],
  "web_server": {"enabled": true, "port": 8010},
  "gateway": {"enabled": true}
}
```

#### 完整配置
见 [1.2 config.json 完整结构](#12-configjson-完整结构)

---

**文档版本**: v1.0
**更新日期**: 2026-03-01
**相关文档**:
- `docs/agent_factory_guide.md` - Agent Factory 详细使用指南
- `docs/system_wait_interruptible.md` - system.wait() 可中断功能
- `skills/agent_deployment/SKILL.md` - Agent 部署流程 Skill
- `skills/self_config/SKILL.md` - Agent 自我配置 Skill
