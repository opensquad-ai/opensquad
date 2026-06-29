# Agent Management Skills Usage Guide

## Overview

We have created several specialized Skills to help Agents master configuration
management, collaboration management, and architecture understanding:

| Skill | Purpose | Activation command |
|-------|---------|--------------------|
| **agent_config_management** | Agent configuration, model, role management | `install_skill("agent_config_management")` |
| **collaboration_management** | Collaboration card management, team collaboration | `install_skill("collaboration_management")` |
| **agent_architecture_im** | Agent architecture, IM account management, troubleshooting | `install_skill("agent_architecture_im")` |

## Quick Activation

### Method 1: via the `agent_setup` tool (recommended)

```xml
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_config_management</skill_path>
  </parameters>
</tool>

<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/collaboration_management</skill_path>
  </parameters>
</tool>

<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_architecture_im</skill_path>
  </parameters>
</tool>
```

### Method 2: in `config.json` (permanent activation)

```json
{
  "skills": {
    "enabled": true,
    "active": [
      "agent_config_management",
      "collaboration_management",
      "agent_architecture_im"
    ]
  }
}
```

Takes effect after restarting the Agent.

## Skill Content Overview

### `agent_config_management` Skill

#### Core flows

1. **Create a new Agent** (5-step complete flow)
   - Register a chat account
   - Create the Agent directory
   - Configure `config.json`
   - Set up `role.md`
   - Start the Agent

2. **Modify an existing Agent**
   - Change model config (provider, model_name, temperature, etc.)
   - Change role definition (`role.md`)
   - Add / remove tools
   - Change collaboration role

3. **Configuration templates**
   - Minimal config
   - Recommended configs for different models (GPT-4, DeepSeek, GLM, etc.)

4. **Best practices**
   - Configuration-change checklist
   - Security considerations
   - Performance optimization tips

5. **Troubleshooting**
   - Configuration changes not taking effect
   - Agent fails to start
   - Cannot join a group chat
   - Cannot restart after modifying its own configuration

#### Full config field reference

```json
{
  "agent_id": "unique ID",
  "agent_name": "display name",
  "model": {
    "provider": "openai_compat | openai | anthropic",
    "api_key": "API key",
    "base_url": "API endpoint",
    "model_name": "model name",
    "token_max": 128000,
    "temperature": 0.3,
    "tool_call_mode": "auto | native | xml",
    "tool_filter": "high (97) | all (124) | baseline (57)"
  },
  "tools": ["system", "filesystem", ...],
  "collaboration": {
    "enabled": true,
    "role": "pm | developer | qa | worker"
  },
  "group_chat": {
    "enabled": true,
    "email": "agent@ai",
    "password": "password",
    "groups": ["gXXXXX"]
  }
}
```

### `collaboration_management` Skill

#### Core flows

1. **View available collaboration cards**
   - List every collaboration card
   - Inspect suggested roles and descriptions

2. **Start a collaboration session (PM)**
   - Pick a collaboration card
   - Invite members
   - Assign tasks
   - Track progress

3. **Join a collaboration session (Worker)**
   - Respond to the invitation
   - Load the collaboration card
   - Follow the conventions

4. **End a collaboration**
   - PM ends the session
   - Workers leave the session

5. **Create a custom collaboration card**
   - Design the collaboration flow
   - Define role responsibilities
   - Define message conventions
   - Set behavioral constraints

#### Built-in collaboration cards

| Card | Use case | Suggested roles |
|------|----------|-----------------|
| **software_dev_team** | Full software-development project | pm, developer, qa |
| **code_review** | Code review | reviewer, author |
| **research_task** | Research-oriented task | lead, researcher |
| **autonomous_vcs_dev** | Autonomous development flow | developer |

#### Collaboration card structure

```markdown
---
name: card_name
description: description
tags: tag1, tag2
suggested_roles: role1, role2
min_members: 2
---

## Project lifecycle
(phases, lead role, deliverables, transition conditions)

## Standard message format
(task assignment, status report, bug report, etc.)

## Behavioral constraints
(no overstepping, no silence, no assumptions, etc.)
```

## Example Scenarios

### Scenario 1: I want to create a new QA Agent

```xml
<!-- 1. Activate the agent_config_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_config_management</skill_path>
  </parameters>
</tool>

<!-- 2. Follow "Flow 1: Create a new Agent" in the Skill -->
<!-- 5 steps: register account → create directory → configure → set role → start -->
```

