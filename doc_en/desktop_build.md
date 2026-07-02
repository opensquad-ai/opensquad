# Building the OpenSquad Desktop App

> The OpenSquad gateway ships as a desktop application (codename **NexusChat
> Pro**) built with **Electron + Vite + PyInstaller**. This document is the
> full how-to: from a one-command dev mode to multi-platform production
> installers and how the CI build pipeline fits in.
>
> For installing/running the **already-built** app, see
> [deployment_guide.md](deployment_guide.md). This file is about **building
> the app from source**.

---

## TL;DR

```bash
# Dev mode (frontend + Electron, expects opensquad start in another terminal)
cd src/opensquad/gateway/nexuschat-pro
npm install
npm run electron:dev

# Build an installer for the current platform
npm run electron:build
# → build/release/   (Windows .exe, macOS .dmg, Linux .AppImage / .deb)

# Build a specific platform
npm run electron:win     # Windows .exe (NSIS + portable)
npm run electron:mac     # macOS .dmg + .zip (x64 + arm64)
npm run electron:linux   # Linux .AppImage + .deb
```

The build pipeline has **two stages**: a Python backend (PyInstaller) and the
Electron wrapper. Both need to succeed; the Electron stage glues the right
backend into the right installer per OS.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  src/opensquad/gateway/nexuschat-pro/   (Electron + React UI)   │
│  ────────────────────────────────────────────────────────────  │
│   electron/         ← main process, preload, tray menus (.ts)  │
│   src/              ← React UI (Vite, TypeScript)              │
│   scripts/          ← compile-electron.mjs, dev-electron-live  │
│   assets/           ← icon.png / .ico / .icns / tray.png       │
│   package.json      ← npm scripts + electron-builder config    │
└─────────────────────────────────────────────────────────────────┘
                              │ bundles
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  opensquad/gateway/backend/   (Python backend → binary)        │
│  ────────────────────────────────────────────────────────────  │
│   opensquad_backend.spec   ← PyInstaller spec                   │
│   app/, routes/, …         ← FastAPI + the rest                │
└─────────────────────────────────────────────────────────────────┘
                              │ pyinstaller
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  build/   (NOT in git; created by build scripts)                │
│  ────────────────────────────────────────────────────────────  │
│   backend-win/run/run.exe + dependencies                        │
│   backend-mac/run/run   + dependencies                          │
│   backend-linux/run/run + dependencies                          │
│   release/  ← electron-builder output (installers)              │
│     *.exe  *.dmg  *.AppImage  *.deb  *.zip                      │
└─────────────────────────────────────────────────────────────────┘
```

The Electron app spawns the bundled backend binary as a child process and
loads the UI in a `BrowserWindow` that points at `http://127.0.0.1:<port>/`.
The port comes from `OPENSQUAD_PORT` (default `9510`) for the bundled
backend, or from `OPENSQUAD_FRONTEND_PORT` / `VITE_DEV_PORT` (default `5173`)
in dev mode. See `electron/main.ts` for the resolution rules.

---

## Prerequisites

| Tool       | Version            | Why |
|------------|--------------------|-----|
| **Node.js** | 20.x or 22.x       | Vite + Electron + TypeScript |
| **npm**    | bundled with Node  | `npm ci`, scripts |
| **Python** | 3.10+ (3.11 used in CI) | `pip install -e .`, PyInstaller, build_icons.py |
| **PyInstaller** | latest from pip | Backend → standalone binary |
| **Pillow + Playwright** | latest | `build_icons.py` (rasterize the SVG master into platform icons) |
| **Platform SDKs** | varies | macOS: Xcode CLT for `.icns`/notarization. Windows: optional, only for code signing. |

The `npm install` step is heavy (Electron is ~200 MB). The first run can take
a few minutes.

---

## Dev mode (three flavours)

All dev modes require a **separate backend** running on port `9510` (or the
value of `OPENSQUAD_PORT`). The most common setup:

```bash
# Terminal 1 — Python backend (any way you like)
uv run opensquad start         # or: python -m opensquad.cli start

# Terminal 2 — Electron in dev mode
cd src/opensquad/gateway/nexuschat-pro
npm install
npm run electron:dev
```

### `npm run electron:dev` — full build, then run

1. `vite build` — bundles the React UI to `dist/`.
2. `node scripts/compile-electron.mjs` — compiles `electron/*.ts` to
   `dist-electron/*.cjs` (TypeScript → CJS, plus a small require-path patch
   so Node can resolve `./foo` → `foo.cjs`).
