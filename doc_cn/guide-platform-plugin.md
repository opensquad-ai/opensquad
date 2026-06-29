# OpenSquad 外部通讯平台插件开发指南

## 概述

外部通讯平台插件（如飞书、Telegram、QQ）负责将 IM 平台的消息接入 OpenSquad Agent。所有平台适配器遵循统一架构，80% 的代码可复用。

## 架构：消息流转

```
IM 平台 (飞书/Telegram/QQ)
    │ (WebSocket / Long Polling / Webhook)
    ▼
  Adapter 进程
    │ HTTP POST
    ▼
  External API 适配器 (:9700)  ← 自动启动，无需配置
    │ WebSocket
    ▼
  Gateway (:9555)
    │ WebSocket
    ▼
  Agent 进程
```

核心原则：**Adapter 只负责"连接 IM 平台"和"协议转换"，不负责"调用 Agent"。所有 IM 适配器共享同一个 External API（端口 9700）。**

## 必需文件

每个外部通讯平台插件需要 4 个文件：

| 文件 | 作用 | 必须 |
|------|------|------|
| `plugin.json` | 元数据 + 服务配置 + UI schema | ✅ |
| `config.py` | 从 system_config.json 读取 bot 配置 | ✅ |
| `adapter.py` | 连接 IM 平台，接收消息，转发到 external_api | ✅ |
| `send_tools.py` | Agent 主动向 IM 平台发消息的工具 | ✅ |

可选文件：

| 文件 | 作用 |
|------|------|
| `debug_config.txt` | 启动时自动写入的调试信息（可 .gitignore） |
| `test_*.py` | 本地测试脚本 |

## 模板

### 1. `plugin.json`

```json
{
  "name": "myplatform",
  "display_name": "My Platform",
  "version": "1.0.0",
  "type": "platform",
  "enabled": false,
  "node_scope": "single",
  "description": "MyPlatform integration. Provides inbound message adapter and outbound send tools.",
  "author": "Your Name",
  "tags": ["im"],
  "tools": [
    {
      "name": "myplatform_send",
      "module": "proxy",
      "level": "extended",
      "auto_register": true,
      "requires_agent_id": true
    }
  ],
  "hooks": [],
  "config": {
    "schema": {
      "service_enabled": {
        "type": "boolean",
        "default": false,
        "description": "Enable MyPlatform service"
      },
      "bots": {
        "type": "bot_list",
        "default": [],
        "description": "MyPlatform bot list",
        "item_schema": {
          "name": {"type": "string", "default": "", "description": "Bot name"},
          "bot_token": {"type": "string", "default": "", "description": "Bot Token", "secret": true},
          "agent_id": {"type": "string", "default": "", "description": "Bound Agent ID"},
          "enabled": {"type": "boolean", "default": true, "description": "Enable this bot"}
        }
      }
    },
    "section": "myplatform"
  },
  "config_schema": {},
  "service_toggle": true,
  "service": {
    "entry": "adapter.py",
    "auto_start": true
  },
  "contributes": {},
  "dependencies": {
    "pip": ["requests"]
  }
}
```

**字段说明**：
- `service_toggle: true` → Web UI 可见开关，用户可启用/禁用
- `service.auto_start: true` → launcher 启动时自动拉起 adapter 进程
- `config.schema.service_enabled` → 映射到 `system_config.json` → `services.{name}.enabled`
- `config.schema.bots` → 映射到 `system_config.json` → `{name}.bots[]`
- `config.section` → 配置存储的 JSON key（如 `"feishu"`、`"telegram"`）
- `node_scope: "single"` → 只需一个 node 运行（避免多台机器重复收消息）

### 2. `config.py`

