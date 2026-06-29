@echo off
chcp 65001 >nul
title OpenSquad External Adapter

echo ============================================
echo   OpenSquad External Adapter (Multi-instance)
echo ============================================
echo.
echo Config source: system_config.json
echo.

cd /d "%~dp0\.."
python -m plugins.external_api.adapter

if errorlevel 1 (
    echo.
    echo [ERROR] Process exited abnormally
    pause
)
