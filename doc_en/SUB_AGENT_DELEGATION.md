# Sub-Agent Delegation — Implementation Notes

## 1. Overview

**Sub-Agent Delegation** lets an Agent, while executing a task, dynamically spin
up a temporary child Agent that inherits its full configuration, hand the
sub-task off to that child, get the result back, and then have the child
disappear.

### Core Features

- **Full inheritance** — the child inherits the parent's full system prompt
  (`base.md` + `role.md`) and complete tool set
- **Isolated context** — fresh conversation history; completely separated from
  the parent
- **In-process execution** — no process forking, no Gateway/WebSocket
  architecture changes, lightweight and zero overhead
- **Two execution modes** — synchronous blocking (single serial task) and
  asynchronous concurrent (parallel multi-task)
- **Recursion-safe** — max delegation depth of 3; the child's tool set
  automatically excludes `delegate_task` to prevent infinite recursion

---

## 2. Architecture

### 2.1 File Layout

```
opensquad/
├── sub_agent_runner.py          # core executor
│   ├── SubAgentRunner           # child Agent execution loop
│   ├── SubAgentJobManager       # in-process singleton background task manager
│   └── _FilteredRegistry        # registry wrapper that filters out delegate_task
└── tools/
    └── delegate.py              # tool definitions and boot-time injection
        ├── delegate_task()          # synchronous blocking delegation
        ├── delegate_task_submit()   # asynchronous submit
        ├── delegate_task_result()   # poll result
        ├── delegate_task_list()     # list active jobs
        └── init_delegate_tool()     # boot-time injection entry point
```

### 2.2 Initialization Chain

```
agents_boot.py
  │
  ├── build_system_prompt()          → system_prompt (base.md + role.md)
  │
  ├── register_tools()               → tool_registry (includes delegate_task module)
  │
  └── init_delegate_tool(            ← called right after tool registration
        chat_api_cfg = {
          provider, api_key, base_url, model,
          token_max, temperature, timeout, ...
          "parent_prompt": system_prompt   ← full system prompt
        },
        tool_registry                ← parent's tool registry (shared read-only)
      )
```

`init_delegate_tool()` writes the configuration into `delegate.py`'s module-level
variables `_chat_api_cfg` and `_tool_registry`; subsequent tool calls use them
directly.

### 2.3 Execution Chain (Synchronous)

```
Parent Agent LLM
  └─ <tool_call name="delegate_task.delegate_task">
       └─ delegate_task(task, context, depth=0)
            └─ _build_runner(depth)
                 ├─ depth check (actual_depth = depth + 1 <= MAX_DEPTH=3)
                 └─ SubAgentRunner(
                      chat_api_cfg = parent_cfg + parent_prompt,
                      tool_registry = _FilteredRegistry(parent registry, exclude={"delegate_task"}),
                      delegation_depth = 1
                    )
                      └─ run_task(full_task)
                           └─ asyncio.wait_for(_execute(), timeout=300s)
                                └─ for turn in range(1, 21):
                                     ├─ chat_api.chat(input)        # fresh ChatAPI instance, empty history
                                     ├─ ResponseParser.parse_tool_call()
                                     ├─ [tool call]  → sub_registry.call() → next input
                                     └─ [no tool call] → extract to_user text → break → return
                                          └─ result injected into parent's next current_input
```

### 2.4 Execution Chain (Asynchronous Concurrent)

```
Parent Agent LLM
  │
  ├─ delegate_task_submit(task_A)  → {"job_id": "abc123", "status": "running"}
  ├─ delegate_task_submit(task_B)  → {"job_id": "def456", "status": "running"}
  │    └─ asyncio.create_task() runs in the background; parent continues
  │
  ├─ [parent waits a while, or sleeps]
  │
  ├─ delegate_task_result("abc123") → {"status": "running", "result": null}
  ├─ delegate_task_result("abc123") → {"status": "done",    "result": "...result A..."}
  └─ delegate_task_result("def456") → {"status": "done",    "result": "...result B..."}
       └─ aggregate the two sub-results and continue the main task
```

