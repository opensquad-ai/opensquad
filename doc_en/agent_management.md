# Agent Management Guide: Role, Model, and Collaboration Card Configuration

## Overview

In OpenSquad, an Agent can manage its own configuration or that of other
Agents via two paths:

| Method | Use Case | Strengths | Tools |
|--------|----------|-----------|-------|
| **Plugin API** | Creating new agents, full config management | Safe, standardized, validated, hot-reload capable | `agent_factory.*` + `chat_account.*` |
| **Direct file edit** | Quick edits, batch operations, complex changes | Flexible, direct, supports any edit | `filesystem.*` |

**Recommended priority:**

1. **Creating a new agent**: must use `agent_factory` (auto-generates
   structure, registers account, starts the process)
2. **Modifying existing config**: prefer `agent_factory`; use `filesystem`
   for complex changes
3. **Modifying your own config**: either method works, `filesystem` is
   simpler

---

## 1. Agent Configuration Structure

### 1.1 Directory Structure

```
agents/
├── your_agent/
│   ├── config.json          # Main configuration (required)
│   ├── role.md              # Role definition (required)
│   ├── mcp_config.json      # MCP server config (optional)
│   └── data/
│       ├── sessions/        # Session history
│       └── logs/            # Runtime logs
```

### 1.2 Full `config.json` Structure

```json
{
  "agent_id": "100001",                    // Unique agent ID
  "agent_name": "my-agent-001",           // Agent name
  "agent_type": "general",                // Agent type
  "description": "Agent description",

  // === Model configuration ===
  "model": {
    "provider": "openai_compat",          // Provider: openai, openai_compat, anthropic, etc.
    "api_key": "sk-xxxx",                 // API key
    "base_url": "https://api.example.com", // API base URL
    "model_name": "gpt-4",                // Model name
    "token_max": 128000,                  // Max context tokens
    "temperature": 0.3,                   // Temperature (0-1)
    "is_think": false,                    // Supports CoT reasoning
    "is_image": false,                    // Supports image input
    "is_video": false,                    // Supports video input
    "_card": "GPT-4",                     // Display name (used by frontend)
    "tool_call_mode": "auto",             // Tool-call mode: auto | native | xml
    "tool_filter": "high"                 // Tool filter level: high (97) | all (124) | baseline (57)
  },

  // === Tools configuration ===
  "tools": [
    "system",                             // System tools
    "filesystem",                         // File system
    "im",                                 // Group chat messages
    "agent_setup",                        // Agent self-management
    "collaboration",                      // Collaboration tools
    "agent_factory",                      // Agent factory (create other agents)
    "chat_account"                        // Chat account management
  ],

  // === Collaboration configuration ===
  "collaboration": {
    "enabled": true,                      // Enable collaboration
    "role": "developer"                   // Collaboration role: pm, developer, qa, worker, etc.
  },

  // === Group chat configuration ===
  "group_chat": {
    "enabled": true,                      // Enable group chat
    "email": "myagent@ai",                // Login email (must be registered first)
    "password": "123456",                 // Login password
    "groups": ["gXXXXX"]                  // Group IDs to join
  },

  // === Web server configuration ===
  "web_server": {
    "enabled": true,                      // Enable the Web UI
    "port": 8010                          // Listening port
  },

  // === Gateway configuration ===
  "gateway": {
    "enabled": true,                      // Register with the Gateway
    "url": "ws://127.0.0.1:9555/ai-ws/register"
  },

  // === Other configuration ===
  "prompt": {
    "role": "role.md"                     // Role file path
  },
  "default_wake_mode": "strict",          // Wake mode: strict | relaxed
  "mcp": {
    "enabled": true                       // Enable MCP
  },
  "skills": {
    "enabled": true,                      // Enable skills
    "active": ["skill_name"]              // Active skill list
  },
  "filesystem": {
    "workspace_dirs": [                   // File-system whitelist
      "/path/to/workspace"
    ]
  },
  "tool_levels": {                        // Tool priority overrides
    "websearch": "core",
    "filesystem": "core"
  }
}
```

### 1.3 `role.md` Structure

`role.md` is the agent's "personality definition", injected into the
system prompt on every conversation turn.

```markdown
# Role: Agent Name

Your group chat nickname is xxx.
You are a professional XXX, specializing in YYY.

## Core Capabilities
- Capability 1
- Capability 2

## Workflow
1. Step 1
2. Step 2

## Behavioral Guidelines
- Guideline 1
- Guideline 2

## Interaction Style
- Concise and professional
- Proactive thinking
```

---

## 2. Manage via the `agent_factory` Plugin (Recommended)

### 2.1 Full Flow for Creating a New Agent

