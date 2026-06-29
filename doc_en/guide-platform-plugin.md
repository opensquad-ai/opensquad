# OpenSquad External Communication Platform Plugin Development Guide

## Overview

External communication platform plugins (e.g., Feishu, Telegram, QQ) connect IM platform messages to OpenSquad Agents. All platform adapters follow a unified architecture with ~80% reusable code.

## Architecture: Message Flow

```
IM Platform (Feishu/Telegram/QQ)
    │ (WebSocket / Long Polling / Webhook)
    ▼
  Adapter Process
    │ HTTP POST
    ▼
  External API Adapter (:9700)  ← Auto-started, no manual configuration needed
    │ WebSocket
    ▼
  Gateway (:9555)
    │ WebSocket
    ▼
  Agent Process
```

Core principle: **The Adapter is only responsible for "connecting to the IM platform" and "protocol conversion", not for "calling the Agent". All IM adapters share the same External API (port 9700).**

## Required Files

Each external communication platform plugin requires 4 files:

| File | Purpose | Required |
|------|------|------|
| `plugin.json` | Metadata + service config + UI schema | ✅ |
| `config.py` | Read bot configuration from system_config.json | ✅ |
| `adapter.py` | Connect to IM platform, receive messages, forward to external_api | ✅ |
| `send_tools.py` | Tools for Agent to proactively send messages to the IM platform | ✅ |

Optional files:

| File | Purpose |
|------|------|
| `debug_config.txt` | Debug info auto-written at startup (can be .gitignore'd) |
| `test_*.py` | Local test scripts |

## Templates

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

**Field descriptions**:
- `service_toggle: true` → Visible toggle in Web UI, user can enable/disable
- `service.auto_start: true` → Launcher auto-starts the adapter process on boot
- `config.schema.service_enabled` → Maps to `system_config.json` → `services.{name}.enabled`
- `config.schema.bots` → Maps to `system_config.json` → `{name}.bots[]`
- `config.section` → JSON key for config storage (e.g. `"feishu"`, `"telegram"`)
- `node_scope: "single"` → Only one node needs to run (avoids duplicate message receipt across machines)

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
    bot_token: str           # ← Replace with your platform's credentials
    agent_id: str
    enabled: bool = True
    request_timeout: int = 60
    # Add platform-specific fields (e.g. proxy, app_id, app_secret, etc.)


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
            name=b.get("name", f"myplatform-bot-{len(configs)+1}"),
            bot_token=token,
            agent_id=b.get("agent_id", "default-001"),
            enabled=True,
            request_timeout=b.get("request_timeout", MYPLATFORM_DEFAULT_TIMEOUT),
        ))
    return configs
```

### 3. `adapter.py`

Core flow (Python example):

```python
# -*- coding: utf-8 -*-
"""MyPlatform Bot Adapter"""
import json, logging, os, sys, threading, time
import requests

# Add project root to sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from plugins.myplatform.config import (
    MyPlatformBotConfig, load_bot_configs, is_service_enabled,
    EXTERNAL_ADAPTER_URL, EXTERNAL_API_KEY, MYPLATFORM_LOG_LEVEL,
)

logger = logging.getLogger("myplatform_adapter")


