# OpenSquad 路径架构约定

## 核心原则：两个"家"，永不混淆

OpenSquad 有两个核心目录，职责完全不同：

```
                    ┌─────────────────────────┐
                    │   安装目录 (install dir)  │
                    │   例: opensquad/src/      │
                    │                          │
                    │   • 代码 (Python 源码)     │
                    │   • 内置插件 (plugins/)    │
                    │   • 内置 Skills            │
                    │   • 模板配置 (.example)     │
                    │                          │
                    │   只读，由 git 管理        │
                    └─────────────────────────┘

                    ┌─────────────────────────┐
                    │   工作区 (workspace)      │
                    │   例: ~/.opensquad/       │
                    │       workspace/          │
                    │                          │
                    │   • system_config.json   │
                    │   • 日志 (data/logs/)     │
                    │   • 数据库 (data/**/*.db) │
                    │   • 插件用户数据          │
                    │   • 会话 (data/sessions/) │
                    │   • Agent 配置 (agents/)  │
                    │                          │
                    │   读写，用户数据           │
                    └─────────────────────────┘
```

**规则**: 所有**运行时产生的数据**写入 workspace，所有**代码和内置资源**从安装目录读取。

## 环境变量

| 变量 | 指向 | 用途 |
|------|------|------|
| `OPENSQUAD_WORKSPACE` | 工作区根 | 所有进程统一查找运行时数据 |
| `PYTHONPATH` | 安装目录 | 子进程能找到 opensquad 包 |

## 代码中的正确写法

### ✅ 写入数据 → 用 workspace

```python
# 从环境变量获取 workspace 根
ws = os.environ.get("OPENSQUAD_WORKSPACE") or default_path
# 写入日志/数据库/会话
log_path = os.path.join(ws, "data", "logs", "gateway.log")
db_path  = os.path.join(ws, "data", "plugins", "foo", "analytics.db")
```

### ✅ 读取配置 → 用 workspace

```python
# system_config.py 已经在模块加载时处理
from opensquad.system_config import syscfg
cfg_path = syscfg.workspace_config_path()  # workspace/system_config.json
port     = syscfg.port("gateway")          # 从 workspace config 读
```

### ✅ 检查端口 → 用 syscfg

```python
# 不要硬编码端口号
# ❌ port = 8371
# ✅
from opensquad.system_config import syscfg
port = syscfg.port("launcher")  # 9600
```

### ✅ 列出插件 → 用安装目录

```python
# 插件代码在安装目录下
plugins_dir = os.path.join(syscfg.get_builtin_root(), "plugins")
```

## 常见反模式（❌ 禁止）

| ❌ 反模式 | ✅ 正确做法 |
|---|---|
| 硬编码端口 `8371` | `syscfg.port("launcher")` |
| 从 `src/system_config.json` 读配置 | workspace config（`syscfg` 自动处理） |
| 往 `src/data/` 写日志/数据库 | workspace `data/` |
| 从 workspace `data/plugins/` 找插件代码 | 安装目录 `plugins/` |
| 不设 `OPENSQUAD_WORKSPACE` 环境变量 | `start_cmd.py` 必须设置 |

## 诊断

```bash
# 查看当前工作区
opensquad doctor          # 显示 workspace 路径
opensquad config validate # 验证配置完整性

# 手动检查
echo %OPENSQUAD_WORKSPACE%          # Windows
echo $OPENSQUAD_WORKSPACE            # Linux/macOS
```

## 历史问题清单（都是违反了此约定）

| 问题 | 违反的规则 | 修复 |
|---|---|---|
| `is_service_enabled()` 默认 False | 读了 src 而非 workspace | 修复默认值 + 统一到 workspace |
| `token_analytics` 柱状图无数据 | 写 install dir，读 workspace | 统一到 workspace |
| `opensquad plugin list` 显示空 | 从 workspace 找插件，实际在 install dir | 改为读 install dir |
| `opensquad status` 端口 8371 | 硬编码端口 | 改为 `syscfg.port()` |
| `opensquad status` API 解析报错 | 读错响应格式 | 对齐 API |
| feishu adapter 不收消息 | CWD 设错 | 改为 project root |
| external_api adapter 不自启 | service_toggle 缺失 | 修复 plugin.json |
