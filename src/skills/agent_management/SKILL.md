# Agent Management Complete Guide

## Overview

This Skill provides a complete management guide for OpenSquad Agents, covering the full workflow of creation, configuration, deployment, and troubleshooting.

**Key Contents**:
- Quick Agent creation (live in 5 minutes)
- Configuration file reference (model, role, tools)
- Architecture internals (process isolation, IM accounts)
- Troubleshooting and best practices

---

**Note**
If the user does not specify a name when creating an agent, you may decide based on the task content. The account email can also be named in relation to the actual business. The password used when registering the account is the group password configured for the agent — remember it.
You may configure a role card for the agent based on business needs.

**Important — account registration is a separate step**: `agent_factory.create_agent` only creates the directory structure and config file. It does **not** automatically register a chat account. You must call `chat_account.register_account` (Step 1) before creating the agent. The five-step flow below reflects the correct order. (As a fallback, if the agent process starts and the account does not yet exist, `bridge.login()` will attempt auto-registration at runtime — but you should not rely on this; always register the account explicitly first.)

# Part 1: Quick Start

## 1. Five Steps to Create an Agent

### Step 1: Register a ChatPro Account

Each Agent needs an independent account to log into the group chat system.

```
chat_account.register_account(
    email="mybot@ai",       # Account email, unique per Agent
    password="Bot@2026",    # Login password
    name="My Assistant"     # Display name
)
```

---

### Step 2: Create or Join a Group

**Create a new group**:
```
chat_account.create_group(
    email="mybot@ai",           # Operating account email
    password="Bot@2026",        # Password
    group_name="AI Workspace"   # Group name
)
# Returns group_id, used when configuring config.json
```

**Join an existing group**:
```
chat_account.join_group(
    email="mybot@ai",       # Operating account email
    password="Bot@2026",    # Password
    group_id="g12345"       # Target group ID
)
```

---

### Step 3: Create Agent Directory

```
agent_factory.create_agent(
    dir_name="my_bot",          # Directory name, only letters/numbers/underscores
    agent_name="My Assistant",  # Display name
    agent_type="general",       # Type: general / coder / pm / qa
    description="A general assistant"  # Description (optional)
)
```

**Generated directory structure**:
```
agents/my_bot/
├── config.json       # Main configuration file
├── role.md           # Role definition
├── mcp_config.json   # MCP tools configuration
└── data/             # Runtime data
```

---

### Step 4: Configure the Agent

Edit `agents/my_bot/config.json` and fill in the account and group information:

```json
{
  "agent_id": "my_bot",
  "agent_name": "My Assistant",
  "model": {
    "provider": "openai_compat",
    "api_key": "your-api-key",
    "base_url": "https://api.deepseek.com",
    "model_name": "deepseek-chat",
    "token_max": 128000,
    "temperature": 0.3
  },
  "tools": ["system", "filesystem", "im", "task"],
  "group_chat": {
    "enabled": true,
    "email": "mybot@ai",
    "password": "Bot@2026",
    "base_url": "http://localhost:9555",
    "groups": ["g12345"]
  },
  "web_server": {
    "enabled": true
  }
}
```

---

### Step 5: Start the Agent

```
agent_factory.start_agent(
    agent_name="my_bot"     # Agent directory name
)
```

**Verify startup**:
```
agent_factory.list_agents()
# Should see my_bot with status: running
```

---

## 2. Complete Example Scenarios

### Scenario A: Single General Assistant

**Steps**:

1. Register account:
```
chat_account.register_account(
    email="helper@ai",
    password="Helper@2026",
    name="General Assistant"
)
```

2. Create group (returns group_id, e.g. g98765):
```
chat_account.create_group(
    email="helper@ai",
    password="Helper@2026",
    group_name="My Workspace"
)
```

3. Create Agent:
```
agent_factory.create_agent(
    dir_name="helper",
    agent_name="General Assistant",
    agent_type="general"
)
```

4. Configure config.json (fill in email, password, groups: ["g98765"])

