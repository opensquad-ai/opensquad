# LLM Context Injection Architecture — Message Input Paths

This document describes how various external data (group messages, long-term memory, plugin notifications, gateway pushes, etc.) enters the LLM's per-turn request context.

---

## Overall Architecture

Each LLM request is assembled from **three independent channels**:

```
┌─────────────────────────────────────────────────────┐
│                LLM Request (per turn)               │
│                                                     │
│  [channel 1] system message                        │
│    └─ System prompt (stable layer, low-frequency   │
│         changes, benefits from prefix cache)        │
│                                                     │
│  [channel 2] user message                          │
│    ├─ [System Context - updated every turn]        │
│    │    ├─ ### Runtime State                       │
│    │    ├─ ### Task Plan                           │
│    │    ├─ ### MCP Service Status                  │
│    │    ├─ ### Long-term Memory (recalled this turn)│
│    │    └─ ### Custom Extension Blocks (plugin/role hooks)│
│    │  [/System Context]                            │
│    │                                               │
│    ├─ <Actual user input>                          │
│    │                                               │
│    └─ (Optional) [Group Messages - For Reference]\n...│
│                                                     │
│  [channel 3] tool messages (tool results in history)│
└─────────────────────────────────────────────────────┘
```

Core implementation entry point: `runner.py:_setup_prompt()` (executed before each LLM call).

---

## Path 1: Stable System Prompt Layer (system message)

**Characteristics**: Low-frequency changes, stable content, leverages LLM prefix cache to reduce token costs.

### Build Flow (`runner.py:_setup_prompt()`)

```
role.md template (placeholder format)
    ↓
tool_call_strategy.prepare_llm_call()
    ├─ XML mode: inject tool descriptions into template, replace {{TOOLS}} placeholder
    └─ Native FC mode: generate OpenAI Tools schema (does not modify system prompt)
    ↓
build_skills_prompt()          → replace {{SKILLS_INSTRUCTIONS}}
    ↓
inject_standard() → system_vars
    ├─ AGENT_PROFILE            → replace {{AGENT_PROFILE}} (agent.md permanent memory)
    ├─ CONTEXT_SUMMARY          → replace {{CONTEXT_SUMMARY}} (context compression summary)
    ├─ AGENT_WORKSPACE          → replace {{AGENT_WORKSPACE}} (working directory path)
    └─ TEAM_COLLAB_CARDS        → replace {{TEAM_COLLAB_CARDS}} (collaboration card directory)
    ↓
Role context.py before_input() → dict (when matching placeholder exists)
    └─ final.replace("{{KEY}}", value)   → inject into system prompt
    ↓
plugin hook: on_before_prompt
    └─ allows plugin to modify final (full replacement)
    ↓
Change detection: final != chat_api.get_system_prompt()
    └─ is_changed=True → chat_api.update_system_prompt(final)
```

**Related files**:
- `runner.py:1425` `_setup_prompt()`
- `context_base.py:101` `inject_standard()`
- `tool_call_strategy.py` `prepare_llm_call()`
- `skill_loader.py` `build_skills_prompt()`

---

## Path 2: Dynamic Context Prefix (user message leading block)

**Characteristics**: Updated every turn, contains high-frequency changing content, wrapped with `[System Context]...[/System Context]`, prepended to the user message.

### Build Flow (`runner.py:_setup_prompt()` continued)

All data sources are merged into the `dynamic_parts` dict, then assembled by `_build_context_prefix()`.

```
dynamic_parts = {}
    ↓
TASK_STATE          ← task_manager.render()                 (current task plan state)
MCP_CURRENT_STATE   ← mcp_adapter.list_servers()            (MCP tool server status)
    ↓
inject_standard() → dynamic_vars
    ├─ RUNTIME_STATE  ← time + source + agent work state + wake level   (changes every turn)
    └─ MEMORY_CONTEXT ← memory_manager.auto_recall(query)               (long-term memory recalled by query)
    ↓
Role context.py before_input() → dict (when no matching placeholder)
    └─ dynamic_parts[KEY] = value                                  (custom dynamic block)
    ↓
_build_context_prefix(dynamic_parts)
    └─ assembled in fixed order: RUNTIME_STATE → TASK_STATE → MCP_CURRENT_STATE
                       → MEMORY_CONTEXT → custom keys
```

### Final Format

```
[System Context - Updated Every Turn]

### Runtime State

2026-03-04 12:00:00 | Source: web | State: working | Wake level: 2

---

### Task Plan

(Current task list...)

---

### MCP Service Status

...

---

### Long-term Memory (recalled this turn)

...

[/System Context]

<Actual user input>
```

Then prepended in the main loop:
```python
# runner.py:581
if self._dynamic_context_prefix:
    current_input = self._dynamic_context_prefix + current_input
```

**Related files**:
- `runner.py:33` `_build_context_prefix()`
- `runner.py:1429` `_setup_prompt()` dynamic layer section
- `context_base.py:101` `inject_standard()` dynamic_vars section
- `memory_manager.py` `auto_recall()`

---

## Path 3: Group Messages / DMs Inline Append (user message tail)

**Characteristics**: Does not go through the `[System Context]` block; appended directly as a string to the end of the user message, explicitly marked "do not auto-reply".

### Data Sources

```
External IM system (ChatPro group chat / DM)
    ↓
bridge.py           ← receives IM platform pushes via WebSocket
    ↓
message_router.py   ← decides routing strategy based on agent state
    ├─ idle: push to input_hub, trigger a new conversation turn
    └─ working / sleeping: push to message_queue (async accumulation)
    ↓
message_queue       ← global queue (QueueMessage: id, type, source, content, ...)
```

