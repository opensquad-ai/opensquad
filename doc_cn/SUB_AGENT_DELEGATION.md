# Sub-Agent Delegation — 实现文档

## 1. 功能概述

**Sub-Agent Delegation（子智能体委托）** 允许 Agent 在执行任务时，动态创建一个与自身配置完全相同的临时子智能体，将子任务委托给它独立完成，完成后将结果返回给父 Agent，子智能体随即销毁。

### 核心特性

- **完全继承**：子智能体继承父智能体的完整系统提示词（base.md + role.md）和全部工具集
- **独立上下文**：对话历史从零开始，与父智能体完全隔离
- **同进程执行**：不 fork 进程，不改变 Gateway/WebSocket 架构，轻量零开销
- **两种执行模式**：同步阻塞（串行单任务）和异步并发（并行多任务）
- **递归安全**：最大委托深度 3 层，子智能体工具集中自动移除 `delegate_task` 防止无限递归

---

## 2. 架构

### 2.1 文件结构

```
opensquad/
├── sub_agent_runner.py          # 核心执行器
│   ├── SubAgentRunner           # 子智能体执行循环
│   ├── SubAgentJobManager       # 后台并发任务管理器（进程内单例）
│   └── _FilteredRegistry        # 过滤掉 delegate_task 的注册表包装
└── tools/
    └── delegate.py              # 工具定义与初始化注入
        ├── delegate_task()          # 同步阻塞委托
        ├── delegate_task_submit()   # 异步提交
        ├── delegate_task_result()   # 轮询结果
        ├── delegate_task_list()     # 列出活跃任务
        └── init_delegate_tool()     # 启动时注入接口
```

### 2.2 初始化链路

```
agents_boot.py
  │
  ├── build_system_prompt()          → system_prompt (base.md + role.md)
  │
  ├── register_tools()               → tool_registry（含 delegate_task 模块）
  │
  └── init_delegate_tool(            ← 工具注册后立即调用
        chat_api_cfg = {
          provider, api_key, base_url, model,
          token_max, temperature, timeout, ...
          "parent_prompt": system_prompt   ← 完整系统提示词
        },
        tool_registry                ← 父 Agent 工具注册表（共享只读）
      )
```

`init_delegate_tool()` 将配置注入 `delegate.py` 的模块级变量 `_chat_api_cfg` 和 `_tool_registry`，后续每次工具调用时直接使用。

### 2.3 执行链路（同步模式）

```
父 Agent LLM
  └─ <tool_call name="delegate_task.delegate_task">
       └─ delegate_task(task, context, depth=0)
            └─ _build_runner(depth)
                 ├─ 深度检查（actual_depth = depth + 1 <= MAX_DEPTH=3）
                 └─ SubAgentRunner(
                      chat_api_cfg = 父配置 + parent_prompt,
                      tool_registry = _FilteredRegistry(父registry, exclude={"delegate_task"}),
                      delegation_depth = 1
                    )
                      └─ run_task(full_task)
                           └─ asyncio.wait_for(_execute(), timeout=300s)
                                └─ for turn in range(1, 21):
                                     ├─ chat_api.chat(input)        # 全新 ChatAPI 实例，历史从零开始
                                     ├─ ResponseParser.parse_tool_call()
                                     ├─ [有工具调用] → sub_registry.call() → 下一轮 input
                                     └─ [无工具调用] → 提取 to_user 文本 → break → 返回结果
                                          └─ 结果注入父 Agent 下一轮 current_input
```

### 2.4 执行链路（异步并发模式）

```
父 Agent LLM
  │
  ├─ delegate_task_submit(task_A)  → {"job_id": "abc123", "status": "running"}
  ├─ delegate_task_submit(task_B)  → {"job_id": "def456", "status": "running"}
  │    └─ asyncio.create_task() 真正后台并发，父 Agent 继续执行
  │
  ├─ [父 Agent 等待一段时间或进入 sleep]
  │
  ├─ delegate_task_result("abc123") → {"status": "running", "result": null}
  ├─ delegate_task_result("abc123") → {"status": "done",    "result": "...结果A..."}
  └─ delegate_task_result("def456") → {"status": "done",    "result": "...结果B..."}
       └─ 汇总两个子任务结果，继续主任务
```

