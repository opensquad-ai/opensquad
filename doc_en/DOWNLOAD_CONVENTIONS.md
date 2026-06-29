# External Resource Download & Installation Conventions

When an Agent downloads files or installs tools from the network, **it must follow this convention**. Violations (e.g. writing files into the project root) can break the framework, affect other Agents, or even cause data loss.

---

## 1. Forbidden Zones (Never Write Here)

The following paths belong to framework infrastructure. Under no circumstances may an Agent write to or overwrite files in these locations (without explicit user authorization):

| Forbidden path | Reason |
|----------------|--------|
| Project root (files directly under `/`) | Root-level pollution, hard to clean up, may overwrite configuration files |
| `opensquad/` | Framework core package; changes affect every Agent |
| `gateway/` | Gateway frontend + backend; changes break the Web UI |
| `agents/boot.py` | Shared Agent bootstrap |
| `launcher.py` | Multi-process manager |
| `system_config.py` / `system_config.json` | Global configuration center |
| Inside any existing `plugins/` subdirectory | May overwrite an existing plugin |
| Non-data subdirectories of `agents/{name}/` | May overwrite Agent configuration |

---

## 2. Correct Landing Paths by Type

### 2.1 Skill Package

A Skill is a directory containing `SKILL.md`, optionally with `tools.py`.

| Purpose | Landing path | Notes |
|---------|--------------|-------|
| **Global public** (available to all Agents) | `skills/{skill_name}/` | Auto-discovered by all Agents |
| **Agent-private** (only the current Agent) | `agents/{name}/skills/{skill_name}/` | Also add the name to `config.json > skills.private` |

Full flow for downloading and installing a Skill:

```
1. Write SKILL.md (required) and tools.py (optional) into the target directory
   Example: skills/my_new_skill/SKILL.md

2. Call install_skill to hot-load — no restart needed:
   install_skill(skill_dir="<project_root>/skills/my_new_skill")

3. Verify with list_installed() that the skill shows up in the list.
```

Minimum SKILL.md format:

```markdown
---
name: my_new_skill
description: One-line summary
allowed-tools: filesystem
---

## Skill description

...body instructions...
```

---

### 2.2 MCP Server

An MCP server itself usually does not require "downloading files" — `npx` pulls the npm package on demand. The only thing to write is the **configuration**.

| Purpose | Landing path | Notes |
|---------|--------------|-------|
| **Global default** (shared by all Agents) | `mcpServers` field in `pymcp/config_basic.json` | Lowest priority |
| **Agent-specific** (only the current Agent) | `mcpServers` field in `agents/{name}/mcp_config.json` | Higher priority than global |

**Do not hand-write configuration files.** Call a tool and let the framework persist it:

```xml
<tool_call>
  <name>mcp_query.add_server</name>
  <arguments>
    {
      "server_name": "my-mcp",
      "command": "npx",
      "args": ["-y", "@scope/mcp-package", "/path/to/target"],
      "timeout": 60
    }
  </arguments>
</tool_call>
```

`add_server` will automatically: write the config → start the process → register the tool, **immediately available, no restart needed**.

If you need Agent-specific rather than global, before calling check whether `agents/{name}/mcp_config.json` exists. If not, create it:

```json
{
  "mcpServers": {}
}
```

Then call `add_server` and manually move the entry into `agents/{name}/mcp_config.json` (or notify the user and let them decide on the scope).

---

### 2.3 Task / Project Files

Code, documentation, data, etc. produced or downloaded by an Agent while executing a task:

| Content type | Landing path | Notes |
|--------------|--------------|-------|
| Project code, engineering files | `workspace/projects/{project_name}/` | Standard workspace for collaborative tasks |
| Data files (CSV, JSON, images, etc.) | `workspace/data/{context}/` | Subdirectory per context |
| Temp / intermediate files | `workspace/tmp/` | Can be cleaned up at any time, no persistence guarantee |
| Agent-private data (not shared with collaborators) | `agents/{name}/data/` | Only the current Agent uses it |

---

### 2.4 Generic Scripts / Tool Files

Downloaded Python helper scripts, shell scripts, etc. **must never be placed in the project root**.

| Purpose | Landing path |
|---------|--------------|
| Helper script belonging to a Skill | Inside the corresponding `skills/{name}/` directory |
| Task-specific script | `workspace/projects/{name}/scripts/` |
| Global utility script | `scripts/` directory (already exists) |

---

### 2.5 pip / npm Packages

When installing packages via `run_command`, **prefer a virtual environment or user-level install**. Do not modify the system Python environment.

```python
# Recommended (user-level, no system impact)
run_command("pip install --user some-package")

# MCP servers use npx, on-demand; no manual install needed
run_command("npx -y @scope/mcp-package --version")  # verify availability
```

---

## 3. Decision Tree Before Downloading

```
Need to download an external resource
       |
       ├─ Is it a Skill package (SKILL.md)?
       │     ├─ Used by all Agents → skills/{skill_name}/
       │     └─ Used only by the current Agent → agents/{name}/skills/{skill_name}/
       │
       ├─ Is it MCP server config?
       │     └─ Call mcp_query.add_server (do not hand-write files)
       │
       ├─ Is it a task / project file?
       │     ├─ Multi-Agent collaboration → workspace/projects/{name}/
       │     ├─ Data file              → workspace/data/{context}/
       │     └─ Temp file              → workspace/tmp/
       │
       └─ Is it a tool script?
             ├─ Belongs to a Skill   → skills/{skill_name}/
             └─ Standalone utility   → scripts/
```

---

## 4. Pre-Write Checklist

Before calling `filesystem.write_file` to write any downloaded content, confirm each of the following:

1. **The path is not in a forbidden zone** (see section 1).
2. **The path is not the project root** (the file path must have at least one subdirectory).
3. **If this is a new directory, you have already used `list_directory` to confirm the parent exists and matches expectations.**
4. **If you are overwriting an existing file, you have used `read_file` to confirm its content and clearly told the user.**
5. **If it is a Skill, call `install_skill` after writing to activate it.**

---

## 5. Quick Reference

| Downloaded content | Command / tool | Landing path |
|--------------------|----------------|--------------|
| Public Skill | `write_file` + `install_skill` | `skills/{name}/SKILL.md` |
| Private Skill | `write_file` + `install_skill` + update config | `agents/{name}/skills/{name}/SKILL.md` |
| MCP server | `mcp_query.add_server` | Auto-writes `pymcp/config_basic.json` |
| Project code | `write_file` | `workspace/projects/{name}/` |
| Data file | `write_file` | `workspace/data/{context}/` |
| Temp file | `write_file` | `workspace/tmp/` |
| pip package | `run_command pip install --user` | Python user directory (system-managed) |
| npm package | `run_command npx -y ...` | node_modules (system-managed) |