---

## 3. Capability Boundaries of the Child Agent

### Inherited from parent (identical)

| Item | Description |
|---|---|
| System prompt | Fully inherited (`base.md` + `role.md`); tool-call format, state-control rules, etc. all identical |
| Tool set | Inherits every tool, but `delegate_task` is automatically removed (to prevent infinite recursion) |
| LLM config | `api_key`, `base_url`, `model`, `temperature`, etc. are all identical |
| provider | Uses the same provider as the parent (OpenAI / Claude / Gemini) |

### Not inherited (isolated)

| Item | Description |
|---|---|
| Conversation history | Fresh `ChatAPI` instance; history starts from zero |
| EventBus | Not connected; the child's output only returns to the parent, not the user |
| sleep/wake state | Not managed by `sleep_controller` |
| Long-term memory writes | Does not write to the parent's long-term memory |
| Delegation tools | The `delegate_task.*` tool namespace is filtered out by `_FilteredRegistry` |

---

## 4. Tool Reference

### 4.1 `delegate_task` — Synchronous Blocking Delegation

Blocks until the child Agent completes, then returns the result. Suitable for
single sub-tasks or scenarios with sequential dependencies.

```xml
<tool_call name="delegate_task.delegate_task">
  <arguments>{
    "task": "Analyze the tool-call flow in <path-to-your-repo>/src/opensquad/runner.py and describe the core steps in Chinese",
    "context": "We are writing technical documentation for the team",
    "depth": 0
  }</arguments>
</tool_call>
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task` | str | yes | Sub-task description; the more detailed the better |
| `context` | str | no | Supplementary background context (information about the parent task) |
| `depth` | int | no | Current delegation depth; managed by the system, no need to set manually |

Returns: the child Agent's final text result.

---

### 4.2 `delegate_task_submit` — Asynchronous Submit

Starts a child Agent in the background, returns `job_id` immediately, does not
block the parent. Suitable for executing multiple independent sub-tasks
concurrently.

```xml
<tool_call name="delegate_task.delegate_task_submit">
  <arguments>{
    "task": "Research the internals of the asyncio event loop, focusing on the task-scheduling strategy",
    "context": "Source material for a Python concurrency best-practices document"
  }</arguments>
</tool_call>
```

Returns: `{"job_id": "b8470c26ec", "status": "running", "label": "first 60 chars of the task"}`

---

### 4.3 `delegate_task_result` — Poll for Result

Query the status and result of an asynchronous sub-task. Returns `running` while
the task is in progress, and the final result text once it completes.

```xml
<tool_call name="delegate_task.delegate_task_result">
  <arguments>{
    "job_id": "b8470c26ec",
    "cleanup_on_done": true
  }</arguments>
</tool_call>
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `job_id` | str | yes | Job ID returned by `delegate_task_submit` |
| `cleanup_on_done` | bool | no | Auto-release memory on completion; default `true` |

Return status values:

| status | Meaning |
|---|---|
| `running` | Child Agent is still running; `result` is `null` |
| `done` | Execution complete; `result` is the final text |
| `error` | Execution failed; `result` is the error message |
| `not_found` | `job_id` does not exist or has already been cleaned up |

---

### 4.4 `delegate_task_list` — List Active Jobs

List all background sub-tasks and their statuses; useful for debugging or
tracking concurrent progress.

```xml
<tool_call name="delegate_task.delegate_task_list">
  <arguments>{}</arguments>
