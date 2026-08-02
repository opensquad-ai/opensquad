@echo off
chcp 65001 >nul
title NexusPro Gateway Server
cd /d "%~dp0\.."

echo ==================================================
echo        NexusPro Gateway Server (<your-gateway-host>)
echo ==================================================
echo        (Replace <your-gateway-host> with this machine's
echo         LAN IP, e.g. 192.168.1.20, before publishing.)
echo ==================================================

:: 切换到 gateway 配置
copy /Y system_config.gateway.json system_config.json >nul
echo [*] Using system_config.gateway.json

:: 使用 start_team.py 统一启动三个服务
set OPENSQUAD_RELOAD=0
set OPENSQUAD_DISABLE_VITE_PROXY=1
python scripts/start_team.py
pause