5. Start:
```
agent_factory.start_agent(agent_name="helper")
```

---

### Scenario B: Collaboration Team (3 Agents)

**Goal**: Create PM, Developer, and QA collaboration agents.

#### B.1 Preparation

**Register 3 accounts**:
```
chat_account.register_account(email="pm@team.ai",  password="PM@2026",  name="PM")
chat_account.register_account(email="dev@team.ai", password="Dev@2026", name="Developer")
chat_account.register_account(email="qa@team.ai",  password="QA@2026",  name="QA")
```

**Create shared group (returns group_id, e.g. g11111)**:
```
chat_account.create_group(
    email="pm@team.ai",
    password="PM@2026",
    group_name="Dev Team"
)
```

**Other accounts join the group**:
```
chat_account.join_group(email="dev@team.ai", password="Dev@2026", group_id="g11111")
chat_account.join_group(email="qa@team.ai",  password="QA@2026",  group_id="g11111")
```

#### B.2 Create 3 Agents

```
agent_factory.create_agent(dir_name="pm",        agent_name="PM",        agent_type="pm")
agent_factory.create_agent(dir_name="developer", agent_name="Developer", agent_type="coder")
agent_factory.create_agent(dir_name="qa",        agent_name="QA",        agent_type="qa")
```

#### B.3 Configure Each Agent

**PM config** (`agents/pm/config.json`):
```json
{
  "agent_id": "pm",
  "agent_name": "PM",
  "model": {
    "provider": "openai_compat",
    "api_key": "your-key",
    "base_url": "https://api.deepseek.com",
    "model_name": "deepseek-chat"
  },
  "tools": ["im", "task", "filesystem"],
  "collaboration": {
    "enabled": true,
    "role": "pm"
  },
  "group_chat": {
    "enabled": true,
    "email": "pm@team.ai",
    "password": "PM@2026",
    "groups": ["g11111"]
  },
  "web_server": {
    "enabled": true
  }
}
```

**PM role** (`agents/pm/role.md`):
```markdown
# Role: Project Manager

Your group chat alias is PM.
You are a professional project manager responsible for coordinating Developer and QA.

## Core Competencies
- Requirements analysis and task decomposition
- Coordinating development and testing workflows
- Monitoring project progress

## Workflow
1. Receive user requirements and decompose into tasks
2. Use `im.send_group_message` to assign tasks to Developer
3. After Developer completes, notify QA to test
4. Report completion to user after QA passes

## Behavioral Guidelines
- Never write code directly — that is Developer's responsibility
- Use @Developer or @QA to assign tasks
- Keep tasks clear and trackable
```

**Developer and QA configs are similar** (change email, password, role).

#### B.4 Start the Team

```
agent_factory.start_agent(agent_name="pm")
agent_factory.start_agent(agent_name="developer")
agent_factory.start_agent(agent_name="qa")
```

---

# Part 2: Configuration Reference

## 3. config.json Full Field Reference

```json
{
  "agent_id": "100001",
  "agent_name": "coder-001",
  "agent_type": "coder",
  "description": "An agent focused on programming, debugging, and code review",
  "capabilities": ["python", "javascript", "debug"],

  "model": {
    "provider": "openai_compat",
    "api_key": "sk-xxx",
    "base_url": "https://api.example.com/v1",
    "model_name": "qwen3.5-plus",
    "token_max": 128000,
    "temperature": 0.7,
    "is_think": false,
    "is_image": false,
    "is_video": false,
    "_card": "kimi_k2_5"
  },

  "tools": [
    "system",
    "filesystem",
    "websearch",
    "agent_setup",
    "api_process",
    "long_memory",
    "im",
    "vision",
    "mcp_query",
    "chat_account",
    "agent_factory"
  ],

  "tool_levels": {
    "chat_account": "core",
    "agent_factory": "core",
    "websearch": "core"
  },

  "collaboration": {
    "enabled": true,
    "role": "developer"
  },

  "group_chat": {
    "enabled": true,
    "email": "coder001@ai",
    "password": "123456",
    "groups": ["gzrtrp"]
  },

  "web_server": {
    "enabled": true,
    "port": 8002
  },

  "gateway": {
    "enabled": true,
    "url": "ws://127.0.0.1:9555/ai-ws/register"
  },

  "prompt": {
    "role": "role.md"
  },

  "mcp": {
    "enabled": true,
    "servers": ["filesystem"]
  },

  "skills": {
    "enabled": true,
    "active": ["speech_to_text"]
  },

  "filesystem": {
    "workspace_dirs": ["/path/to/your/workspace"]
  },

  "default_wake_mode": "strict"
}
```

