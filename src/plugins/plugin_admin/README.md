# Plugin Admin

Version: 1.0.0 | Author: lihua179 | Type: tool

## Overview

Lets the Agent manage other plugins directly in chat, without entering an admin backend. All operations are pure file-system reads and writes; no external HTTP service, no subprocess/eval/exec/os.system.

## Tool Reference

| Method | Description |
|---|---|
| `list_plugins()` | List all discovered plugins and their status (name, enabled, version, type, tags…) |
| `get_plugin_info(name)` | View full metadata for a plugin: tool list, hooks, dependencies, config schema |
| `enable_plugin(name)` | Enable a plugin (writes `enabled=true` to `plugin.json`, triggers hot-reload) |
| `disable_plugin(name)` | Disable a plugin (writes `enabled=false`, triggers hot-reload, plugin is unloaded from memory immediately) |
| `get_plugin_config(name)` | Read plugin configuration (schema defaults + user-persisted values; `secret` fields shown as `***`) |
| `set_plugin_config(name, config)` | Write plugin configuration and trigger hot-reload so new config takes effect immediately |
| `reload_plugins()` | Manually trigger a full hot-reload (writes `plugins/.reload_ts`) |

## Usage Examples

```
List all plugins:
  plugin_admin.list_plugins()

View email_assistant details:
  plugin_admin.get_plugin_info("email_assistant")

Enable the websearch plugin:
  plugin_admin.enable_plugin("websearch")

Disable the telegram plugin:
  plugin_admin.disable_plugin("telegram")

Read email_assistant config:
  plugin_admin.get_plugin_config("email_assistant")

Set email_assistant's IMAP address (other fields are not affected):
  plugin_admin.set_plugin_config("email_assistant", {
      "imap_host": "imap.gmail.com",
      "username": "me@gmail.com"
  })

Manually trigger hot-reload:
  plugin_admin.reload_plugins()
```

## Hot-Reload Mechanism

Every call to `enable_plugin` / `disable_plugin` / `set_plugin_config` / `reload_plugins` writes `plugins/.reload_ts`. The AgentRunner calls `plugin_manager.check_reload_needed()` on each loop tick; when a timestamp change is detected it runs `reload_plugins()` — no process restart required.

## Security Notes

- `disable_plugin("plugin_admin")` is rejected (prevents the Agent from disabling its own management capability)
- `get_plugin_config` returns `***` placeholder for `secret: true` fields — plaintext passwords are never exposed
- `set_plugin_config` performs incremental updates (merge) and will not clear any existing fields

## Installation (from GitHub)

```bash
# Copy the plugin directory into plugins/
cp -r plugin_admin /path/to/project/plugins/
# Trigger hot-reload (or restart the Agent process)
touch plugins/.reload_ts
```

## Release Info

- GitHub: https://github.com/lihua179/plugin_admin
- No third-party dependencies
