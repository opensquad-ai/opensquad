# LLM 上下文注入架构 — 消息输入路径

本文档描述各种外部数据（群聊消息、长期记忆、插件通知、Gateway 推送等）如何进入 LLM 每轮请求的上下文。

---

## 整体架构

每次 LLM 请求由**三个独立通道**组装：

```
┌─────────────────────────────────────────────────────┐
│                LLM 请求（每轮）                       │
│                                                     │
│  [通道 1] system message                            │
│    └─ 系统提示词（稳定层，低频变更，                   │
│       可利用前缀缓存）                                │
│                                                     │
│  [通道 2] user message                              │
│    ├─ [System Context - 每轮更新]                    │
│    │    ├─ ### 运行时状态                             │
│    │    ├─ ### 任务计划                               │
│    │    ├─ ### MCP 服务状态                           │
│    │    ├─ ### 长期记忆（本轮召回）                    │
│    │    └─ ### 自定义扩展块（插件/角色钩子）           │
│    │  [/System Context]                             │
│    │                                               │
│    ├─ <实际用户输入>                                 │
│    │                                               │
│    └─ （可选）[群聊消息 - 仅供参考]\n...              │
│                                                     │
│  [通道 3] tool messages（历史中的工具结果）            │
└─────────────────────────────────────────────────────┘
```

核心实现入口：`runner.py:_setup_prompt()`（每次 LLM 调用前执行）。

---

## 路径 1：稳定系统提示词层（system message）

**特点**：低频变更，内容稳定，利用 LLM 前缀缓存降低 token 开销。

### 构建流程（`runner.py:_setup_prompt()`）

```
role.md 模板（占位符格式）
    ↓
tool_call_strategy.prepare_llm_call()
    ├─ XML 模式：将工具描述注入模板，替换 {{TOOLS}} 占位符
    └─ Native FC 模式：生成 OpenAI Tools schema（不修改系统提示词）
    ↓
build_skills_prompt()          → 替换 {{SKILLS_INSTRUCTIONS}}
    ↓
inject_standard() → system_vars
    ├─ AGENT_PROFILE            → 替换 {{AGENT_PROFILE}}（agent.md 长期记忆）
    ├─ CONTEXT_SUMMARY          → 替换 {{CONTEXT_SUMMARY}}（上下文压缩摘要）
    ├─ AGENT_WORKSPACE          → 替换 {{AGENT_WORKSPACE}}（工作区目录路径）
    └─ TEAM_COLLAB_CARDS        → 替换 {{TEAM_COLLAB_CARDS}}（协作卡片目录）
    ↓
角色 context.py before_input() → dict（当存在匹配占位符时）
    └─ final.replace("{{KEY}}", value)   → 注入系统提示词
    ↓
插件钩子：on_before_prompt
    └─ 允许插件修改 final（完整替换）
    ↓
变更检测：final != chat_api.get_system_prompt()
    └─ is_changed=True → chat_api.update_system_prompt(final)
```

**相关文件**：
- `runner.py:1425` `_setup_prompt()`
- `context_base.py:101` `inject_standard()`
- `tool_call_strategy.py` `prepare_llm_call()`
- `skill_loader.py` `build_skills_prompt()`

---

## 路径 2：动态上下文前缀（user message 前导块）

**特点**：每轮更新，包含高频变化内容，用 `[System Context]...[/System Context]` 包裹，拼接在用户消息前面。

### 构建流程（`runner.py:_setup_prompt()` 续）

所有数据源合并到 `dynamic_parts` 字典，由 `_build_context_prefix()` 组装。