3. `electron dist-electron/main.cjs` — launches the Electron shell.

The Electron window points at the **bundled** UI (no Vite dev server) and
talks to the externally running backend. Frontend changes still need a
`vite build` (or just rerun the script). Useful when you want to test the
exact production build behaviour.

### `npm run electron:dev:fast` — skip the Vite rebuild

Same as above but **skips** `vite build`. Use this when you're iterating on
`electron/*.ts` (main / preload / tray menus) only and you don't want to wait
for Vite.

### `npm run electron:dev:live` — HMR via Vite dev server

`node scripts/dev-electron-live.mjs` sets `ELECTRON_DEV=1`, which makes
the main process:

- **Skip spawning the bundled backend** (assumes the backend is already
  running on its own port).
- **Load the UI from the Vite dev server** at `http://127.0.0.1:5173/`
  (or `OPENSQUAD_FRONTEND_PORT`), giving you React HMR.

Use this when you're iterating on UI/UX and want instant feedback. The
backend still needs to be running in another terminal.

---

## Production build (one command, one platform)

```bash
cd src/opensquad/gateway/nexuschat-pro
npm run electron:build
# or
npm run electron:win       # Windows-only
npm run electron:mac       # macOS-only
npm run electron:linux     # Linux-only
```

Each of these runs:

1. `npm run icons:build` — runs `python ../../../scripts/build_icons.py`,
   which rasterizes `assets/logo-source.svg` into `icon.png` / `icon@2x.png`
   / `icon.ico` / `icon.icns` / `tray.png`. Requires Pillow + Playwright +
   Chromium.
2. `npm run build` — `vite build`, bundles the React UI to `dist/`.
3. `node scripts/compile-electron.mjs` — TypeScript → CJS (see above).
4. `electron-builder` — packages everything into the platform installer(s).

### Build outputs

| Platform | Command | Outputs (in `build/release/`) |
|----------|---------|------------------------------|
| Windows  | `electron:win`  | `*-setup.exe` (NSIS installer), `*portable.exe` (no install) |
| macOS    | `electron:mac`  | `*.dmg` and `*.zip` for **both** x64 and arm64 |
| Linux    | `electron:linux`| `*.AppImage`, `*.deb` |
| All three (current OS only) | `electron:build` | whatever the current platform builds |

The output directory is `build/release/` at the **project root**, **not**
inside the frontend folder (see `package.json` → `build.directories.output`
= `../../../build/release`).

### Prerequisites for the **PyInstaller backend step**

The `electron-builder` step expects `build/backend-<os>/run/` to already
contain the bundled Python backend for the target OS. The frontend build
**does not** produce it — you need a separate PyInstaller step first.

Two ways to populate `build/backend-<os>/`:

#### Option A — per-OS build script (local)

```bash
# Windows
scripts\build_backend.bat
# macOS / Linux
bash scripts/build_backend.sh
```

Both scripts call PyInstaller with `opensquad/gateway/backend/opensquad_backend.spec`
and write to `build/backend-<win|mac|linux}/run/`.

#### Option B — let CI do it (recommended for releases)

