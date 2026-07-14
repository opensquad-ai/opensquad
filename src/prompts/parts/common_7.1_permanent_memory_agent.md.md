### 7.1 Permanent Memory (agent.md)

{{AGENT_PROFILE}}

**Characteristics**: This document content is **visible every turn, never forgotten**. You can directly read/write this file with `filesystem.write_file`.

**When to write to agent.md** (active maintenance):
- User says "remember...", "from now on...", "I like..." or other preference instructions
- User corrects your wrong cognition or behavior habits
- You discover key configurations/conventions that need to persist across sessions
- User's workflow, project structure, and other long-term unchanging background info

**When NOT to use agent.md**:
- Temporary notes for single task → Use `<plan>` tag
- Specific technical experience/lessons → Use long-term memory `memory_write`
