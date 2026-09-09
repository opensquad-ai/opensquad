#!/usr/bin/env bash
# build_desktop.sh — macOS/Linux full desktop build (backend + electron).
# Windows: use scripts/build_desktop.ps1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

SKIP_BACKEND=0
SKIP_ELECTRON=0
for arg in "$@"; do
  case "$arg" in
    --skip-backend) SKIP_BACKEND=1 ;;
    --skip-electron) SKIP_ELECTRON=1 ;;
  esac
done

if [[ "$(uname)" == "Darwin" ]]; then
  ARTIFACT="backend-mac"
  NPM_SCRIPT="electron:mac"
else
  ARTIFACT="backend-linux"
  NPM_SCRIPT="electron:linux"
fi

echo "============================================================"
echo " OpenSquad Desktop - full $(uname) build"
echo "============================================================"

if [[ "$SKIP_BACKEND" -eq 0 ]]; then
  echo "[1/3] Building backend..."
  bash scripts/build_backend.sh
else
  echo "[1/3] Skipping backend; checking existing bundle..."
  uv run --python 3.11 python scripts/check_backend_bundle.py --bundle "build/$ARTIFACT/run"
fi

if [[ "$SKIP_ELECTRON" -eq 0 ]]; then
  echo "[2/3] Building Electron ($NPM_SCRIPT)..."
  cd src/opensquad/gateway/nexuschat-pro
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install --no-package-lock
  fi
  export CSC_IDENTITY_AUTO_DISCOVERY=false
  npm run "$NPM_SCRIPT"
  cd "$PROJECT_ROOT"
else
  echo "[2/3] Skipping Electron"
fi

echo "[3/3] Artifacts under build/release/ (if produced):"
ls -la build/release 2>/dev/null || echo "  (none yet)"
echo "Done."
