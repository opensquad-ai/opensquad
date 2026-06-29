# Agent Factory Plugin

Dynamically create, configure, and manage Agents, including model card management.

**Author:** OpenSquad  
**Version:** 1.1.0  
**Type:** tool

## Feature Overview

### Agent Management
- `create_agent` - Create a new Agent directory
- `configure_agent` - Configure an Agent (model, email, groups, etc.)
- `set_agent_role` - Set the Agent's role
- `start_agent` - Start an Agent
- `stop_agent` - Stop an Agent
- `restart_agent` - Restart an Agent
- `list_agents` - List all Agents and their status

### Model Card Management (new)
- `list_model_cards` - List all available model cards
- `get_model_card` - Get detailed model card configuration
- `assign_model_card` - Assign a model card to an Agent
- `create_model_card` - Create a new model card

## Model Card Usage Examples

### View Available Model Cards

```python
# List all model cards
result = agent_factory__list_model_cards()
print(result)
# Output:
# {
#   "success": true,
#   "count": 8,
#   "cards": [
#     {
#       "name": "deepseek_chat",
#       "title": "DeepSeek Chat",
#       "provider": "openai_compat",
#       "model_name": "deepseek-chat",
#       "base_url": "https://api.deepseek.com/v1",
#       "token_max": 64000,
#       "temperature": 0.7
#     },
#     {
#       "name": "GLM-5",
#       "title": "GLM-5",
#       "provider": "openai_compat",
#       "model_name": "glm-5-plus",
#       ...
#     },
#     ...
#   ]
# }
```

### View Model Card Details

```python
# Get the full configuration of a specific model card
result = agent_factory__get_model_card(card_name="deepseek_chat")
print(result)
# Output:
# {
#   "success": true,
#   "name": "deepseek_chat",
#   "card": {
#     "name": "deepseek_chat",
#     "title": "DeepSeek Chat",
#     "provider": "openai_compat",
#     "api_key": "sk-xxx",
#     "base_url": "https://api.deepseek.com",
#     "model_name": "deepseek-chat",
#     "token_max": 64000,
#     "temperature": 0.7,
#     "tool_call_mode": "native",
#     "is_think": false,
#     "is_image": false,
#     "is_video": false
#   }
# }
```

### Assign a Model Card to an Agent

```python
# Assign a model card to an existing Agent
result = agent_factory__assign_model_card(
    dir_name="coder",
    card_name="deepseek_chat"
)
print(result)
# Output:
# {
#   "success": true,
#   "message": "Model card 'deepseek_chat' has been assigned to Agent 'coder'. Restart the Agent for the change to take effect."
# }

# Restart the Agent to apply the configuration
agent_factory__restart_agent(dir_name="coder")
```

### Create a New Model Card

```python
# Create a custom model card
result = agent_factory__create_model_card(
    card_name="my_gpt4",
    title="My GPT-4 Configuration",
    provider="openai",
    model_name="gpt-4o",
    api_key="sk-xxx",
    token_max=128000,
    temperature=0.3,
    is_image=True
)
print(result)
# Output:
# {
#   "success": true,
#   "message": "Model card 'my_gpt4' created successfully."
# }
```

## Complete Workflow Example

### Create a New Agent Using a Model Card

```python
# 1. Register a ChatPro account
chat_account__register_account(
    email="new_agent@ai",
    password="password123",
    name="New Agent"
)

# 2. Create the Agent directory
agent_factory__create_agent(
    dir_name="new_agent",
    agent_name="New Agent",
    description="Test Agent"
)

# 3. Assign a model card (instead of manually configuring model parameters)
agent_factory__assign_model_card(
    dir_name="new_agent",
    card_name="deepseek_chat"
)

# 4. Configure other options (groups, tools, etc.)
agent_factory__configure_agent(
    dir_name="new_agent",
    config={
        "group_chat": {
            "enabled": True,
            "email": "new_agent@ai",
            "password": "password123",
            "groups": ["g1"]
        },
        "tools": {
            "level": "extended"
        }
    }
)

# 5. Set the role
agent_factory__set_agent_role(
    dir_name="new_agent",
    role_content="You are a professional programming assistant..."
)

# 6. Start the Agent
agent_factory__start_agent(dir_name="new_agent")
```

## Advantages of Model Cards

1. **Simplified configuration**: No need to memorize complex API endpoints and parameters
2. **Centralized management**: All model configurations are stored in the `model_cards/` directory
3. **Quick switching**: Quickly switch the model used by an Agent via `assign_model_card`
4. **Reusability**: Multiple Agents can share the same model card
5. **Version control**: Model card configurations can be tracked in Git

## Model Card File Location

Model cards are stored in the `model_cards/` directory in JSON format:

```
model_cards/
  ├── deepseek_chat.json
  ├── GLM-5.json
  ├── kimi-k2.5.json
  └── qwen3.5-plus.json
```

## API Endpoints

The plugin interacts with the system through the Launcher HTTP API:

- `GET /api/model-cards` - List all model cards
- `GET /api/model-cards/{name}` - Get model card details
- `PUT /api/model-cards/{name}` - Create/update a model card
- `PUT /api/agents/{name}/model-card` - Assign a model card to an Agent
- `DELETE /api/model-cards/{name}` - Delete a model card

## Related Documentation

- `doc_cn/agent_management.md` - Complete Agent management guide
- `doc_cn/configuration_reference.md` - Configuration field reference
