# 构建 OpenSquad 桌面应用

> OpenSquad 网关以桌面应用形式分发（代号 **NexusChat Pro**），基于
> **Electron + Vite + PyInstaller** 打包。本文档是完整 how-to：
> 从一行命令的开发模式，到多平台生产安装包，以及 CI 流水线是怎么串起来的。
>
> 想要**安装/运行已经构建好的应用**请看
> [deployment_guide.md](deployment_guide.md)。本文讲的是**从源码构建应用**。

---

## TL;DR

```bash
# 开发模式（前端 + Electron，需要另一个终端跑 opensquad start）
cd src/opensquad/gateway/nexuschat-pro
npm install
npm run electron:dev

# 为当前平台构建安装包
npm run electron:build
# → build/release/   (Windows .exe、macOS .dmg、Linux .AppImage / .deb)

# 构建指定平台
npm run electron:win     # Windows .exe（NSIS + portable）
npm run electron:mac     # macOS .dmg + .zip（x64 + arm64）
npm run electron:linux   # Linux .AppImage + .deb
```

构建流水线分**两段**：一段 Python 后端（PyInstaller），一段 Electron 外壳。
两段都要成功；Electron 阶段会按平台把对应的后端塞进对应的安装包。

---

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│  src/opensquad/gateway/nexuschat-pro/   (Electron + React UI)   │
│  ────────────────────────────────────────────────────────────  │
│   electron/         ← 主进程、preload、托盘菜单（.ts）         │
│   src/              ← React UI（Vite + TypeScript）            │
│   scripts/          ← compile-electron.mjs、dev-electron-live  │
│   assets/           ← icon.png / .ico / .icns / tray.png       │
│   package.json      ← npm scripts + electron-builder 配置      │
└─────────────────────────────────────────────────────────────────┘
                              │ 打包
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  opensquad/gateway/backend/   (Python 后端 → 二进制)           │
│  ────────────────────────────────────────────────────────────  │
│   opensquad_backend.spec   ← PyInstaller spec                  │
│   app/、routes/、…         ← FastAPI 及其余                    │
└─────────────────────────────────────────────────────────────────┘
                              │ pyinstaller
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  build/   （不进 git；由构建脚本创建）                          │
│  ────────────────────────────────────────────────────────────  │
│   backend-win/run/run.exe + 依赖                                │
│   backend-mac/run/run   + 依赖                                  │
│   backend-linux/run/run + 依赖                                  │
│   release/  ← electron-builder 产物（安装包）                   │
│     *.exe  *.dmg  *.AppImage  *.deb  *.zip                      │
└─────────────────────────────────────────────────────────────────┘
```

Electron 应用把打包好的后端二进制作为子进程拉起来，在 `BrowserWindow` 里加载
`http://127.0.0.1:<port>/`。端口从 `OPENSQUAD_PORT`（默认 `9510`，打包后端）
或 `OPENSQUAD_FRONTEND_PORT` / `VITE_DEV_PORT`（默认 `5173`，dev 模式）取。
具体解析规则见 `electron/main.ts`。

---

## 前置条件

| 工具          | 版本                  | 用途 |
|---------------|----------------------|------|
| **Node.js**   | 20.x 或 22.x          | Vite + Electron + TypeScript |
| **npm**       | Node 自带              | `npm ci`、scripts |
| **Python**    | 3.11+（CI 用 3.11）    | `pip install -e .`、PyInstaller、build_icons.py |
| **PyInstaller** | pip 最新版            | 后端 → 独立二进制 |
| **Pillow + Playwright** | 最新版       | `build_icons.py`（把 SVG 主图标栅格化成各平台图标）|
| **平台 SDK**  | 视情况                | macOS: Xcode CLT（生成 .icns / 公证）。Windows: 可选，仅签名时需要。 |

`npm install` 阶段很重（Electron 约 200 MB）。首次跑可能要几分钟。

---

## 开发模式（三种变体）

所有 dev 模式都要求**单独的后端**跑在 `9510`（或 `OPENSQUAD_PORT` 的值）。
最常见的起法：

```bash
# 终端 1 — Python 后端（任选你喜欢的姿势）
uv run opensquad start         # 或：python -m opensquad.cli start

# 终端 2 — Electron 开发模式
cd src/opensquad/gateway/nexuschat-pro
npm install
npm run electron:dev
```

