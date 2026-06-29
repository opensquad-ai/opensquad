# ServicePlugin 使用指南

## 概述

`ServicePlugin` 是一个通用的插件基类，用于自动管理需要后台服务的插件。任何带 `service/` 文件夹的插件都可以继承此类，自动获得以下功能：

- ✅ 插件加载时自动启动服务
- ✅ 服务健康检查（定期检测服务状态）
- ✅ 服务自动重启（健康检查失败时）
- ✅ 插件卸载时优雅停止服务
- ✅ 支持 `auto_start` 配置项控制是否自动启动

## 快速开始

### 1. 插件目录结构

```
plugins/
└── my_plugin/
    ├── plugin.py              # 插件主文件
    ├── my_tool.py             # 工具实现
    └── service/
        ├── main.py            # FastAPI 服务（或 service.py 用于 Flask）
        └── requirements.txt
```

### 2. 实现插件类

在 `plugin.py` 中继承 `ServicePlugin`：

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
            "description": "服务端口",
        },
        "auto_start": {
            "type": "boolean",
            "default": True,
            "description": "插件加载时自动启动服务",
        },
    },
)
class MyPlugin(ServicePlugin):
    """My plugin with auto-start service support."""

    def __init__(self, context: Context):
        # 调用 ServicePlugin 的初始化，配置服务参数
        super().__init__(
            context=context,
            service_script="main.py",        # 或 "service.py" 用于 Flask
            health_endpoint="/health",       # 健康检查端点
            service_name="MyPlugin",         # 日志中显示的名称
            max_startup_wait=30,             # 最大启动等待时间（秒）
            health_check_interval=60,        # 健康检查间隔（秒）
        )

    def get_tool_modules(self) -> List[Dict[str, Any]]:
        """返回工具模块"""
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

### 3. 实现服务

在 `service/main.py` 中实现 FastAPI 服务：

```python
from fastapi import FastAPI
import uvicorn

app = FastAPI()


@app.get("/health")
def health():
    """健康检查端点（必需）"""
    return {"status": "ok"}


@app.get("/my-endpoint")
def my_endpoint():
    """你的业务端点"""
    return {"result": "success"}


if __name__ == "__main__":
    # 从环境变量或配置文件读取端口
    import os
    port = int(os.environ.get("PORT", 9000))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
```

### 4. 完成！

就这样！你的插件现在会在加载时自动启动服务，卸载时自动停止服务。

## ServicePlugin 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `context` | `Context` | （必需） | 插件上下文 |
| `service_script` | `str` | `"main.py"` | service 文件夹下的启动脚本名称 |
| `health_endpoint` | `str` | `"/health"` | 健康检查端点路径 |
| `service_name` | `str` | `"Service"` | 服务名称（用于日志） |
| `max_startup_wait` | `int` | `30` | 最大启动等待时间（秒） |
| `health_check_interval` | `int` | `60` | 健康检查间隔（秒） |

## 配置项

插件需要在 `config_schema` 中定义以下配置项：

```python
config_schema={
    "port": {
        "type": "integer",
        "default": 9000,
        "description": "服务端口",
    },
    "auto_start": {
        "type": "boolean",
        "default": True,
        "description": "插件加载时自动启动服务",
    },
}
```

- **`port`（必需）**：服务监听端口
- **`auto_start`（可选）**：是否自动启动服务，默认 `True`

## 健康检查端点要求

服务必须实现一个健康检查端点（默认 `/health`），返回 HTTP 200 状态码：

**FastAPI 示例：**
```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

**Flask 示例：**
```python
@app.route("/health")
def health():
    return {"status": "ok"}, 200
```

## 实际案例

### Whisper 插件

```python
class WhisperPlugin(ServicePlugin):
    def __init__(self, context: Context):
        super().__init__(
            context=context,
            service_script="service.py",     # Flask 服务
            health_endpoint="/health",
            service_name="WhisperPlugin",
        )
```

### WebSearch 插件

```python
class WebSearchPlugin(ServicePlugin):
    def __init__(self, context: Context):
        super().__init__(
            context=context,
            service_script="main.py",        # FastAPI 服务
            health_endpoint="/health",
            service_name="WebSearchPlugin",
        )
```

## 代码对比

**使用 ServicePlugin 前**（需要 ~120 行代码）：
- ❌ 手动实现 `_start_service()`
- ❌ 手动实现 `_stop_service()`
- ❌ 手动实现 `_check_service_health()`
- ❌ 手动实现 `_start_health_monitor()`
- ❌ 大量重复代码

**使用 ServicePlugin 后**（只需 ~10 行代码）：
- ✅ 只需继承 `ServicePlugin`
- ✅ 调用 `super().__init__()` 配置参数
- ✅ 所有服务管理逻辑自动处理
- ✅ 代码简洁、易维护

## 常见问题

### Q: 如何禁用服务自动启动？

在插件配置中设置 `auto_start: false`：

```json
{
  "plugins": {
    "my_plugin": {
      "auto_start": false
    }
  }
}
```

### Q: 如何修改健康检查间隔？

在插件初始化时设置：

```python
super().__init__(
    context=context,
    health_check_interval=120,  # 每 120 秒检查一次
    ...
)
```

### Q: 服务启动失败怎么办？

检查以下几点：
1. 端口是否被占用（`netstat -ano | findstr <port>`）
2. `service_script` 路径是否正确
3. 服务脚本是否有语法错误
4. 查看日志输出（`plugins.<plugin_name>`）

### Q: 如何查看服务日志？

服务进程的 stdout/stderr 会输出到独立的控制台窗口（Windows）或被重定向到父进程（Linux）。

---

## 总结

使用 `ServicePlugin` 基类，你可以：
- **减少 90% 的样板代码**
- **避免重复实现服务管理逻辑**
- **保证所有服务插件的一致性**
- **专注于业务逻辑开发**

开始使用 `ServicePlugin` 让你的插件开发更简单！ 🚀