### Two Consumption Timing Points

**Timing A: At turn start**
```python
# input_hub.py:106
pending_messages = message_queue.get_all()
user_input["has_messages"] = True
user_input["message_context"] = self._format_messages(pending_messages)

# runner.py:402
initial_query += f"\n\n[Group messages received simultaneously - For reference only, do NOT auto-call im.send_message to reply]\n{msg_ctx}"
```

**Timing B: Mid-turn (between multiple tool calls)**
```python
# runner.py:726-728
pending = message_queue.get_all()
msg_context = "\n".join([f"[{msg.source_name}] {msg.sender_name}: {msg.content}" for msg in pending])
current_input += f"\n\n[Group messages - For reference only, do NOT auto-call im.send_message to reply]\n{msg_context}"
```

### Formatting Example

```
<Original user input>

[Group messages received simultaneously - For reference only, do NOT auto-call im.send_message to reply]
[Group chat Dev Group (ID: g001)] Alice: Server is down
[DM] Bob: Hello
```

**Related files**:
- `message_queue.py` (full file)
- `message_router.py` `route_group_message()`
- `input_hub.py:106` `get_user_response()`
- `runner.py:399` group message append at turn start
- `runner.py:710` group message append mid-turn
- `bridge.py` IM platform WebSocket integration

---

## Path 4: Gateway Push → InputHub → New Conversation Turn

**Characteristics**: Active input from Web frontend or API clients, becomes the `initial_query` that triggers a new LLM call.

```
Web frontend / API client
    ↓
gateway WebSocket
    ↓
gateway_adapter.py:on_receive()
    ├─ __STOP_TASK__           → input_hub.request_stop()      (interrupt current task)
    ├─ __NEW_SESSION__ etc.    → input_hub.push_urgent()       (urgent queue)
    └─ regular message         → input_hub.push(source="gateway")
    ↓
input_hub (asyncio.Queue / urgent_queue)
    ↓
runner.py main loop await input_hub.get_user_response()
    ↓
initial_query (enters this turn's processing flow)
```

Plugins that need to proactively push content to the agent can also call `input_hub.push()` to enter this path.

**Related files**:
- `gateway_adapter.py:120` `on_receive()`
- `input_hub.py:202` `push()` / `push_urgent()`
- `runner.py:348` main loop `get_user_response()`

---

## Path 5: Plugin before_input Hook (Dynamic Extension)

A plugin or role's `context.py` can inject custom content before each LLM call via `before_input()`:

```python
# Role context.py
def before_input(context: dict) -> dict:
    return {
        "MY_STATUS": "Current custom state...",   # No placeholder → enters [System Context] dynamic block
        "ROLE_INTRO": "I am...",                   # Has placeholder {{ROLE_INTRO}} → injected into system prompt
    }
```

**Routing rules (`runner.py:1509`)**:

| Condition | Injection Location |
|---|---|
| Template has `{{KEY}}` placeholder | System prompt stable layer (replaces placeholder) |
| Template has no `{{KEY}}` placeholder | `dynamic_parts[KEY]` → new section in `[System Context]` dynamic block |

---

## Data Source Summary

| Data Source | Injection Path | Final Location |
|---|---|---|
| role.md template | Used directly as base | system message |
| Tool descriptions (XML mode) | tool_call_strategy | system message |
| Skill packages (skills) | build_skills_prompt | system message |
| Permanent memory (agent.md) | inject_standard → AGENT_PROFILE | system message |
| Context compression summary | inject_standard → CONTEXT_SUMMARY | system message |
| Collaboration card directory | inject_standard → TEAM_COLLAB_CARDS | system message |
| Runtime state (time/state/wake) | inject_standard → RUNTIME_STATE | `[System Context]` dynamic block |
| Long-term memory (auto-recalled) | inject_standard → MEMORY_CONTEXT | `[System Context]` dynamic block |
| Task plan | dynamic_parts → TASK_STATE | `[System Context]` dynamic block |
| MCP service status | dynamic_parts → MCP_CURRENT_STATE | `[System Context]` dynamic block |
| Role/plugin before_input (no placeholder) | dynamic_parts[KEY] | `[System Context]` dynamic block |
| Role/plugin before_input (has placeholder) | final.replace(placeholder) | system message |
| Plugin on_before_prompt hook | modifies final | system message |
| Group chat / DM messages | message_queue → inline concat | user message tail (plain text) |
| Web/API active input | input_hub.push() → initial_query | triggers new turn, becomes user message |
| Urgent commands (stop/switch session) | input_hub.push_urgent() | priority queue, does not enter LLM |

---

## Design Notes

### Why Two Layers (Stable vs. Dynamic)

Claude / GPT-series models provide prompt caching for the **prefix of the system prompt** (Anthropic calls it extended thinking cache, OpenAI calls it prompt caching). If the system prompt changes every turn, the cache is invalidated, increasing both cost and latency.

Therefore, the design principle is:
- **High-frequency changing content** (time, task state, memory recall) → moved out of system prompt, placed in user message dynamic prefix
- **Low-frequency stable content** (role definition, tool list, workspace path) → kept in system prompt to maximize cache hits

### Why Group Messages Don't Use the `[System Context]` Block

Group messages are asynchronously received, their content is unpredictable, and their timing is irregular — fundamentally different from "structured context that must be updated every turn." Handling them as inline appends is simpler in logic, and the explicit "do not auto-reply" marker prevents the agent from accidentally replying to IM mid-way through multi-turn tool calls.
