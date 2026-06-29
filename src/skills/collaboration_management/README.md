# Collaboration Management Skill

## Overview

This Skill provides a complete management workflow and best practices for multi-agent collaboration cards (Collab Cards), including:
- Viewing and using collaboration cards
- Starting a collaboration session (PM)
- Joining a collaboration session (Worker)
- Creating custom collaboration cards
- Collaboration workflow best practices

## Activation

```xml
<tool name="agent_setup">
  <function>install_skill</function>
  <parameters>
    <skill_path>skills/collaboration_management</skill_path>
  </parameters>
</tool>
```

## Contents

### 1. Collaboration Card Basics
- What is a collaboration card
- Collaboration card structure
- Built-in collaboration card descriptions

### 2. Viewing Available Collaboration Cards
- List all collaboration cards
- View suggested roles and descriptions

### 3. Starting a Collaboration Session (PM)
- Select a collaboration card
- Invite members
- Assign tasks
- Manage progress

### 4. Joining a Collaboration Session (Worker)
- Respond to invitations
- Load collaboration card
- Work according to conventions
- Report status

### 5. Creating Custom Collaboration Cards
- Design the collaboration workflow
- Define role responsibilities
- Establish message conventions
- Set behavioral constraints

### 6. Collaboration Role Configuration
- PM configuration
- Developer configuration
- QA configuration
- Reviewer configuration

## Built-in Collaboration Cards

| Collaboration Card | Use Case | Suggested Roles |
|-------|---------|---------|
| **software_dev_team** | Full software development project | pm, developer, qa |
| **general_software_dev_collab** | General software development | pm, dev, qa, devops, reviewer |
| **code_review** | Code review | reviewer, author |
| **distributed_deep_research** | Deep research tasks | pm, researcher, analyst |
| **autonomous_vcs_dev** | Async concurrent Git development | pm, coder, reviewer |
| **quant_backtesting_dev** | Quantitative backtesting system | pm, developer, qa |
| **godot_roguelike_team** | Godot roguelike game development | pm, architect, developer, artist, qa |

## Applicable Scenarios

- ✅ Need to start team collaboration
- ✅ Invited to join a collaboration
- ✅ Need to create a custom collaboration mode
- ✅ Unclear on collaboration workflows and conventions
- ✅ Encountering collaboration-related issues

## Related Documentation

- `doc_cn/agent_management.md` - Complete reference documentation
- `skills/agent_config_management/` - Agent configuration management Skill
- `skills/vcs_collaboration/` - VCS collaboration Skill
- `collab_cards/*.md` - Collaboration card definition files

## Version

- **Version**: 1.0.0
- **Last Updated**: 2026-03-01
- **Author**: OpenSquad