---

## 3. 子智能体的能力边界

### 继承自父智能体（相同）
| 项目 | 说明 |
|---|---|
| 系统提示词 | 完整继承（base.md + role.md），工具调用格式、状态控制等规则完全一致 |
| 工具集 | 继承全部工具，但自动移除 `delegate_task`（防止无限递归） |
| LLM 配置 | api_key、base_url、model、temperature 等完全相同 |
| provider | 与父智能体使用同一 provider（OpenAI / Claude / Gemini） |

### 不继承（独立隔离）
| 项目 | 说明 |
|---|---|
| 对话历史 | 全新 ChatAPI 实例，历史从零开始 |
| EventBus | 不连接，子智能体的输出只返回给父智能体，不发送给用户 |
| sleep/wake 状态 | 不受 sleep_controller 管理 |
| 长期记忆写入 | 不会向父智能体的长期记忆写入数据 |
| 委托工具 | `delegate_task` 系列工具被 `_FilteredRegistry` 自动过滤 |

---

## 4. 工具参考

### 4.1 `delegate_task` — 同步阻塞委托

阻塞等待子智能体完成后返回结果。适合单个子任务或顺序依赖的场景。

```xml
<tool_call name="delegate_task.delegate_task">
  <arguments>{
    "task": "分析 <path-to-your-repo>/src/opensquad/runner.py 的工具调用流程，用中文描述核心步骤",
    "context": "我们正在为团队编写技术文档",
    "depth": 0
  }</arguments>
</tool_call>
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | str | 是 | 子任务描述，越详细越好 |
| `context` | str | 否 | 补充背景上下文（父任务相关信息） |
| `depth` | int | 否 | 当前委托深度，系统自动管理，无需手动设置 |

返回：子智能体的最终文本结果。

---

### 4.2 `delegate_task_submit` — 异步提交

在后台启动子智能体，立即返回 `job_id`，不阻塞父智能体。适合并发执行多个独立子任务。

```xml
<tool_call name="delegate_task.delegate_task_submit">
  <arguments>{
    "task": "调研 asyncio 事件循环的内部机制，重点关注任务调度策略",
    "context": "用于撰写 Python 并发编程最佳实践文档"
  }</arguments>
</tool_call>
```

返回：`{"job_id": "b8470c26ec", "status": "running", "label": "任务摘要前60字"}`

---

### 4.3 `delegate_task_result` — 查询结果

查询异步子任务的状态与结果。任务未完成时返回 `running`，完成后返回结果文本。

```xml
<tool_call name="delegate_task.delegate_task_result">
  <arguments>{
    "job_id": "b8470c26ec",
    "cleanup_on_done": true
  }</arguments>
</tool_call>
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `job_id` | str | 是 | 由 `delegate_task_submit` 返回的任务 ID |
| `cleanup_on_done` | bool | 否 | 完成后自动释放内存，默认 `true` |

返回状态说明：

| status | 含义 |
|---|---|
| `running` | 子智能体仍在执行，`result` 为 `null` |
| `done` | 执行完成，`result` 为最终文本 |
| `error` | 执行出错，`result` 为错误信息 |
| `not_found` | job_id 不存在或已被清理 |

---

### 4.4 `delegate_task_list` — 列出活跃任务

列出所有后台子任务的状态，用于调试或追踪并发进度。

```xml
<tool_call name="delegate_task.delegate_task_list">
  <arguments>{}</arguments>
</tool_call>
```

返回：`[{"job_id": "b8470c26ec", "label": "...", "status": "running"}, ...]`

---

## 5. 配置与启用

