# OpenSquad Path Architecture Conventions

## Core Principle: Two "Homes", Never Confused

OpenSquad has two core directories with completely different responsibilities:

```
                    ┌─────────────────────────┐
                    │   Install Directory      │
                    │   e.g. opensquad/src/    │
                    │                          │
                    │   • Code (Python source) │
                    │   • Built-in plugins     │
                    │     (plugins/)           │
                    │   • Built-in Skills      │
                    │   • Template configs     │
                    │     (.example)           │
                    │                          │
                    │   Read-only, git-managed │
                    └─────────────────────────┘

                    ┌─────────────────────────┐
                    │   Workspace              │
                    │   e.g. ~/.opensquad/     │
                    │       workspace/         │
                    │                          │
                    │   • system_config.json   │
                    │   • Logs (data/logs/)    │
                    │   • Databases            │
                    │     (data/**/*.db)       │
                    │   • Plugin user data     │
                    │   • Sessions             │
                    │     (data/sessions/)     │
                    │   • Agent configs        │
                    │     (agents/)            │
                    │                          │
                    │   Read-write, user data  │
                    └─────────────────────────┘
```

**Rule**: All **runtime-generated data** goes to the workspace; all **code and built-in resources** are read from the install directory.

## Environment Variables

| Variable | Points To | Purpose |
|------|------|------|
| `OPENSQUAD_WORKSPACE` | Workspace root | All processes uniformly locate runtime data |
| `PYTHONPATH` | Install directory | Child processes can find the opensquad package |

## Correct Patterns in Code

### ✅ Writing Data → Use workspace

```python
# Get workspace root from environment variable
ws = os.environ.get("OPENSQUAD_WORKSPACE") or default_path
# Write logs / databases / sessions
log_path = os.path.join(ws, "data", "logs", "gateway.log")
db_path  = os.path.join(ws, "data", "plugins", "foo", "analytics.db")
```

### ✅ Reading Config → Use workspace

```python
# system_config.py already handles this at module load time
from opensquad.system_config import syscfg
cfg_path = syscfg.workspace_config_path()  # workspace/system_config.json
port     = syscfg.port("gateway")          # read from workspace config
```

### ✅ Checking Ports → Use syscfg

```python
# Never hardcode port numbers
# ❌ port = 8371
# ✅
from opensquad.system_config import syscfg
port = syscfg.port("launcher")  # 9600
```

### ✅ Listing Plugins → Use install directory

```python
# Plugin code lives under the install directory
plugins_dir = os.path.join(syscfg.get_builtin_root(), "plugins")
```

## Common Anti-Patterns (❌ Forbidden)

| ❌ Anti-Pattern | ✅ Correct Approach |
|---|---|
| Hardcoding port `8371` | `syscfg.port("launcher")` |
| Reading config from `src/system_config.json` | workspace config (`syscfg` handles it automatically) |
| Writing logs/DB to `src/data/` | workspace `data/` |
| Finding plugin code in workspace `data/plugins/` | install directory `plugins/` |
| Not setting `OPENSQUAD_WORKSPACE` env var | `start_cmd.py` must set it |

## Diagnostics

```bash
# Check current workspace
opensquad doctor          # shows workspace path
opensquad config validate # validates config integrity

# Manual check
echo %OPENSQUAD_WORKSPACE%          # Windows
echo $OPENSQUAD_WORKSPACE            # Linux/macOS
```

## Historical Issues (all were violations of this convention)

| Issue | Rule Violated | Fix |
|---|---|---|
| `is_service_enabled()` defaulting to False | Read from src instead of workspace | Fixed default + unified to workspace |
| `token_analytics` bar chart had no data | Wrote to install dir, read from workspace | Unified to workspace |
| `opensquad plugin list` showed empty | Found plugins in workspace, actual in install dir | Changed to read install dir |
| `opensquad status` port 8371 | Hardcoded port | Changed to `syscfg.port()` |
| `opensquad status` API parsing error | Read wrong response format | Aligned API |
| feishu adapter not receiving messages | CWD set incorrectly | Changed to project root |
| external_api adapter not auto-starting | Missing service_toggle | Fixed plugin.json |
