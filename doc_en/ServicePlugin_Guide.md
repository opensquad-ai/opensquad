# ServicePlugin Usage Guide

## Overview

`ServicePlugin` is a generic plugin base class for automatically managing plugins
that need a background service. Any plugin with a `service/` folder can inherit
from this class and automatically get the following features:

- ✅ Service auto-start when the plugin is loaded
- ✅ Service health check (periodic status probe)
- ✅ Service auto-restart when the health check fails
- ✅ Graceful service shutdown when the plugin is unloaded
- ✅ An `auto_start` config option to control whether the service starts automatically

## Quick Start

### 1. Plugin directory layout

```
plugins/
└── my_plugin/
    ├── plugin.py              # main plugin file
    ├── my_tool.py             # tool implementation
    └── service/
        ├── main.py            # FastAPI service (or service.py for Flask)
        └── requirements.txt
```

### 2. Implement the plugin class

In `plugin.py`, inherit from `ServicePlugin`:

```python
from opensquad.plugin_api import register, Context
from opensquad.service_plugin import ServicePlugin
import importlib
import logging
from typing import Any, Dict, List

logger = logging.getLogger("plugins.my_plugin")


@register(
    name="my_plugin",
    author="Your Name",
    description="My awesome plugin with auto-start service",
    version="1.0.0",
    plugin_type="tool",
    display_name="My Plugin",
    dependencies={"pip": ["requests", "fastapi", "uvicorn"]},
    tags=["custom"],
    config_schema={
        "port": {
            "type": "integer",
            "default": 9000,
            "description": "Service port",
        },
        "auto_start": {
            "type": "boolean",
            "default": True,
            "description": "Auto-start the service when the plugin is loaded",
        },
    },
)
class MyPlugin(ServicePlugin):
    """My plugin with auto-start service support."""

    def __init__(self, context: Context):
        # Call ServicePlugin.__init__ to configure the service parameters
        super().__init__(
            context=context,
            service_script="main.py",        # or "service.py" for Flask
            health_endpoint="/health",       # health check endpoint
            service_name="MyPlugin",         # name shown in logs
            max_startup_wait=30,             # max startup wait (seconds)
            health_check_interval=60,        # health check interval (seconds)
        )

    def get_tool_modules(self) -> List[Dict[str, Any]]:
        """Return the tool modules"""
        tools = []
        try:
            module = importlib.import_module("plugins.my_plugin.my_tool")
            tools.append({
                "name": "my_plugin",
                "module": module,
                "level": "core",
                "auto_register": False,
                "requires_agent_id": False,
            })
        except ImportError as e:
            logger.error(f"[MyPlugin] Cannot import my_tool module: {e}")
        return tools
```

### 3. Implement the service

Implement the FastAPI service in `service/main.py`:

```python
from fastapi import FastAPI
import uvicorn

app = FastAPI()


@app.get("/health")
def health():
    """Health check endpoint (required)"""
    return {"status": "ok"}


@app.get("/my-endpoint")
def my_endpoint():
    """Your business endpoint"""
    return {"result": "success"}


if __name__ == "__main__":
    # Read the port from an environment variable or config file
    import os
    port = int(os.environ.get("PORT", 9000))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
```

### 4. Done!

That's it. Your plugin will now auto-start the service on load and auto-stop it
on unload.

## ServicePlugin Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `context` | `Context` | (required) | Plugin context |
| `service_script` | `str` | `"main.py"` | Startup script name inside the `service/` folder |
| `health_endpoint` | `str` | `"/health"` | Health check endpoint path |
| `service_name` | `str` | `"Service"` | Service name (used in logs) |
| `max_startup_wait` | `int` | `30` | Max startup wait time (seconds) |
| `health_check_interval` | `int` | `60` | Health check interval (seconds) |

## Config Schema

The plugin must define the following entries in its `config_schema`:

```python
config_schema={
    "port": {
        "type": "integer",
        "default": 9000,
        "description": "Service port",
    },
    "auto_start": {
        "type": "boolean",
        "default": True,
        "description": "Auto-start the service when the plugin is loaded",
    },
}
```

- **`port` (required)**: port the service listens on
- **`auto_start` (optional)**: whether to start the service automatically; default `True`

## Health Check Endpoint Requirements

The service must implement a health check endpoint (default `/health`) that
returns HTTP 200:

**FastAPI example:**
```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

**Flask example:**
```python
@app.route("/health")
def health():
    return {"status": "ok"}, 200
```

## Real-World Examples

### Whisper plugin

```python
class WhisperPlugin(ServicePlugin):
    def __init__(self, context: Context):
        super().__init__(
            context=context,
            service_script="service.py",     # Flask service
            health_endpoint="/health",
            service_name="WhisperPlugin",
        )
```

### WebSearch plugin

```python
class WebSearchPlugin(ServicePlugin):
    def __init__(self, context: Context):
        super().__init__(
            context=context,
            service_script="main.py",        # FastAPI service
            health_endpoint="/health",
            service_name="WebSearchPlugin",
        )
```

## Before/After Comparison

**Before using ServicePlugin** (~120 lines of code):
- ❌ Manually implement `_start_service()`
- ❌ Manually implement `_stop_service()`
- ❌ Manually implement `_check_service_health()`
- ❌ Manually implement `_start_health_monitor()`
- ❌ Lots of repeated boilerplate

**After using ServicePlugin** (~10 lines of code):
- ✅ Just inherit from `ServicePlugin`
- ✅ Call `super().__init__()` to configure parameters
- ✅ All service management is handled automatically
- ✅ Concise, easy-to-maintain code

## FAQ

### Q: How do I disable service auto-start?

Set `auto_start: false` in the plugin config:

```json
{
  "plugins": {
    "my_plugin": {
      "auto_start": false
    }
  }
}
```

### Q: How do I change the health check interval?

Configure it when initializing the plugin:

```python
super().__init__(
    context=context,
    health_check_interval=120,  # check every 120 seconds
    ...
)
```

### Q: What if the service fails to start?

Check the following:
1. Whether the port is in use (`netstat -ano | findstr <port>`)
2. Whether the `service_script` path is correct
3. Whether the service script has syntax errors
4. Check the log output (`plugins.<plugin_name>`)

### Q: How do I view the service logs?

The service process's stdout/stderr is routed to a separate console window on
Windows, or to the parent process on Linux.

---

## Summary

With the `ServicePlugin` base class you can:

- **Cut ~90% of boilerplate code**
- **Avoid re-implementing service management logic**
- **Guarantee consistency across all service plugins**
- **Focus on business-logic development**

Get started with `ServicePlugin` and make plugin development simpler! 🚀
