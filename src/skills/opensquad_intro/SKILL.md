---
name: opensquad_intro
description: When you need to understand how to use OpenSquad, its architectural design, or its features, load this skill to get a system overview and documentation navigation guide.
allowed-tools: filesystem
---

## OpenSquad System Overview

OpenSquad is a multi-agent framework. Each agent runs as an independent process with its own LLM connection, tool set, and session state. Agents communicate via natural language through ChatPro group chats, define structured collaboration workflows through Blueprints, and extend their capabilities through Plugin/Skill mechanisms.

```
               Telegram / Feishu / External API
                             |
                       Gateway (9555)
                      /      |      \
                 WebSocket  HTTP   Proxy
                 (realtime) (REST) (-> Launcher)
                     |       |        |
                Frontend  Backend   Launcher (9600)
                 (9530)   (FastAPI)   |
                                   Agent Process
                                  /    |    \
                              PM    Coder   QA
                            (8003) (8002) (8006)
                               \     |     /
                            ChatPro Group Chat
```

---

## Feature Modules and Documentation Paths

| Feature Module | Key Content | Documentation Path |
|---|---|---|
| System Architecture & Core Modules | Agent Runtime, Session/Memory, Prompt placeholder architecture, built-in tool list, plugin loading mechanism, bootstrap flow | `doc_en/ARCHITECTURE.md` |
| Multi-Agent Collaboration | Blueprint workflows, Task Board, shared workspace `workspace/collab/`, PM monitoring protocol | `doc_en/COLLABORATION.md` |
| Agent Management | Directory structure, config fields, profile/memory layout, lifecycle, plugin/agent creation flow, collab cards | `doc_en/agent_management.md` |
| MCP Dynamic Integration | Add/remove/restart MCP servers at runtime without restarting the agent | `doc_cn/MCP_DYNAMIC_SETUP.md` (Chinese only) |
| External Resource Download Conventions | Landing path conventions when downloading Skills/MCPs/files from the internet; writing to root or framework directories is prohibited | `doc_cn/DOWNLOAD_CONVENTIONS.md` (Chinese only) |
| Group Chat Integration | Group chat architecture, configuration, and messaging flow | `doc_en/group_chat_agent.md` |

---

## Reading Documentation

All documentation paths are relative to the project root. English docs are in `doc_en/` and Chinese docs are in `doc_cn/`.
Use the filesystem tool to read them:

```
filesystem.read_file("doc_en/ARCHITECTURE.md")
filesystem.read_file("doc_en/COLLABORATION.md")
filesystem.read_file("doc_en/agent_management.md")
filesystem.read_file("doc_cn/MCP_DYNAMIC_SETUP.md")
filesystem.read_file("doc_cn/DOWNLOAD_CONVENTIONS.md")
filesystem.read_file("doc_en/group_chat_agent.md")
```

---

## Common Questions → Documentation Quick Reference

| Question | Where to Look |
|---|---|
| What is OpenSquad overall, and what modules does it have? | `doc_en/ARCHITECTURE.md` — Overview & Core Modules |
| How do I write config.json for an Agent? How do I register tools? | `doc_en/agent_management.md` |
| How is the Prompt built? What placeholders are available? | `doc_en/ARCHITECTURE.md` — Prompt Architecture section |
| What built-in tools are there (filesystem, im, system, etc.)? | `doc_en/ARCHITECTURE.md` — Built-in Tools section |
| What plugins exist? What type is each? | `doc_en/ARCHITECTURE.md` — Plugin System section |
| What is the agent startup sequence? | `doc_en/ARCHITECTURE.md` — Agent Bootstrap Flow section |
| How do I get multiple agents to collaborate on a task? | `doc_en/COLLABORATION.md` |
| What is the Task Board? How do I use it? | `doc_en/COLLABORATION.md` — Task Board section |
| How do I add MCP tools to an agent? | `doc_cn/MCP_DYNAMIC_SETUP.md` |
| Where do I put downloaded external Skills/files/tools? | `doc_cn/DOWNLOAD_CONVENTIONS.md` |
| How do I configure group chat integration? | `doc_en/group_chat_agent.md` |

---

## Important Restriction: Core Framework Files Must Not Be Modified Directly

OpenSquad framework core files are infrastructure. **No agent may directly modify them without explicit authorization from the user.**

Protected file scope:

| Path | Description |
|------|-------------|
| `src/opensquad/` | Core framework package, runtime foundation |
| `src/opensquad/agents_boot.py` | Common launcher flow and startup helpers |
| `src/opensquad/launcher.py` | Multi-agent process manager |
| `src/opensquad/system_config.py` | Global port and configuration center |
| `src/opensquad/gateway/backend/app/` | Gateway backend API layer |

**Correct procedure when a suspected framework bug is found:**
1. Explain the issue to the user (file path, line number, root cause analysis)
2. Wait for explicit user authorization
3. Prioritize workarounds at the application layer (plugins, agent config, custom code) rather than directly modifying the framework

Directly modifying the framework, even when it seems correct at the time, may break other agents, interfere with the hot-reload mechanism, or introduce hard-to-trace side effects.