</tool_call>
```

Returns: `[{"job_id": "b8470c26ec", "label": "...", "status": "running"}, ...]`

---

## 5. Configuration and Activation

Add `"delegate_task"` to the `tools` list in
`agents/{agent_name}/config.json`:

```json
{
  "tools": [
    "filesystem",
    "system",
    "delegate_task"
  ]
}
```

The front-end tool list (`KNOWN_TOOLS` array in `AgentManagerPage.tsx`) already
includes `delegate_task`; checking it in the admin panel will automatically
write it into `config.json`.

---

## 6. Safety Limits

| Limit | Value | Description |
|---|---|---|
| Max recursion depth | 3 levels | `MAX_DEPTH = 3`; exceeding this rejects execution and returns an error |
| Max LLM turns per task | 20 turns | `MAX_TURNS = 20`; beyond this, returns the text already produced (may be incomplete) |
| Per-task timeout | 300 seconds | `TASK_TIMEOUT = 300`; timeout returns a timeout error |
| Automatic recursion guard | `_FilteredRegistry` | The child's tool set automatically filters out the `delegate_task.*` namespace |

---

## 7. Example — Concurrent Mode

Below is the full interaction flow of a parent Agent running three independent
research sub-tasks concurrently:

```
# Step 1: batch submit (3 delegate_task_submit calls, each returns immediately)
submit(task="Research A") → job_id: "aaa"
submit(task="Research B") → job_id: "bbb"
submit(task="Research C") → job_id: "ccc"

# Step 2: wait (sleep a while, or do other work)

# Step 3: poll results
result("aaa") → status: running
result("bbb") → status: done,    result: "Conclusions of B..."
result("ccc") → status: done,    result: "Conclusions of C..."
result("aaa") → status: done,    result: "Conclusions of A..."

# Step 4: parent aggregates the three results and continues the main task
```

Because the parent Agent can only invoke one tool per turn
(`ResponseParser.parse_tool_call` only parses the first `tool_call`), real
concurrency requires the submit/poll combination — a single `delegate_task`
cannot achieve true parallelism.

---

## 8. Implementation Details

### `_FilteredRegistry`

A thin wrapper around the parent's `ToolRegistry`. The parent registry is not
modified; the wrapper just filters the excluded namespace at call time:

```python
def _is_excluded(self, name: str) -> bool:
    ns = name.split('.', 1)[0] if '.' in name else name
    return ns in self._exclude  # exclude = {"delegate_task"}
```

Both fully-qualified tool names (e.g. `delegate_task.delegate_task_submit`) and
namespaces (e.g. `delegate_task`) are filtered correctly.

### `SubAgentRunner._execute()`

- When calling the LLM, uses `loop.run_in_executor(None, chat_api.chat)` to
  run the synchronous `chat()` in a thread pool, avoiding event-loop blocking.
- If `self._chat_api` was injected from outside, it is reused directly
  (handy for mocking in tests).
- Tool results are injected into the next turn's `input` using the format
  `[tool_result name="..."]...[/tool_result]`, matching the format used by
  `runner.py` for the parent Agent.

### `SubAgentJobManager`

In-process singleton (`job_manager = SubAgentJobManager()`), using
`asyncio.create_task()` for true background concurrency. Task state:
`pending → running → done / error`.

---

## 9. Files Changed

| File | Type | Description |
|---|---|---|
| `opensquad/sub_agent_runner.py` | new | `SubAgentRunner`, `SubAgentJobManager`, `_FilteredRegistry` |
| `opensquad/tools/delegate.py` | new | 4 tool functions + `init_delegate_tool()` injection entry point |
| `opensquad/agents_boot.py` | modified | Added `TOOL_MODULES` mapping; boot flow calls `init_delegate_tool()`; `_delegate_cfg` includes `parent_prompt` |
| `opensquad/runner.py` | modified | `AgentRunner.__init__` adds `self.delegation_depth = 0` |
| `opensquad/gateway/nexuschat-pro/components/AgentManagerPage.tsx` | modified | `KNOWN_TOOLS` includes `'delegate_task'` |
| `agents/coder/config.json` | modified | `tools` list includes `"delegate_task"` |
