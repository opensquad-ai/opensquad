@echo off
chcp 65001 >nul
title OpenSquad Launcher
cd /d "%~dp0\.."

echo [*] Starting Launcher (port 9600)...
python -m opensquad.launcher
pause
