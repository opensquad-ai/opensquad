#!/usr/bin/env bash
# ==============================================================
# build_backend.sh  —  在 macOS / Linux 上构建 Python 后端二进制
#
# 输出到：项目根目录 build/backend-{mac|linux}/run/
# 不在源码目录内产生任何构建产物
# 与 CI / build_backend.bat 一致：固定 Python 3.11
# ==============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/src/opensquad/gateway/backend"
FRONTEND_DIR="$PROJECT_ROOT/src/opensquad/gateway/nexuschat-pro"
SPEC_FILE="$BACKEND_DIR/opensquad_backend.spec"
PYTHON_VERSION="3.11"

# 根据平台决定输出目录名
if [[ "$(uname)" == "Darwin" ]]; then
  ARTIFACT_NAME="backend-mac"
else
  ARTIFACT_NAME="backend-linux"
fi

# 构建产物统一放在 build/ 下，与源码隔离
DIST_PATH="$PROJECT_ROOT/build/$ARTIFACT_NAME"
WORK_PATH="$PROJECT_ROOT/build/.pyinstaller-work"

echo "============================================================"
echo " OpenSquad Desktop - $(uname) Backend Build"
echo " Output: $DIST_PATH/run/"
echo " Python: $PYTHON_VERSION (required)"
echo "============================================================"
echo

echo "[1/6] Sync project deps (Python $PYTHON_VERSION, matches CI)..."
cd "$PROJECT_ROOT"
uv sync --python "$PYTHON_VERSION" --quiet

echo "[2/6] Installing PyInstaller into project venv..."
uv pip install pyinstaller --quiet

echo "[3/6] Verify Python $PYTHON_VERSION interpreter..."
uv run --python "$PYTHON_VERSION" python scripts/check_build_python.py

echo "[4/6] Building frontend (React)..."
cd "$FRONTEND_DIR"
npm run build

echo "[5/6] Running PyInstaller (uv / Python $PYTHON_VERSION)..."
cd "$PROJECT_ROOT"
uv run --python "$PYTHON_VERSION" pyinstaller "$SPEC_FILE" \
  --distpath "$DIST_PATH" \
  --workpath "$WORK_PATH" \
  --clean --noconfirm

echo "[6/6] Verify PyInstaller bundle is Python $PYTHON_VERSION..."
uv run --python "$PYTHON_VERSION" python scripts/check_build_python.py --bundle "$DIST_PATH/run"

# 确保可执行权限
chmod +x "$DIST_PATH/run/run"

echo ""
echo "============================================================"
echo " Backend built successfully!"
echo " Binary: $DIST_PATH/run/run"
echo ""
echo " Next: cd src/opensquad/gateway/nexuschat-pro"
if [[ "$(uname)" == "Darwin" ]]; then
  echo "       npm run electron:mac"
else
  echo "       npm run electron:linux"
fi
echo " Final installer: build/release/"
echo "============================================================"
