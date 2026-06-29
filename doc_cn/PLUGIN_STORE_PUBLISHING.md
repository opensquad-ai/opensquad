# OpenSquad 插件开发与商店发布指南

本文面向希望将自己开发的插件上架到 OpenSquad 插件商店的开发者，完整覆盖从环境搭建、代码实现、本地测试，到向插件注册表提交 Issue 审核、发布上架、发布更新的全流程。

---

## 全流程概览

```
开发环境搭建
    │
    ▼
编写插件代码（plugin.py + plugin.json + README.md）
    │
    ▼
本地测试验证（语法 → 加载 → 功能）
    │
    ▼
将插件代码推送到你自己的 GitHub 仓库
    │
    ▼
向插件注册表仓库提交 Issue（填写插件名、git_url、版本、描述等元信息）
    │
    ▼ AI 机器人自动代码审查（静态检查 + AI 代码审查）
    │
    ▼ 维护者审核通过 → 将插件元信息写入注册表 index
    │
    ▼
插件出现在 OpenSquad 插件市场（用户可一键安装，从你的仓库下载）
```

---

## 一、开发环境准备

### 1.1 项目结构了解

```
opensquad/
├── plugins/                    ← 所有插件部署在此目录
│   ├── plugin_manager.py       ← 插件加载器（只读，不要修改）
│   ├── .reload_ts              ← 热重载触发文件（写入时触发重载）
│   └── your_plugin/            ← 你的插件目录
│       ├── __init__.py
│       ├── plugin.py           ← 必须存在，包含 @register 装饰的类
│       └── plugin.json         ← 自动生成，请勿手动编辑
│
├── opensquad/
│   └── plugin_api.py           ← 插件 API 唯一入口，所有装饰器均从此导入
│
├── data/
│   └── plugins/
│       └── your_plugin/        ← 运行时数据目录（context.data_dir 指向此处）
│           └── config.json     ← 用户保存的配置（自动管理）
│
└── gateway/
    └── plugin_registry/
        └── plugins_db.json     ← 商店数据库（27 个已上架插件）
```

### 1.2 所需依赖

```bash
pip install fastapi uvicorn httpx
```

---

## 二、插件实现规范

### 2.1 命名规则（强制约束）

插件 ID 由插件名称自动派生，算法如下：

```
原始名称 → 全部转小写 → 空格和连字符替换为下划线
        → 删除非字母数字下划线字符 → 合并连续下划线 → 去除首尾下划线
```

示例：

| 你填写的 name | 生成的 plugin_id | 备注 |
|---|---|---|
| `Web Search` | `web_search` | 空格 → 下划线 |
| `Rate-Limiter` | `rate_limiter` | 连字符 → 下划线 |
| `My Awesome Plugin v2` | `my_awesome_plugin_v2` | 混合情况 |
| `HTTP/2 Proxy` | `http2_proxy` | `/` 被删除 |

**关键约束（必须遵守）：**

1. `@register(name=...)` 中的 `name` 值**必须**等于生成的 plugin_id
2. 插件目录名**必须**等于 plugin_id
3. plugin_id 在商店中**全局唯一**，大小写不敏感，先到先得
4. 上架后 name 不可更改（更改将被服务器拒绝）

> **在命名之前先验证唯一性：** 打开商店页面搜索你计划使用的名称，确认无同名插件。

### 2.2 目录结构（必须遵守）

```
plugins/
└── {plugin_id}/
    ├── __init__.py          # 空文件，Python 包标识，必须存在
    ├── plugin.py            # 必须存在，且包含唯一一个 @register 装饰的类
    ├── plugin.json          # 由框架自动生成，请勿手动创建或编辑
    ├── README.md            # 建议提供：安装说明、配置项说明
    └── （其他辅助模块）      # 可按需添加 storage.py、utils.py 等
```

`plugin.json` 是框架**每次加载时自动覆盖生成**的，手动写入无效。版本号、描述等均以 `@register` 中的声明为准。

### 2.3 plugin.py 规范

#### 最小合法结构

