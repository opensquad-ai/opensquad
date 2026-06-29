# Quick Start Guide

This guide will help you go from zero to having your first AI Agent up and running.

---

## Step 1: Install

### Option A: Local Install (uv)

```bash
# Clone the project
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad

# Install dependencies
uv sync

# Initialize the project
uv run opensquad init
```

### Option B: Docker

```bash
docker-compose up -d
```

Access the Web UI at: `http://localhost:9555`

---

## Step 2: Configure a Model Card

OpenSquad uses model cards to manage LLM configurations.

1. Open the Web UI → **Model Cards** page
2. Click **New Model Card**
3. Choose a provider and fill in the model details:

```json
{
  "name": "deepseek-v4-flash",
  "provider": "openai_compat",
  "model_name": "deepseek-v4-flash",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-your-api-key",
  "token_max": 128000,
  "temperature": 0.7
}
```

> **Tip**: For local models (e.g., Ollama), set `provider` to `"openai_compat"` and `base_url` to `"http://localhost:11434/v1"`.

For detailed configuration, see [Model Cards Guide](model_cards_guide.md).

---

## Step 3: Configure Your Agent

Edit `src/agents/default/config.json`:

```json
{
  "agent_id": "default-001",
  "agent_name": "My Assistant",
  "agent_type": "general",
  "description": "My first AI assistant",
  "model": {
    "provider": "openai_compat",
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model_name": "deepseek-v4-flash",
    "token_max": 128000,
    "temperature": 0.7,
    "tool_call_mode": "auto"
  },
  "tools": [
    "system", "filesystem", "agent_setup", "im",
    "collaboration", "delegate_task", "workspace", "task_watch",
    "websearch", "vision", "mcp_query"
  ],
  "group_chat": { "enabled": false },
  "web_server": { "enabled": true },
  "gateway": { "enabled": true },
  "prompt": { "role": "role.md" },
  "skills": { "enabled": true, "active": [] }
}
```

---

## Step 4: Set the Agent's Role

Edit `src/agents/default/role.md`:

```markdown
# AI Assistant

You are a helpful, friendly AI assistant powered by OpenSquad.

## Guidelines

- Provide clear, accurate responses
- Ask clarifying questions when needed
- Use tools when it helps the task
```

---

## Step 5: Start OpenSquad

```bash
# Start all services
uv run opensquad start

# Check status
uv run opensquad status
```

---

## Step 6: Enable Optional Skills

Edit `config.json` and add skills to the `active` list:

```json
{
  "skills": {
    "enabled": true,
    "active": ["data-analysis", "code_reviewer_lite"]
  }
}
```

The Agent will auto-load these skills on startup. For more skills, see [Skill Development Guide](skill_development_guide.md).

---

## Step 7: Start Chatting!

Open `http://localhost:9555` in your browser and start a conversation with your Agent.

---

## What's Next?

| Topic | Document |
|------|------|
| All configuration options | [configuration_reference.md](configuration_reference.md) |
| Deployment to production | [deployment_guide.md](deployment_guide.md) |
| IM platform integration | [guide-platform-plugin.md](guide-platform-plugin.md) |
| Developing custom tools | [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md#custom-tool-development) |
| Working with skills | [skill_development_guide.md](skill_development_guide.md) |
| Multi-Agent collaboration | [COLLABORATION.md](COLLABORATION.md) |

---

## Troubleshooting

### "Agent not responding"

1. Check the LLM API key is valid: `opensquad doctor`
2. View Agent logs: `opensquad logs -s agent -n 50`
3. Verify the model card configuration is correct

### "Port already in use"

```bash
# Find and kill the process on the port
# Windows: netstat -ano | findstr :9555
# Linux: lsof -i :9555
opensquad stop
opensquad start
```

### Context Compression

When conversations get long, enable context compression in `system_config.json`:

```json
{
  "context_compression": {
    "trigger_threshold": 0.75,
    "keep_recent_fraction": 0.1,
    "keep_recent_rounds": 2,
    "summary_max_tokens": 4000,
    "conv_text_budget_chars": 24000
  }
}
```
