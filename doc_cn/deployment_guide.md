# 部署指南

本文档介绍如何在不同环境中部署 OpenSQuad。
**快速开始** 见 [README 快速开始](../README_ZH.md#快速开始约-10-分钟)
章节 —— 本文档面向需要深入配置的用户。

---

## 系统架构概览

OpenSquad 由以下服务组成：

| 服务 | 默认端口 | 说明 |
|------|----------|------|
| Gateway Backend | 9555 (本地) | Web UI 后端，FastAPI 服务 |
| Launcher | 9600 | Agent 管理与生命周期控制 |
| Plugin Registry | 9720 | 插件注册与发现服务 |
| Frontend | 9530 | React 前端开发服务器(仅开发模式) |

---

## 方式一:一键脚本安装(推荐,新手友好)

一键脚本在一个命令内完成前置检查、依赖安装、工作区初始化和服务启动。

### 前置条件

- Python 3.11+
- Node.js 18+(Web UI 前端)
- Git

### 步骤

**Windows**

```bash
git clone https://github.com/opensquad-ai/opensquad.git && cd opensquad && install.bat
```

**Linux / macOS**

```bash
git clone https://github.com/opensquad-ai/opensquad.git && cd opensquad && bash install.sh
```

脚本会自动完成：检查环境 → 安装依赖 → 初始化工作区 → 启动全部服务。

启动后，访问 `http://localhost:9555`，在 **Model Cards** 页面配置 LLM API Key
(详见 [Model Cards 指南](model_cards_guide.md))。

### 安装内容

- Python 依赖(优先使用 `uv`,没有则回退 `pip`)
- 前端依赖(Node.js、npm)
- 工作区目录(默认 `~/.opensquad/workspace`,可通过 `OPENSQUAD_WORKSPACE` 环境变量覆盖)
- 工作区内的默认 Agent 配置

### 适用场景

适合想最快跑起来、不需要自定义安装(Python 版本、镜像源、system service
集成等)的用户。如果需要这些,切换到方式二/三。

---

## 方式二:本地部署(uv)

`uv` 是极速 Python 包管理器,通过 `uv.lock` 提供可复现的安装。推荐开发者
和贡献者使用。

### 前置条件

- Python 3.11+
- Node.js 18+(前端开发)
- [uv](https://github.com/astral-sh/uv)

### 步骤

**1. 安装 uv**

```bash
pip install uv
```

**2. 克隆并安装**

```bash
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad

# 安装 Python 依赖
uv sync

# 安装前端依赖
cd src/opensquad/gateway/nexuschat-pro
npm install
cd ../../../..
```

**3. 初始化项目**

```bash
uv run opensquad init
```

初始化会创建：
- 工作区目录结构
- 默认 Agent 配置
- `system_config.json`(基于示例模板)

**4. 启动服务**

```bash
uv run opensquad start
```

Web UI 访问地址：`http://localhost:9555`。

---

## 方式三:本地部署(pip)

如果不能或不想安装 `uv`,使用此方式。功能与方式二等价,冷安装更慢。

### 前置条件

- Python 3.11+
- Node.js 18+(前端开发)
- pip(Python 自带)

### 步骤

**1. 克隆并安装**

```bash
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad

# 安装 Python 依赖
pip install -e .
pip install -r src/opensquad/gateway/backend/requirements.txt

# 安装前端依赖
cd src/opensquad/gateway/nexuschat-pro
npm install
cd ../../../..
```

**2. 初始化项目**

```bash
opensquad init
```

**3. 启动服务**

```bash
opensquad start
```

Web UI 访问地址：`http://localhost:9555`。

---

## 方式四:Docker 部署

Docker 是以隔离服务方式运行 OpenSQuad 的最简方式,适合类生产环境、共享
服务器,或想快速评估、不污染本地 Python 环境的用户。

### 前置条件

- Docker 20.10+
- Docker Compose 2.0+

### 步骤

**1. 克隆项目**

```bash
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad
```

**2. 启动服务**

```bash
docker compose up -d
```

**3. 访问 Web UI**

打开浏览器访问 `http://localhost:9555`。

### Docker Compose 配置说明

```yaml
services:
  opensquad:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: opensquad
    restart: unless-stopped
    ports:
      - "9555:9555"   # Gateway Backend
      - "9600:9600"   # Launcher
      - "9720:9720"   # Plugin Registry
    volumes:
      - opensquad-data:/data          # 持久化数据
      # 可选：挂载自定义配置
      # - ./src/system_config.json:/app/src/system_config.json:ro
    environment:
      - OPENSQUAD_WORKSPACE=/data/workspaces/default
      - PYTHONPATH=/app/src
```

### 数据持久化

Docker 部署使用命名卷 `opensquad-data` 存储所有持久化数据：

- 工作区文件(`/data/workspaces/`)
- 数据库文件(`/data/gateway/backend/chat.db`)
- 日志文件(`/data/logs/`)
- 插件数据(`/data/plugins/`)
- 会话数据(`/data/sessions/`)

### 自定义配置

如需自定义配置,可以将 `system_config.json` 挂载到容器中：

```bash
cp src/system_config.example.json src/system_config.json
# 编辑 src/system_config.json
```

然后在 `docker-compose.yml` 中取消注释 volumes 配置中的自定义配置行。

### 健康检查

容器内置健康检查，每 30 秒检查一次 Gateway 的 `/health` 端点。

---

## 前端开发模式

开发前端时，可以单独启动前端开发服务器：

```bash
cd src/opensquad/gateway/nexuschat-pro

# 创建 .env.local（如果还没有）
echo "VITE_BACKEND_HOST=127.0.0.1" > .env.local
echo "VITE_BACKEND_PORT=9555" >> .env.local

# 启动开发服务器
npm run dev
```

前端开发服务器默认运行在 `http://localhost:9530`，支持热更新。

---

## 环境变量

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `OPENSQUAD_WORKSPACE` | 工作区目录路径 | 项目根目录 |
| `GATEWAY_PORT` | Gateway 端口 | 9555 |
| `GATEWAY_HOST` | Gateway 绑定地址 | 127.0.0.1 |
| `LAUNCHER_PORT` | Launcher 端口 | 9600 |
| `LAUNCHER_HOST` | Launcher 绑定地址 | 127.0.0.1 |
| `EXTERNAL_ADAPTER_PORT` | 外部适配器端口 | 9700 |
| `LOG_LEVEL` | 日志级别 | INFO |
| `LOG_DIR` | 日志目录 | 工作区/data/logs |

---

## 桌面应用(Electron)

OpenSQuad 也以桌面应用形式分发(Electron + Vite + PyInstaller,
代号 **NexusChat Pro**),覆盖 Windows / macOS / Linux。

**→ 完整构建指南(开发模式、多平台 build、CI 流水线、前置条件、
常见问题)见 [桌面应用构建指南](desktop_build.md)。**

> 已有打包产物、需要**替换上线**（打新包 → 停服务 → 替换 run.exe → 重启 →
> 端到端验证）时，直接看 [desktop_build.md 的「从源码打包到部署上线」章节](desktop_build.md#从源码打包到部署上线完整流程与踩坑记录)，
> 含干净环境/代理/损坏目录/run.exe 占用等历次踩坑与处理方式。

---

## 生产环境建议

### 1. 使用反向代理

建议使用 Nginx 或 Caddy 作为反向代理：

```nginx
server {
    listen 80;
    server_name opensquad.example.com;

    location / {
        proxy_pass http://127.0.0.1:9555;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 2. 配置 API 密钥

- 不要在配置文件中硬编码 API 密钥
- 使用环境变量注入敏感信息
- 建议使用 Docker secrets 或 .env 文件管理密钥

### 3. 日志管理

- 日志默认存储在 `data/logs/` 目录
- 支持日志轮转：`log_max_size_mb` 控制单文件大小，`log_backup_count` 控制保留数量
- 生产环境建议设置 `LOG_LEVEL=WARNING`

### 4. 资源限制

- LLM API 调用需要稳定的网络连接
- 建议为 Agent 节点分配足够的 CPU 和内存
- 大型工作区建议使用 SSD 存储
