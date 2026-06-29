# Agent Factory Usage Guide

## Overview

Agent Factory is a powerful plugin that lets you programmatically create,
configure, and manage OpenSquad Agents at runtime.

## Prerequisites

### 1. Enable the plugins

Make sure the agent's `config.json` includes these two plugins:

```json
{
  "tools": [
    "chat_account",
    "agent_factory"
  ],
  "tool_levels": {
    "chat_account": "core",
    "agent_factory": "core"
  }
}
```

### 2. Confirm the Launcher is running

The Launcher listens on `http://127.0.0.1:9600` by default.

## Complete Workflow

### Step 1: Register an OpenSquad Account

Every Agent needs a unique OpenSquad account (like an ID card).

```python
# Use the chat_account plugin to register
result = chat_account.register_account(
    email="mybot@ai",
    password="MyBot@123",
    name="My Bot"
)

# Return example:
# {
#   "success": true,
#   "user_id": "u_12345",
#   "email": "mybot@ai",
#   "name": "My Bot"
# }
```

### Step 2: Create the Agent Directory Structure

```python
# Create the Agent's base directory and default config files
result = agent_factory.create_agent(
    dir_name="my_bot",          # directory name (letters / digits / underscore only)
    agent_name="My Bot",         # display name
    agent_type="general",        # Agent type
    description="A general-purpose assistant"   # description (optional)
)

# Return example:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "message": "Agent created successfully"
# }
```

This creates the following files under `agents/my_bot/`:
- `config.json` — Agent configuration
- `role.md` — role definition
- `mcp_config.json` — MCP configuration

### Step 3: Configure the Agent

```python
# Write the full Agent config (overwrites the default)
config = {
    "agent_id": "mybot-001",
    "agent_name": "My Bot",
    "agent_type": "general",
    "description": "A general-purpose assistant",

    # Model config
    "model": {
        "provider": "openai",
        "api_key": "sk-xxx",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "token_max": 128000,
        "temperature": 0.3,
        "tool_call_mode": "auto",  # auto | native | xml
        "tool_filter": "high"      # high (97 tools) | all (124) | baseline (57)
    },

    # Tool set
    "tools": ["system", "filesystem", "websearch", "im", "agent_setup"],

    # Group-chat config (binds to the registered account)
    "group_chat": {
        "enabled": true,
        "email": "mybot@ai",      # email registered in step 1
        "password": "MyBot@123",  # password registered in step 1
        "groups": ["g813q4"]       # group IDs to join
    },

    # Web server
    "web_server": {
        "enabled": true,
        "port": 8010
    },

    # Gateway connection
    "gateway": {
        "enabled": true,
        "url": "ws://127.0.0.1:9555/ai-ws/register"
    },

    # Collaboration mode
    "collaboration": {
        "enabled": true,
        "role": "worker"
    }
}

result = agent_factory.configure_agent(
    dir_name="my_bot",
    config=config
)

# Return example:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "message": "Configuration saved"
# }
```

### Step 4: Set the Agent Role

```python
# Define the Agent's identity, specialty, and behavior
role_content = """
# My Bot

## Identity
You are a general-purpose assistant focused on helping users with everyday tasks.

## Specialty
- Information lookup
- Task management
- Question answering

## Code of conduct
- Friendly and professional
- Concise, clear answers
- Ask for clarification when uncertain
"""

result = agent_factory.set_agent_role(
    dir_name="my_bot",
    role_content=role_content
)

# Return example:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "message": "Role file saved"
# }
```

### Step 5: Start the Agent

```python
# Start the Agent process
result = agent_factory.start_agent(dir_name="my_bot")

# Return example:
# {
#   "success": true,
#   "dir_name": "my_bot",
#   "pid": 12345,
#   "port": 8010,
#   "message": "Agent started"
# }
```

### Managing Agents

#### List all Agents

```python
result = agent_factory.list_agents()

# Return example:
# {
#   "success": true,
#   "count": 3,
#   "agents": [
#     {
#       "dir_name": "my_bot",
#       "agent_id": "mybot-001",
#       "agent_name": "My Bot",
#       "alive": true,
#       "pid": 12345,
#       "port": 8010
#     },
#     ...
#   ]
# }
```

#### Stop an Agent

```python
result = agent_factory.stop_agent(dir_name="my_bot")
```

#### Restart an Agent

```python
# After changing config, restart for the new config to take effect
result = agent_factory.restart_agent(dir_name="my_bot")
```

## Configuration Reference

### Model config

#### `tool_call_mode` (tool-call mode)
- `auto` — auto-detect (recommended)
- `native` — force native Function Calling
- `xml` — force XML format

#### `tool_filter` (tool filter level)
- `high` — 97 high-frequency tools (recommended)
- `all` — all 124 tools
- `baseline` — 57 basic tools

### Group chat config

#### Create a new group

```python
# Use the chat_account plugin to create a group
result = chat_account.create_group(
    name="My Team",
    description="Team collaboration group",
    is_private=False,
    email="mybot@ai",      # creator account
    password="MyBot@123"
)

# Returns the group ID, used in group_chat.groups
```

#### Join an existing group

```python
result = chat_account.join_group(
    group_id="g813q4",
    email="mybot@ai",
    password="MyBot@123"
)
```

## Complete Example

```python
# 1. Register the account
register_result = chat_account.register_account(
    email="assistant@ai",
    password="Assistant@123",
    name="Smart Assistant"
)

# 2. Create the Agent
create_result = agent_factory.create_agent(
    dir_name="assistant",
    agent_name="Smart Assistant"
)

# 3. Configure the Agent
config = {
    "agent_id": "assistant-001",
    "agent_name": "Smart Assistant",
    "model": {
        "provider": "openai",
        "api_key": "sk-xxx",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "token_max": 128000,
        "temperature": 0.3,
        "tool_call_mode": "auto",
        "tool_filter": "high"
    },
    "tools": ["system", "filesystem", "websearch", "im"],
    "group_chat": {
        "enabled": true,
        "email": "assistant@ai",
        "password": "Assistant@123",
        "groups": ["g813q4"]
    },
    "web_server": {"enabled": true, "port": 8011},
    "gateway": {
        "enabled": true,
        "url": "ws://127.0.0.1:9555/ai-ws/register"
    }
}

configure_result = agent_factory.configure_agent(
    dir_name="assistant",
    config=config
)

# 4. Set the role
role = """
# Smart Assistant

You are a friendly general-purpose assistant who helps users with a variety of tasks.
"""

role_result = agent_factory.set_agent_role(
    dir_name="assistant",
    role_content=role
)

# 5. Start the Agent
start_result = agent_factory.start_agent(dir_name="assistant")

print(f"Agent started: PID {start_result['pid']}, Port {start_result['port']}")
```

## Troubleshooting

### Plugin not loaded

Make sure the agent's `config.json` has:
1. `"chat_account"` and `"agent_factory"` in the `tools` array
2. Their level set in `tool_levels`

Takes effect after a restart:
```bash
curl -X POST http://127.0.0.1:9600/api/agents/{agent_dir}/restart
```

### Account registration failed

- Check that the email format is valid (the `xxx@ai` style is recommended)
- Make sure the email is not already taken
- The password must meet the security requirements

### Agent failed to start

- Check whether the port is in use
- Make sure `config.json` is well-formed
- Check the agent log: `agents/{dir_name}/logs/`

## API Reference

For detailed API documentation, see:
- `plugins/agent_factory/plugin.py`
- `plugins/chat_account/plugin.py`
