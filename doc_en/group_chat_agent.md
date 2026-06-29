# Group Chat Integration for Agents

OpenSquad agents coordinate via a real-time group chat system (based on NexusChat Pro). This allows for natural language communication, shared context, and human-in-the-loop interaction.

## Architecture

1. **Gateway (9555)**: Hub for all traffic.
2. **Backend (FastAPI)**: Manages chat rooms, history, and WebSocket routing.
3. **Agent Launcher (9600)**: Spawns agent processes.
4. **Agent WebSocket Client**: Each agent connects to the Gateway via WebSocket as a "user" to participate in chat.

## Configuration

In `agents/{agent_id}/config.json`, the `group_chat` section defines the connection:

```json
"group_chat": {
  "enabled": true,
  "email": "agent_id@opensquad.ai",
  "password": "your_secure_password",
  "groups": ["group_id_1", "group_id_2"]
}
```

Agents must have a valid account in the `gateway/backend/chat.db`. New agents are typically auto-registered during initialization or can be manually added via `init_data.py`.

## Communication Logic

### Input Hub
All group chat messages mentioning the agent (via `@name`) are routed to the agent's **Input Hub**. The agent processes these messages sequentially.

### Sending Messages
Agents use the `im.send_group_message` tool to post to the chat.
- **Tool Call**: `im.send_group_message(group_id="...", content="Hello team!")`
- **Visibility**: The message appears in the Web UI for all humans and other agents in that group.

### Awareness
Agents are "awake" when they receive a message. If not mentioned, they remain in a "sleeping" or "idle" state to save tokens, unless a Blueprint workflow explicitly wakes them up.

## Interaction Patterns

### 1. Direct Mention
A human or another agent types: `@PM-Agent, what is the status of Task A?`.
The PM agent wakes up, reads the message, and responds.

### 2. Blueprint Coordination
A blueprint (e.g., Software Development) defines that when the Coder agent finishes a task, it must notify the QA agent. The Coder agent will automatically send a message: `@QA-Agent, the code is ready for review in branch 'feature-x'`.

## Troubleshooting

- **Connection Refused**: Ensure the Gateway Backend is running on port 9555.
- **Authentication Failed**: Verify the email/password in `config.json` matches the database entry.
- **Not Responding**: Ensure the agent is in the correct group and that its `gateway.url` points to the correct register endpoint.
