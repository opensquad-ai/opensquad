# OpenSquad 文档索引

> 文档已按语言拆分：中文在 `doc_cn/`，英文在 `doc_en/`。
> 跨语言 / 维护者向的内容在 [`docs/README.md`](../docs/README.md)。

---

## 快速入门

| 文档 | 说明 |
|------|------|
| [getting_started.md](getting_started.md) | 从零搭建 Agent，完成第一个任务 |

---

## 架构与总览

| 文档（中文） | 文档（English） | 说明 |
|--------------|------------------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | [ARCHITECTURE.md](../doc_en/ARCHITECTURE.md) | 系统架构、模块关系、数据流与启动流程 |
| [architecture-paths.md](architecture-paths.md) | [architecture-paths.md](../doc_en/architecture-paths.md) | 安装目录 vs 工作区双目录设计原则 |
| [CONTEXT_FLOW.md](CONTEXT_FLOW.md) | [CONTEXT_FLOW.md](../doc_en/CONTEXT_FLOW.md) | LLM 上下文注入架构与消息输入路径 |

---

## 部署与配置

| 文档（中文） | 文档（English） | 说明 |
|--------------|------------------|------|
| [deployment_guide.md](deployment_guide.md) | [deployment_guide.md](../doc_en/deployment_guide.md) | 部署指南（一键脚本 / uv / pip / Docker 四种方式） |
| [desktop_build.md](desktop_build.md) | [desktop_build.md](../doc_en/desktop_build.md) | 桌面应用从源码构建指南（Electron + Vite + PyInstaller） |
| [configuration_reference.md](configuration_reference.md) | [configuration_reference.md](../doc_en/configuration_reference.md) | 系统级配置项参考手册 |

---

## Agent 配置

| 文档（中文） | 文档（English） | 说明 |
|--------------|------------------|------|
| [agent_management.md](agent_management.md) | [agent_management.md](../doc_en/agent_management.md) | Agent 配置完全指南（目录结构、config.json、角色、协作卡） |
| [model_cards_guide.md](model_cards_guide.md) | [model_cards_guide.md](../doc_en/model_cards_guide.md) | 模型卡配置与能力查询 |
| [role_card_guide.md](role_card_guide.md) | [role_card_guide.md](../doc_en/role_card_guide.md) | 角色卡开发指南 |
| [agent_factory_guide.md](agent_factory_guide.md) | - | Agent 工厂插件（动态创建 Agent） |
| [agent_management_skills_guide.md](agent_management_skills_guide.md) | - | Agent 管理的 Skills 集成 |
| [group_chat_agent.md](group_chat_agent.md) | [group_chat_agent.md](../doc_en/group_chat_agent.md) | Agent 群聊集成 |

---

## 开发指南

| 文档（中文） | 文档（English） | 说明 |
|--------------|------------------|------|
| [skill_development_guide.md](skill_development_guide.md) | [skill_development_guide.md](../doc_en/skill_development_guide.md) | 技能系统开发指南 |
| [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) | [PLUGIN_DEVELOPMENT.md](../doc_en/PLUGIN_DEVELOPMENT.md) | 插件系统开发与配置指南（含自定义工具开发） |
| [PLUGIN_STORE_PUBLISHING.md](PLUGIN_STORE_PUBLISHING.md) | [PLUGIN_STORE_PUBLISHING.md](../doc_en/PLUGIN_STORE_PUBLISHING.md) | 插件商店提交与发布流程 |
| - | [PLUGIN_ECOSYSTEM.md](../doc_en/PLUGIN_ECOSYSTEM.md) | 插件生态总览（tools / hooks / platforms / services） |
| [guide-platform-plugin.md](guide-platform-plugin.md) | [guide-platform-plugin.md](../doc_en/guide-platform-plugin.md) | IM 平台适配器插件开发指南（飞书/Telegram/QQ） |
| [ServicePlugin_Guide.md](ServicePlugin_Guide.md) | - | ServicePlugin 使用指南 |
| [MCP_DYNAMIC_SETUP.md](MCP_DYNAMIC_SETUP.md) | - | Agent 动态 MCP 服务管理 |
| [plugin_navigation_guide.md](plugin_navigation_guide.md) | - | 插件导航指南 |

---

## 协作与约定

| 文档（中文） | 文档（English） | 说明 |
|--------------|------------------|------|
| [COLLABORATION.md](COLLABORATION.md) | [COLLABORATION.md](../doc_en/COLLABORATION.md) | 多 Agent 协作规范 |
| [COLLAB_BOARD_DESIGN.md](COLLAB_BOARD_DESIGN.md) | [COLLAB_BOARD_DESIGN.md](../doc_en/COLLAB_BOARD_DESIGN.md) | 协作看板设计 |

---

## 排障

| 文档（中文） | 文档（English） | 说明 |
|--------------|------------------|------|
| [troubleshooting.md](troubleshooting.md) | [troubleshooting.md](../doc_en/troubleshooting.md) | 故障排查与工具调用模式切换 |

---

## 维护者向 / 内部

> 这部分文档给 OpenSquad 自身的维护者/开发者看，不直接面向最终用户。
> 跨语言内容请看 [`docs/README.md`](../docs/README.md)。

| 文档 | 说明 |
|------|------|
| [VCS_COLLABORATION_RULES.md](VCS_COLLABORATION_RULES.md) | VCS 协作规则 |
| [DOWNLOAD_CONVENTIONS.md](DOWNLOAD_CONVENTIONS.md) | 外部资源下载与安装规范 |
| [SUB_AGENT_DELEGATION.md](SUB_AGENT_DELEGATION.md) | Sub-Agent 委派实现文档 |
| [system_wait_interruptible.md](system_wait_interruptible.md) | 可中断 sleep 实现 |
