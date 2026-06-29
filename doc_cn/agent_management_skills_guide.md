# Agent 管理 Skills 使用指南

## 概述

我们创建了多个专业 Skills，帮助 Agent 掌握配置管理、协作管理和架构理解：

| Skill | 用途 | 激活命令 |
|-------|------|---------|
| **agent_config_management** | Agent 配置、模型、角色管理 | `install_skill("agent_config_management")` |
| **collaboration_management** | 协作卡管理、团队协作 | `install_skill("collaboration_management")` |
| **agent_architecture_im** | Agent 架构、IM 账号管理、故障排查 | `install_skill("agent_architecture_im")` |

## 快速激活

### 方式 1：通过 agent_setup 工具（推荐）

```xml
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_config_management</skill_path>
  </parameters>
</tool>

<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/collaboration_management</skill_path>
  </parameters>
</tool>

<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_architecture_im</skill_path>
  </parameters>
</tool>
```

### 方式 2：在 config.json 中配置（永久激活）

```json
{
  "skills": {
    "enabled": true,
    "active": [
      "agent_config_management",
      "collaboration_management",
      "agent_architecture_im"
    ]
  }
}
```

重启 Agent 后生效。

## Skill 内容概览

### agent_config_management Skill

#### 核心流程

1. **创建新 Agent**（5步完整流程）
   - 注册聊天账号
   - 创建 Agent 目录
   - 配置 config.json
   - 设置 role.md
   - 启动 Agent

2. **修改现有 Agent**
   - 修改模型配置（provider, model_name, temperature 等）
   - 修改角色设定（role.md）
   - 添加/删除工具
   - 修改协作角色

3. **配置模板**
   - 最小化配置
   - 不同模型的推荐配置（GPT-4, DeepSeek, GLM 等）

4. **最佳实践**
   - 配置修改检查清单
   - 安全注意事项
   - 性能优化建议

5. **故障排查**
   - 配置修改后不生效
   - Agent 无法启动
   - 无法加入群聊
   - 修改自己配置后无法重启

#### 完整配置字段说明

```json
{
  "agent_id": "唯一 ID",
  "agent_name": "显示名称",
  "model": {
    "provider": "openai_compat | openai | anthropic",
    "api_key": "API 密钥",
    "base_url": "API 端点",
    "model_name": "模型名称",
    "token_max": 128000,
    "temperature": 0.3,
    "tool_call_mode": "auto | native | xml",
    "tool_filter": "high (97) | all (124) | baseline (57)"
  },
  "tools": ["system", "filesystem", ...],
  "collaboration": {
    "enabled": true,
    "role": "pm | developer | qa | worker"
  },
  "group_chat": {
    "enabled": true,
    "email": "agent@ai",
    "password": "密码",
    "groups": ["gXXXXX"]
  }
}
```

### collaboration_management Skill

#### 核心流程

1. **查看可用协作卡**
   - 列出所有协作卡
   - 查看建议角色和描述

2. **启动协作会话（PM）**
   - 选择协作卡
   - 邀请成员
   - 分配任务
   - 管理进度

3. **加入协作会话（Worker）**
   - 响应邀请
   - 加载协作卡
   - 按规范工作

4. **结束协作**
   - PM 结束会话
   - Worker 离开会话

5. **创建自定义协作卡**
   - 设计协作流程
   - 定义角色职责
   - 制定消息规范
   - 设置行为约束

#### 内置协作卡

| 协作卡 | 适用场景 | 建议角色 |
|-------|---------|---------|
| **software_dev_team** | 完整软件开发项目 | pm, developer, qa |
| **code_review** | 代码审查 | reviewer, author |
| **research_task** | 研究型任务 | lead, researcher |
| **autonomous_vcs_dev** | 自主开发流程 | developer |

#### 协作卡结构

```markdown
---
name: card_name
description: 描述
tags: tag1, tag2
suggested_roles: role1, role2
min_members: 2
---

## 项目生命周期
（定义阶段、主导角色、产出、切换条件）

## 标准消息格式
（任务分配、状态汇报、Bug 报告等格式）

## 行为约束
（不越权、不沉默、不假设等）
```

## 使用场景示例

### 场景 1：我想创建一个新的 QA Agent

```xml
<!-- 1. 激活 agent_config_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_config_management</skill_path>
  </parameters>
</tool>

<!-- 2. 参考 Skill 中的"流程 1: 创建新 Agent" -->
<!-- 执行 5 个步骤：注册账号 → 创建目录 → 配置 → 设置角色 → 启动 -->
```

