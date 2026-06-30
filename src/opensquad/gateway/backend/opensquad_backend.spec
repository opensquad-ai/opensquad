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

# opensquad 包的数据文件（prompts、role_cards、plugin_registry 等）。
# 必须过滤掉 node_modules / .map / .d.ts / 构建元数据：
# - node_modules 里是 electron / electron-builder 等 build-time 工具，
#   既有 200+ MB 的 electron.exe，也有 macOS 的 Electron.app 二进制，
#   PyInstaller 的 COLLECT 阶段无法处理 macOS 的 Mach-O bundle，会直接
#   报 SystemError: Failed to process binary。Windows/Linux 同样会中招。
# - 已经在上面显式处理 nexuschat-pro/dist（前端构建产物），运行时不需要
#   任何 node_modules 里的内容。
_BUILD_ARTIFACT_PATTERNS = (
    "node_modules",
    os.sep + "node_modules",
    ".tsbuildinfo",
)
_BUILD_METADATA_SUFFIXES = (".map", ".d.ts", ".d.ts.map")

def _is_runtime_data(src):
    norm = src.replace("\\", "/")
    if any(p in norm for p in _BUILD_ARTIFACT_PATTERNS):
        return False
    # nexuschat-pro/resources/ is the electron-builder extraResources staging
    # area (PyInstaller backend binaries copied here for local electron:dev:fast
    # runs). It is NOT runtime data and must never be bundled — doing so pulls
    # the entire backend bundle (hundreds of MB) into _internal/.
    if "/nexuschat-pro/resources/" in norm:
        return False
    # nexuschat-pro source (.ts/.tsx/components/electron) is dev-only; only the
    # built dist/ is served at runtime. Exclude the rest of the frontend tree
    # except dist/ (which the spec adds explicitly above).
    if "/nexuschat-pro/" in norm and "/nexuschat-pro/dist/" not in norm:
        return False
    base = os.path.basename(src)
    if any(base.endswith(sfx) for sfx in _BUILD_METADATA_SUFFIXES):
        return False
    if base in ("package.json", "package-lock.json", "tsconfig.json", ".eslintrc"):
        return False
    return True

datas += [pair for pair in collect_data_files("opensquad") if _is_runtime_data(pair[0])]
print(f"[spec] Filtered to {len(datas)} runtime data files (node_modules + build metadata excluded)")

# launcher.py — the standalone launcher module (opensquad/launcher.py, ~3.4k
# lines, holds main()). It is shadowed by the opensquad/launcher/ PACKAGE, so
# PyInstaller's collect_submodules("opensquad") picks up the package but NOT
# this .py file. The frozen run.py --service launcher loads it by file path
# (importlib.util.spec_from_file_location) to reach main(), so it must ship as
# a data file at _internal/opensquad/launcher.py. Source lives at
# GATEWAY_DIR.parent = src/opensquad/ (same dir as the opensquad package root).
datas += [(str(GATEWAY_DIR.parent / "launcher.py"), "opensquad")]

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

# Launcher dependencies (lazy-imported inside opensquad/launcher.py and
# opensquad/launcher/process_manager.py). PyInstaller can't see lazy imports,
# so list them explicitly. The launcher's management server is stdlib
# http.server, but the node-registration WS tunnel uses `websockets` and the
# port-owner lookup uses `psutil`.
hiddenimports += collect_submodules("websockets")
hiddenimports += ["psutil"]

# ── 收集完整包数据 ─────────────────────────────────────────────────────────────
for pkg in ("uvicorn", "fastapi", "starlette", "sqlalchemy", "httpx", "h11"):
    _datas, _binaries, _hidden = collect_all(pkg)
    datas     += _datas
    hiddenimports += _hidden

# ── Builtin resource packages: plugins, skills ───────────────────────────────
# These live at src/ top-level (NOT inside the opensquad package), so
# collect_submodules("opensquad") never reaches them. The launcher/gateway load
# plugin code via `import plugins.<name>.plugin` (importlib.import_module), so
# the .py must be importable in the bundle — collect_submodules puts them in the
# PYZ. The non-.py runtime data (plugin.json, role prompts, etc.) ships as data
# files. CRITICAL: filter out node_modules + ui/ build dirs — plugins/ contains
# 10k+ files of UI build tooling (task_watch/ui, token_analytics/ui = ~140MB)
# that is useless at runtime and would bloat the installer tenfold.
def _is_plugin_runtime_data(src: str) -> bool:
    norm = src.replace("\\", "/")
    if "node_modules" in norm or "/ui/" in norm or "/__pycache__/" in norm:
        return False
    base = os.path.basename(src)
    if base.endswith((".map", ".d.ts", ".ts")):
        return False
    if base in ("package.json", "package-lock.json", "tsconfig.json"):
        return False
    return True

for _res_pkg in ("plugins", "skills"):
    hiddenimports += collect_submodules(_res_pkg)
    _res_datas, _res_bins, _res_hidden = collect_all(_res_pkg)
    datas += [pair for pair in _res_datas if _is_plugin_runtime_data(pair[0])]
    print(f"[spec] {_res_pkg}: {len([p for p in _res_datas if _is_plugin_runtime_data(p[0])])} runtime data files (node_modules/ui excluded)")

# ── Builtin resource DIRECTORIES (not importable packages): cards/agents/pymcp ─
# role_cards / model_cards / collab_cards / agents / pymcp are plain data dirs
# (JSON manifests, markdown, seed agent configs). They sit under src/ top-level
# too. Frozen mode _DEFAULT_ROOT = _internal/, so builtin_resources_dir() and
# _copy_default_resources() look for them at _internal/<name>. Ship each dir as
# a data tree rooted at _internal/<name>.
_builtin_root = GATEWAY_DIR.parent.parent  # = src/ (PROJECT_ROOT is also src/)
for _res_dir in ("role_cards", "model_cards", "collab_cards", "agents", "pymcp"):
    _src = _builtin_root / _res_dir
    if _src.exists():
        datas += [(str(_src), _res_dir)]
        print(f"[spec] bundling {_res_dir}/ -> _internal/{_res_dir}/")
    else:
        print(f"[spec] WARNING: {_src} not found, skipping")

# init_workspace() copies system_config.{template,json,example}.json from
# _DEFAULT_ROOT into a fresh workspace. Bundle the example so a first-run
# desktop workspace gets a real config.
for _cfg_name in ("system_config.example.json", "system_config.json"):
    _cfg_src = _builtin_root / _cfg_name
    if _cfg_src.exists():
        datas += [(str(_cfg_src), ".")]
        print(f"[spec] bundling {_cfg_name} -> _internal/")

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
