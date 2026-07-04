@echo off

:: ============================================================
:: build_backend.bat  —  在 Windows 上构建 Python 后端二进制
::
:: 输出到：项目根目录 build\backend-win\run\
:: 不在源码目录内产生任何构建产物
:: 与 CI / build_backend.sh 一致：固定 Python 3.11
:: ============================================================

setlocal

set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..
set BACKEND_DIR=%PROJECT_ROOT%\src\opensquad\gateway\backend
set FRONTEND_DIR=%PROJECT_ROOT%\src\opensquad\gateway\nexuschat-pro
set SPEC_FILE=%BACKEND_DIR%\opensquad_backend.spec
set PYTHON_VERSION=3.11

:: 构建产物统一放在 build/ 下，与源码隔离
set DIST_PATH=%PROJECT_ROOT%\build\backend-win
set WORK_PATH=%PROJECT_ROOT%\build\.pyinstaller-work

echo ============================================================
echo  OpenSquad Desktop - Windows Backend Build
echo  Output: %DIST_PATH%\run\
echo  Python: %PYTHON_VERSION% (required)
echo ============================================================
echo.

echo [1/7] Sync project deps (Python %PYTHON_VERSION%, matches CI)...
cd /d "%PROJECT_ROOT%"
uv sync --python %PYTHON_VERSION% --quiet
if %errorlevel% neq 0 ( echo ERROR: uv sync failed & exit /b 1 )

echo [2/7] Installing PyInstaller into project venv...
uv pip install pyinstaller --quiet
if %errorlevel% neq 0 ( echo ERROR: uv pip install failed & exit /b 1 )

echo [3/7] Verify Python %PYTHON_VERSION% interpreter...
uv run --python %PYTHON_VERSION% python scripts/check_build_python.py
if %errorlevel% neq 0 ( echo ERROR: Python version check failed & exit /b 1 )

echo [4/7] Building frontend (React)...
cd /d "%FRONTEND_DIR%"
call npm run build
if %errorlevel% neq 0 ( echo ERROR: npm build failed & exit /b 1 )

echo [5/7] Building plugin UI bundles (Token Analytics, Quick Note, Task Watch, Email Assistant)...
cd /d "%PROJECT_ROOT%"
powershell -ExecutionPolicy Bypass -File scripts\build_plugin_ui.ps1
if %errorlevel% neq 0 ( echo ERROR: Plugin UI build failed & exit /b 1 )

echo [6/7] Running PyInstaller (uv / Python %PYTHON_VERSION%)...
cd /d "%PROJECT_ROOT%"
uv run --python %PYTHON_VERSION% pyinstaller "%SPEC_FILE%" --distpath "%DIST_PATH%" --workpath "%WORK_PATH%" --clean --noconfirm
if %errorlevel% neq 0 ( echo ERROR: PyInstaller failed & exit /b 1 )

echo [7/7] Verify PyInstaller bundle is Python %PYTHON_VERSION%...
uv run --python %PYTHON_VERSION% python scripts/check_build_python.py --bundle "%DIST_PATH%\run"
if %errorlevel% neq 0 ( echo ERROR: Bundle Python version check failed & exit /b 1 )

echo.
echo ============================================================
echo  Backend built successfully!
echo  Binary: %DIST_PATH%\run\run.exe
echo.
echo  Next: cd src\opensquad\gateway\nexuschat-pro
echo        set CSC_IDENTITY_AUTO_DISCOVERY=false
echo        npx electron-builder --win --publish never --config.win.signAndEditExecutable=false
echo  Final installer: build\release\
echo ============================================================