```xml
<!-- Step 1: Register the chat account -->
<tool name="chat_account">
  <function>register_account</function>
  <parameters>
    <email>newagent@ai</email>
    <password>123456</password>
    <name>New Agent</name>
  </parameters>
</tool>

<!-- Step 2: Create the agent directory structure -->
<tool name="agent_factory">
  <function>create_agent</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
    <agent_name>My New Assistant</agent_name>
    <agent_type>general</agent_type>
    <description>A general-purpose assistant</description>
  </parameters>
</tool>

<!-- Step 3: Configure the full config.json -->
<tool name="agent_factory">
  <function>configure_agent</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
    <config>{
      "agent_id": "100002",
      "agent_name": "my-new-agent",
      "agent_type": "general",
      "description": "My new assistant",
      "model": {
        "provider": "openai_compat",
        "api_key": "sk-xxxx",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "token_max": 128000,
        "temperature": 0.3,
        "tool_call_mode": "auto",
        "tool_filter": "high"
      },
      "tools": ["system", "filesystem", "im", "collaboration"],
      "group_chat": {
        "enabled": true,
        "email": "newagent@ai",
        "password": "123456",
        "groups": ["gXXXXX"]
      },
      "collaboration": {
        "enabled": true,
        "role": "developer"
      },
      "web_server": {"enabled": true, "port": 8011},
      "gateway": {"enabled": true, "url": "ws://127.0.0.1:9555/ai-ws/register"}
    }</config>
  </parameters>
</tool>

<!-- Step 4: Set the role -->
<tool name="agent_factory">
  <function>set_agent_role</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
    <role_content># Role: Python Expert

Your group chat nickname is newagent.
You are a professional Python developer, specializing in code review and performance optimization.

## Core Capabilities
- Python programming
- Code review
- Performance analysis

## Working Principles
- Clean, elegant code
- Performance-aware
- Follows PEP 8</role_content>
  </parameters>
</tool>

<!-- Step 5: Start the agent -->
<tool name="agent_factory">
  <function>start_agent</function>
  <parameters>
    <dir_name>my_new_agent</dir_name>
  </parameters>
</tool>
```

### 2.2 Modifying an Existing Agent

#### Changing the model configuration

```xml
<!-- 1. Read the current config first -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>

<!-- 2. Edit and write back (via configure_agent) -->
<tool name="agent_factory">
  <function>configure_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
    <config>{
      ... full configuration (with the updated model section) ...
    }</config>
  </parameters>
</tool>

<!-- 3. Restart the agent for the change to take effect -->
<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
  </parameters>
</tool>
```

#### Changing the role

```xml
<tool name="agent_factory">
  <function>set_agent_role</function>
  <parameters>
    <dir_name>my_agent</dir_name>
    <role_content># Role: New role

New role description...</role_content>
  </parameters>
</tool>

<!-- Restart to take effect -->
<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
  </parameters>
</tool>
```

#### Changing the collaboration role

```xml
<!-- Read → modify collaboration.role → write back → restart -->
<tool name="agent_factory">
  <function>configure_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
    <config>{
      ...,
      "collaboration": {
        "enabled": true,
        "role": "qa"  // change to QA
      },
      ...
    }</config>
  </parameters>
</tool>

<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>my_agent</dir_name>
  </parameters>
</tool>
```

---

## 3. Direct File Edit via `filesystem` (Flexible Method)

### 3.1 Modifying your own configuration (most common)

```xml
<!-- Example: adding a new tool to yourself -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/coder/config.json</path>
  </parameters>
</tool>

<!-- Add the new tool to the tools array, then write back -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/coder/config.json</path>
    <content>{
  "agent_id": "100001",
  ...,
  "tools": [
    "system",
    "filesystem",
    "new_tool"  // newly added
  ],
  ...
}</content>
  </parameters>
</tool>
```

**Important notes:**

- After editing `config.json` you must restart the agent for the change
  to take effect.
- If you edited your own config, ask another agent or the user to
  restart you.
- If you edited another agent's config, use
  `agent_factory.restart_agent()` to restart it.

### 3.2 Changing the model configuration

```xml
<!-- Edit the model section of config.json directly -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>

<!-- Modify the model object, then write back -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
    <content>{
  ...,
  "model": {
    "provider": "openai_compat",
    "api_key": "new-api-key",
    "base_url": "https://new-endpoint.com",
    "model_name": "new-model",
    "token_max": 200000,
    "temperature": 0.5,
    "tool_call_mode": "native",
    "tool_filter": "all"
  },
  ...
}</content>
  </parameters>
</tool>
```

### 3.3 Changing the role

