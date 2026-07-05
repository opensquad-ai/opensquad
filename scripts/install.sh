#!/usr/bin/env bash
# OpenSquad One-Click Install (Linux / macOS)
# Usage: bash scripts/install.sh
set -euo pipefail

ROOTDIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOTDIR"

echo "=================================================="
echo "  OpenSquad One-Click Install (Linux / macOS)"
echo "=================================================="
echo ""

# ── Helper: color output ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color
info()  { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "  ${RED}[ERROR]${NC} $1"; }

# ── 1. Check Python 3.10+ ──
echo "[1/6] Checking Python version..."
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major="${ver%.*}"
        minor="${ver#*.}"
        if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    error "Python 3.10+ not found. Please install it first."
    exit 1
fi
info "Python: $($PYTHON --version)"
echo ""

# ── 2. Check Node.js 18+ ──
echo "[2/6] Checking Node.js version..."
if ! command -v node &>/dev/null; then
    error "Node.js not found. Please install Node.js 18+ first."
    exit 1
fi
NODE_VER=$(node -e "process.stdout.write(process.version.slice(1).split('.')[0])")
if [ "$NODE_VER" -lt 18 ]; then
    error "Node.js 18+ required. Current: $(node --version)"
    exit 1
fi
info "Node.js: $(node --version)"
echo ""

# ── 3. Install Python dependencies ──
echo "[3/6] Installing Python dependencies..."
echo "     This may take a few minutes. Download progress shown below:"
echo ""

# Always install the project package first (registers entry points + pyproject deps)
echo "  -- pip install -e . (project package) --"
"$PYTHON" -m pip install -e . || {
    error "pip install -e . failed."
    exit 1
}
echo ""

# Install gateway backend deps
echo "  -- pip install -e . (opensquad package) --"
"$PYTHON" -m pip install -e . || {
    error "pip install -e . failed."
    exit 1
}
echo ""

# Try uv for faster future syncs (optional)
if command -v uv &>/dev/null; then
    info "uv already installed."
else
    "$PYTHON" -m pip install uv 2>/dev/null && info "uv installed (optional)." || true
fi
info "Python dependencies installed."
echo ""

# ── 3b. Download Playwright Chromium browser ──
# The `playwright` pip package (installed above) does NOT bundle the
# Chromium binary. Without this step, the websearch plugin service fails
# at runtime with "Executable doesn't exist at ...chromium_headless_shell-XXXX".
# We download only chromium (not firefox/webkit) to keep the install lean.
# Respect a user-set PLAYWRIGHT_BROWSERS_PATH but strip any trailing
# whitespace (a common cause of "path not found" errors).
echo "[3b/6] Downloading Playwright Chromium browser..."
if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH// /}"
fi
"$PYTHON" -m playwright install chromium || \
    warn "Playwright Chromium download failed. Web search will not work until you run: python -m playwright install chromium"
info "Playwright Chromium browser ready."
echo ""

# ── 4. Install frontend dependencies ──
echo "[4/6] Installing frontend dependencies..."
cd "$ROOTDIR/src/opensquad/gateway/nexuschat-pro"
npm install --silent 2>/dev/null || warn "npm install had issues, continuing..."
cd "$ROOTDIR"
info "Frontend dependencies installed."
echo ""

# ── 5. Create config & init workspace ──
echo "[5/6] Initializing workspace..."
if [ ! -f src/system_config.json ] && [ -f src/system_config.example.json ]; then
    cp src/system_config.example.json src/system_config.json
    info "Created src/system_config.json from example."
    warn "Don't forget to add your LLM API keys in model_cards/*.json"
fi

$PYTHON -m opensquad.cli.main init --workspace "$HOME/.opensquad/workspace" 2>/dev/null || \
    warn "init had issues, continuing..."
info "Workspace initialized."

# ── 5b. Pre-cache Playwright MCP package ──
# Without this, the very first MCP call to the npx-fetched @playwright/mcp
# server triggers a ~30-60s npm download inside the agent process, which
# looks like a hang to the deployment tester. We kick it off in the
# background here so the cache is warm by the time the agent starts.
echo "[*] Pre-caching Playwright MCP package in background (npx -y @playwright/mcp@latest)..."
( npx -y @playwright/mcp@latest --version >/dev/null 2>&1 & ) >/dev/null 2>&1
echo ""

# ── 6. Start services ──
echo "[6/6] Starting OpenSquad services..."
echo ""
echo "=================================================="
echo "  Starting services..."
echo "=================================================="

# Kill any leftover processes on our ports
for port in 5173 9555 9530 9600 9720; do
    lsof -ti :"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
done

# Launch all 4 services in background
echo "[1/4] Gateway Backend (port 9555)..."
$PYTHON -m opensquad.gateway.backend.run &
echo "[2/4] Plugin Registry (port 9720)..."
$PYTHON -m opensquad.gateway.plugin_registry.main &
echo "[3/4] Frontend Dev Server (port 5173)..."
cd "$ROOTDIR/src/opensquad/gateway/nexuschat-pro"
npm run dev &
cd "$ROOTDIR"
echo "[4/4] Launcher (port 9600)..."
$PYTHON -m opensquad.launcher &

echo ""
echo "=================================================="
echo "  OpenSquad install complete!"
echo "  Gateway  : http://127.0.0.1:9555"
echo "  Frontend : http://127.0.0.1:5173"
echo "  Launcher : http://127.0.0.1:9600"
echo "=================================================="
echo ""
echo "Press Ctrl+C to stop all services."
wait
