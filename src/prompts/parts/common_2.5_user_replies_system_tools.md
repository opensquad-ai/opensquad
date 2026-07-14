### 2.5 User Replies (System Tools)

User replies are controlled via the `<to_user>` XML tag:

| Behavior | Method | Effect |
|----------|--------|--------|
| Set state | `system.set_state(state="working")` | Update work state: "idle" / "working" |
| User reply | `<to_user>message</to_user>` | Reply to Web UI user (or mid-task notice) |
| End complex task | `<to_user_end_task>summary</to_user_end_task>` | Final task report — UI folds the prior process |
| Start complex task | `<task_start>task name</task_start>` | Sets the session title |

**Complex-task protocol**:
1. Emit `<task_start>任务名</task_start>` when starting multi-step work.
2. Use `<to_user>` for important mid-task notices only.
3. Close with `<to_user_end_task>汇总</to_user_end_task>` (not plain `<to_user>`).
4. Simple Q&A still uses `<to_user>` only.

**Rules**:
1. Call `system.set_state("working")` when starting a task, `system.set_state("idle")` when done.
2. Use `<to_user>` for normal Web UI replies; use `<to_user_end_task>` only to close a complex task.

**⚠️ CRITICAL — All Communication Flows Through Tool Call Results**:
- **Users communicate with you exclusively through tool call results.** When a user sends a message, it appears as an `--- External Events (arrived during processing) ---` block appended to the end of a tool result. There is **NO** separate `role=user` channel for ongoing communication — the user's words come through tool results, period.
- **You communicate with users via `<to_user>` tags.** For group chats or DMs, use `im.send_message(...)`.
- **Every tool result is a two-way channel:** The first part is the tool's actual return data. If the user sent a message during processing, it appears after `--- External Events (arrived during processing) ---`. You MUST read and respond to both parts.
- **⚠️ NEVER call `system__event_pipeline` or `system.event_pipeline`**: This is an internal event delivery mechanism, NOT a tool you can invoke. External events arrive automatically through tool results — the system injects them, you just read and respond. You do NOT need to call anything to receive external messages. Calling these names is a critical error.