```xml
<!-- Overwrite role.md directly -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/my_agent/role.md</path>
    <content># Role: New Role

You are a professional XXX...

## Capabilities
- Capability 1
- Capability 2

## Principles
- Principle 1
- Principle 2</content>
  </parameters>
</tool>
```

### 3.4 Changing the collaboration configuration

```xml
<!-- Method 1: edit the collaboration object in config.json -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>

<!-- Edit, then write back -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
    <content>{
  ...,
  "collaboration": {
    "enabled": true,
    "role": "pm"  // change to PM
  },
  ...
}</content>
  </parameters>
</tool>
```

---

## 4. Collaboration Cards (Collab Cards)

### 4.1 What is a collaboration card?

A collaboration card is a predefined multi-agent collaboration pattern,
stored as `collab_cards/*.md`, that defines:

- Collaboration flow and phases
- Role responsibilities
- Message format and conventions
- Behavioral constraints

### 4.2 Listing available collaboration cards

```xml
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>
```

Example response:

```json
{
  "status": "success",
  "cards": [
    {
      "name": "software_dev_team",
      "display_name": "Software Development Team",
      "description": "Multi-agent protocol for end-to-end software development projects",
      "suggested_roles": ["pm", "developer", "qa"],
      "tags": "software, team, pm, dev, qa"
    },
    {
      "name": "code_review",
      "display_name": "Code Review",
      "description": "Collaboration pattern focused on code review",
      "suggested_roles": ["reviewer", "author"],
      "tags": "code, review"
    }
  ]
}
```

### 4.3 Starting a collaboration session (PM only)

```xml
<tool name="collaboration">
  <function>start_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
    <members>["coder", "qa_agent"]</members>
    <group_id>gXXXXX</group_id>
    <project_name>New feature development</project_name>
    <project_description>Implement the user-login module</project_description>
  </parameters>
</tool>
```

**Flow:**

1. The collaboration card content is loaded into the PM's system prompt.
2. The system automatically @-mentions the suggested members in the
   group chat, inviting them to join.
3. The PM is responsible for task assignment and progress management.

### 4.4 Joining a collaboration session (Worker only)

```xml
<tool name="collaboration">
  <function>join_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
  </parameters>
</tool>
```

**Effect:**

- The collaboration card content is loaded into the Worker's system
  prompt.
- The Worker starts working according to the card's conventions.

### 4.5 Ending a collaboration session

```xml
<!-- PM ends the session -->
<tool name="collaboration">
  <function>end_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
    <group_id>gXXXXX</group_id>
  </parameters>
</tool>

<!-- Worker leaves the session -->
<tool name="collaboration">
  <function>leave_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
  </parameters>
</tool>
```

### 4.6 Creating a custom collaboration card

```xml
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>collab_cards/my_custom_card.md</path>
    <content>---
name: my_custom_card
description: My custom collaboration pattern
tags: custom, workflow
suggested_roles: leader, worker
min_members: 2
---

## Collaboration Flow

1. Phase 1: Plan
2. Phase 2: Execute
3. Phase 3: Verify

## Role Responsibilities

### Leader
- Make the plan
- Assign tasks

### Worker
- Execute tasks
- Report progress

## Message Conventions

**Task assignment**
```
@Worker [TASK] Task name
Description: ...
Acceptance criteria: ...
```

**Status report**
```
[STATUS] Done with XXX, hit problem YYY
```

## Behavioral Constraints

- Don't overstep your role
- Communicate promptly
- Document decisions</content>
  </parameters>
</tool>
```

---

## 5. Best Practices

### 5.1 Pre-edit checklist

Before modifying configuration, confirm:

- [ ] Do you need a backup of the current config?
- [ ] Is the new JSON well-formed?
- [ ] Are the ports and group IDs in the new config valid?
- [ ] Is the API key correct?
- [ ] Do you need to restart the agent afterward?
- [ ] Will this affect any running collaboration session?

### 5.2 Model configuration suggestions

| Model | `tool_call_mode` | `tool_filter` | `temperature` | Use case |
|-------|------------------|---------------|---------------|----------|
| GPT-4 / Claude | `auto` | `high` | 0.3 | General programming, complex reasoning |
| DeepSeek-V3 | `native` | `all` | 0.0 | Strict tool-call, precise execution |
| GLM-4/5 | `auto` | `high` | 0.0 | Chinese-first, stable output |
| Qwen | `native` | `baseline` | 0.5 | Creative generation, conversation |

### 5.3 Choosing a collaboration role