```python
# plugins/my_plugin/plugin.py
from opensquad.plugin_api import register, Plugin, Context

@register(
    name="my_plugin",           # 必须 = plugin_id（目录名）
    author="Your Name",
    description="插件功能的一句话说明",
    version="1.0.0",
    plugin_type="tool",         # "tool" | "hook" | "platform"
    display_name="My Plugin",
)
class MyPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        """插件加载时调用。初始化资源、数据库连接等。"""
        pass

    def on_unload(self) -> None:
        """插件卸载/禁用时调用。释放资源、关闭连接等。"""
        pass
```

#### @register 完整参数表

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | 是 | plugin_id，必须与目录名完全一致 |
| `author` | str | 是 | 作者名，上传时需与商店记录一致（用于版本更新鉴权） |
| `description` | str | 是 | 功能描述，商店卡片展示 |
| `version` | str | 是 | 语义化版本，如 `"1.0.0"` |
| `plugin_type` | str | 是 | `"tool"` / `"hook"` / `"platform"` |
| `display_name` | str | 否 | 展示名称，不填则自动生成 |
| `config_schema` | dict | 否 | 用户可配置项，见 2.5 节 |
| `dependencies` | dict | 否 | `{"pip": ["requests", "httpx"]}` |
| `contributes` | dict | 否 | 前端视图贡献，见 2.6 节 |
| `tags` | list | 否 | 搜索标签，如 `["search", "web"]` |

---

### 2.4 三种插件类型的完整实现模板

#### 类型一：工具插件（tool）

工具插件为 Agent 提供可调用的工具函数。

```python
# plugins/my_search/plugin.py
import logging
from opensquad.plugin_api import register, tool, Plugin, Context

logger = logging.getLogger("plugins.my_search")


@register(
    name="my_search",
    author="Your Name",
    description="自定义搜索工具：从指定数据源检索信息",
    version="1.0.0",
    plugin_type="tool",
    display_name="My Search",
    dependencies={"pip": ["requests"]},
    tags=["search", "api"],
)
class MySearchPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)
        self._api_key: str = ""

    def on_load(self) -> None:
        self._api_key = self.context.config.get("api_key", "")
        logger.info(f"[MySearch] loaded (agent={self.context.agent_id})")

    def on_unload(self) -> None:
        logger.info("[MySearch] unloaded")

    @tool(
        name="my_search",           # 工具命名空间，Agent 调用时用此名
        description="在自定义数据源中搜索",
        level="core",               # "core"=详细文档  "extended"=摘要文档
        auto_register=False,        # True=自动注册到所有 Agent
    )
    def search(self, query: str, max_results: int = 10):
        """
        在自定义数据源中搜索。

        Args:
            query:       搜索关键词
            max_results: 返回结果数量上限（默认 10）

        Returns:
            list[dict]: 包含 title, url, snippet 的结果列表
        """
        import requests
        # 实际实现：调用你的 API
        results = []
        try:
            resp = requests.get(
                "https://your-api.example.com/search",
                params={"q": query, "n": max_results, "key": self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                results.append({
                    "title": item["title"],
                    "url": item["url"],
                    "snippet": item.get("snippet", ""),
                })
        except Exception as e:
            return f"Error: {e}"
        return results
```

> 同一类下可以有**多个** `@tool` 方法共享同一个 `name`（工具命名空间），这些方法会被组合成一个工具模块供 Agent 调用。

#### 类型二：钩子插件（hook）

钩子插件拦截 Agent 的生命周期事件，可以修改输入/输出，或收集数据。

