# Deployment Guide

This document describes how to deploy OpenSquad in different environments.
For the **Quick Start** see the [README Quick Start](../README.md#quick-start-about-10-minutes)
section — this guide is for users who need deeper configuration.

---

## System Architecture Overview

OpenSquad consists of the following services:

| Service | Default Port | Description |
|------|----------|------|
| Gateway Backend | 9555 (local) | Web UI backend, FastAPI service |
| Launcher | 9600 | Agent management and lifecycle control |
| Plugin Registry | 9720 | Plugin registration and discovery service |
| Frontend | 9530 | React frontend dev server (dev mode only) |

---

## Method 1: One-Click Script (Recommended for Beginners)

The one-click script handles prerequisites, dependency installation, workspace
initialization, and service startup in a single command.

### Prerequisites

- Python 3.11+
- Node.js 18+ (for the Web UI frontend)
- Git

### Steps

**Windows**

```bash
git clone https://github.com/opensquad-ai/opensquad.git && cd opensquad && install.bat
```

**Linux / macOS**

```bash
git clone https://github.com/opensquad-ai/opensquad.git && cd opensquad && bash install.sh
```

The script automatically: checks prerequisites → installs dependencies →
initializes workspace → starts all services.

After first start, open the Web UI at `http://localhost:9555` and add your
LLM API key via **Model Cards** (see
[Model Cards Guide](model_cards_guide.md)).

### What gets installed

- Python dependencies (via `uv` if available, otherwise `pip`)
- Frontend dependencies (Node.js, npm)
- Workspace directory at `~/.opensquad/workspace` (override with
  `OPENSQUAD_WORKSPACE` env var)
- Default agent configuration in the workspace

### When to use

Use this method if you want the fastest path to a running OpenSquad and
don't need to customize the install (Python version, mirror, system
service integration, etc.). For any of those, switch to Method 2/3.

---

## Method 2: Local Deployment (uv)

`uv` is a fast Python package manager that gives reproducible installs via
`uv.lock`. Recommended for developers and contributors.

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend development)
- [uv](https://github.com/astral-sh/uv)

### Steps

**1. Install uv**

```bash
pip install uv
```

**2. Clone and install**

```bash
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad

# Install Python dependencies
uv sync

# Install frontend dependencies
cd src/opensquad/gateway/nexuschat-pro
npm install
cd ../../../..
```

**3. Initialize the project**

```bash
uv run opensquad init
```

This creates:
- Workspace directory structure
- Default Agent configuration
- `system_config.json` from the example template

**4. Start services**

```bash
uv run opensquad start
```

The Web UI is available at `http://localhost:9555`.

---

## Method 3: Local Deployment (pip)

Use this method if you can't or don't want to install `uv`. Functionally
equivalent to Method 2 but slower on cold installs.

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend development)
- pip (bundled with Python)

### Steps

**1. Clone and install**

```bash
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad

# Install Python dependencies
pip install -e .
pip install -r src/opensquad/gateway/backend/requirements.txt

# Install frontend dependencies
cd src/opensquad/gateway/nexuschat-pro
npm install
cd ../../../..
```

**2. Initialize the project**

```bash
opensquad init
```

**3. Start services**

```bash
opensquad start
```

The Web UI is available at `http://localhost:9555`.

---

## Method 4: Docker Deployment

Docker is the simplest way to run OpenSquad as an isolated service with
persistent data, useful for production-like setups, shared servers, or
quick evaluation without polluting your local Python environment.

### Prerequisites

- Docker 20.10+
- Docker Compose 2.0+

### Steps

**1. Clone the project**

```bash
git clone https://github.com/opensquad-ai/opensquad.git
cd opensquad
```

**2. Start services**

```bash
docker compose up -d
```

**3. Access the Web UI**

Open your browser and navigate to `http://localhost:9555`.

### Docker Compose Configuration Overview

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
      - opensquad-data:/data          # Persistent data
      # Optional: mount custom configuration
      # - ./src/system_config.json:/app/src/system_config.json:ro
    environment:
      - OPENSQUAD_WORKSPACE=/data/workspaces/default
      - PYTHONPATH=/app/src
```

### Data Persistence

Docker deployment uses the named volume `opensquad-data` to store all
persistent data:

- Workspace files (`/data/workspaces/`)
- Database files (`/data/gateway/backend/chat.db`)
- Log files (`/data/logs/`)
- Plugin data (`/data/plugins/`)
- Session data (`/data/sessions/`)

### Custom Configuration

To use a custom configuration, mount `system_config.json` into the
container:

```bash
cp src/system_config.example.json src/system_config.json
# edit src/system_config.json
```

Then uncomment the mount line in the `volumes` section of
`docker-compose.yml`.

### Health Check

The container includes a built-in health check that probes the Gateway
`/health` endpoint every 30s.

---

## Frontend Development

For development of the React frontend, run the frontend separately:

```bash
cd src/opensquad/gateway/nexuschat-pro
npm run dev
```

The frontend dev server runs on port 9530. The backend should be running
in parallel via `opensquad start` (or `docker compose up`).

---

## Environment Variables

| Variable | Required | Default | Description |
|------|------|------|------|
| `OPENSQUAD_WORKSPACE` | No | auto-detected | Workspace root directory |
| `PYTHONPATH` | No | auto-set | Path to opensquad package |
| `GATEWAY_PORT` | No | 9555 | Gateway backend port |
| `LAUNCHER_PORT` | No | 9600 | Launcher port |
| `EXTERNAL_ADAPTER_PORT` | No | 9700 | External adapter port |
| `EXTERNAL_API_KEY` | No | auto-generated | External API access key |
| `LOG_LEVEL` | No | INFO | Log level |

---

## Desktop Application (Electron)

OpenSquad also ships as a desktop application (Electron + Vite +
PyInstaller, codename **NexusChat Pro**) for Windows, macOS, and Linux.

**→ For the full build guide (dev modes, multi-platform builds, CI
pipeline, prerequisites, troubleshooting) see
[Desktop Build Guide](desktop_build.md).**

---

## Production Recommendations

### Security

1. **Enable Gateway Token**: Set `auth.gateway_token` in `system_config.json`
2. **Use HTTPS**: Place a reverse proxy (Nginx/Caddy) in front
3. **Restrict bind addresses**: Set hosts to `127.0.0.1` where external
   access is not needed
4. **JWT secret key**: Set a strong `jwt.secret_key` (minimum 32 chars)

### Performance

1. **Enable context compression**: Configure `context_compression` in
   `system_config`
2. **Log rotation**: `logging.max_size_mb` and `backup_count` control disk
   usage
3. **Nginx reverse proxy**:

```nginx
server {
    listen 443 ssl;
    server_name opensquad.example.com;

    location / {
        proxy_pass http://127.0.0.1:9555;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
    }
}
```

### Monitoring

```bash
# Check all service statuses
opensquad status

# View recent Gateway logs
opensquad logs -s gateway -n 100

# Run full diagnostics
opensquad doctor
```