在 `agents/{agent_name}/config.json` 的 `tools` 列表中加入 `"delegate_task"`：

```json
{
  "tools": [
    "filesystem",
    "system",
    "delegate_task"
  ]
}
```

前端工具列表（`AgentManagerPage.tsx` 的 `KNOWN_TOOLS` 数组）已内置 `delegate_task`，在管理面板勾选后会自动写入 `config.json`。

---

## 6. 安全限制

| 限制 | 值 | 说明 |
|---|---|---|
| 最大递归深度 | 3 层 | `MAX_DEPTH = 3`，超出则拒绝执行并返回错误 |
| 单任务最大 LLM 轮数 | 20 轮 | `MAX_TURNS = 20`，超出后返回已有文本（可能不完整） |
| 单任务超时 | 300 秒 | `TASK_TIMEOUT = 300`，超时后返回超时错误 |
| 自动防递归 | `_FilteredRegistry` | 子智能体工具集中自动过滤掉 `delegate_task.*` 命名空间 |

---

## 7. 并发模式使用示例

以下是父智能体并发执行三个独立调研子任务的完整交互流程：

```
# Step 1: 批量提交（3次 delegate_task_submit，每次立即返回）
submit(task="调研A") → job_id: "aaa"
submit(task="调研B") → job_id: "bbb"
submit(task="调研C") → job_id: "ccc"

# Step 2: 等待（可以 sleep 一段时间或做其他工作）

# Step 3: 轮询结果
result("aaa") → status: running
result("bbb") → status: done,    result: "调研B的结论..."
result("ccc") → status: done,    result: "调研C的结论..."
result("aaa") → status: done,    result: "调研A的结论..."

# Step 4: 父智能体汇总三份结果，继续主任务
```

由于父智能体每轮只能调用一个工具（`ResponseParser.parse_tool_call` 只解析第一个 tool_call），并发模式必须使用 submit/poll 组合，不能用单次 `delegate_task` 实现真正并发。

---

## 8. 实现细节

### `_FilteredRegistry`

对父 `ToolRegistry` 的薄包装，不修改父注册表，仅在调用时过滤指定 namespace：

```python
def _is_excluded(self, name: str) -> bool:
    ns = name.split('.', 1)[0] if '.' in name else name
    return ns in self._exclude  # exclude = {"delegate_task"}
```

工具全名（如 `delegate_task.delegate_task_submit`）和 namespace（如 `delegate_task`）均可被正确过滤。

### `SubAgentRunner._execute()`

- 调用 LLM 时使用 `loop.run_in_executor(None, chat_api.chat)` 在线程池中执行同步 `chat()`，避免阻塞事件循环
- 若 `self._chat_api` 已从外部注入则直接复用（便于测试 mock）
- 工具结果以 `[tool_result name="..."]...[/tool_result]` 格式注入下一轮 input，格式与父 Agent runner.py 保持一致

### `SubAgentJobManager`

进程内单例（`job_manager = SubAgentJobManager()`），使用 `asyncio.create_task()` 真正后台并发。任务状态：`pending → running → done / error`。

---

## 9. 修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `opensquad/sub_agent_runner.py` | 新建 | `SubAgentRunner`、`SubAgentJobManager`、`_FilteredRegistry` |
| `opensquad/tools/delegate.py` | 新建 | 4 个工具函数 + `init_delegate_tool()` 注入接口 |
| `opensquad/agents_boot.py` | 修改 | `TOOL_MODULES` 添加映射；boot 流程注入 `init_delegate_tool()`；`_delegate_cfg` 加入 `parent_prompt` |
| `opensquad/runner.py` | 修改 | `AgentRunner.__init__` 添加 `self.delegation_depth = 0` |
| `opensquad/gateway/nexuschat-pro/components/AgentManagerPage.tsx` | 修改 | `KNOWN_TOOLS` 加入 `'delegate_task'` |
| `agents/coder/config.json` | 修改 | `tools` 列表加入 `"delegate_task"` |