`build-desktop.yml` runs the PyInstaller step in parallel for all three
OSes on `push` of a `v*` tag, then assembles the Electron installers on
matching runners. See [CI / release pipeline](#ci--release-pipeline) below.

---

## CI / release pipeline

`.github/workflows/build-desktop.yml` is the canonical way to produce
release installers. Two paths trigger it:

- **Tag push**: `git tag -a v0.X.Y && git push origin v0.X.Y` — runs the
  full multi-platform build and creates a GitHub Release with the
  installers attached.
- **Manual**: `Actions → Build Desktop App → Run workflow` — useful for
  testing the build without a release.

Three stages:

1. **`build-backend`** (3-way matrix) — install Python deps, build icons,
   `npm ci && npm run build` (so PyInstaller can bundle the UI), run
   PyInstaller with `opensquad_backend.spec`, upload `backend-{win,mac,linux}`
   artifact.
2. **`build-electron`** (3-way matrix, `needs: build-backend`) — download
   the matching backend artifact, `npm ci`, run `npm run electron:{win,mac,linux}`,
   upload the resulting `build/release/*.{exe,dmg,AppImage,deb}` as
   `release-<os>` artifacts (retained 7 days).
3. **`create-release`** (only on tag push) — downloads the three
   `release-*` artifacts and attaches them to a GitHub Release with
   auto-generated notes.

The full pipeline takes **~25–35 minutes** for a clean run on a fresh
tag (3 backend builds in parallel + 3 electron builds in parallel + release).

### Verifying a desktop build

After the workflow finishes:

1. `https://github.com/opensquad-ai/opensquad/releases/tag/v0.X.Y` — the
   GitHub Release page should have platform-specific installers attached.
2. The **artifacts tab** of the workflow run also has them (if you ran
   via `workflow_dispatch` and didn't create a release).
3. Smoke test: download the installer for your platform, install, launch.
   The app should open to the gateway UI, spawn its bundled backend
   (visible in the system tray), and the backend port should be reachable
   on `127.0.0.1:9555/health`.

### Release versioning rules (strictly enforced from v0.4.10)

History lesson: we shipped untested versions as stable Releases multiple
times, and users hit bugs after downloading. To prevent repeats, we
introduced the beta.N tag + three-stage testing flow.

#### Version format

| Format | Meaning |
|--------|---------|
| `vX.Y.Zbeta.N` | CI build package, not yet passed the three-stage test (N starts at 0, increments by 1 after each fix-and-rebuild) |
| `vX.Y.Z` | Stable release, only tagged after all three stages pass |

#### Three-stage testing (any failure → fix → restart from stage 1)

1. **Local quick verification** (~6 min) —
   `scripts\build_backend.bat` to rebuild `run.exe`, then
   `uv run python scripts\smoke_frozen_all.py`.
   All hard-gate smokes must PASS (path checks, gateway, model/role cards,
   skills, plugin service discovery, MCP config).
2. **Local packaging verification** (~3 min) —
   `cd src\opensquad\gateway\nexuschat-pro && npx electron-builder --win --dir --publish never`
   to produce the unpacked dir, then manually run
   `build\release\win-unpacked\OpenSquad.exe` to verify the desktop app
   opens, UI loads, Service Manager lists services.
3. **CI build download + manual testing** (~30 min + test time) —
   Push `vX.Y.Zbeta.N` tag to trigger CI, wait for build to finish,
   download the installer from GitHub Release, install and test all
   desktop features (chat, service start/stop, Token Analytics dashboard,
   MCP, skills).

#### Flow diagram

```
fix → push vX.Y.Zbeta.0 tag → CI build (~30 min)
                                ↓
                          download beta.0 and test
                                │
        ┌───────────────────────┼───────────────────────┐
        │ Stage 1 (local quick) │ Stage 2 (local pack)  │ Stage 3 (CI manual) │
        └───────────────────────┴───────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
                all PASS               any FAIL
                    │                       │
                    ▼                       ▼
        delete beta.0 tag          fix → push vX.Y.Zbeta.1 tag
        push vX.Y.Z stable tag          → rebuild CI → restart stages
        → CI produces stable Release         │
                                            └─ N increments until all pass
```

#### Command quick reference

```powershell
# Publish beta.0
git tag -a v0.4.10beta.0 -m "v0.4.10 beta.0: <fix description>"
git push origin v0.4.10beta.0

# After all three stages pass, promote to stable (delete beta tag first, then push stable tag)
git tag -d v0.4.10beta.0
git push origin :refs/tags/v0.4.10beta.0
git tag -a v0.4.10 -m "v0.4.10: <release notes>"
git push origin v0.4.10
```

#### Historical exception

`v0.4.9` was pushed as a stable tag before this rule was established and is
left as-is. Strictly enforced from `v0.4.10` onward.

---

## Frozen-mode quick verification (change → 6 min result)

### Why quick verification is needed

PyInstaller frozen-mode bugs **only reproduce after packaging** — the dev mode
(`uv run opensquad start`) runs source code with venv Python, which is a
completely different runtime path from frozen `run.exe`. If every code change
requires a full cycle of "rebuild backend (5 min) → electron-builder (2.5 min)
→ install → click UI → find bug", each iteration takes 10+ minutes.

**Key insight**: electron-builder just copies `build/backend-win/run/` into the
installer — it doesn't change `run.exe` behavior. So **most frozen bugs can be
reproduced and verified with the backend bundle alone**, no electron-builder,
no installer.

### Quick verification flow

```
change code → rebuild backend (5 min) → test with run.exe directly (10 s)
                                              ↓
                                         PASS → then run electron-builder
                                         FAIL → fix code, repeat
```

#### Step 0: One-time setup

```powershell
# Ensure Agent Python runtime is installed (first time only)
build\release\win-unpacked\OpenSquad.exe --setup-runtime

# Or verify manually
Test-Path "$env:LOCALAPPDATA\OpenSquad\runtime\python311\python.exe"
```

#### Step 1: Rebuild backend (~5 min)

```powershell
scripts\build_backend.bat

# Or call PyInstaller directly (skips frontend build, faster):
uv run --python 3.11 pyinstaller src\opensquad\gateway\backend\opensquad_backend.spec `
  --distpath build\backend-win --workpath build\.pyinstaller-work --clean --noconfirm
```

> **Note**: `build_backend.bat` may pass empty arguments due to `^` line
> continuation + blank lines. If you see
> `pyinstaller: error: unrecognized arguments`, use the direct PyInstaller
> command above.

#### Step 2: Smoke test — can agents start? (~10 s)

```powershell
uv run python scripts\smoke_frozen_agent.py
```

The script:
1. Starts `run.exe --service launcher --mgmt-port 9600 --no-auto-start --no-services`
2. Waits for port 9600
3. Calls `POST /api/agents/coder/start`
4. Polls `/api/agents` until `alive=True`
5. Cleans up processes

**Expected output**:
```
[smoke] Launcher up after 1s, agents: ['coder', 'pm', 'qa']
[smoke] Start response: {'message': 'coder started', 'pid': 123456, 'port': 8001}
[smoke] 0s: alive=True pid=123456 port=8001 restarts=0
PASS: coder agent is alive on port 8001
```

#### Step 3: Smoke test — can agents chat? (~10 s)

Requires Gateway running (start the desktop app or run.exe separately):

```powershell
Start-Process build\release\win-unpacked\OpenSquad.exe
Start-Sleep -Seconds 20  # wait for Gateway

uv run python scripts\smoke_chat.py
```

The script:
1. `POST /api/auth/login` to get JWT
2. Connects `ws://127.0.0.1:9555/ai-web/ws/coder-001?token=<JWT>`
3. Sends `{"type": "chat", "content": "Hello, reply with one sentence to confirm you work"}`
4. Receives `thought` (thinking stream) + `message` (reply)
5. Reports SUCCESS / FAIL

#### Step 4: Only after all PASS, run electron-builder

```powershell
cd src\opensquad\gateway\nexuschat-pro
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
# --dir produces unpacked dir only (~1 min), no installer
npx electron-builder --win --dir --publish never --config.win.signAndEditExecutable=false
# Or full installer (~2.5 min)
npx electron-builder --win --publish never --config.win.signAndEditExecutable=false
```

### Ultra-fast iteration: patch bundle without rebuild (10 s)

If smoke test reports `ModuleNotFoundError` or `FileNotFoundError`, it usually
means PyInstaller missed a data file or submodule. **No need to wait 5 min for
a rebuild** — copy the missing files directly into the bundle and retest:

```powershell
# Example: prompts directory wasn't bundled
Copy-Item -Path src\prompts -Destination build\backend-win\run\_internal\prompts -Recurse -Force

# Example: tiktoken_ext submodule wasn't in PYZ
Copy-Item -Path .venv\Lib\site-packages\tiktoken_ext `
  -Destination build\backend-win\run\_internal\tiktoken_ext -Recurse -Force

# Immediately retest (10 s)
uv run python scripts\smoke_frozen_agent.py
```

After verifying the fix works, add the corresponding `datas` / `hiddenimports`
to `opensquad_backend.spec` and do a clean rebuild to confirm the spec is
correct.

### Recommended gate (local ~1 min, skip Electron)

After PyInstaller, run frozen smokes before pushing a tag or waiting for Setup CI:

```powershell
scripts\build_backend.bat
uv run python scripts\smoke_frozen_all.py
```

**Architecture rule (frozen desktop)**:

> User-writable data **must** use `syscfg.workspace_*()` / `get_workspace()`.  
> `builtin_resources_dir()` / `get_builtin_root()` are **read-only** seeds.  
> Reads should use **workspace first, then builtin** via `resource_search_dirs()`.

CI: `build-desktop.yml` runs the same gate on the Windows backend job right after
PyInstaller — **before** the Electron stage (~10+ min saved on backend path bugs).

### Smoke scripts

| Script | Purpose | Time | Requires |
|--------|---------|------|----------|
| `scripts/smoke_frozen_all.py` | **Run all** frozen gates below | ~30s | `build/backend-win/run/run.exe` |
| `scripts/check_frozen_writable_paths.py` | Static scan for write + builtin anti-patterns | ~1s | None |
| `scripts/smoke_frozen_gateway.py` | Verify frozen gateway startup (`/health` ready) | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_model_card_save.py` | Verify model cards save to workspace | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_role_card_save.py` | Verify role cards save to workspace | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_skill_upload.py` | Verify skill upload to workspace | ~5s | `build/backend-win/run/run.exe` |
| `scripts/smoke_frozen_agent.py` | Verify frozen launcher + agent startup | ~10s | `build/backend-win/run/run.exe` |
| `scripts/smoke_chat.py` | Verify end-to-end chat (login→WS→send→reply) | ~10s | Gateway running on 9555 |
| `scripts/check_build_python.py --bundle <dir>` | Verify bundle uses Python 3.11 | ~1s | None |

### Common frozen-only bug patterns

These bugs **never appear in dev mode** — only in the frozen bundle:

| Bug | Root cause | Fix |
|-----|-----------|-----|
| `ModuleNotFoundError: opensquad.launcher.process_manager` | `launcher.py` shadowed `launcher/` package | Renamed to `launcher_main.py` |
| `ModuleNotFoundError: No module named 'opensquad'` | External Python can't import from PYZ | Use `run.exe --service agent` instead of external `python -m` |
| `FileNotFoundError: base_fc.md` | `prompts/` directory not bundled | Add explicitly to spec `datas` |
| `ValueError: Unknown encoding cl100k_base` | `tiktoken_ext` not in PYZ | Add to spec `hiddenimports` |
| `Module use of python311.dll conflicts` | System Python 3.13 + PATH polluted with `_internal` | Setup wizard downloads embed Python 3.11 |
| `Backend did not start in time` (NSIS install under Program Files) | Import-time writes to read-only `_internal/` (`session_manager`, `bot_api.uploads`, etc.) | Route all writable paths via `OPENSQUAD_USER_DATA` |
| Model card / plugin / skill / market install save fails | Launcher/Gateway writes via `builtin_resources_dir` | Use `workspace_*_dir()`; reads via `resource_search_dirs()` |

---

## Common pitfalls

### `electron:dev` white-screens on first run

- The Vite build succeeded but the bundled backend isn't running. Start
  it in another terminal (`uv run opensquad start`). Look at the
  Electron DevTools console — if you see `ECONNREFUSED 127.0.0.1:9510`,
  that's the cause.
- The dev variant you want is `electron:dev:live` for HMR, or
  `electron:dev` if you want production-bundle behaviour.

### `electron:build` complains about missing icons

- `npm run icons:build` failed (Pillow / Playwright not installed, or
  Chromium download blocked). Run it manually to see the error:
  `python scripts/build_icons.py`.
- Re-run the `electron:build` script — `icons:build` is its first step.

### `electron:build` for a non-native platform

- You **cannot** produce a Windows `.exe` from macOS or Linux (or vice
  versa) without Wine / a Windows VM. The matrix in `build-desktop.yml`
  exists exactly because of this. For local builds, stick to the variant
  matching your host OS.

### `electron-builder` complains about `extraResources`

- The `build/backend-<os>/run/` directory doesn't exist or is empty.
  Either run the matching `build_backend.{sh,bat}` first, or download the
  artifact from a `build-desktop.yml` run that completed the
  `build-backend` stage.

### `appimage` won't run on Linux

- `chmod +x <file>.AppImage` first, then run it.
- Some FUSE-less environments need `--appimage-extract-and-run` as a
  fallback. Or use the `.deb` instead.

### Code signing (macOS / Windows)

- **Not configured** in this repo. macOS builds will be unsigned (Gatekeeper
  warning on first launch, right-click → Open to bypass). Windows builds
  are similarly unsigned. Adding signing is a project-level decision;
  see [RELEASING.md](../RELEASING.md) for the implications.

### Output is in `build/release/` at the **project root**, not in `nexuschat-pro/`

- Confusing on first look — `package.json` →
  `build.directories.output = "../../../build/release"`. Three `..`s
  because `nexuschat-pro/` is three directories deep from the project
  root. The `build/` directory is in `.gitignore` and is **not** cleaned
  by the npm scripts; remove it manually with `rm -rf build/` if you
  want a fresh slate.

---

## What this file does NOT cover

- **Backend install / first-launch wizard / web UI** →
  [deployment_guide.md](deployment_guide.md) and
  [getting_started.md](getting_started.md).
- **Branch model and version policy** → [BRANCHING.md](../BRANCHING.md).
- **End-to-end release process (when does a tag get cut, what runs)** →
  [RELEASING.md](../RELEASING.md).
- **Plugin development** → [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md)
  (covered in `doc_cn/` only at the moment; ask in an issue if you need
  an English version).
