@echo off
chcp 65001 >nul
:: ============================================================================
:: switch_remote.bat
::   将本地 Launcher / 前端指向一台远程 Gateway。
::   下面三个占位符请在本地环境里替换成你自己的 LAN IP 后再运行：
::     <your-gateway-host>  - 运行 Gateway 的机器 LAN IP
::     <your-frontend-host> - 运行前端 dev server 的机器 LAN IP（通常与
::                             Gateway 同机，可保持两者一致）
::   例如家用网络可以填 192.168.1.20、192.168.1.21 等；公网部署请用域名。
::   不要把公司/家里的真实 IP 直接提交到 git。
:: ============================================================================
title 切换到远程模式 (Gateway=<your-gateway-host>)
cd /d "%~dp0\.."

echo [*] 切换到远程模式...
echo     Gateway : <your-gateway-host>:9555
echo     前端Dev : <your-frontend-host>:9530 连接远程Gateway

:: 只改 hosts.gateway 一处，Gateway 通过 WS 隧道反向感知 Launcher，无需配 Launcher IP
python -c "import json; f=open('system_config.json','r',encoding='utf-8'); cfg=json.load(f); f.close(); cfg.setdefault('hosts',{})['gateway']='<your-gateway-host>'; f=open('system_config.json','w',encoding='utf-8'); json.dump(cfg,f,ensure_ascii=False,indent=2); f.close(); print('[OK] system_config.json: hosts.gateway = <your-gateway-host>')"

:: 同步更新 workspace 的 system_config.json（若存在）
python scripts\update_workspace_config.py <your-gateway-host>

:: 前端同步指向同一个 Gateway
(echo VITE_BACKEND_HOST=<your-gateway-host>& echo VITE_BACKEND_PORT=9555) > opensquad\gateway\nexuschat-pro\.env.local
echo [OK] .env.local: VITE_BACKEND_HOST = <your-gateway-host>

echo.
echo [完成] 重启 Launcher 和前端 dev server 后生效。
pause