### Field Reference

#### Basic Info

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Unique Agent identifier (should match directory name) |
| `agent_name` | Yes | Agent display name |
| `agent_type` | No | Type: `general` / `coder` / `pm` / `qa` |
| `description` | No | Agent description |
| `capabilities` | No | Capability tag list |

#### `model` Model Configuration

| Field | Description |
|-------|-------------|
| `provider` | `openai_compat` (OpenAI-compatible) / `openai` / `anthropic` |
| `api_key` | API key |
| `base_url` | API endpoint (only needed for `openai_compat`) |
| `model_name` | Model name |
| `token_max` | Context token limit |
| `temperature` | Randomness (0-1) |
| `is_think` | Whether this is a reasoning model (affects prompts and wait logic) |
| `is_image` | Whether image input is supported |
| `is_video` | Whether video input is supported |
| `_card` | Source model card name (for reference only, not functional) |

#### `tool_levels` Tool Levels (optional)

All tools are `extended` level by default. Some tools need to be explicitly upgraded to `core` to appear in the system prompt:

```json
"tool_levels": {
  "chat_account": "core",
  "agent_factory": "core"
}
```

#### `collaboration` Collaboration Configuration

| Field | Description |
|-------|-------------|
| `enabled` | Whether collaboration mode is enabled |
| `role` | Role: `pm` / `developer` / `qa` / `worker` |

#### `group_chat` Group Chat Configuration

| Field | Description |
|-------|-------------|
| `enabled` | Whether group chat is enabled |
| `email` | ChatPro login email |
| `password` | Login password |
| `groups` | List of group IDs to join |

#### `web_server` Web Server Configuration

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | Yes | Must be `true`; each Agent needs its own Web Server |
| `port` | No | Listening port. **If unset, the system auto-assigns a free port (recommended)**; when set manually, ensure no conflict with other Agents |

#### `gateway` Gateway Configuration

> **The entire `gateway` field can be omitted**; the system will automatically inject defaults at startup (`enabled: true`, `url` read from system config).

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | No | Whether to connect to Gateway (if disabled, the Web UI cannot communicate with this Agent); defaults to `true` |
| `url` | No | Gateway WebSocket address; **auto-injected by system if omitted**, no manual configuration needed |

#### `mcp` MCP Tool Server Configuration

| Field | Description |
|-------|-------------|
| `enabled` | Whether MCP is enabled |
| `servers` | List of MCP servers to enable (empty enables all) |

#### `skills` Skill Configuration

| Field | Description |
|-------|-------------|
| `enabled` | Whether the Skill system is enabled |
| `active` | List of active Skill names |

#### `filesystem` Filesystem Configuration

| Field | Description |
|-------|-------------|
| `workspace_dirs` | List of allowed working directories |

#### `default_wake_mode`

- `strict` (default): Only responds to messages @-mentioning this agent in group chat
- `normal`: Responds to all messages in group chat

---

## 4. Model Card Management

### What is a Model Card?

A Model Card is a pre-configured model parameter template containing a complete configuration including provider, model_name, base_url, token_max, etc. Using a model card avoids manually configuring complex model parameters, allowing you to quickly assign a model to an Agent.

### 4.1 List All Model Cards

```
agent_factory.list_model_cards()
```