```python
# plugins/my_filter/plugin.py
import logging
from opensquad.plugin_api import register, hook, on_event, Plugin, Context

logger = logging.getLogger("plugins.my_filter")


@register(
    name="my_filter",
    author="Your Name",
    description="消息过滤与审计插件：拦截敏感词并记录所有对话",
    version="1.0.0",
    plugin_type="hook",
    display_name="My Filter",
    config_schema={
        "blocked_words": {
            "type": "string",
            "default": "",
            "description": "逗号分隔的屏蔽词列表，如：spam,ads,scam",
        },
    },
    tags=["security", "filter"],
)
class MyFilterPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)
        self._blocked: list = []

    def on_load(self) -> None:
        raw = self.context.config.get("blocked_words", "")
        self._blocked = [w.strip().lower() for w in raw.split(",") if w.strip()]
        logger.info(f"[MyFilter] loaded, {len(self._blocked)} blocked words")

    def on_unload(self) -> None:
        logger.info("[MyFilter] unloaded")

    @hook.on_message_received
    async def filter_message(self, context: dict) -> dict:
        """
        拦截含有屏蔽词的消息。

        context 字段：message, channel, sender_name, chat_name,
                       source_chat_id, input_source
        特殊控制：设置 context['__stop__'] = True 可丢弃该消息
        """
        message = context.get("message", "").lower()
        for word in self._blocked:
            if word in message:
                logger.warning(f"[MyFilter] Blocked message from {context.get('sender_name')}")
                context["__stop__"] = True
                return context
        return context

    @hook.on_after_tool
    async def log_tool(self, context: dict) -> dict:
        """
        记录每次工具调用（不修改结果）。

        context 字段：tool_name, arguments, result, agent_id, model
        修改 context['result'] 可重写工具返回值。
        """
        logger.info(
            f"[MyFilter] tool={context.get('tool_name')} "
            f"result_len={len(str(context.get('result', '')))}"
        )
        return context  # 钩子方法必须返回 context
```

**全部可用钩子（13 个）：**

| 钩子名 | 触发时机 | 可修改字段 | 特殊控制 |
|---|---|---|---|
| `on_message_received` | 用户消息到达 | — | `__stop__=True` 丢弃消息 |
| `on_before_llm` | LLM 调用前 | `messages` (list) | `__stop__=True` 跳过 LLM |
| `on_after_llm` | LLM 响应后 | `response` (str) | — |
| `on_before_tool` | 工具调用前 | `arguments` | `skip=True` + 提供 `result` 跳过执行 |
| `on_after_tool` | 工具调用后 | `result` (str) | — |
| `on_tool_error` | 工具报错后 | `error` (str) | — |
| `on_before_send` | 回复发送前 | `message` (str) | `__stop__=True` 取消发送 |
| `on_after_send` | 回复发送后 | — | — |
| `on_before_prompt` | 系统提示词最终化前 | `prompt` (str) | — |
| `on_task_start` | 任务开始 | — | — |
| `on_task_complete` | 任务结束 | — | — |
| `on_state_change` | Agent 状态切换 | — | — |

所有钩子支持优先级参数（数字越大越先执行）：

```python
@hook.on_after_tool(priority=100)   # 在其他钩子之前执行
async def high_priority_hook(self, context: dict) -> dict:
    ...
```

#### 类型三：平台插件（platform）

平台插件接入外部 IM 平台，提供入站消息适配器和出站发送工具。

```python
# plugins/my_platform/plugin.py
import importlib
import logging
from typing import Any, Dict, List

from opensquad.plugin_api import register, Plugin, Context

logger = logging.getLogger("plugins.my_platform")


@register(
    name="my_platform",
    author="Your Name",
    description="MyPlatform 平台适配器：接收消息并提供发送工具",
    version="1.0.0",
    plugin_type="platform",
    display_name="My Platform",
    dependencies={"pip": ["requests"]},
    tags=["im", "platform"],
)
class MyPlatformPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[MyPlatform] loaded")

    def on_unload(self) -> None:
        logger.info("[MyPlatform] unloaded")

    def get_tool_modules(self) -> List[Dict[str, Any]]:
        """
        代理模式：将独立的工具模块文件注册为工具。
        适合工具逻辑复杂或已有遗留实现的情况。
        """
        tools = []
        try:
            module = importlib.import_module("plugins.my_platform.send_tools")
            tools.append({
                "name": "my_platform_send",
                "module": module,
                "level": "extended",
                "auto_register": True,
                "requires_agent_id": True,
            })
        except ImportError as e:
            logger.error(f"[MyPlatform] Cannot import send_tools: {e}")
        return tools
```

---

### 2.5 用户可配置项（config_schema）

在 `@register` 中声明 `config_schema` 后，管理 UI 会自动生成设置面板。

