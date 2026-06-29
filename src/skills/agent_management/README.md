# Agent Management Complete Guide

An all-in-one OpenSquad Agent management Skill covering creation, configuration, deployment, architectural principles, and troubleshooting.

## Main Contents

### Quick Start
- Five steps to create an Agent (account registration → group management → directory creation → configuration → launch)
- Complete example scenarios (single Agent, collaborative team)

### Configuration Reference
- Complete field descriptions for `config.json`
- Guide for writing `role.md`
- Model, tool, and collaboration configuration

### Model Card Management
- List and view model cards
- Assign model cards to Agents
- Create custom model cards
- Common model card introductions (Kimi, DeepSeek, GLM, etc.)

### Architecture Principles
- Agent process isolation mechanism
- IM account management principles
- Bridge instance creation workflow

### Troubleshooting
- Common issues and solutions
- Best practices
- Tool quick reference

## Quick Start

Creating a new Agent takes only 5 steps:

```bash
1. chat_account__register_account(email="mybot@ai", ...)
2. chat_account__create_group(group_name="Workspace", ...)
3. agent_factory__create_agent(dir_name="mybot", ...)
4. Edit agents/mybot/config.json (fill in account and group info)
5. agent_factory__start_agent(agent_name="mybot")
```

## Required Tools

- `chat_account` - ChatPro account and group management (required)
- `agent_factory` - Agent creation and lifecycle management (required)
- `im` - Group chat message sending and receiving (optional, for verification)

## Related Skills

- [plugin_dev](../plugin_dev/) - Develop custom plugins for Agents
- [opensquad_intro](../opensquad_intro/) - OpenSquad platform introduction

## Version

1.0.0 (2026-03-01) - Consolidates agent_creation, agent_config_management, agent_deployment, agent_architecture_im

## Maintainers

OpenSquad