**Example response**:
```json
{
  "success": true,
  "count": 5,
  "cards": [
    {
      "name": "kimi_k2_5",
      "title": "Kimi k2.5",
      "provider": "openai_compat",
      "model_name": "moonshot-v1-128k",
      "token_max": 128000
    },
    {
      "name": "deepseek_chat",
      "title": "DeepSeek Chat",
      "provider": "openai_compat",
      "model_name": "deepseek-chat",
      "token_max": 128000
    },
    ...
  ]
}
```

---

### 4.2 Get Model Card Details

```
agent_factory.get_model_card(
    card_name="kimi_k2_5"   # Model card name
)
```

**Example response**:
```json
{
  "success": true,
  "name": "kimi_k2_5",
  "card": {
    "name": "kimi_k2_5",
    "title": "Kimi k2.5",
    "provider": "openai_compat",
    "api_key": "",
    "base_url": "https://api.moonshot.cn/v1",
    "model_name": "moonshot-v1-128k",
    "token_max": 128000,
    "temperature": 0.7,
    "is_think": false,
    "is_image": false,
    "is_video": false
  }
}
```

**Field reference**:

| Field | Description |
|-------|-------------|
| `name` | Unique model card identifier |
| `title` | Display name |
| `provider` | Model provider (openai_compat, openai, anthropic) |
| `api_key` | API key (empty string means read from environment variable) |
| `base_url` | API endpoint |
| `model_name` | Model name |
| `token_max` | Token limit |
| `temperature` | Temperature (randomness, 0-1) |
| `is_think` | Whether this is a reasoning model |
| `is_image` | Whether image input is supported |
| `is_video` | Whether video input is supported |

---

### 4.3 Assign a Model Card to an Agent

```
agent_factory.assign_model_card(
    dir_name="my_bot",      # Agent directory name
    card_name="kimi_k2_5"   # Model card to assign
)
```

**Effect**:
- Automatically writes the model card config into the `model` field of `agents/my_bot/config.json`
- The Agent will use the new model config after restart

**Example response**:
```json
{
  "success": true,
  "message": "Model card 'kimi_k2_5' assigned to agent 'my_bot'. Restart required."
}
```

**Note**: The Agent must be restarted after assigning a model card:
```
agent_factory.restart_agent(
    agent_name="my_bot"
)
```

---

### 4.4 Create a Custom Model Card

```
agent_factory.create_model_card(
    card_name="my_custom_model",
    card_config={
        "title": "My Custom Model",
        "provider": "openai_compat",
        "api_key": "",
        "base_url": "https://api.example.com/v1",
        "model_name": "custom-model",
        "token_max": 100000,
        "temperature": 0.5,
        "is_think": False,
        "is_image": False,
        "is_video": False
    }
)
```

**Example response**:
```json
{
  "success": true,
  "message": "Model card 'my_custom_model' created successfully."
}
```

---

### 4.5 Common Model Cards

| Card Name | Model | Token Limit | Features |
|-----------|-------|-------------|---------|
| `kimi_k2_5` | Kimi k2.5 (moonshot-v1-128k) | 128K | Long context |
| `deepseek_chat` | DeepSeek Chat | 128K | General conversation |
| `deepseek_reasoner` | DeepSeek Reasoner (v3) | 64K | Reasoning model |
| `GLM-5` | Zhipu GLM-5 | 128K | Chinese domestic model |
| `qwen3.5-plus` | Qwen 3.5+ | 128K | Alibaba Cloud |

---

### 4.6 Usage Example

**Scenario: Quickly configure Kimi model for a new Agent**

```
# 1. Create Agent
agent_factory.create_agent(
    dir_name="kimi_bot",
    agent_name="Kimi Assistant",
    agent_type="general"
)

# 2. View Kimi model card details
agent_factory.get_model_card(
    card_name="kimi_k2_5"
)

# 3. Assign model card
agent_factory.assign_model_card(
    dir_name="kimi_bot",
    card_name="kimi_k2_5"
)

# 4. Configure account and group (edit config.json)
# 5. Start Agent
agent_factory.start_agent(
    agent_name="kimi_bot"
)
```

---

## 5. role.md Writing Guide

### Basic Structure