```python
config_schema={
    "api_key": {
        "type": "string",
        "default": "",
        "description": "API 密钥（在服务商控制台获取）",
    },
    "timeout_sec": {
        "type": "integer",
        "default": 10,
        "description": "请求超时时间（秒）",
    },
    "enable_cache": {
        "type": "boolean",
        "default": True,
        "description": "是否缓存搜索结果",
    },
    "base_url": {
        "type": "string",
        "default": "https://api.example.com",
        "description": "API 服务地址，私有部署时修改此项",
    },
},
```

支持的类型：`string`、`integer`、`number`、`boolean`

在 `on_load()` 中读取配置：

```python
def on_load(self) -> None:
    api_key   = self.context.config.get("api_key", "")
    timeout   = self.context.config.get("timeout_sec", 10)
    use_cache = self.context.config.get("enable_cache", True)
    base_url  = self.context.config.get("base_url", "https://api.example.com")
```

配置合并顺序：`config_schema 默认值` → `用户在 UI 保存的值`（存于 `data/plugins/{name}/config.json`）

### 2.6 数据持久化

使用 `self.context.data_dir` 获取专属数据目录（框架保证目录存在）：

```python
import json, os

def on_load(self) -> None:
    self._data_file = os.path.join(self.context.data_dir, "records.json")
    # data_dir = data/plugins/{plugin_name}/
```

**JSON 文件持久化（小数据量）：**

```python
def _load(self) -> list:
    if not os.path.exists(self._data_file):
        return []
    with open(self._data_file, "r", encoding="utf-8") as f:
        return json.load(f)

def _save(self, data: list) -> None:
    with open(self._data_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

**SQLite 持久化（大数据量 / 需要查询）：**

```python
import sqlite3