```python
# -*- coding: utf-8 -*-
"""MyPlatform Bot Adapter Configuration"""
import os
import sys
from dataclasses import dataclass
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from opensquad.system_config import syscfg


@dataclass
class MyPlatformBotConfig:
    name: str
    bot_token: str           # ← 改为你的平台所需凭证
    agent_id: str
    enabled: bool = True
    request_timeout: int = 60
    # 添加你的平台特有字段（如 proxy、app_id、app_secret 等）


MYPLATFORM_LOG_LEVEL: str = syscfg.get("myplatform", "log_level", "INFO")
MYPLATFORM_DEFAULT_TIMEOUT: int = syscfg.get_int("myplatform", "request_timeout", 60)


def is_service_enabled() -> bool:
    return syscfg.is_service_enabled("myplatform")


# ── External Adapter Connection ──
EXTERNAL_ADAPTER_URL: str = os.environ.get("EXTERNAL_ADAPTER_URL") or syscfg.external_adapter_url()
EXTERNAL_API_KEY: str = syscfg.auth("external_api_key")


def load_bot_configs() -> List[MyPlatformBotConfig]:
    raw_bots = syscfg.get("myplatform", "bots", [])
    configs = []
    for b in raw_bots:
        if not b.get("enabled", True):
            continue
        token = b.get("bot_token", "")
        if not token:
            continue
        configs.append(MyPlatformBotConfig(
            name=b.get("name", f"{APP_PREFIX}-bot-{len(configs)+1}"),
            bot_token=token,
            agent_id=b.get("agent_id", "default-001"),
            enabled=True,
            request_timeout=b.get("request_timeout", MYPLATFORM_DEFAULT_TIMEOUT),
        ))
    return configs
```

### 3. `adapter.py`

核心流程（以 Python 为例）：

```python
# -*- coding: utf-8 -*-
"""MyPlatform Bot Adapter"""
import json, logging, os, sys, threading, time
import requests

# 添加项目根到 sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from plugins.myplatform.config import (
    MyPlatformBotConfig, load_bot_configs, is_service_enabled,
    EXTERNAL_ADAPTER_URL, EXTERNAL_API_KEY, MYPLATFORM_LOG_LEVEL,
)

logger = logging.getLogger("myplatform_adapter")


class MyPlatformBotRunner:
    """每个 bot 一个 runner，管理连接、接收、转发"""

    def __init__(self, cfg: MyPlatformBotConfig):
        self.cfg = cfg
        self._log = logging.getLogger(f"myplatform.{cfg.name}")

    def run(self):
        """主循环：连接平台 → 接收消息 → 转发 → 回复"""
        self._log.info(f"[{self.cfg.name}] Starting...")
        # 1. 初始化你的平台 SDK / 连接
        # 2. 循环接收消息
        # 3. 对每条消息调用 self._process_and_reply(...)

    def _process_and_reply(self, chat_id, user_id, text, chat_type):
        """转发消息到 External API，获取回复，发回 IM 平台"""
        url = f"{EXTERNAL_ADAPTER_URL}/api/chat"
        headers = {"Content-Type": "application/json"}
        if EXTERNAL_API_KEY:
            headers["X-API-Key"] = EXTERNAL_API_KEY

        payload = {
            "agent_id": self.cfg.agent_id,
            "message": text,
            "user_id": f"myplatform_{user_id}",     # ← 平台前缀
            "timeout": self.cfg.request_timeout,
            "channel": f"myplatform_{chat_type}",    # ← 如 "myplatform_private"
            "sender_name": "",                       # ← 可选
            "chat_name": "",                         # ← 可选
            "source_chat_id": chat_id,
        }

        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=self.cfg.request_timeout + 10)
            if resp.status_code == 200:
                reply = resp.json().get("message", "")
                if reply:
                    self._reply(chat_id, reply)
                else:
                    self._reply(chat_id, "Agent did not return a valid reply.")
            else:
                detail = ""
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    pass
                self._log.error(f"Adapter error: {resp.status_code}, detail={detail}")
                if detail:
                    self._reply(chat_id, f"Processing failed (error {resp.status_code}): {detail}")
                else:
                    self._reply(chat_id, f"Processing failed (error {resp.status_code}).")
        except requests.Timeout:
            self._log.error("Adapter request timed out")
            self._reply(chat_id, "Agent processing timed out.")
        except requests.ConnectionError:
            self._log.error("Cannot connect to External API adapter")
            self._reply(chat_id, "Agent service unavailable.")
        except Exception as e:
            self._log.error(f"Adapter call error: {e}", exc_info=True)
            self._reply(chat_id, "Internal error.")

    def _reply(self, chat_id, text):
        """发回消息到 IM 平台（使用你的平台 SDK/API）"""
        # 实现你的平台的消息发送 API
        pass


def main():
    if not is_service_enabled():
        logger.info("MyPlatform service is disabled. Exiting.")
        sys.exit(0)

    bot_configs = load_bot_configs()
    if not bot_configs:
        logger.error("No enabled MyPlatform bots configured.")
        sys.exit(1)

    # 启动所有 bot
    runners = []
    for cfg in bot_configs:
        runner = MyPlatformBotRunner(cfg)
        runners.append(runner)
        thread = threading.Thread(target=runner.run, daemon=True)
        thread.start()

    # 保持主进程运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted.")


if __name__ == "__main__":
    main()
```