```markdown
# Role: Agent Name

Your group chat alias is XXX.
You are a professional YYY, skilled at ZZZ.

## Core Competencies
- Competency 1
- Competency 2

## Workflow
1. Step 1
2. Step 2

## Behavioral Guidelines
- Guideline 1
- Guideline 2

## Collaboration Standards (collaboration agents only)
- How to collaborate with other agents
- @mention rules
```

### Example: Developer Role

```markdown
# Role: Senior Software Engineer

Your group chat alias is Developer.
You are an experienced full-stack software engineer, skilled in Python, JavaScript, and system design.

## Core Competencies
- Write high-quality code
- Debug and fix bugs
- Code review and refactoring

## Workflow
1. Receive tasks assigned by PM (via @Developer)
2. Analyze requirements and plan the implementation
3. Write code and self-test
4. Commit code and notify QA to test

## Behavioral Guidelines
- Always use `filesystem` tools to read/write files
- Run tests after each modification to verify
- Proactively @-mention QA after code is complete

## Collaboration Standards
- Respond immediately when @Developer is mentioned
- Use `im.send_group_message` to notify QA after task completion
- Report blockers to PM promptly
```

---

# Part 3: Architecture Internals

## 6. Agent Process Isolation

### Key Conclusion
**Each Agent has its own independent ChatPro Bridge instance — there is no "global singleton shared account" issue!**

### Process Architecture

```
Launcher (launcher.py)
  │
  ├─→ AgentProcess(alice)  - PID: 1001
  │    └─→ python -m opensquad.agents_boot --agent-dir agents/alice
  │         └─→ ChatProBridge(email="alice@ai")
  │
  ├─→ AgentProcess(bob)    - PID: 1002
  │    └─→ python -m opensquad.agents_boot --agent-dir agents/bob
  │         └─→ ChatProBridge(email="bob@ai")
  │
  └─→ AgentProcess(pm)     - PID: 1003
       └─→ python -m opensquad.agents_boot --agent-dir agents/pm
            └─→ ChatProBridge(email="pm@ai")
```

**Key characteristics**:
- Each Agent is an independent process (`subprocess.Popen`)
- Independent Python interpreter and memory space
- Independent `config.json` and `data/` directory
- Independent ChatPro login token and WebSocket connection

### Bridge Instance Creation Flow

**agents_boot.py startup flow**:
```python
# 1. Load config
config = load_config_from("agents/{name}/config.json")

# 2. Create independent Bridge instance
from opensquad.bridge import create_bridge
agent_bridge = create_bridge(config)  # <- each process creates independently

# 3. Login with account from config
if agent_bridge.login():  # <- uses config.group_chat.email/password
    # 4. Replace process-level singleton (affects only current process)
    import opensquad.bridge as bridge_module
    bridge_module.bridge = agent_bridge  # <- process-level replacement
    
    # 5. Join configured groups
    for group_id in config.group_chat.groups:
        agent_bridge.join_group_api(group_id)
    
    # 6. Start WebSocket listener
    asyncio.create_task(agent_bridge.connect_ws())
```

**Key code locations**:
- `opensquad/agents_boot.py:390-412` - Bridge creation and login
- `opensquad/bridge.py:703-724` - `create_bridge()` function
- `opensquad/bridge.py:52-93` - `login()` method

---

## 7. IM Account Management Internals

### Account Configuration Methods

**Recommended: config.json configuration (auto-login on startup)**

```json
{
  "group_chat": {
    "enabled": true,
    "email": "agent@ai",
    "password": "password",
    "base_url": "http://localhost:9555",
    "groups": ["g12345"]
  }
}
```

**Runtime login (not recommended, debug only)**:
```
chat_account.login(
    email="agent@ai",
    password="password"
)
```

### Common Misconceptions

❌ **Misconception**: All Agents share one global Bridge instance  
✅ **Reality**: Each Agent process has its own independent Bridge instance

❌ **Misconception**: You need to manually call `chat_account.login` to log in  
✅ **Reality**: Configuring `group_chat` enables auto-login

