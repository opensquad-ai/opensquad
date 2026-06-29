# resources/ 目录说明
#
# 此目录存放各平台的 Python 后端二进制（PyInstaller 构建产物）
# electron-builder 打包时会把对应平台的目录作为 extraResources 打入安装包
#
# 目录结构：
#   resources/
#   ├── backend-win/        ← Windows 构建产物 (run.exe + 依赖)
#   │   ├── run.exe
#   │   ├── config.json
#   │   ├── nexuschat-pro/
#   │   │   └── dist/       ← 前端构建产物（由 spec 文件打入）
#   │   └── _internal/      ← Python 运行时和所有依赖
#   ├── backend-mac/        ← macOS 构建产物 (run + 依赖)
#   └── backend-linux/      ← Linux 构建产物 (run + 依赖)
#
# 如何生成：
#   1. 在对应平台上运行 scripts/build_backend.bat (Windows)
#      或 scripts/build_backend.sh (macOS/Linux)
#   2. 或者通过 GitHub Actions 自动构建（.github/workflows/build-desktop.yml）
#
# 注意：此目录下的二进制文件不提交到 Git（见 .gitignore）
# 只有 .gitkeep 和 README.md 会被提交
