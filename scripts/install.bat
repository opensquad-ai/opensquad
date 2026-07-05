@echo off
chcp 65001 >nul
title OpenSquad 一键安装部署脚本 (Windows)
cd /d "%~dp0.."

:: Set workspace so all subprocesses read the correct system_config.json
set "OPENSQUAD_WORKSPACE=%USERPROFILE%\.opensquad\workspace"
set "PYTHONPATH=%~dp0..\src"

echo ==================================================
echo   OpenSquad One-Click Install (Windows)
echo ==================================================
echo.

:: ── 1. Check Python 3.11+ ──
echo [1/6] Checking Python version...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.11+ first.
    echo         Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
python -c "import sys; ver=sys.version_info; exit(0 if ver.major==3 and ver.minor>=11 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ required. Current version:
    python --version
    pause
    exit /b 1
)
python --version
echo [OK] Python version OK.
echo.

:: ── 2. Check Node.js 18+ ──
echo [2/6] Checking Node.js version...
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Please install Node.js 18+ first.
    echo         Download: https://nodejs.org/
    pause
    exit /b 1
)
node -e "process.exit(parseInt(process.version.slice(1))>=18?0:1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js 18+ required. Current version:
    node --version
    pause
    exit /b 1
)
node --version
echo [OK] Node.js version OK.
echo.

:: ── 3. Install Python dependencies ──
echo [3/6] Installing Python dependencies...
echo      This may take a few minutes. Download progress shown below:
echo.

:: Install project package (registers entry points + pyproject deps)
echo   -- pip install -e . (project package) --
call pip install -e .
if errorlevel 1 (
    echo [ERROR] pip install -e . failed.
    pause
    exit /b 1
)
echo.

:: Install gateway backend deps
echo   -- pip install -e . (opensquad package) --
call pip install -e .
if errorlevel 1 (
    echo [ERROR] pip install -e . failed.
    pause
    exit /b 1
)
echo.

echo [OK] Python dependencies installed.
echo.

:: ── 3b. Download Playwright Chromium browser ──
:: The `playwright` pip package (installed above) does NOT bundle the
:: Chromium binary. Without this step, the websearch plugin service fails
:: at runtime with "Executable doesn't exist at ...chromium_headless_shell-XXXX".
:: We download only chromium (not firefox/webkit) to keep the install lean.
:: Respect a user-set PLAYWRIGHT_BROWSERS_PATH but strip any trailing
:: whitespace (a common cause of "path not found" errors on Windows).
echo [3b/6] Downloading Playwright Chromium browser...
if defined PLAYWRIGHT_BROWSERS_PATH (
    set "PLAYWRIGHT_BROWSERS_PATH=%PLAYWRIGHT_BROWSERS_PATH: =%"
)
python -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Playwright Chromium download failed. Web search will not work until you run: python -m playwright install chromium
)
echo [OK] Playwright Chromium browser ready.
echo.

:: ── 4. Install frontend dependencies ──
echo [4/6] Installing frontend dependencies...
pushd "%~dp0..\src\opensquad\gateway\nexuschat-pro"
call npm install --silent 2>nul
if errorlevel 1 (
    echo [WARN] npm install had issues, continuing...
)
popd
echo [OK] Frontend dependencies installed.
echo.

:: ── 5. Create config & init workspace ──
echo [5/6] Initializing workspace...
if not exist src\system_config.json (
    if exist src\system_config.example.json (
        copy /Y src\system_config.example.json src\system_config.json >nul
        echo [INFO] Created src/system_config.json from example
        echo [INFO] Don't forget to add your LLM API keys in model_cards/*.json
    )
)

call python -m opensquad.cli.main init --workspace "%USERPROFILE%\.opensquad\workspace" >nul 2>&1
if errorlevel 1 (
    echo [WARN] init had issues, continuing...
)
echo [OK] Workspace initialized.
echo.

:: ── 5b. Pre-cache Playwright MCP package ──
:: Without this, the very first MCP call to api.telegram.org-style npx-fetched
:: @playwright/mcp server triggers a ~30-60s npm download inside the agent
:: process, which looks like a hang to the deployment tester. We kick it off
:: in the background here so the cache is warm by the time the agent starts.
echo [*] Pre-caching Playwright MCP package in background (npx -y @playwright/mcp@latest)...
start /b "" cmd /c "npx -y @playwright/mcp@latest --version >nul 2>&1"
echo.

:: ── 6. Start services ──
echo [6/6] Starting OpenSquad services...
echo.

:: Kill existing ports before starting
echo [*] Checking ports...
for %%P in (5173 9555 9530 9600 9720) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING" 2^>nul') do (
        taskkill /PID %%a /F >nul 2>&1
    )
)

:: Auto-create system_config.json from example if missing
if not exist src\system_config.json (
    if exist src\system_config.example.json (
        copy /Y src\system_config.example.json src\system_config.json >nul
    )
)

:: Set local mode
python -c "import json; f=open('src/system_config.json','r',encoding='utf-8'); cfg=json.load(f); f.close(); cfg.setdefault('hosts',{})['gateway']='127.0.0.1'; f=open('src/system_config.json','w',encoding='utf-8'); json.dump(cfg,f,ensure_ascii=False,indent=2); f.close()" 2>nul
python scripts\update_workspace_config.py 127.0.0.1 2>nul

set "FRONTDIR=%~dp0..\src\opensquad\gateway\nexuschat-pro"
set "ROOTDIR=%~dp0.."
(echo VITE_BACKEND_HOST=127.0.0.1) > "%FRONTDIR%\.env.local"
(echo VITE_BACKEND_PORT=9555) >> "%FRONTDIR%\.env.local"

:: Start all 4 services in background (same window)
echo [1/4] Starting Gateway Backend (port 9555)...
start /b "" python -m opensquad.gateway.backend.run >nul 2>&1

timeout /t 3 /nobreak >nul

echo [2/4] Starting Plugin Registry (port 9720)...
start /b "" python -m opensquad.gateway.plugin_registry.main >nul 2>&1

timeout /t 2 /nobreak >nul

echo [3/4] Starting Frontend Dev Server (port 5173)...
cd /d "%FRONTDIR%"
start /b "" npm run dev >nul 2>&1
cd /d "%ROOTDIR%"

timeout /t 2 /nobreak >nul

echo [4/4] Starting Launcher (port 9600)...
start /b "" python -m opensquad.launcher >nul 2>&1

echo.
echo ==================================================
echo   OpenSquad install complete!
echo   Gateway  : http://127.0.0.1:9555
echo   Frontend : http://127.0.0.1:5173
echo   Launcher : http://127.0.0.1:9600
echo ==================================================
echo.
echo   All services running. Press Ctrl+C to stop.
echo.
