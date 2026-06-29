@echo off
REM Whisper Speech-to-Text Service Startup Script
REM
REM Usage:
REM   start_whisper.bat              # default: base model
REM   start_whisper.bat small        # small model
REM   start_whisper.bat medium       # medium model
REM   start_whisper.bat large-v3     # large-v3 model (best accuracy)

echo ========================================
echo  Whisper Speech-to-Text Service
echo ========================================
echo.
echo Config source: system_config.json
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

REM Check whisper
python -c "import whisper" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] openai-whisper not installed, installing...
    pip install openai-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple
)

REM Check flask
python -c "import flask" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] flask not installed, installing...
    pip install flask flask-cors -i https://pypi.tuna.tsinghua.edu.cn/simple
)

REM Set model name (from arg, default: base)
set MODEL=%1
if "%MODEL%"=="" set MODEL=base

echo.
echo [Config] Model: %MODEL%
echo.
echo Model comparison:
echo   - base:     74MB,  ~30s startup,  accuracy: medium  (recommended for daily use)
echo   - small:    461MB, ~1min startup,  accuracy: good
echo   - medium:   1.5GB, ~2min startup,  accuracy: very good
echo   - large-v3: 3GB,   ~4min startup,  accuracy: best
echo.
echo [Info] Model location: %USERPROFILE%\.cache\whisper\%MODEL%.pt
echo [Info] First startup will auto-download the model
echo.

REM Set model env (port comes from system_config.json)
set WHISPER_MODEL=%MODEL%

REM Start service
echo Starting Whisper service...
cd /d "%~dp0\.."
python services/whisper/service.py

pause