❌ **Misconception**: One account can be used by multiple Agents simultaneously  
✅ **Reality**: Only one Agent can log in to an account at a time (a later login kicks out the earlier one)

---

# Part 4: Troubleshooting

## 8. Common Issues

### Issue 1: Agent starts but cannot receive group messages

**Symptoms**:
- Agent starts successfully
- `agent_factory.list_agents` shows `running`
- Sending messages in the group gets no response

**Diagnosis steps**:

1. **Check account login status**:
```
chat_account.list_groups(
    email="agent@ai",
    password="password"
)
```

If this returns an error, there is a problem with the account configuration.

2. **Check group configuration**:
```bash
# Check the groups field in config.json
cat agents/my_bot/config.json | grep -A 5 "group_chat"
```

Confirm the `groups` list contains the correct group ID.

3. **Check Agent logs**:
```bash
tail -f agents/my_bot/data/agent.log
```

Look for WebSocket connection errors or message processing exceptions.

---

### Issue 2: Tool call failures

**Symptoms**:
- Agent tries to use a tool but gets `Tool not found`
- Or the tool execution throws an error

**Diagnosis steps**:

1. **Check if the tool is enabled in config.json**:
```json
{
  "tools": ["system", "filesystem", "im"]
}
```

If the tool is not in the list, add it and restart the Agent.

2. **Check tool permissions**:
Some tools require special permissions, for example:
- `agent_factory`: requires access to the `agents/` directory
- `chat_account`: requires network access

3. **Reload plugins** (if config was modified):
```
agent_setup.reload_plugins()
```

---

### Issue 3: Multiple Agents competing for the same account

**Symptoms**:
- Two Agents are configured with the same email
- The later-started Agent works normally; the earlier-started one loses connection

**Cause**:
ChatPro only allows one client per account; a later login kicks out the earlier one.

**Solution**:
Register a separate account for each Agent; never share an email.

---

### Issue 4: Agent fails to start

**Symptoms**:
- `agent_factory.start_agent` returns failure
- Or the Agent starts and immediately exits

**Diagnosis steps**:

1. **Check config.json syntax**:
```bash
python -m json.tool agents/my_bot/config.json
```

If this returns an error, there is a JSON format problem.

2. **Check required fields**:
Ensure `config.json` contains:
- `agent_id`
- `agent_name`
- `model.api_protocol`, `model.base_url`, `model.model_name`
- `group_chat` (`enabled: true`, with non-empty `email`, `password`, `groups` array)
- `gateway`: **The entire field can be omitted**; the system auto-injects at startup (`enabled: true`, `url` auto-read from system config)

`web_server` is optional; the system auto-assigns a port if omitted.

3. **Start manually for debugging**:
```bash
cd agents/my_bot
python -m opensquad.agents_boot --agent-dir .
```

View detailed error messages from the startup process.

---

## 9. Best Practices

### Configuration Management

1. **Use environment variables for sensitive info**:
```json
{
  "model": {
    "api_key": "${OPENAI_API_KEY}"
  },
  "group_chat": {
    "password": "${AGENT_PASSWORD}"
  }
}
```

2. **Create separate configs for different environments**:
```
agents/my_bot/
├── config.json          # Production
├── config.dev.json      # Development
└── config.test.json     # Testing
```

3. **Version control**:
- Commit `config.json.template` (without keys)
- Ignore `config.json` (with keys)

---

### Collaboration Design

1. **Clear role boundaries**:
- PM: requirements analysis, task assignment, progress tracking
- Developer: write code, self-test, commit
- QA: testing, defect reporting

2. **Use @-mentions for task handoff**:
```markdown
@Developer Please implement the user login feature with the following requirements: ...
```

3. **Proactively notify after task completion**:
```
im.send_group_message(
    group_id="g12345",
    content="@QA Login feature is complete, please test"
)
```

---

### Tool Usage

1. **Prefer high-level tools**:
- Use `agent_factory` instead of manually creating files
- Use `chat_account` instead of calling APIs manually

