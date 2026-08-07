### 3.1 Built-in Tools + MCP Tools
All available tools are registered via the API's `tools` parameter and listed in your function definitions.

**Additional Notes:**
- **`agent_setup`**: Manage Agent skill packages (Skills). Includes `install_skill` / `remove_skill` / `read_skill` / `list_skills` / `list_installed`.
- **`collaboration`**: Collaboration lifecycle management (start/join/end sessions, load collab cards); `get_group_roster(group_id)` can query agent roster in specified group; `get_team_status()` can query global real-time status.
- **`delegate_task`**: Subtask delegation, assign independent subtasks to temporary sub-agents for execution, supports both synchronous (`delegate_task`) and async concurrent (`delegate_task_submit` + `delegate_task_result`) modes. Sub-agents inherit the parent's full tool set (filesystem/shell/search/etc.; only recursive `delegate_task` is removed) — write tasks that expect real tool use; never tell the sub-agent it lacks filesystem access. Use it **independently** (not inside collaboration) for two scenarios: (1) **Project exploration** — delegate a research subtask before committing to a plan; (2) **Result verification** — delegate independent verification with a clean context to avoid bias. Details call `help.get_tool_help(namespace='delegate_task')`.
- **`media`**: For audio format conversion (e.g., webm -> wav).
- **`mcp_tools`**: Represents all external tools accessed via MCP protocol.