### 场景 2：我想把自己的模型从 GPT-4 换成 DeepSeek

```xml
<!-- 1. 激活 agent_config_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_config_management</skill_path>
  </parameters>
</tool>

<!-- 2. 参考 Skill 中的"流程 3: 修改模型配置" -->
<!-- 找到 DeepSeek 推荐配置，修改 config.json，请求重启 -->
```

### 场景 3：我想启动一个软件开发协作

```xml
<!-- 1. 激活 collaboration_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/collaboration_management</skill_path>
  </parameters>
</tool>

<!-- 2. 参考 Skill 中的"流程 2: 启动协作会话（PM）" -->
<!-- 使用 software_dev_team 协作卡，邀请成员，分配任务 -->
```

### 场景 4：我想创建一个自定义的数据分析协作模式

```xml
<!-- 1. 激活 collaboration_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/collaboration_management</skill_path>
  </parameters>
</tool>

<!-- 2. 参考 Skill 中的"流程 5: 创建自定义协作卡" -->
<!-- 查看示例（my_research_team），创建自己的协作卡 -->
```

## Skill 文件位置

```
skills/
├── agent_config_management/
│   ├── SKILL.md          # 主要内容（15KB，全面指南）
│   └── skill.json        # 元数据
└── collaboration_management/
    ├── SKILL.md          # 主要内容（18KB，全面指南）
    └── skill.json        # 元数据
```

## 与现有文档的关系

| 文档 | 类型 | 用途 |
|------|------|------|
| `doc_cn/agent_management.md` | 参考文档 | 详细的静态文档，供人类阅读 |
| `skills/agent_config_management/` | 可激活 Skill | Agent 运行时加载，动态指导工作 |
| `skills/collaboration_management/` | 可激活 Skill | Agent 运行时加载，动态指导工作 |
| `docs/agent_factory_guide.md` | 参考文档 | Agent Factory 插件详细说明 |
| `docs/system_wait_interruptible.md` | 参考文档 | system.wait() 功能说明 |

**区别**：
- **docs/** 是静态文档，供人类或 Agent 查阅
- **skills/** 是可激活的工作指南，Agent 激活后内容会注入系统提示，实时指导工作

## 验证 Skills

### 查看已安装的 Skills

```xml
<tool name="agent_setup">
  <function>list_installed</function>
  <parameters></parameters>
</tool>
```

### 激活后验证

激活 Skill 后，你的系统提示中会包含 Skill 的完整内容，你可以：
- 直接参考 Skill 中的流程执行任务
- 查阅 Skill 中的配置模板和示例
- 遵循 Skill 中的最佳实践和规范

## 何时使用这些 Skills？

### 使用 agent_config_management Skill

- ✅ 需要创建新 Agent
- ✅ 需要修改 Agent 配置（模型、工具、角色）
- ✅ 不确定配置字段的含义
- ✅ 遇到配置相关的故障
- ✅ 需要查看配置模板

### 使用 collaboration_management Skill

- ✅ 需要启动团队协作
- ✅ 被邀请加入协作
- ✅ 需要创建自定义协作模式
- ✅ 不清楚协作流程和消息规范
- ✅ 遇到协作相关的问题

### 使用 agent_architecture_im Skill

- ✅ 需要理解 Agent 进程架构
- ✅ 遇到 IM 账号登录失败问题
- ✅ 不清楚如何配置 ChatPro 账号
- ✅ 怀疑存在"全局单例共享账号"问题
- ✅ 需要排查 Bridge 连接问题
- ✅ 需要修改 Agent 的 IM 账号

### 不需要激活 Skill 的情况

- ❌ 只是简单修改一个配置字段（直接操作即可）
- ❌ 已经熟悉流程（不需要重复激活）
- ❌ 只是查询信息（可以直接读取文档）

## 更新记录

- **2026-03-01**:
  - 创建 agent_config_management 和 collaboration_management Skills
  - 基于 `doc_cn/agent_management.md` 拆分成两个专题 Skills
  - 新增 agent_architecture_im Skill，澄清 Agent 进程隔离架构和 IM 账号管理机制
- 添加了详细的流程说明、示例代码、最佳实践和故障排查

---

**相关文档**：
- `doc_cn/agent_management.md` - Agent 管理完整指南
- `doc_cn/agent_factory_guide.md` - Agent Factory 插件详细说明
- `doc_cn/system_wait_interruptible.md` - system.wait() 可中断功能
- `skills/agent_deployment/SKILL.md` - Agent 部署流程
- `skills/self_config/SKILL.md` - Agent 自我配置