2. **Minimize toolchain**:
Only enable required tools; too many tools make it harder for the model to choose.

3. **Validate tools**:
After each tool call, check the return value to confirm the operation succeeded.

---

## 10. Quick Tool Reference

### chat_account Tools

| Function | Description | Key Parameters |
|----------|-------------|----------------|
| `register_account` | Register new account | email, password, name |
| `create_group` | Create group | email, password, group_name |
| `join_group` | Join group | email, password, group_id |
| `list_groups` | List joined groups | email, password |
| `login` | Login (not recommended) | email, password |

### agent_factory Tools

| Function | Description | Key Parameters |
|----------|-------------|----------------|
| `create_agent` | Create Agent directory | dir_name, agent_name, agent_type |
| `start_agent` | Start Agent | agent_name |
| `stop_agent` | Stop Agent | agent_name |
| `restart_agent` | Restart Agent | agent_name |
| `list_agents` | List all Agents | (none) |
| `delete_agent` | Delete Agent | agent_name |
| `list_model_cards` | List all model cards | (none) |
| `get_model_card` | Get model card details | card_name |
| `assign_model_card` | Assign model card to Agent | dir_name, card_name |
| `create_model_card` | Create custom model card | card_name, card_config |

### im Tools

| Function | Description | Key Parameters |
|----------|-------------|----------------|
| `send_group_message` | Send group message | group_id, content |
| `get_group_history` | Get group message history | group_id, limit |

---

# Appendix: Complete Creation Script

Below is a complete Python script example demonstrating how to automate Agent creation and configuration.

```python
#!/usr/bin/env python3
"""
Automate Agent creation and configuration
Usage: python create_agent.py --name mybot --email mybot@ai
"""

import argparse
import json
from pathlib import Path

def create_agent(name: str, email: str, password: str, group_id: str = None):
    """Automate Agent creation"""
    
    # 1. Register account
    print(f"[1/5] Registering account {email}...")
    # Call chat_account.register_account tool
    
    # 2. Create or join group
    if group_id:
        print(f"[2/5] Joining group {group_id}...")
        # Call chat_account.join_group tool
    else:
        print(f"[2/5] Creating new group...")
        # Call chat_account.create_group tool
        # group_id = returned group ID
    
    # 3. Create Agent directory
    print(f"[3/5] Creating Agent directory...")
    # Call agent_factory.create_agent tool
    
    # 4. Configure Agent
    print(f"[4/5] Configuring Agent...")
    config_path = Path(f"agents/{name}/config.json")
    
    config = json.loads(config_path.read_text())
    config["group_chat"] = {
        "enabled": True,
        "email": email,
        "password": password,
        "base_url": "http://localhost:9555",
        "groups": [group_id]
    }
    
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    
    # 5. Start Agent
    print(f"[5/5] Starting Agent...")
    # Call agent_factory.start_agent tool
    
    print(f"Agent {name} created successfully!")
    print(f"   - Email: {email}")
    print(f"   - Group: {group_id}")
    print(f"   - Directory: agents/{name}/")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automate Agent creation")
    parser.add_argument("--name", required=True, help="Agent name")
    parser.add_argument("--email", required=True, help="Account email")
    parser.add_argument("--password", default="Agent@2026", help="Account password")
    parser.add_argument("--group", help="Group ID (optional)")
    
    args = parser.parse_args()
    create_agent(args.name, args.email, args.password, args.group)
```

---

## Summary

This Skill covers all key knowledge for OpenSquad Agent management:

- Five-step Agent creation
- Configuration file reference (config.json, role.md)
- Model card management (list, assign, create)
- Architecture internals (process isolation, Bridge instances)
- Troubleshooting (common issues and solutions)
- Best practices (config management, collaboration design, tool usage)
- Complete examples (single Agent, collaboration team)

**Next steps**:
- Create your first Agent in practice
- Use model cards to quickly configure a model
- Try collaboration scenarios
- Read the [plugin_dev Skill](../plugin_dev/) to learn custom plugin development
