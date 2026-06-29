#!/usr/bin/env bash
# ==============================================================
# build_backend.sh  —  在 macOS / Linux 上构建 Python 后端二进制
#
# 输出到：项目根目录 build/backend-{mac|linux}/run/
# 不在源码目录内产生任何构建产物
# ==============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/opensquad/gateway/backend"
FRONTEND_DIR="$PROJECT_ROOT/opensquad/gateway/nexuschat-pro"
SPEC_FILE="$BACKEND_DIR/opensquad_backend.spec"

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
echo "============================================================"
echo

echo "[1/4] Installing opensquad package..."
cd "$PROJECT_ROOT"
pip install -e . --quiet

echo "[2/4] Installing backend dependencies..."
pip install -e . --quiet
pip install pyinstaller --quiet

echo "[3/4] Building frontend (React)..."
cd "$FRONTEND_DIR"
npm run build

echo "[4/4] Running PyInstaller..."
cd "$PROJECT_ROOT"
pyinstaller "$SPEC_FILE" \
  --distpath "$DIST_PATH" \
  --workpath "$WORK_PATH" \
  --clean --noconfirm

# 确保可执行权限
chmod +x "$DIST_PATH/run/run"

echo ""
echo "============================================================"
echo " Backend built successfully!"
echo " Binary: $DIST_PATH/run/run"
echo ""
echo " Next: cd opensquad/gateway/nexuschat-pro"
if [[ "$(uname)" == "Darwin" ]]; then
  echo "       npm run electron:mac"
else
  echo "       npm run electron:linux"
fi
echo " Final installer: build/release/"
echo "============================================================"
