# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for OpenSquad / NexusChat Pro backend
#
# 构建方法（推荐使用构建脚本，不要手动执行 pyinstaller）:
#   Windows:  scripts\build_backend.bat
#   macOS:    bash scripts/build_backend.sh
#   Linux:    bash scripts/build_backend.sh
#
# 如需手动执行（在项目根目录）:
#   pip install -e . && pip install pyinstaller
#   pyinstaller opensquad/gateway/backend/opensquad_backend.spec \
#     --distpath build/backend-win \    # (或 backend-mac / backend-linux)
#     --workpath build/.pyinstaller-work \
#     --noconfirm
#
# 输出统一到项目根目录 build/backend-{win|mac|linux}/run/
# （不在源码目录内产生任何构建产物）

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# ── 路径定义 ──────────────────────────────────────────────────────────────────
BACKEND_DIR  = Path(SPECPATH)                           # opensquad/gateway/backend/
GATEWAY_DIR  = BACKEND_DIR.parent                       # opensquad/gateway/
PROJECT_ROOT = GATEWAY_DIR.parent.parent                # 项目根目录（含 opensquad/ 包）
FRONTEND_DIST = GATEWAY_DIR / "nexuschat-pro" / "dist"  # 前端构建产物

# ── 数据文件（打包进二进制） ───────────────────────────────────────────────────
datas = []

# gateway/config.json → 与可执行文件同级
datas += [(str(GATEWAY_DIR / "config.json"), ".")]

# 前端 dist（由 FastAPI StaticFiles 服务）
if FRONTEND_DIST.exists():
    datas += [(str(FRONTEND_DIST), "nexuschat-pro/dist")]
else:
    print(f"[spec] WARNING: Frontend dist not found at {FRONTEND_DIST}")
    print(f"[spec]   Run: cd nexuschat-pro && npm run build")

# opensquad 包的数据文件（prompts、role_cards、plugin_registry 等）
datas += collect_data_files("opensquad")

# alembic 迁移脚本
alembic_dir = BACKEND_DIR / "alembic"
if alembic_dir.exists():
    datas += [(str(alembic_dir), "alembic")]
alembic_ini = BACKEND_DIR / "alembic.ini"
if alembic_ini.exists():
    datas += [(str(alembic_ini), ".")]

# ── 隐式导入（FastAPI/uvicorn/SQLAlchemy 动态加载的模块） ─────────────────────
hiddenimports = []

# uvicorn 内部模块
hiddenimports += [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.logging",
]

# FastAPI / Starlette
hiddenimports += [
    "fastapi",
    "starlette.middleware.cors",
    "starlette.staticfiles",
    "starlette.responses",
    "starlette.routing",
]

# SQLAlchemy 异步 + aiosqlite
hiddenimports += [
    "sqlalchemy.ext.asyncio",
    "sqlalchemy.dialects.sqlite",
    "aiosqlite",
]

# 认证相关
hiddenimports += [
    "passlib.handlers.bcrypt",
    "passlib.handlers.sha2_crypt",
    "jose",
    "jose.jwt",
    "jose.backends",
    "jose.backends.cryptography_backend",
    "cryptography",
    "cryptography.hazmat.bindings._rust",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
]

# multipart（文件上传）
hiddenimports += [
    "multipart",
    "python_multipart",
]

# 邮件（部分库内部使用）
hiddenimports += [
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.base",
]

# pydantic v2
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")
hiddenimports += collect_submodules("pydantic_settings")

# opensquad 子模块（动态导入的部分）
hiddenimports += collect_submodules("opensquad")

# app 子模块
hiddenimports += [
    "app.main",
    "app.api",
    "app.bot_api",
    "app.auth",
    "app.database",
    "app.models",
    "app.schemas",
    "app.websocket",
    "app.workspace_api",
    "app.ai_web.routes",
    "app.ai_web.websocket",
    "app.ai_web.agent_sessions",
    "app.ai_web.model_preset_service",
    "init_data",
]

# watchfiles（uvicorn reload 依赖，打包后不用但需避免 import 报错）
hiddenimports += ["watchfiles"]

# httpx
hiddenimports += collect_submodules("httpx")

# ── 收集完整包数据 ─────────────────────────────────────────────────────────────
for pkg in ("uvicorn", "fastapi", "starlette", "sqlalchemy", "httpx", "h11"):
    _datas, _binaries, _hidden = collect_all(pkg)
    datas     += _datas
    hiddenimports += _hidden

# ── 分析 ──────────────────────────────────────────────────────────────────────
a = Analysis(
    [str(BACKEND_DIR / "run.py")],
    pathex=[
        str(BACKEND_DIR),
        str(PROJECT_ROOT),   # 让 PyInstaller 找到 opensquad 包
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大型包
        "tkinter", "matplotlib", "numpy", "pandas",
        "PIL", "cv2", "torch", "tensorflow",
        "IPython", "notebook", "jupyter",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="run",                  # 可执行文件名（Windows 自动加 .exe）
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                # 保留控制台窗口，方便排查问题
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="run",                  # dist/run/ 目录
)
