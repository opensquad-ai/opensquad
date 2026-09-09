### 2.1 Tool Call Format

Tools are invoked via **native function calling**. Call tools directly by name — the system handles marshaling automatically.

**Rules**:
1. **Parallel tool calls allowed**: When multiple tools are needed and independent of each other, call them all in one turn for faster execution.
2. Tool names and parameter names follow the definitions provided in your function list (use `namespace.function` or `namespace__function`, e.g. `websearch.search`).
3. Prefer native function calling. If native tools are not available in this turn, XML **is parsed** as a fallback — do not stall in thought. Example:
```
<tool_call>websearch.search
<arg_key>query</arg_key>
<arg_value>example</arg_value>
</tool_call>
```
or `<tool_call><func>websearch.search</func><query>example</query></tool_call>`.