```
dynamic_parts = {}
    ↓
TASK_STATE          ← task_manager.render()                 （当前任务计划状态）
MCP_CURRENT_STATE   ← mcp_adapter.list_servers()            （MCP 工具服务器状态）
    ↓
inject_standard() → dynamic_vars
    ├─ RUNTIME_STATE  ← 时间 + 来源 + Agent 工作状态 + 唤醒级别（每轮变化）
    └─ MEMORY_CONTEXT ← memory_manager.auto_recall(query)    （按查询召回的长期记忆）
    ↓
角色 context.py before_input() → dict（当无匹配占位符时）
    └─ dynamic_parts[KEY] = value                             （自定义动态块）
    ↓
_build_context_prefix(dynamic_parts)
    └─ 按固定顺序组装：RUNTIME_STATE → TASK_STATE → MCP_CURRENT_STATE
                       → MEMORY_CONTEXT → 自定义键
```

### 最终格式

```
[System Context - Updated Every Turn]

### Runtime State

2026-03-04 12:00:00 | Source: web | State: working | Wake level: 2

---

### Task Plan

（当前任务列表...）

---

### MCP Service Status

...

---

### Long-term Memory (recalled this turn)

...

[/System Context]

<实际用户输入>
```

在主循环中拼接：
```python
# runner.py:581
if self._dynamic_context_prefix:
    current_input = self._dynamic_context_prefix + current_input
```

**相关文件**：
- `runner.py:33` `_build_context_prefix()`
- `runner.py:1429` `_setup_prompt()` 动态层部分
- `context_base.py:101` `inject_standard()` dynamic_vars 部分
- `memory_manager.py` `auto_recall()`

---

## 路径 3：群聊消息 / 私聊消息内联追加（user message 尾部）

**特点**：不经过 `[System Context]` 块，直接以字符串形式追加到用户消息末尾，明确标记"不要自动回复"。

### 数据来源

```
外部 IM 系统（ChatPro 群聊 / 私聊）
    ↓
bridge.py           ← 通过 WebSocket 接收 IM 平台推送
    ↓
message_router.py   ← 根据 Agent 状态决定路由策略
    ├─ idle：推入 input_hub，触发新一轮对话
    └─ working / sleeping：推入 message_queue（异步积累）
    ↓
message_queue       ← 全局队列（QueueMessage: id, type, source, content, ...）
```

### 两个消费时机

**时机 A：轮次开始时**
```python
# input_hub.py:106
pending_messages = message_queue.get_all()
user_input["has_messages"] = True
user_input["message_context"] = self._format_messages(pending_messages)

# runner.py:402
initial_query += f"\n\n[同时收到的群聊消息 - 仅供参考，不要自动调用 im.send_message 回复]\n{msg_ctx}"
```

**时机 B：轮次中间（多次工具调用之间）**
```python
# runner.py:726-728
pending = message_queue.get_all()
msg_context = "\n".join([f"[{msg.source_name}] {msg.sender_name}: {msg.content}" for msg in pending])
current_input += f"\n\n[群聊消息 - 仅供参考，不要自动调用 im.send_message 回复]\n{msg_context}"
```

### 格式示例

```
<原始用户输入>

[同时收到的群聊消息 - 仅供参考，不要自动调用 im.send_message 回复]
[群聊 开发组 (ID: g001)] Alice: 服务器挂了
[私聊] Bob: 你好
```

**相关文件**：
- `message_queue.py`（完整文件）
- `message_router.py` `route_group_message()`
- `input_hub.py:106` `get_user_response()`
- `runner.py:399` 轮次开始时群聊消息追加
- `runner.py:710` 轮次中间群聊消息追加
- `bridge.py` IM 平台 WebSocket 集成

---

## 路径 4：Gateway 推送 → InputHub → 新轮次

**特点**：来自 Web 前端或 API 客户端的主动输入，成为触发新一轮 LLM 调用的 `initial_query`。

```
Web 前端 / API 客户端
    ↓
gateway WebSocket
    ↓
gateway_adapter.py:on_receive()
    ├─ __STOP_TASK__           → input_hub.request_stop()      （中断当前任务）
    ├─ __NEW_SESSION__ 等      → input_hub.push_urgent()       （紧急队列）
    └─ 普通消息                 → input_hub.push(source="gateway")
    ↓
input_hub (asyncio.Queue / urgent_queue)
    ↓
runner.py 主循环 await input_hub.get_user_response()
    ↓
initial_query（进入本轮处理流程）
```

