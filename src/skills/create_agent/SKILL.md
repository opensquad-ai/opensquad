---
name: create_agent
description: Create an Agent that can send/receive messages and hold normal conversations in a specified group. Covers the full flow: register IM account → create Agent directory → assign a model card → write role.md → configure config.json (note: tools must include "im") → join the group → start.
version: 1.0.0
author: OpenSquad
---

## Purpose
Create an Agent that can send/receive messages and hold normal conversations in a specified group.

## Prerequisites
- Know the target group's `group_id` (e.g. `gjxwzp`)
- Pick a model card (e.g. `deepseek-flash`)
- Launcher is running and the `agent_factory` and `chat_account` tools are available

## Steps

### Step 1: Register an IM account
```
chat_account.register_account(email="agentXXX@ai", password="...", name="AgentXXX")
```
- Recommended email format: `agentXXX@ai`; keep the password simple and consistent.

### Step 2: Create the Agent directory
```
agent_factory.create_agent(dir_name="agentXXX", agent_name="AgentXXX")
```
- `dir_name` uses lowercase letters, digits, and underscores.
- This generates a default `config.json`, `role.md`, and `mcp_config.json`.

### Step 3: Assign a model card
```
agent_factory.assign_model_card(dir_name="agentXXX", card_name="deepseek-flash")
```
- First, call `agent_factory.list_model_cards()` to see which model cards are available.

### Step 4: Write the role definition (`role.md`)
```
agent_factory.set_agent_role(dir_name="agentXXX", role_content="...")
```
- Use Markdown; describe the core responsibilities and behavioral rules.
- For a group chat assistant, recommended rules include: be concise and direct, reply only when @-mentioned, stay polite.

### Step 5: Configure `config.json` (bind the account and group)
```
agent_factory.configure_agent(dir_name="agentXXX", config={...})
```
Key fields:
- `group_chat.enabled: true`
- `group_chat.email: "agentXXX@ai"` (the email registered in Step 1)
- `group_chat.password: "..."` (the password set in Step 1)
- `group_chat.groups: ["target-group-id"]`
- `tools: ["system","filesystem","websearch","vision","reminder","im"]` (these basic tools at minimum; `"im"` is **required**, otherwise the Agent cannot connect to the IM channel to send/receive group messages)
- `ui.auto_start_on_boot: true` (start automatically on boot)

Keep other fields at their defaults; in particular, preserve the original `model` and `gateway` configuration.

### Step 6: Make the account join the group
```
chat_account.join_group(group_id="target-group-id", email="agentXXX@ai", password="...")
```

### Step 7: Start the Agent
```
agent_factory.start_agent(dir_name="agentXXX")
```
- After startup, send a welcome message in the group to verify communication is working.

## Verification
After startup, run `agent_factory.list_agents()` and confirm `alive` is `true`.
In the group, @-mentioning AgentXXX should produce a normal reply.

## Troubleshooting
- **Model card mismatch**: First call `list_model_cards()` to confirm which cards are available.
- **Not alive after startup**: Check the `config.json` format and that the `gateway` section is complete.
- **Agent doesn't receive group messages**: Verify `chat_account.join_group` succeeded and the `group_id` is correct.
- **Agent is online but doesn't reply**: Check whether the `tools` list includes `"im"`. A missing `im` tool prevents the Agent from connecting to the IM channel and therefore from sending/receiving any messages.
