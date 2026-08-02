@echo off
chcp 65001 >nul
title OpenSquad All-in-One (Local Mode)
cd /d "%~dp0\.."

set ROOTDIR=%~dp0..
set PYTHONPATH=%ROOTDIR%\src
set FRONTDIR=%~dp0..\src\opensquad\gateway\nexuschat-pro

echo ==================================================
echo   OpenSquad All-in-One  (Local Mode)
echo ==================================================

:: Auto-create system_config.json from example if missing
if not exist src\system_config.json (
    if exist src\system_config.example.json (
        copy /Y src\system_config.example.json src\system_config.json >nul
        echo [OK] src\system_config.json created from example
    ) else (
        echo [!!] src\system_config.example.json not found, cannot create config
    )
)

:: Auto switch to local mode: hosts.gateway = 127.0.0.1
echo [*] Switching to local mode...
if exist src\system_config.json (
    python -c "import json; f=open('src/system_config.json','r',encoding='utf-8'); cfg=json.load(f); f.close(); cfg.setdefault('hosts',{})['gateway']='127.0.0.1'; f=open('src/system_config.json','w',encoding='utf-8'); json.dump(cfg,f,ensure_ascii=False,indent=2); f.close(); print('[OK] hosts.gateway = 127.0.0.1 (src/system_config.json)')"
) else (
    echo [--] src/system_config.json not found, skip local rewrite
)
python scripts\update_workspace_config.py 127.0.0.1
(echo VITE_BACKEND_HOST=127.0.0.1& echo VITE_BACKEND_PORT=9555) > src\opensquad\gateway\nexuschat-pro\.env.local
echo [OK] .env.local: VITE_BACKEND_HOST = 127.0.0.1

:: Production-like local mode: no uvicorn watcher, no Vite dev server
set OPENSQUAD_RELOAD=0
set OPENSQUAD_DISABLE_VITE_PROXY=1

if not exist src\data\mcp_config.json (
    echo [*] Creating central MCP config with playwright disabled...
    if not exist src\data mkdir src\data >nul
    python -c "import json,os; p='src/data/mcp_config.json'; d={'mcpServers':{'playwright':{'enabled':False,'command':'npx','args':['-y','@playwright/mcp'],'timeout':60,'autoApprove':['browser_navigate','browser_snapshot','browser_click','browser_screenshot','browser_type','browser_evaluate','browser_take_screenshot']}}}; open(p,'w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False,indent=2))"
)

if not exist "%FRONTDIR%\dist\index.html" (
    echo [!!] dist\index.html not found. Run "npm run build" in %FRONTDIR%
) else (
    echo [OK] Serving built frontend dist via Gateway :9555
)

:: Kill existing ports
echo [*] Checking ports...
for %%P in (5173 9555 9530 9600 9720) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING" 2^>nul') do (
        echo     Killing PID %%a on port %%P ...
        taskkill /PID %%a /F >nul 2>&1
    )
)

:: [1/4] Gateway Backend
echo.
echo [1/4] Starting Gateway Backend (port 9555, reload off, static dist)...
start "Gateway Backend" cmd /k "chcp 65001 >nul && cd /d %ROOTDIR% && python -m opensquad.gateway.backend.run"

:: [2/4] Plugin Registry
echo [2/4] Starting Plugin Registry (port 9720)...
start "Plugin Registry" cmd /k "chcp 65001 >nul && cd /d %ROOTDIR% && python -m opensquad.gateway.plugin_registry.main"

:: [3/4] Frontend: built dist served by Gateway, Vite dev server is not started
echo [3/4] Frontend: built dist served by Gateway (Vite dev server skipped)...

:: [4/4] Launcher
echo [4/4] Starting Launcher (port 9600)...
start "OpenSquad Launcher" cmd /k "chcp 65001 >nul && cd /d %ROOTDIR% && python -m opensquad.launcher_main"

echo.
echo [*] Waiting for Gateway / Plugin Registry / Launcher readiness...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wait_ready.ps1"
if errorlevel 1 (
    echo [!!] Some services did not become ready. Check their windows/logs.
) else (
    echo [OK] All services ready.
)

echo.
echo [OK] All services started (local mode, production-like frontend).
echo      Frontend : http://127.0.0.1:9555
echo      Gateway  : http://127.0.0.1:9555
echo      Launcher : http://127.0.0.1:9600
echo.
pause