需要主动向 Agent 推送内容的插件也可以调用 `input_hub.push()` 进入此路径。

**相关文件**：
- `gateway_adapter.py:120` `on_receive()`
- `input_hub.py:202` `push()` / `push_urgent()`
- `runner.py:348` 主循环 `get_user_response()`

---

## 路径 5：插件 before_input 钩子（动态扩展）

插件或角色的 `context.py` 可以通过 `before_input()` 在每次 LLM 调用前注入自定义内容：

```python
# 角色 context.py
def before_input(context: dict) -> dict:
    return {
        "MY_STATUS": "当前自定义状态...",       # 无占位符 → 进入 [System Context] 动态块
        "ROLE_INTRO": "我是...",                # 有占位符 {{ROLE_INTRO}} → 注入系统提示词
    }
```

**路由规则（`runner.py:1509`）**：

| 条件 | 注入位置 |
|------|----------|
| 模板中存在 `{{KEY}}` 占位符 | 系统提示词稳定层（替换占位符） |
| 模板中不存在 `{{KEY}}` 占位符 | `dynamic_parts[KEY]` → `[System Context]` 动态块的新段落 |

---

## 数据源汇总

| 数据源 | 注入路径 | 最终位置 |
|--------|----------|----------|
| role.md 模板 | 直接作为基础 | system message |
| 工具描述（XML 模式） | tool_call_strategy | system message |
| 技能包（skills） | build_skills_prompt | system message |
| 长期记忆（agent.md） | inject_standard → AGENT_PROFILE | system message |
| 上下文压缩摘要 | inject_standard → CONTEXT_SUMMARY | system message |
| 协作卡片目录 | inject_standard → TEAM_COLLAB_CARDS | system message |
| 运行时状态（时间/状态/唤醒） | inject_standard → RUNTIME_STATE | `[System Context]` 动态块 |
| 长期记忆（自动召回） | inject_standard → MEMORY_CONTEXT | `[System Context]` 动态块 |
| 任务计划 | dynamic_parts → TASK_STATE | `[System Context]` 动态块 |
| MCP 服务状态 | dynamic_parts → MCP_CURRENT_STATE | `[System Context]` 动态块 |
| 角色/插件 before_input（无占位符） | dynamic_parts[KEY] | `[System Context]` 动态块 |
| 角色/插件 before_input（有占位符） | final.replace(占位符) | system message |
| 插件 on_before_prompt 钩子 | 修改 final | system message |
| 群聊/私聊消息 | message_queue → 内联拼接 | user message 尾部（纯文本） |
| Web/API 主动输入 | input_hub.push() → initial_query | 触发新轮次，成为 user message |
| 紧急命令（停止/切换会话） | input_hub.push_urgent() | 优先队列，不进入 LLM |

---

## 设计说明

### 为什么分两层（稳定 vs 动态）

Claude / GPT 系列模型对**系统提示词的前缀**提供缓存（Anthropic 称为 extended thinking cache，OpenAI 称为 prompt caching）。如果系统提示词每轮都变，缓存会失效，增加成本和延迟。

因此设计原则是：
- **高频变化内容**（时间、任务状态、记忆召回）→ 移出系统提示词，放入 user message 动态前缀
- **低频稳定内容**（角色定义、工具列表、工作区路径）→ 保留在系统提示词中，最大化缓存命中

### 为什么群聊消息不使用 `[System Context]` 块

群聊消息是异步接收的，内容不可预测，时机不规律——与"每轮必须更新的结构化上下文"本质不同。作为内联追加处理逻辑更简单，且"不要自动回复"标记可以防止 Agent 在多轮工具调用过程中误回复 IM 消息。