### Scenario 2: I want to switch my model from GPT-4 to DeepSeek

```xml
<!-- 1. Activate the agent_config_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/agent_config_management</skill_path>
  </parameters>
</tool>

<!-- 2. Follow "Flow 3: Modify the model configuration" -->
<!-- Pick the DeepSeek recommended config, edit config.json, request a restart -->
```

### Scenario 3: I want to start a software-development collaboration

```xml
<!-- 1. Activate the collaboration_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/collaboration_management</skill_path>
  </parameters>
</tool>

<!-- 2. Follow "Flow 2: Start a collaboration session (PM)" -->
<!-- Use the software_dev_team card, invite members, assign tasks -->
```

### Scenario 4: I want to create a custom data-analysis collaboration mode

```xml
<!-- 1. Activate the collaboration_management Skill -->
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/collaboration_management</skill_path>
  </parameters>
</tool>

<!-- 2. Follow "Flow 5: Create a custom collaboration card" -->
<!-- Review the example (my_research_team), then author your own -->
```

## Skill File Locations

```
skills/
├── agent_config_management/
│   ├── SKILL.md          # main content (15KB, comprehensive guide)
│   └── skill.json        # metadata
└── collaboration_management/
    ├── SKILL.md          # main content (18KB, comprehensive guide)
    └── skill.json        # metadata
```

## Relationship with Existing Documentation

| Doc | Type | Purpose |
|------|------|---------|
| `doc_en/agent_management.md` | Reference doc | Detailed static documentation for humans |
| `skills/agent_config_management/` | Activatable Skill | Loaded by Agents at runtime; provides dynamic, real-time guidance |
| `skills/collaboration_management/` | Activatable Skill | Loaded by Agents at runtime; provides dynamic, real-time guidance |
| `docs/agent_factory_guide.md` | Reference doc | Detailed description of the Agent Factory plugin |
| `docs/system_wait_interruptible.md` | Reference doc | Description of the `system.wait()` feature |

**The difference**:
- **`docs/`** is static documentation, for humans or Agents to read.
- **`skills/`** is an activatable work guide — once the Agent activates it, the
  content is injected into the system prompt and provides real-time
  in-the-loop guidance.

## Verifying Skills

### List installed Skills

```xml
<tool name="agent_setup">
  <function>list_installed</function>
  <parameters></parameters>
</tool>
```

### Verifying after activation

After activation, the Skill's full content is included in your system prompt.
You can then:
- Follow the Skill's flows directly to execute tasks
- Reference the Skill's config templates and examples
- Adhere to the Skill's best practices and conventions

## When to use these Skills

### Use the `agent_config_management` Skill

- ✅ You need to create a new Agent
- ✅ You need to modify an Agent's config (model, tools, role)
- ✅ You are unsure about the meaning of a config field
- ✅ You hit a config-related failure
- ✅ You need to look up a config template

### Use the `collaboration_management` Skill

- ✅ You need to start a team collaboration
- ✅ You have been invited to join a collaboration
- ✅ You need to create a custom collaboration mode
- ✅ You are not sure about the collaboration flow or message conventions
- ✅ You run into a collaboration-related problem

### Use the `agent_architecture_im` Skill

- ✅ You need to understand the Agent process architecture
- ✅ You hit an IM account login failure
- ✅ You are unsure how to configure a ChatPro account
- ✅ You suspect a "global singleton shared account" issue
- ✅ You need to debug Bridge connection issues
- ✅ You need to modify an Agent's IM account

### When you don't need to activate a Skill

- ❌ You're only making a simple field change (do it directly)
- ❌ You already know the flow (no need to re-activate)
- ❌ You're only looking something up (read the doc directly)

## Update Log

- **2026-03-01**:
  - Created the `agent_config_management` and `collaboration_management` Skills
  - Split them off from `doc_cn/agent_management.md` into two focused Skills
  - Added the `agent_architecture_im` Skill, clarifying the Agent process
    isolation architecture and IM account management model
- Added detailed flows, sample code, best practices, and troubleshooting

---

**Related documentation**:
- `doc_en/agent_management.md` — complete Agent management guide
- `doc_en/agent_factory_guide.md` — Agent Factory plugin in detail
- `doc_en/system_wait_interruptible.md` — interruptible `system.wait()`
- `skills/agent_deployment/SKILL.md` — Agent deployment flow
- `skills/self_config/SKILL.md` — Agent self-configuration
