@echo off
:: ============================================================
:: build_backend.bat  —  在 Windows 上构建 Python 后端二进制
::
:: 输出到：项目根目录 build\backend-win\run\
:: 不在源码目录内产生任何构建产物
:: ============================================================
setlocal

set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..
set BACKEND_DIR=%PROJECT_ROOT%\opensquad\gateway\backend
set FRONTEND_DIR=%PROJECT_ROOT%\opensquad\gateway\nexuschat-pro
set SPEC_FILE=%BACKEND_DIR%\opensquad_backend.spec

:: 构建产物统一放在 build/ 下，与源码隔离
set DIST_PATH=%PROJECT_ROOT%\build\backend-win
set WORK_PATH=%PROJECT_ROOT%\build\.pyinstaller-work

echo ============================================================
echo  OpenSquad Desktop - Windows Backend Build
echo  Output: %DIST_PATH%\run\
echo ============================================================
echo.

echo [1/4] Installing opensquad package...
cd /d "%PROJECT_ROOT%"
pip install -e . --quiet
if %errorlevel% neq 0 ( echo ERROR: pip install failed & exit /b 1 )

echo [2/4] Installing backend dependencies...
pip install -e . --quiet
pip install pyinstaller --quiet
if %errorlevel% neq 0 ( echo ERROR: pip install failed & exit /b 1 )

echo [3/4] Building frontend (React)...
cd /d "%FRONTEND_DIR%"
call npm run build
if %errorlevel% neq 0 ( echo ERROR: npm build failed & exit /b 1 )

echo [4/4] Running PyInstaller...
cd /d "%PROJECT_ROOT%"
pyinstaller "%SPEC_FILE%" ^
  --distpath "%DIST_PATH%" ^
  --workpath "%WORK_PATH%" ^
  --clean --noconfirm
if %errorlevel% neq 0 ( echo ERROR: PyInstaller failed & exit /b 1 )

echo.
echo ============================================================
echo  Backend built successfully!
echo  Binary: %DIST_PATH%\run\run.exe
echo.
echo  Next: cd opensquad\gateway\nexuschat-pro
echo        npm run electron:win
echo  Final installer: build\release\
echo ============================================================
