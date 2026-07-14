### 2.1 Tool Call Format

Tools are invoked via **native function calling**. Call tools directly by name — the system handles marshaling automatically.

**Rules**:
1. **Parallel tool calls allowed**: When multiple tools are needed and independent of each other, call them all in one turn for faster execution.
2. Tool names and parameter names follow the definitions provided in your function list.
3. **Do NOT output XML <tool_call> blocks** — they will not be parsed in this mode.
