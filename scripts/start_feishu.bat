@echo off
chcp 65001 >nul
title OpenSquad Feishu Bot Adapter

echo ==============================
echo   OpenSquad Feishu Bot Adapter
echo ==============================
echo.
echo Config source: system_config.json
echo.

cd /d "%~dp0\.."
python -m plugins.feishu.adapter

if errorlevel 1 (
    echo.
    echo [ERROR] Process exited abnormally
    pause
)