class MyPlatformBotRunner:
    """One runner per bot, managing connection, receive, forward"""

    def __init__(self, cfg: MyPlatformBotConfig):
        self.cfg = cfg
        self._log = logging.getLogger(f"myplatform.{cfg.name}")

    def run(self):
        """Main loop: connect to platform → receive messages → forward → reply"""
        self._log.info(f"[{self.cfg.name}] Starting...")
        # 1. Initialize your platform SDK / connection
        # 2. Loop to receive messages
        # 3. For each message, call self._process_and_reply(...)

    def _process_and_reply(self, chat_id, user_id, text, chat_type):
        """Forward message to External API, get reply, send back to IM platform"""
        url = f"{EXTERNAL_ADAPTER_URL}/api/chat"
        headers = {"Content-Type": "application/json"}
        if EXTERNAL_API_KEY:
            headers["X-API-Key"] = EXTERNAL_API_KEY

        payload = {
            "agent_id": self.cfg.agent_id,
            "message": text,
            "user_id": f"myplatform_{user_id}",     # ← Platform prefix
            "timeout": self.cfg.request_timeout,
            "channel": f"myplatform_{chat_type}",    # ← e.g. "myplatform_private"
            "sender_name": "",                       # ← Optional
            "chat_name": "",                         # ← Optional
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
        """Send reply back to IM platform (use your platform SDK/API)"""
        # Implement your platform's message send API
        pass


def main():
    if not is_service_enabled():
        logger.info("MyPlatform service is disabled. Exiting.")
        sys.exit(0)

    bot_configs = load_bot_configs()
    if not bot_configs:
        logger.error("No enabled MyPlatform bots configured.")
        sys.exit(1)

    # Start all bots
    runners = []
    for cfg in bot_configs:
        runner = MyPlatformBotRunner(cfg)
        runners.append(runner)
        thread = threading.Thread(target=runner.run, daemon=True)
        thread.start()

    # Keep main process alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted.")


if __name__ == "__main__":
    main()
```

### 4. `send_tools.py`

Agent tool for proactively sending messages:

```python
# -*- coding: utf-8 -*-
"""Agent tool: send messages to MyPlatform"""
import json, logging, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from opensquad.system_config import syscfg
from plugins.myplatform.config import load_bot_configs, EXTERNAL_ADAPTER_URL, EXTERNAL_API_KEY

logger = logging.getLogger("myplatform_send")


def _get_bot_token(agent_id):
    """Find the corresponding bot token by agent_id"""
    for cfg in load_bot_configs():
        if cfg.agent_id == agent_id:
            return cfg.bot_token
    return None


def myplatform_send(agent_id, chat_id, text):
    """Send a message to the specified chat (using your platform API)"""
    token = _get_bot_token(agent_id)
    if not token:
        return {"ok": False, "error": f"No MyPlatform bot bound to agent '{agent_id}'"}
    # Call your platform API to send the message
    # ...
    return {"ok": True, "message_id": "msg_xxx"}


# Tool registration (called by proxy.py)
def register(registry):
    registry["myplatform_send"] = myplatform_send
```

## Platform Differences

| | Feishu (Lark) | Telegram | QQ (NapCat) |
|---|---|---|---|
| **SDK** | `lark-oapi` | `python-telegram-bot` | HTTP API (NapCat reverse WS) |
| **Receive** | WebSocket | Long Polling | HTTP Webhook + WS |
| **Multi-bot** | Subprocess isolation | Single-process asyncio | HTTP multi-port |
| **Credentials** | app_id + app_secret | bot_token | access_token |
| **Group chat** | @bot trigger | @bot or reply | @ or reply |
| **Extra deps** | `pycryptodome` | None (needs internet) | NapCat service required |

## Local Debugging

```bash
# 1. Ensure system_config.json is correctly configured
{
  "services": {"myplatform": {"enabled": true}},
  "myplatform": {
    "bots": [{"name": "test", "bot_token": "xxx", "agent_id": "agent305-001", "enabled": true}]
  }
}

# 2. Manually start the adapter (bypass launcher for easier log viewing)
cd opensquad/
set OPENSQUAD_WORKSPACE=C:\Users\xxx\.opensquad\workspace
python src\plugins\myplatform\adapter.py

# 3. Test whether External API is working
curl -X POST http://127.0.0.1:9700/api/chat \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"agent305-001","message":"hello","user_id":"test","timeout":60,"channel":"myplatform_test"}'

# 4. Check service status
opensquad doctor
opensquad status
```

## Development Checklist

- [ ] `plugin.json` includes `service_toggle: true` + `service: {entry, auto_start}`
- [ ] `config.py` correctly reads `system_config.json`
- [ ] `adapter.py` correctly forwards to `EXTERNAL_ADAPTER_URL/api/chat`
- [ ] `user_id` has a platform prefix (e.g. `telegram_{id}`) to avoid cross-platform conflicts
- [ ] `channel` field distinguishes `private` / `group`
- [ ] Error handling is complete (timeout, connection failure, 502)
- [ ] `send_tools.py` supports Agent proactively sending messages
- [ ] `node_scope: "single"` avoids multi-node duplication
- [ ] Local `External API` test passes → `opensquad doctor` all ✅
