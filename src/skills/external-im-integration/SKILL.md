---
name: external-im-integration
description: Connect OpenSquad agents to external IM platforms (Feishu/Lark, QQ, Telegram). Covers plugin service setup, adapter processes, Gateway auth, workspace path fixes, and troubleshooting. Uses Feishu as the reference example.
version: 2.0.0
author: OpenSquad
---

## Purpose

Enable agents to send and receive messages from external IM platforms. After setup, the agent can:
- Receive group chat and direct messages from external IM
- Reply to incoming messages
- Proactively send messages to groups or users

## Architecture (read this first for troubleshooting)

The message flow for all external IM platforms is the same:

```
External IM (Feishu/QQ/Telegram) → Platform WebSocket/Webhook
  → Platform Adapter Process (listens for incoming events)
  → POST http://127.0.0.1:9700/api/chat
  → External API Adapter Process (relay layer)
  → Gateway WebSocket → Agent
```

**Each platform needs TWO separate processes** managed by the Launcher:
1. **Platform adapter** (e.g., `plugins.feishu.adapter`) — listens for incoming messages from the IM platform
2. **External API adapter** (`plugins.external_api.adapter`, port 9700) — shared relay that forwards to Gateway

These are **not** in-process plugins — they are subprocesses spawned by the Launcher via the `service` field in `plugin.json`.

## Prerequisites

- An app on the target IM platform's developer console (Feishu Open Platform, Telegram BotFather)
- Launcher running (manages adapter processes)
- `pip install lark-oapi` for Feishu, or respective SDK for Telegram

## Step-by-Step Guide (Feishu Example)

### Step 1: Verify `service` field in both `plugin.json` files

**This step is mandatory** — without the `service` field, Launcher won't start the adapter processes.

**`plugins/external_api/plugin.json`** (shared by all platforms):
```json
"service": {
  "cmd": "python -m plugins.external_api.adapter",
  "default_port": 9700,
  "health_check": "/api/health",
  "startup_timeout": 30,
  "auto_start": true
}
```

**`plugins/feishu/plugin.json`** (or `plugins/telegram/plugin.json`):
```json
"service": {
  "cmd": "python -m plugins.feishu.adapter",
  "auto_start": true,
  "startup_timeout": 30
}
```

**⚠️ Note** — PluginManager uses a merge strategy that preserves the `service` field when updating `plugin.json`. If `service` is `null` (older versions may have overwritten it), re-write it manually and restart Launcher.

### Step 2: Fix workspace path (common pitfall)

Launcher starts adapter processes from the **installation directory** (e.g., `deploy_test/src/`), but config is in the **workspace** (e.g., `runtime_deploy/`).

**Fix A — `opensquad/launcher.py`** (pass workspace to child processes):
```python
child_env["OPENSQUAD_WORKSPACE"] = syscfg.get_workspace()
```

**Fix B — `opensquad/system_config.py`** (read env var on init):
```python
_WORKSPACE_ROOT = os.environ.get("OPENSQUAD_WORKSPACE") or _DEFAULT_ROOT
```

Both fixes must be in place for adapter processes to read the correct config.

### Step 3: Configure `system_config.json`

Edit the file in your workspace (e.g., `runtime_deploy/system_config.json`).

**IM Platform config (Feishu example):**
```json
{
  "feishu": {
    "log_level": "INFO",
    "bots": [
      {
        "agent_id": "your-agent-id",
        "app_id": "cli_xxxxxxxxxxxxxxxxxxxx",
        "app_secret": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    ]
  }
}
```

For Telegram: use `"telegram"` key (same `bots` structure).

**External API config (shared, required once):**
```json
{
  "external_api": {
    "log_level": "INFO",
    "instances": [
      {
        "name": "default",
        "host": "0.0.0.0",
        "port": 9700,
        "default_agent_id": "your-agent-id",
        "api_key": "YOUR_EXTERNAL_API_KEY_HERE",
        "request_timeout": 120,
        "async_result_ttl": 600,
        "enabled": true
      }
    ]
  }
}
```

**Auth config (API key must match External API instance):**
```json
{
  "auth": {
    "gateway_token": "YOUR_GATEWAY_TOKEN_HERE",
    "external_api_key": "YOUR_EXTERNAL_API_KEY_HERE"
  }
}
```

### Step 4: Gateway Token Verification

The Gateway automatically validates the adapter's token against `auth.gateway_token`
in `system_config.json`. Ensure both Gateway and External API use the **same** token.

### Step 5: Restart services (order matters)

```
1. Stop Launcher
2. Check port 9700: netstat -ano | findstr :9700
   If an old External API process is stuck, kill it: taskkill /F /PID <pid>
3. Restart Launcher (auto-starts External API and IM platform adapters)
4. Start agent
5. Verify: netstat -ano | findstr :9700 — External API should be listening
```

### Step 6: Verify

Send a message from the IM platform → agent should receive and reply.

Check Launcher logs for adapter output:
- `[svc:external_api]` — External API logs
- `[svc:feishu]` / `[svc:telegram]` — platform adapter logs

## Verification Checklist

- [ ] `external_api/plugin.json` has `service` field
- [ ] IM platform `plugin.json` (feishu/telegram) has `service` field
- [ ] `plugin_manager.py` preserves `service` field
- [ ] `launcher.py` passes `OPENSQUAD_WORKSPACE` env var
- [ ] `system_config.py` reads `OPENSQUAD_WORKSPACE` env var
- [ ] `system_config.json` has platform bot config
- [ ] `system_config.json` has `external_api.instances`
- [ ] `system_config.json` has `auth.gateway_token` and `auth.external_api_key`
- [ ] `auth.gateway_token` is set (not placeholder) and matches External API config
- [ ] Adapter processes auto-start after Launcher restart
- [ ] Messages from IM platform reach the agent

## Common Problems

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `No enabled Feishu/Telegram bots configured` | Workspace path mismatch — adapter read wrong config | Check `OPENSQUAD_WORKSPACE` env var + `system_config.py` init |
| `Cannot connect to adapter` + `401 Unauthorized` | API key mismatch between External API and platform adapter | Sync `external_api.instances[].api_key` with `auth.external_api_key` |
| `502 Bad Gateway` + `Agent 'xxx' is not available` | Agent not registered on Gateway, or adapter token mismatch | Start agent; verify `auth.gateway_token` matches on Gateway and External API |
| `service: null` in plugin.json | PluginManager overwrote `service` field | Re-write plugin.json + fix `plugin_manager.py` preserve logic |
| `Plugin service external_api exited (code: 1)` | Port in use or config error | Kill stale process on port 9700, restart Launcher |
| `Missing lark-oapi SDK` (Feishu) | SDK not installed | `pip install lark-oapi` |
| `Missing aiogram SDK` (Telegram) | SDK not installed | `pip install aiogram`