### `npm run electron:dev` — 完整 build 后启动

1. `vite build` — 把 React UI 打包到 `dist/`。
2. `node scripts/compile-electron.mjs` — 把 `electron/*.ts` 编译到
   `dist-electron/*.cjs`（TypeScript → CJS，再加一个小补丁让 Node 能
   解析 `./foo` → `foo.cjs`）。
3. `electron dist-electron/main.cjs` — 启动 Electron 窗口。

Electron 窗口指向**打包后的** UI（不走 Vite dev server），跟外部跑着的后端
通信。改前端代码需要重新 `vite build`（或重跑整个脚本）。想要测生产 build
行为时用这个。

### `npm run electron:dev:fast` — 跳过 Vite rebuild

跟上面一样，但**跳过** `vite build`。当你只改 `electron/*.ts`（主进程 /
preload / 托盘菜单）且不想等 Vite 时用这个。

### `npm run electron:dev:live` — 走 Vite dev server + HMR

`node scripts/dev-electron-live.mjs` 设置 `ELECTRON_DEV=1`，主进程会：

- **跳过拉起打包后端**（假设你已经在另一端把后端跑起来了）。
- **从 Vite dev server 加载 UI**（默认 `http://127.0.0.1:5173/`，
  可用 `OPENSQUAD_FRONTEND_PORT` 覆盖），拿到 React HMR。

做 UI/UX 迭代、想要即时反馈时用这个。后端还是要在另一端跑着。

---

## 生产构建（一行命令、单个平台）

```bash
cd src/opensquad/gateway/nexuschat-pro
npm run electron:build
# 或
npm run electron:win       # 仅 Windows
npm run electron:mac       # 仅 macOS
npm run electron:linux     # 仅 Linux
```

每个命令依次执行：

1. `npm run icons:build` — 跑 `python ../../../scripts/build_icons.py`，
   把 `assets/logo-source.svg` 栅格化成 `icon.png` / `icon@2x.png` /
   `icon.ico` / `icon.icns` / `tray.png`。需要 Pillow + Playwright + Chromium。
2. `npm run build` — `vite build`，把 React UI 打到 `dist/`。
3. `node scripts/compile-electron.mjs` — TypeScript → CJS（见上）。
4. `electron-builder` — 把所有东西打成该平台的安装包。

### 产物

| 平台      | 命令           | 产物（在 `build/release/` 下）|
|-----------|----------------|------------------------------|
| Windows   | `electron:win`  | `*-setup.exe`（NSIS 安装器）、`*portable.exe`（免安装）|
| macOS     | `electron:mac`  | `*.dmg` 和 `*.zip`，**同时**覆盖 x64 和 arm64 |
| Linux     | `electron:linux`| `*.AppImage`、`*.deb` |
| 三平台（仅当前 OS）| `electron:build` | 当前平台能产出的那些 |

产物目录是**项目根**的 `build/release/`，**不是**前端目录内（见
`package.json` → `build.directories.output = "../../../build/release"`）。

### PyInstaller 后端这一步的前置条件

`electron-builder` 阶段假设 `build/backend-<os>/run/` 已经塞好了该 OS 对应的
Python 后端二进制。**前端 build 不会产生它**——你得另外跑一次 PyInstaller。

两种塞法：

#### 方式 A — 各 OS 的本地 build 脚本

```bash
# Windows
scripts\build_backend.bat
# macOS / Linux
bash scripts/build_backend.sh
```

两个脚本都调用 PyInstaller 加 `opensquad/gateway/backend/opensquad_backend.spec`，
输出到 `build/backend-{win|mac|linux}/run/`。

#### 方式 B — 让 CI 跑（发版时推荐）

