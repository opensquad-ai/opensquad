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
echo [1/4] Starting Gateway Backend (port 9555)...
start "Gateway Backend" cmd /k "chcp 65001 >nul && cd /d %ROOTDIR% && python -m opensquad.gateway.backend.run"
timeout /t 3 /nobreak >nul

:: [2/4] Plugin Registry
echo [2/4] Starting Plugin Registry (port 9720)...
start "Plugin Registry" cmd /k "chcp 65001 >nul && cd /d %ROOTDIR% && python -m opensquad.gateway.plugin_registry.main"
timeout /t 2 /nobreak >nul

:: [3/4] Frontend Dev Server
echo [3/4] Starting Frontend Dev Server (port 5173)...
start "Nexus Frontend Dev" cmd /k "chcp 65001 >nul && cd /d %FRONTDIR% && npm run dev"
timeout /t 2 /nobreak >nul

:: [4/4] Launcher
echo [4/4] Starting Launcher (port 9600)...
start "OpenSquad Launcher" cmd /k "chcp 65001 >nul && cd /d %ROOTDIR% && python -m opensquad.launcher"

echo.
echo [OK] All services started (local mode).
echo      Gateway  : http://127.0.0.1:9555
echo      Frontend : http://127.0.0.1:5173
echo      Launcher : http://127.0.0.1:9600
echo.
pause
