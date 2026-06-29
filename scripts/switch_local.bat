@echo off
chcp 65001 >nul
title 切换到本机模式 (Gateway=127.0.0.1)
cd /d "%~dp0\.."

echo [*] 切换到本机模式...
echo     Gateway : 127.0.0.1:9555
echo     前端Dev : 本机 (浏览器访问当前IP)

:: 改安装目录 hosts.gateway
python -c "import json; f=open('system_config.json','r',encoding='utf-8'); cfg=json.load(f); f.close(); cfg.setdefault('hosts',{})['gateway']='127.0.0.1'; f=open('system_config.json','w',encoding='utf-8'); json.dump(cfg,f,ensure_ascii=False,indent=2); f.close(); print('[OK] system_config.json: hosts.gateway = 127.0.0.1')"

:: 同步更新 workspace 的 system_config.json（若存在）
python scripts\update_workspace_config.py 127.0.0.1

:: 前端同步，0.0.0.0 表示用 window.location.hostname 自动解析
(echo VITE_BACKEND_HOST=0.0.0.0& echo VITE_BACKEND_PORT=9555) > opensquad\gateway\nexuschat-pro\.env.local
echo [OK] .env.local: VITE_BACKEND_HOST = 0.0.0.0 (auto)

echo.
echo [完成] 重启 Launcher、Gateway 和前端 dev server 后生效。
pause
