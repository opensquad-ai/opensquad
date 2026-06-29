#!/bin/bash
set -e

# ── OpenSquad Docker Entrypoint ──
# Starts all 4 services in a single container

export PYTHONPATH="/app/src"
export OPENSQUAD_WORKSPACE="${OPENSQUAD_WORKSPACE:-/data/workspaces/default}"

# Auto-create config from example if not mounted
if [ ! -f /app/src/system_config.json ]; then
    if [ -f /app/src/system_config.example.json ]; then
        cp /app/src/system_config.example.json /app/src/system_config.json
        echo "[entrypoint] WARNING: Created system_config.json from example template."
        echo "[entrypoint] WARNING: The config contains PLACEHOLDER credentials."
        echo "[entrypoint] WARNING: Mount a production config or set OPENSQUAD__AUTH__NODE_SECRET / OPENSQUAD__AUTH__JWT_SECRET before deploying."
    fi
fi

# ── Validate config: refuse placeholder credentials in production ──
python -c "
import json, os, sys
cfg_path = '/app/src/system_config.json'
if not os.path.exists(cfg_path):
    sys.exit(0)
with open(cfg_path, 'r', encoding='utf-8-sig') as f:
    cfg = json.load(f)
auth = cfg.get('auth', {})
node_secret = auth.get('node_secret', '')
jwt_secret = auth.get('jwt_secret_key', '')
placeholders = ['YOUR_NODE_SECRET_HERE', 'YOUR_JWT_SECRET_KEY_CHANGE_ME_MIN_32_CHARS']
if node_secret in placeholders or jwt_secret in placeholders:
    print('[entrypoint] FATAL: system_config.json still contains placeholder credentials.')
    print('[entrypoint] Set OPENSQUAD__AUTH__NODE_SECRET and OPENSQUAD__AUTH__JWT_SECRET env vars,')
    print('[entrypoint] or mount a valid system_config.json before starting the container.')
    sys.exit(1)
"

# Set gateway host to 0.0.0.0 for Docker
python -c "
import json, os
cfg_path = '/app/src/system_config.json'
if os.path.exists(cfg_path):
    with open(cfg_path, 'r', encoding='utf-8-sig') as f:
        cfg = json.load(f)
    cfg.setdefault('hosts', {})['gateway'] = '0.0.0.0'
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print('[entrypoint] hosts.gateway = 0.0.0.0')
"

# Create .env.local for frontend
cat > /app/src/opensquad/gateway/nexuschat-pro/.env.local <<EOF
VITE_BACKEND_HOST=0.0.0.0
VITE_BACKEND_PORT=9555
EOF

# Initialize workspace
python -c "
from opensquad.system_config import syscfg
from opensquad.workspace_utils import _copy_default_resources
import os
ws = os.environ.get('OPENSQUAD_WORKSPACE', '/data/workspaces/default')
os.makedirs(ws, exist_ok=True)
syscfg.init_workspace(ws, copy_config=False)
_copy_default_resources(ws, '/app/src')
print(f'[entrypoint] Workspace: {ws}')
"

echo ""
echo "=================================="
echo "  OpenSquad Docker"
echo "=================================="
echo "  Gateway  : http://0.0.0.0:9555"
echo "  Launcher : http://0.0.0.0:9600"
echo "  Registry : http://0.0.0.0:9720"
echo "=================================="
echo ""

# Start services in background
echo "[entrypoint] Starting Gateway Backend (port 9555)..."
python -m opensquad.gateway.backend.run &
GATEWAY_PID=$!

sleep 2

echo "[entrypoint] Starting Plugin Registry (port 9720)..."
python -m opensquad.gateway.plugin_registry.main &
REGISTRY_PID=$!

sleep 1

echo "[entrypoint] Starting Launcher (port 9600)..."
python -m opensquad.launcher &
LAUNCHER_PID=$!

# Trap signals for graceful shutdown
cleanup() {
    echo ""
    echo "[entrypoint] Shutting down..."
    kill $GATEWAY_PID $REGISTRY_PID $LAUNCHER_PID 2>/dev/null
    wait $GATEWAY_PID $REGISTRY_PID $LAUNCHER_PID 2>/dev/null
    echo "[entrypoint] All services stopped."
    exit 0
}
trap cleanup SIGTERM SIGINT

# Wait for any process to exit
wait -n $GATEWAY_PID $REGISTRY_PID $LAUNCHER_PID
cleanup