| Role | Use case | Required tools | Example card |
|------|----------|----------------|--------------|
| `pm` | Project management, task assignment | `collaboration`, `im`, `delegate_task` | `software_dev_team` |
| `developer` | Code writing | `filesystem`, `vcs_remote`, `api_browser` | `software_dev_team` |
| `qa` | Testing and verification | `filesystem`, `api_browser`, `im` | `software_dev_team` |
| `reviewer` | Code review | `vcs_remote`, `filesystem` | `code_review` |
| `worker` | General executor | Depends on the task | any |

### 5.4 Security notes

1. **Protect API keys:**
   - Never send API keys in plain text in group chat.
   - Use environment variables or secure storage.

2. **Permission isolation:**
   - Use `filesystem.workspace_dirs` to limit file-access scope.
   - Different agents should use different working directories.

3. **Password management:**
   - Use strong passwords for group chat.
   - Rotate passwords regularly.

4. **Configuration backup:**
   - Back up `config.json` before editing.
   - Manage configuration files with version control.

---

## 6. Troubleshooting

### 6.1 Configuration change does not take effect

**Cause:** the agent was not restarted.

**Fix:**

```xml
<tool name="agent_factory">
  <function>restart_agent</function>
  <parameters>
    <dir_name>target_agent_dir_name</dir_name>
  </parameters>
</tool>
```

### 6.2 Agent cannot join the group chat

**Checks:**

1. Is the `group_chat.email` / `password` already registered?
2. Are the group IDs in `group_chat.groups` correct?
3. Is the Gateway running normally?

**Fix:**

```xml
<!-- 1. Make sure the account is registered -->
<tool name="chat_account">
  <function>register_account</function>
  <parameters>
    <email>agent@ai</email>
    <password>123456</password>
    <name>Agent</name>
  </parameters>
</tool>

<!-- 2. Inspect config.json -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>
```

### 6.3 Collaboration card fails to load

**Checks:**

1. Does the card file exist in the `collab_cards/` directory?
2. Is the card format correct (YAML front matter + Markdown)?
3. Is the `collaboration` tool in the agent's `tools` list?

**Fix:**

```xml
<!-- 1. List all available cards -->
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>

<!-- 2. Verify collaboration is enabled on the agent -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>
<!-- Confirm "collaboration" is in the tools array, and
     collaboration.enabled = true -->
```

### 6.4 Cannot restart after editing your own config

**Problem:** an agent edits its own `config.json` but cannot restart
itself.

**Solutions:**

1. **Ask the user to restart manually:**

   ```
   I've updated my configuration. Please restart me from the Launcher
   UI, or run:
   restart agent: coder
   ```

2. **Ask another agent to restart you:**

   ```xml
   <!-- In group chat, @mention another agent that has agent_factory -->
   @admin_agent please restart me, I just changed my config.

   <!-- admin_agent runs: -->
   <tool name="agent_factory">
     <function>restart_agent</function>
     <parameters>
       <dir_name>coder</dir_name>
     </parameters>
   </tool>
   ```

---

## 7. Quick Reference

### 7.1 Common commands

| Action | Recommended tool | Alternative |
|--------|------------------|-------------|
| Create a new agent | `agent_factory.create_agent` | - |
| Change model config | `agent_factory.configure_agent` | `filesystem.write_file` |
| Change role | `agent_factory.set_agent_role` | `filesystem.write_file` |
| Change collaboration role | `agent_factory.configure_agent` | `filesystem.write_file` |
| Add a tool | `filesystem.write_file` (config.json) | - |
| Start collaboration | `collaboration.start_collaboration` | - |
| Join collaboration | `collaboration.join_collaboration` | - |
| Restart agent | `agent_factory.restart_agent` | Ask user / another agent |

### 7.2 Configuration templates

#### Minimal configuration

```json
{
  "agent_id": "100xxx",
  "agent_name": "my-agent",
  "model": {
    "provider": "openai_compat",
    "api_key": "sk-xxx",
    "base_url": "https://api.example.com",
    "model_name": "model-name",
    "token_max": 128000,
    "temperature": 0.3
  },
  "tools": ["system", "filesystem"],
  "web_server": {"enabled": true, "port": 8010},
  "gateway": {"enabled": true}
}
```

#### Full configuration

See [1.2 Full `config.json` Structure](#12-full-configjson-structure).

---

**Document version:** v1.0
**Last updated:** 2026-03-01
**Related documents:**

- [Agent Factory Guide](../doc_cn/agent_factory_guide.md) (中文) - detailed
  `agent_factory` usage
- [Sub-Agent Delegation](../doc_cn/SUB_AGENT_DELEGATION.md) (中文) - sub-agent
  delegation feature
- `skills/agent_deployment/SKILL.md` - agent deployment workflow skill
- `skills/self_config/SKILL.md` - agent self-configuration skill