### 4. `send_tools.py`

Agent 主动发消息的工具注册：

```python
# -*- coding: utf-8 -*-
"""Agent tool: send messages to MyPlatform"""
import json, logging, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from opensquad.system_config import syscfg
from plugins.myplatform.config import load_bot_configs, EXTERNAL_ADAPTER_URL, EXTERNAL_API_KEY

logger = logging.getLogger("myplatform_send")


def _get_bot_token(agent_id):
    """根据 agent_id 查找对应的 bot token"""
    for cfg in load_bot_configs():
        if cfg.agent_id == agent_id:
            return cfg.bot_token
    return None


def myplatform_send(agent_id, chat_id, text):
    """发送消息到指定聊天（使用你的平台 API）"""
    token = _get_bot_token(agent_id)
    if not token:
        return {"ok": False, "error": f"No MyPlatform bot bound to agent '{agent_id}'"}
    # 调用你的平台 API 发送消息
    # ...
    return {"ok": True, "message_id": "msg_xxx"}


# 工具注册（由 proxy.py 调用）
def register(registry):
    registry["myplatform_send"] = myplatform_send
```

## 平台差异映射

| | Feishu (Lark) | Telegram | QQ (NapCat) |
|---|---|---|---|
| **SDK** | `lark-oapi` | `python-telegram-bot` | HTTP API (NapCat 反向 WS) |
| **接收方式** | WebSocket | Long Polling | HTTP Webhook + WS |
| **多 bot** | 子进程隔离 | 单进程 asyncio | HTTP 多端口 |
| **凭证** | app_id + app_secret | bot_token | access_token |
| **群聊** | @bot 触发 | @bot 或 reply | @ 或 reply |
| **特殊依赖** | `pycryptodome` | 无（需外网） | 需 NapCat 服务 |

## 本地调试

```bash
# 1. 确保 system_config.json 正确配置
{
  "services": {"myplatform": {"enabled": true}},
  "myplatform": {
    "bots": [{"name": "test", "bot_token": "xxx", "agent_id": "agent305-001", "enabled": true}]
  }
}

# 2. 手动启动 adapter（绕过 launcher，方便看日志）
cd opensquad/
set OPENSQUAD_WORKSPACE=C:\Users\xxx\.opensquad\workspace
python src\plugins\myplatform\adapter.py

# 3. 测试 External API 是否正常
curl -X POST http://127.0.0.1:9700/api/chat \
  -H "X-API-Key: opensquad-feishu-bridge-key" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"agent305-001","message":"hello","user_id":"test","timeout":60,"channel":"myplatform_test"}'

# 4. 检查服务状态
opensquad doctor
opensquad status
```

## 开发检查清单

- [ ] `plugin.json` 含 `service_toggle: true` + `service: {entry, auto_start}`
- [ ] `config.py` 正确读取 `system_config.json`
- [ ] `adapter.py` 正确转发到 `EXTERNAL_ADAPTER_URL/api/chat`
- [ ] `user_id` 带平台前缀（如 `telegram_{id}`），避免跨平台冲突
- [ ] `channel` 字段区分 `private` / `group`
- [ ] 错误处理完整（超时、连接失败、502）
- [ ] `send_tools.py` 支持 agent 主动发消息
- [ ] `node_scope: "single"` 避免多节点重复
- [ ] 本地 `External API` 测试通过 → `opensquad doctor` 全部 ✅