def on_load(self) -> None:
    db_path = os.path.join(self.context.data_dir, "data.db")
    self._conn = sqlite3.connect(db_path, check_same_thread=False)
    self._conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT,
            created_at TEXT
        )
    """)
    self._conn.commit()

def on_unload(self) -> None:
    if self._conn:
        self._conn.close()
        self._conn = None
```

---

## 三、本地测试规范

在提交审核之前，必须完成以下三步测试。

### 3.1 语法检查

```bash
python -m py_compile plugins/{plugin_id}/plugin.py
```

**不报错 = 语法正确。** 语法错误会导致插件静默加载失败，日志中没有明显提示。

### 3.2 加载验证

将插件名称加入目标 Agent 的 `config.json`（`tools` 列表），然后重启 Agent 进程：

```bash
# 通过 Launcher HTTP API 重启（端口 9600）
POST http://127.0.0.1:9600/api/agents/{agent_dir_name}/restart
```

重启后，确认以下两点：

1. Agent 日志中出现 `[PluginManager] Loaded plugin: {plugin_id}`，无 ERROR
2. 在管理页面的「插件管理」中，插件状态为**已启用**

> 对于**新增插件**（首次加载），重启一次即可。对于**后续代码修改**，每次修改 `.py` 文件都需要重启——热重载仅对配置变更和 enable/disable 切换有效，不能重新导入已修改的模块。

### 3.3 功能测试

在 Agent 对话中调用工具，验证端到端功能：

```
# 工具插件测试
my_search.search(query="test query", max_results=5)

# 钩子插件测试（发送含屏蔽词的消息，观察是否被过滤）
```

确认：

- [ ] 工具调用成功，返回预期格式
- [ ] 错误输入有合理的错误提示（返回 `"Error: ..."` 字符串，而非抛出异常）
- [ ] `on_unload()` 干净关闭（重启时日志无异常）
- [ ] 数据文件路径在 `data/plugins/{plugin_id}/` 下，而非代码目录

### 3.4 常见加载错误

| 现象 | 原因 | 解决方法 |
|---|---|---|
| 插件管理页面不显示此插件 | `plugin.py` 缺少或语法错误 | `python -m py_compile` 检查 |
| 加载成功但工具不可用 | `@register(name=...)` 与目录名不一致 | 保持两者完全一致 |
| `ModuleNotFoundError` | 依赖未安装 | `pip install -r requirements.txt` |
| `on_load` 报错 | 初始化逻辑异常 | 加 `try/except` 保护，防止阻塞加载 |
| 热重载后仍是旧代码 | 修改了 `.py` 文件，仅触发了热重载 | 必须重启 Agent 进程 |

---

## 四、发布插件到注册表

### 4.1 前置条件

- 拥有 GitHub 账户
- Git 基础操作（clone、push）
- 完成第三章的本地测试

### 4.2 将插件推送到你自己的 GitHub 仓库

```bash
# 1. 在 GitHub 上新建一个仓库，例如：https://github.com/{your_github_username}/email_assistant

# 2. 在本地初始化并推送
cd plugins/{plugin_id}
git init
git add .
git commit -m "feat: initial release v1.0.0"
git remote add origin https://github.com/{your_github_username}/{plugin_id}.git
git push -u origin main
```

仓库根目录的结构应如下：

```
{plugin_id}/          ← 仓库根目录（即插件目录）
├── __init__.py
├── plugin.py
├── plugin.json       ← 市场格式，见 4.3 节
├── requirements.txt  ← 可选，但强烈推荐
└── README.md         ← 可选但推荐
```

### 4.3 plugin.json 格式

插件目录内必须包含 `plugin.json`，格式如下：

```json
{
  "id": "my_plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "简要描述插件功能",
  "author": "YourGitHubUsername",
  "type": "tool",
  "tags": ["example"],
  "homepage": "https://github.com/yourname/my_plugin",
  "git_url": "https://github.com/yourname/my_plugin"
}
```

**必填字段：** `id`、`name`、`version`、`description`、`author`、`type`

- `id` 必须与目录名完全一致（同时也必须与 `plugin.py` 中 `@register(name=...)` 一致）。
- `git_url` 是真正的安装来源——用户安装时系统从此地址下载插件代码。
- `homepage` 通常与 `git_url` 相同，也可以是插件文档或演示页面地址。

### 4.4 向注册表提交 Issue

前往插件注册表仓库 [**github.com/opensquad-ai/opensquad-plugins**](https://github.com/opensquad-ai/opensquad-plugins)，点击 **New Issue**，选择 **"Submit a Plugin"** 模板，填写以下信息：

```
插件名（plugin_id）: my_plugin
展示名称: My Plugin
版本: 1.0.0
类型: tool
描述: 简要描述插件功能
作者（GitHub 用户名）: YourGitHubUsername
git_url: https://github.com/YourGitHubUsername/my_plugin
标签（可选）: example, api
```

Issue 标题格式建议：`[Plugin] 插件展示名称 (plugin_id)`

### 4.5 AI 自动代码审查

Issue 提交后，机器人会自动拉取 `git_url` 中的代码进行审查，包括：

1. **静态检查**
   - `plugin.json` 必填字段完整性
   - `plugin.py` 语法合法性（`ast.parse`）
   - `@register` 装饰器是否存在
   - 是否继承 `Plugin` 基类
   - 安全性扫描（`subprocess`、`eval`、`exec`、`os.system` 等危险调用）

2. **AI 代码审查**
   - 代码质量与可读性
   - 功能描述与实现一致性
   - 安全性深度分析

审查结果将作为 Issue 评论自动发布，格式如下：

```
## OpenSquad 插件自动审查报告 ✅

**总体评价**: PASS — 建议合并

### 静态检查
无静态检查问题

### AI 代码审查
代码结构清晰，正确继承 Plugin 类并使用 @register 装饰器...
```

总体评价分三档：
- **PASS** — 建议收录
- **WARN** — 建议修改后收录（评论中有具体说明）
- **FAIL** — 建议拒绝（存在安全问题或规范违反）

### 4.6 人工审核与收录

维护者会参考 AI 审查结果进行人工复核。通过后，维护者将插件元信息写入注册表 index 文件，插件即可在市场中被发现和安装。

### 4.7 收录后的发布流程

```
维护者将插件元信息写入注册表 index
    │
    ▼
插件市场读取 index → 展示插件卡片（名称、描述、版本、作者）
    │
    ▼
用户点击「安装」
    │
    ├── 系统从 git_url（你的仓库）下载代码
    │
    ├── 若仓库包含预构建产物（如 ui/index.js）→ 直接安装
    │
    └── 若无预构建产物 → 自动触发构建（如 npm run build）
```

---

## 五、版本更新发布

### 5.1 版本号规范

遵循语义化版本（SemVer）：

| 类型 | 版本号变化 | 适用情况 |
|---|---|---|
| Patch | `1.0.0` → `1.0.1` | Bug 修复、文档更新 |
| Minor | `1.0.0` → `1.1.0` | 新增功能、向后兼容 |
| Major | `1.0.0` → `2.0.0` | 破坏性变更（工具名称/参数变化） |

**商店「可更新」角标逻辑：** 当注册表版本 > 用户已安装版本时，插件卡片显示「更新 vX.Y.Z」按钮（黄色）。

### 5.2 提交版本更新

与首次发布流程完全相同：修改代码 → 更新 `plugin.json` 中的 `version` → 提交 PR → 合并后自动发布。

`publish.yml` 会检测版本号变化，自动创建新 Release 并同步注册表。

### 5.3 所有权保护

注册表通过 `github_user` 字段记录插件归属（取自 PR 作者的 GitHub 用户名）。同一插件 ID 的后续更新 PR 必须来自同一 GitHub 用户，否则 sync 接口将拒绝写入。

---

## 六、安装后的使用流程（供参考）

了解用户侧流程，有助于你设计友好的插件交互体验：

```
用户在商店点击「安装」
    │
    ▼
后端 POST /api/ai-web/market/plugins/{id}/install
    │
    ├── 1. 从插件注册服务获取元数据（含版本、download_url）
    │
    ├── 2. 检查本地已安装版本和 enabled 状态
    │
    ├── 3. 从 GitHub Release 下载 ZIP
    │
    ├── 4. 解压到 plugins/{plugin_id}/（自动处理顶层 wrapper 目录）
    │
    ├── 5. 还原 enabled 状态到 plugin.json
    │
    └── 6. 写入 plugins/.reload_ts → 触发热重载（5 秒内生效）
```

**工具类插件激活步骤**（用户需手动操作）：

编辑 `agents/{agent_dir}/config.json`，在 `tools` 列表中添加插件 ID，然后重启 Agent 进程。

---

## 七、用户安装后的使用流程（供参考）

了解用户侧流程，有助于你设计友好的插件交互体验：

```
用户在商店点击「安装」
    │
    ▼
插件代码从 GitHub Release 下载到 plugins/{plugin_id}/
    │
    ▼
框架热重载（无需重启）
    │
    ▼
管理页面「插件管理」中出现该插件（enabled=true）
    │
    ├── 工具类：Agent 配置文件的 tools 列表中加入 plugin_id
    │           然后重启 Agent 进程（工具注册需要重启）
    │
    └── 钩子类 / 平台类：热重载后立即生效，无需重启
```

**工具类插件激活步骤**（用户需手动操作）：

编辑 `agents/{agent_dir}/config.json`，在 `tools` 列表中添加：

```json
{
  "tools": [
    "existing_tool_1",
    "web_search_pro"    ← 新增
  ]
}
```

然后重启 Agent 进程。此后 Agent 可调用 `web_search_pro.{function_name}(...)` 工具。

---

## 八、完整上架检查清单

### 代码规范

- [ ] `plugin.py` 中有且仅有一个 `@register` 装饰的类
- [ ] `@register(name=...)` 与目录名完全一致
- [ ] 目录下存在空 `__init__.py`
- [ ] 已实现 `on_load()` 和 `on_unload()`
- [ ] 工具方法有 docstring（包含 Args 和 Returns 说明）
- [ ] 所有异常均被捕获，工具返回 `"Error: ..."` 字符串
- [ ] 文件路径均基于 `context.data_dir` 或 `context.project_root`，无硬编码绝对路径
- [ ] API Key 等敏感信息通过 `config_schema` 声明，不硬编码
- [ ] `dependencies` 中声明了所有第三方 pip 包
- [ ] `on_unload()` 关闭了 `on_load()` 打开的所有资源
- [ ] **无危险调用**（`subprocess`、`eval`、`exec`、`os.system` 等）——AI 审查机器人会自动检测

### plugin.json 规范

- [ ] `id` 字段与目录名一致，也与 `@register(name=...)` 一致
- [ ] `name`、`version`、`description`、`author`、`type` 均已填写
- [ ] `type` 为 `"tool"`、`"hook"`、`"platform"` 之一
- [ ] `version` 为语义化版本格式（如 `"1.0.0"`）

### 本地测试

- [ ] `python -m py_compile plugins/{plugin_id}/plugin.py` 无报错
- [ ] Agent 日志显示 `Loaded plugin: {plugin_id}`，无 ERROR
- [ ] 工具/钩子功能正常，结果格式符合预期
- [ ] 错误输入（空字符串、None、超长输入）有合理响应
- [ ] 重启 Agent 后插件正常恢复，无状态泄露

### GitHub Issue 提交

- [ ] 在 GitHub 上创建了自己的插件仓库
- [ ] 仓库中插件目录命名为 plugin_id（无空格，小写字母/数字/下划线）
- [ ] 仓库中包含 `plugin.py`、`plugin.json`（`README.md` 可选但推荐）
- [ ] 向插件注册表仓库提交 Issue，填写了 plugin_id、git_url、版本、描述等元信息
- [ ] Issue 标题格式：`[Plugin] 插件展示名称 (plugin_id)`
- [ ] 等待 AI 自动审查评论，按反馈修改后再请求人工复核

---

## 九、常见错误速查

| 错误 | 原因 | 解决方法 |
|---|---|---|
| AI 审查报告：缺少 @register | plugin.py 没有 @register 装饰器 | 参照规范添加装饰器 |
| AI 审查报告：安全检测不通过 | 使用了 subprocess/eval/exec 等 | 改用安全替代方案 |
| AI 审查报告：plugin.json 解析失败 | JSON 格式错误 | 用 JSON 校验工具检查格式 |
| AI 审查报告：id 与目录名不一致 | plugin.json 中 id 字段写错 | 保持 id、目录名、@register name 三者完全一致 |
| 合并后插件未出现在市场 | publish.yml 失败 | 检查 GitHub Actions 日志 |
| 插件安装后 Agent 无法调用工具 | 工具类插件未加入 tools 列表 | 编辑 config.json → tools，重启 Agent |
| 插件出现但状态 disabled | plugin.json enabled=false | 在插件管理页面手动启用 |
| 版本更新后用户不显示「更新」按钮 | 新版本号 ≤ 已安装版本号 | 使用更大的版本号 |
| 热重载后代码未生效 | 修改了 .py 文件（需重启） | `POST /api/agents/{dir}/restart` |

---

## 附录：插件 ID 预算工具

上架前可在本地运行此脚本验证 plugin_id：

```python
import re

def name_to_id(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "plugin"

# 验证示例
names = [
    "My Awesome Plugin",
    "Web Search Pro",
    "Rate-Limiter v2",
    "HTTP/2 Proxy Tool",
]
for name in names:
    plugin_id = name_to_id(name)
    print(f"{name!r:30s} -> {plugin_id!r}")
    print(f"  @register(name={plugin_id!r}, ...)")
    print(f"  目录: plugins/{plugin_id}/")
```

---

## 附录：接口速查

| 接口 | 方法 | 用途 |
|---|---|---|
| 插件注册表仓库 Issues | Issue | 发布 / 更新插件（主要入口） |
| `http://127.0.0.1:9555/api/ai-web/market/review-pr` | POST | AI 代码审查（由审查机器人调用） |
| `http://127.0.0.1:9720/admin/plugins/sync` | POST | 同步插件到注册表（由维护者调用，需 ADMIN_KEY） |
| `http://127.0.0.1:9720/plugins` | GET | 查询商店列表（支持 search/type/page） |
| `http://127.0.0.1:9720/plugins/{id}` | GET | 查询单个插件详情 |
| `http://127.0.0.1:9555/api/ai-web/market/plugins/{id}/install` | POST | 安装/更新插件（需 Bearer token） |
| `http://127.0.0.1:9600/api/agents/{dir}/restart` | POST | 重启 Agent 进程 |