`build-desktop.yml` 在 `push` 一个 `v*` tag 时会并行给三个 OS 跑 PyInstaller，
再在匹配的 runner 上组装 Electron 安装包。见下面 [CI / 发版流水线](#ci--发版流水线)。

---

## CI / 发版流水线

`.github/workflows/build-desktop.yml` 是出发布安装包的标准姿势。两种触发方式：

- **push tag**：`git tag -a v0.X.Y && git push origin v0.X.Y` — 跑完整的多平台
  build，并创建一个 GitHub Release 把安装包挂上去。
- **手动**：`Actions → Build Desktop App → Run workflow` — 不发版只想验证
  build 时用。

三段：

1. **`build-backend`**（3 路 matrix）— 装 Python 依赖、生成图标、
   `npm ci && npm run build`（让 PyInstaller 能把前端打进去）、跑
   PyInstaller 加 `opensquad_backend.spec`、上传 `backend-{win,mac,linux}`
   artifact。
2. **`build-electron`**（3 路 matrix，`needs: build-backend`）— 下载匹配
   的后端 artifact、`npm ci`、跑 `npm run electron:{win,mac,linux}`、
   上传 `build/release/*.{exe,dmg,AppImage,deb}` 为 `release-<os>` artifact
   （保留 7 天）。
3. **`create-release`**（仅 tag push）— 下载三个 `release-*` artifact，
   挂到 GitHub Release 上并自动生成 release notes。

全量一次冷启动约 **25–35 分钟**（3 个后端 build 并行 + 3 个 Electron build
并行 + release）。

### 验证一个桌面 build

跑完之后：

1. `https://github.com/opensquad-ai/opensquad/releases/tag/v0.X.Y` —
   GitHub Release 页应该挂着各平台的安装包。
2. workflow run 的 **artifacts 标签**也能下到（如果你是 `workflow_dispatch`
   跑的、没创建 release）。
3. 冒烟测试：下你平台的安装包、装、打开。app 应该能开网关 UI、
   拉起打包好的后端（系统托盘能看到）、后端在
   `127.0.0.1:9555/health` 应该能访问。

### 发版版本号规则（从 v0.4.10 起严格执行）

历史教训：多次把未充分测试的版本直接推为正式 Release，用户下载后才发现
bug 不可用。为避免重复，引入 beta.N 标记 + 三环节测试流程。

#### 版本号格式

| 格式 | 含义 |
|------|------|
| `vX.Y.Zbeta.N` | CI 构建包，未通过三环节测试（N 从 0 递增，每次修复重 CI 加 1） |
| `vX.Y.Z` | 正式版，三环节测试全过才打此 tag |

#### 三环节测试（任何一环失败都修复后从环节 1 重新开始）

1. **本地快速验证**（~6 分钟）—
   `scripts\build_backend.bat` 重建 `run.exe`，再跑
   `uv run python scripts\smoke_frozen_all.py`。
   全套 hard-gate 冒烟必须 PASS（含路径检查、gateway、模型卡/角色卡/技能、
   插件服务发现、MCP 配置、skills）。
2. **本地打包验证**（~3 分钟）—
   `cd src\opensquad\gateway\nexuschat-pro && npx electron-builder --win --dir --publish never`
   产 unpacked 目录，手动跑 `build\release\win-unpacked\OpenSquad.exe`
   验证桌面端能开、UI 能加载、Service Manager 能列服务。
3. **CI 构建下载人工测试**（~30 分钟 + 测试时间）—
   推 `vX.Y.Zbeta.N` tag 触发 CI，等 build 完成后从 GitHub Release
   下载安装包，实际安装并测试桌面端全部功能（对话、服务启停、
   Token Analytics dashboard、MCP、技能）。

#### 流程图

```
修复 → 推 vX.Y.Zbeta.0 tag → CI 构建（~30 min）
                              ↓
                        下载 beta.0 测试
                              │
        ┌─────────────────────┼─────────────────────┐
        │ 环节 1 (本地快速)   │ 环节 2 (本地打包)   │ 环节 3 (CI 人工) │
        └─────────────────────┴─────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                 全过 PASS           任一环 FAIL
                    │                   │
                    ▼                   ▼
        删 beta.0 tag          修复 → 推 vX.Y.Zbeta.1 tag
        推 vX.Y.Z 正式 tag           → 重新 CI → 重新三环节
        → CI 出正式 Release              │
                                          └─ N 递增直到全过
```

#### 命令速查

```powershell
# 发 beta.0
git tag -a v0.4.10beta.0 -m "v0.4.10 beta.0: <修复说明>"
git push origin v0.4.10beta.0

# 测试全过后升正式版（先删 beta tag 再推正式 tag）
git tag -d v0.4.10beta.0
git push origin :refs/tags/v0.4.10beta.0
git tag -a v0.4.10 -m "v0.4.10: <release 说明>"
git push origin v0.4.10
```

#### 历史例外

`v0.4.9` 在此规则制定前已推正式 tag，保留不改正。从 `v0.4.10` 起严格执行。

---

## Frozen 模式快速验证（改一行 → 6 分钟出结果）

### 为什么需要快速验证

PyInstaller frozen 模式的 bug **只能在打包后复现**——开发模式（`uv run opensquad start`）
用 venv Python 直接跑源码，跟 frozen `run.exe` 是两套完全不同的运行路径。
如果每次改代码都要走「重建 backend (5 min) → 打 electron-builder (2.5 min) →
安装 → 手动点 UI → 发现 bug」，一轮迭代 10+ 分钟，效率极低。

**关键洞察**：electron-builder 只是把 `build/backend-win/run/` 塞进安装包，
不会改变 `run.exe` 的行为。所以**绝大多数 frozen bug 只用 backend bundle 就能复现和验证**，
不需要打 electron-builder、不需要安装。

### 快速验证流程

```
改代码 → 重建 backend (5 min) → 直接用 run.exe 测试 (10 s)
                                   ↓
                              PASS → 才打 electron-builder
                              FAIL → 改代码，重来
```

#### 第 0 步：环境准备（一次性）

```powershell
# 确保 Agent Python 运行时已安装（首次需要，后续跳过）
# 方式 1：运行安装向导
build\release\win-unpacked\OpenSquad.exe --setup-runtime

# 方式 2：手动确认
Test-Path "$env:LOCALAPPDATA\OpenSquad\runtime\python311\python.exe"
```

#### 第 1 步：重建 backend（~5 分钟）

```powershell
# 方式 A：用构建脚本（推荐，含 Python 3.11 校验）
scripts\build_backend.bat

# 方式 B：直接调 PyInstaller（跳过前端 build，更快）
uv run --python 3.11 pyinstaller src\opensquad\gateway\backend\opensquad_backend.spec `
  --distpath build\backend-win --workpath build\.pyinstaller-work --clean --noconfirm
```

> **注意**：`build_backend.bat` 的 `^` 续行符后空行可能导致空参数报错。
> 如果遇到 `pyinstaller: error: unrecognized arguments`，用方式 B 直接跑。

#### 第 2 步：冒烟测试 — Agent 能否启动（~10 秒）

```powershell
uv run python scripts\smoke_frozen_agent.py
```

脚本做的事：
1. 启动 `run.exe --service launcher --mgmt-port 9600 --no-auto-start --no-services`
2. 等 9600 端口就绪
3. 调 `POST /api/agents/coder/start` 启动 coder agent
4. 轮询 `/api/agents` 确认 `alive=True`
5. 自动清理进程

**预期输出**：
```
[smoke] Launcher up after 1s, agents: ['coder', 'pm', 'qa']
[smoke] Starting coder agent...
[smoke] Start response: {'message': 'coder started', 'pid': 123456, 'port': 8001}
[smoke] 0s: alive=True pid=123456 port=8001 restarts=0
PASS: coder agent is alive on port 8001
```

#### 第 3 步：冒烟测试 — Agent 能否对话（~10 秒）

需要 Gateway 在跑（手动启动或用桌面端）：

```powershell
# 先启动完整桌面端（Gateway + Launcher + UI）
Start-Process build\release\win-unpacked\OpenSquad.exe
Start-Sleep -Seconds 20  # 等 Gateway 就绪

# 然后跑对话测试
uv run python scripts\smoke_chat.py
```

脚本做的事：
1. `POST /api/auth/login` 登录获取 JWT
2. 连接 `ws://127.0.0.1:9555/ai-web/ws/coder-001?token=<JWT>`
3. 发送 `{"type": "chat", "content": "你好，请回复一句话确认你能正常工作"}`
4. 接收 `thought`（思考流）+ `message`（回复）
5. 报告 SUCCESS / FAIL

**预期输出**：
```
[chat] Login OK
[chat] WS connected!
[chat] [connected] agent_status: online
[chat] [thought] The user is asking me to confirm...
[chat] [message] 你好，Coder 正常运行，随时可以执行编程任务。
[chat] SUCCESS: Got response (25 chars)
```

#### 第 4 步：全部 PASS 后才打 electron-builder

```powershell
cd src\opensquad\gateway\nexuschat-pro
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
# --dir 只产 unpacked 目录（~1 min），不产安装包
npx electron-builder --win --dir --publish never --config.win.signAndEditExecutable=false
# 或完整安装包（~2.5 min）
npx electron-builder --win --publish never --config.win.signAndEditExecutable=false
```

### 超快迭代：不重建，直接补文件（10 秒）

如果冒烟测试报 `ModuleNotFoundError` 或 `FileNotFoundError`，通常是 PyInstaller
漏打了某个数据文件或子模块。这时**不需要等 5 分钟重建**——直接把缺的文件
复制进 bundle 目录即可验证：

```powershell
# 例：prompts 目录没打包
Copy-Item -Path src\prompts -Destination build\backend-win\run\_internal\prompts -Recurse -Force

# 例：tiktoken_ext 子模块没进 PYZ
Copy-Item -Path .venv\Lib\site-packages\tiktoken_ext `
  -Destination build\backend-win\run\_internal\tiktoken_ext -Recurse -Force

# 立即重跑冒烟测试（10 秒）
uv run python scripts\smoke_frozen_agent.py
```

验证通过后，再把对应的 `datas` / `hiddenimports` 写进
`opensquad_backend.spec`，做一次完整重建确认 spec 正确。

### 推荐门禁（本地 ~1 分钟，不必等 Electron）

PyInstaller 编完后**先跑 frozen 冒烟**，全部 PASS 再 push tag / 等 CI 打 Setup：

```powershell
scripts\build_backend.bat
uv run python scripts\smoke_frozen_all.py
```

`smoke_frozen_all.py` 会依次跑静态扫描 + gateway + 模型卡/角色卡/技能写入 + agent 启动。

**架构规则（frozen 桌面必守）**：

> 用户可写数据 **只能** 走 `syscfg.workspace_*()` / `get_workspace()`；  
> `builtin_resources_dir()` / `get_builtin_root()` **只读**，仅用于读 bundled 种子资源。  
> 读取时若 workspace 为空，应 **workspace 优先 + builtin 兜底**（`resource_search_dirs()`）。

CI：`build-desktop.yml` 在 Windows backend job 的 PyInstaller 之后自动跑同一套门禁，
**不必等 Electron 阶段**（约省 10+ 分钟才发现 backend 写路径 bug）。

### 冒烟脚本一览

| 脚本 | 用途 | 耗时 | 依赖 |
|------|------|------|------|
| `scripts/smoke_frozen_all.py` | **一键跑齐**下面所有 frozen 门禁 | ~30s | `build/backend-win/run/run.exe` |
| `scripts/check_frozen_writable_paths.py` | 静态扫描：写磁盘 + builtin 路径反模式 | ~1s | 无 |
| `scripts/smoke_frozen_gateway.py` | 验证 frozen gateway 完整启动（`/health` ready） | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_model_card_save.py` | 验证模型卡保存到 workspace（非 _internal） | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_role_card_save.py` | 验证角色卡保存到 workspace | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_skill_upload.py` | 验证技能上传到 workspace/plugins | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_plugin_services.py` | 验证 `--no-services` 下服务/插件/skills/MCP 都能被发现 | ~10s | `build/backend-win/run/run.exe` |
| `scripts/smoke_frozen_agent.py` | 验证 frozen launcher + agent 启动 | ~10s | `build/backend-win/run/run.exe` |
| `scripts/smoke_chat.py` | 验证端到端对话（登录→WS→发送→回复）| ~10s | Gateway 在 9555 跑着 |
| `scripts/check_build_python.py --bundle <dir>` | 校验 bundle 用 Python 3.11 | ~1s | 无 |

### 常见的 frozen-only bug 模式

以下 bug 在开发模式下**永远不会出现**，只在 frozen bundle 里复现：

| Bug | 根因 | 修复 |
|-----|------|------|
| `ModuleNotFoundError: opensquad.launcher.process_manager` | `launcher.py` 与 `launcher/` 包名冲突 | 重命名为 `launcher_main.py` |
| `ModuleNotFoundError: No module named 'opensquad'` | 外部 Python 无法从 PYZ 导入 | 用 `run.exe --service agent` 代替外部 `python -m` |
| `FileNotFoundError: base_fc.md` | `prompts/` 目录未打包 | spec 显式添加 `datas` |
| `ValueError: Unknown encoding cl100k_base` | `tiktoken_ext` 未进 PYZ | spec 加 `hiddenimports` |
| `Module use of python311.dll conflicts` | 系统 Python 3.13 + PATH 混入 `_internal` | 安装向导下载 embed Python 3.11 |
| `Backend did not start in time`（Setup 装到 Program Files）| 导入时在只读 `_internal/` 写目录（`session_manager`、`bot_api.uploads` 等）PermissionError | workspace / uploads 路径改读 `OPENSQUAD_USER_DATA` |
| 模型卡/插件/技能/市场安装保存失败 | Launcher/Gateway 用 `builtin_resources_dir` 当可写目录 | 改 `workspace_plugins_dir()` 等；读用 `resource_search_dirs()` |

---

## 本文没覆盖的内容

### `electron:dev` 第一次跑白屏

- Vite build 成功了但打包后端没跑。在另一终端把它起来
  （`uv run opensquad start`）。看 Electron DevTools 的 console—
  如果看到 `ECONNREFUSED 127.0.0.1:9510`，就是这个原因。
- 想要 HMR 就用 `electron:dev:live`；想要生产 build 行为就用 `electron:dev`。

### `electron:build` 报缺图标

- `npm run icons:build` 失败了（Pillow / Playwright 没装好、或 Chromium 下不下来）。
  手动跑一下看错误：`python scripts/build_icons.py`。
- 再跑 `electron:build` 就行——`icons:build` 是它的第一步。

### 在本机给非原生平台跑 `electron:build`

- **没法**在 macOS / Linux 上产出 Windows `.exe`（反之亦然），除非
  装 Wine / Windows VM。`build-desktop.yml` 的 matrix 就是为了这个。
  本地 build 只能挑跟你宿主 OS 一致的那个变体。

### `electron-builder` 报 `extraResources` 错

- `build/backend-<os>/run/` 不存在或为空。要么先跑对应的
  `build_backend.{sh,bat}`，要么从跑完 `build-backend` 阶段的
  `build-desktop.yml` run 里下 artifact。

### AppImage 在 Linux 上跑不起来

- 先 `chmod +x <file>.AppImage` 再跑。
- 没有 FUSE 的环境要加 `--appimage-extract-and-run` 兜底。也可以用 `.deb`。

### macOS 最低系统版本

- 桌面端目标：**macOS 12 Monterey 及以上**（与 Electron 40 官方下限一致）。
- CI 固定 `macos-15` runner，并设置 `MACOSX_DEPLOYMENT_TARGET=12.0` /
  `minimumSystemVersion: 12.0`，避免 `macos-latest` 用过新 SDK 把 Mach-O
  minOS 抬到 13+。本地 mac 构建脚本同样默认 `MACOSX_DEPLOYMENT_TARGET=12.0`。

### 代码签名（macOS / Windows）

- **macOS**：electron-builder 已启用 Hardened Runtime + entitlements；
  `afterSign` 钩子（`scripts/notarize.cjs`）在提供 Apple 凭证时做公证。
  仓库 **Secrets 未配置时** CI 仍打**未签名**包（首次打开需右键 → 打开）。
  需要签名/公证时配置的 Secrets 列表见 [RELEASING.md](../RELEASING.md)
  「macOS code signing & notarization」。
- **Windows**：签名仍未强制；未配置证书时产物为未签名安装包。

### 产物在**项目根**的 `build/release/`，不在 `nexuschat-pro/`

- 一开始挺迷惑——`package.json` →
  `build.directories.output = "../../../build/release"`。三个 `..` 是
  因为 `nexuschat-pro/` 离项目根正好三层。`build/` 在 `.gitignore` 里，
  npm 脚本**不会**清理它；想从零来就手动 `rm -rf build/`。

---

## 本文没覆盖的内容

- **后端安装 / 首次启动向导 / Web UI** →
  [deployment_guide.md](deployment_guide.md) 和
  [getting_started.md](getting_started.md)。
- **分支模型和版本号策略** → [BRANCHING.md](../BRANCHING.md)。
- **端到端发版流程（什么时候切 tag、跑了什么）** →
  [RELEASING.md](../RELEASING.md)。
- **插件开发** → [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md)。
