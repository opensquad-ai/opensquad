# Skill: Self Configuration

**Skill ID**: `self_config`  
**Display Name**: Self Configuration  
**Description**: Allows the Agent to modify its own configuration file and reload

---

## Tool: Add Plugin to Configuration

### add_plugin_to_config

Adds a plugin to the current Agent's `config.json` and automatically reloads.

**Usage Example:**

```python
# Step 1: Read current configuration
import json

config_path = "agents/coder/config.json"

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

# Step 2: Add plugin to tools array (if not already present)
plugins_to_add = ["chat_account", "agent_factory"]

for plugin in plugins_to_add:
    if plugin not in config.get("tools", []):
        config.setdefault("tools", []).append(plugin)
        print(f"Added {plugin} to tools")
    else:
        print(f"{plugin} already in tools")

# Step 3: Ensure tool_levels configuration exists
config.setdefault("tool_levels", {})
for plugin in plugins_to_add:
    if plugin not in config["tool_levels"]:
        config["tool_levels"][plugin] = "core"
        print(f"Set {plugin} level to core")

# Step 4: Write back to configuration file
with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)

print("Configuration updated")

# Step 5: Reload plugins
import opensquad.tools.agent_setup as agent_setup

result = agent_setup.reload_plugins()
print(result)
```

---

## Complete Workflow

### 1. Read Configuration
Use `filesystem.read_file` to read `agents/{agent_dir}/config.json`

### 2. Modify Configuration
Parse JSON, add plugin to the `tools` array and `tool_levels` dictionary

### 3. Save Configuration
Use `filesystem.write_file` to write back to the configuration file

### 4. Reload
Call `agent_setup.reload_plugins()` to apply the configuration immediately

---

## Notes

1. **Backup**: It is recommended to back up the original file before modifying the configuration
2. **JSON Format**: Ensure the written-back JSON is correctly formatted
3. **Restart vs Reload**:
   - `reload_plugins()` - Hot reload, no restart required
   - Launcher API restart - Full restart of the agent process
4. **Tool Levels**:
   - `core` - Core tools, always loaded
   - `extended` - Extended tools, loaded on demand
   - `high` - High-level tools

---

## Quick Commands

### Add a Single Plugin
```python
# Read
config = json.loads(filesystem.read_file("agents/coder/config.json"))

# Add
if "chat_account" not in config["tools"]:
    config["tools"].append("chat_account")
    config.setdefault("tool_levels", {})["chat_account"] = "core"

# Save
filesystem.write_file("agents/coder/config.json", json.dumps(config, ensure_ascii=False, indent=2))

# Reload
agent_setup.reload_plugins()
```
