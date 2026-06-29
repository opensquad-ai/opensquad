@echo off
chcp 65001 >nul
title OpenSquad - 一键清理工具
color 0c

echo ==================================================
echo           OpenSquad 进程强行清理工具
echo ==================================================
echo.
echo 注意：此脚本将强制终止所有 Python、Node.js 及相关服务进程。
echo       请确保你没有其他重要的 Python 或 Node 程序正在运行。
echo.
set /p confirm="确认清理？(Y/N): "
if /i "%confirm%" neq "Y" exit

echo.
echo [1/4] 正在清理 Python 进程 (Launcher/Agents/Backend)...
taskkill /F /IM python.exe /T 2>nul

echo [2/4] 正在清理 Node.js 进程 (Frontend/MCP)...
taskkill /F /IM node.exe /T 2>nul

echo [3/4] 正在清理可能残留的 NapCat/QQ 进程...
taskkill /F /IM NapCat.exe /T 2>nul

echo [4/4] 正在清理所有残留的 CMD 窗口 (当前窗口除外)...
:: 尝试杀掉除了当前进程之外的所有 cmd.exe
taskkill /F /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE ne OpenSquad - 一键清理工具" /T 2>nul

echo.
echo ==================================================
echo           清理完成！所有服务已彻底停止。
echo ==================================================
echo.
pause
