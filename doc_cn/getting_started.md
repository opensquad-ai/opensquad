# 快速入门教程

本教程将带你从头搭建一个可用的 OpenSquad Agent，并完成第一个任务。

---

## 前置条件

- Python 3.10+
- 一个 LLM API 密钥（DeepSeek、OpenAI、Claude、Gemini 等均可）

---

## 第一步：安装

```bash
# 克隆项目
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad

# 安装（推荐使用 uv）
pip install uv
uv sync

# 安装前端
cd src/opensquad/gateway/nexuschat-pro
npm install
cd ../../../..
```

---

## 第二步：初始化

```bash
uv run opensquad init
```

这会创建：
- 工作区目录结构
- 默认 Agent 配置
- 系统配置文件

---

## 第三步：配置 API 密钥

### 方式 A：编辑模型卡（推荐）

编辑 `src/model_cards/deepseek-v4-flash.json`，填入你的 API 密钥：

```json
{
  "name": "deepseek-v4-flash",
  "title": "DeepSeek V4 Flash",
  "provider": "openai_compat",
  "api_key": "sk-your-api-key-here",
  "base_url": "https://api.deepseek.com",
  "model_name": "deepseek-v4-flash",
  "token_max": 128000,
  "temperature": 0.7
}
```

### 方式 B：通过 Web UI

启动后在模型卡管理页面中编辑。

### 方式 C：使用其他模型

如果你使用 OpenAI、Claude 或 Gemini，修改 `src/agents/default/config.json` 中的 model 配置，或创建新的模型卡。

---

## 第四步：启动

```bash
uv run opensquad start
```

启动后访问：
- **Web UI**：`http://localhost:9555`
- **Launcher**：`http://localhost:9600`

---

## 第五步：创建 Agent

### 通过 Web UI

1. 打开 Web UI
2. 进入 **Agent 管理** 页面
3. 点击 **新建 Agent**
4. 填写 Agent 名称
5. 选择模型卡
6. 选择启用的工具
7. 保存

### 通过配置文件

在 `src/agents/` 下创建新目录：

```
src/agents/my-agent/
├── config.json
├── role.md
```

`config.json`：

```json
{
  "agent_id": "my-agent-001",
  "agent_name": "My Agent",
  "agent_type": "general",
  "description": "我的第一个 Agent",
  "model": {
    "provider": "openai_compat",
    "api_key": "sk-xxx",
    "base_url": "https://api.deepseek.com",
    "model_name": "deepseek-v4-flash",
    "token_max": 128000,
    "temperature": 0.7
  },
  "tools": ["system", "filesystem", "websearch"],
  "group_chat": { "enabled": false },
  "web_server": { "enabled": true },
  "gateway": { "enabled": true },
  "prompt": { "role": "role.md" },
  "mcp": { "enabled": true },
  "skills": {
    "enabled": true,
    "active": []
  }
}
```

`role.md`：

```markdown
# My Agent

You are a helpful assistant. Answer questions concisely and accurately.
```

---

## 第六步：对话

在 Web UI 的聊天页面中：

1. 选择你的 Agent
2. 在输入框中输入问题
3. 观察 Agent 的思考、工具调用和回复

---

## 第七步：进阶配置

### 启用技能

编辑 `config.json`，激活需要的技能：

```json
{
  "skills": {
    "enabled": true,
    "active": ["data-analysis", "code_reviewer_lite"]
  }
}
```

### 配置上下文压缩

在 `system_config.json` 中调整：

```json
{
  "context_compression": {
    "trigger_threshold": 0.75,
    "keep_recent_fraction": 0.10,
    "summary_max_tokens": 4000
  }
}
```

### 添加插件

通过 Web UI 的插件管理页面启用插件，如：
- **websearch**：网页搜索
- **long_memory**：长期记忆
- **vision**：图像识别

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `uv run opensquad init` | 初始化项目 |
| `uv run opensquad start` | 启动所有服务 |
| `uv run opensquad status` | 查看服务状态 |
| `uv run opensquad plugin list` | 列出插件 |
| `uv run opensquad plugin install <name>` | 安装插件 |
| `uv run opensquad update` | 更新配置 |

---

## 下一步

- [模型卡配置指南](model_cards_guide.md) — 配置更多 LLM 模型
- [技能开发指南](skill_development_guide.md) — 开发自定义技能
- [角色卡开发指南](role_card_guide.md) — 创建专业 Agent 角色
- [插件开发指南](PLUGIN_DEVELOPMENT.md) — 开发自定义插件
- [部署指南](deployment_guide.md) — 生产环境部署
