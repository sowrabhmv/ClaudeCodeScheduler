@echo off
REM ============================================================
REM  Install Claude Code Scheduler from source (Python required)
REM ============================================================

echo.
echo === Claude Code Scheduler - Install from Source ===
echo.

REM Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Download from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Check Claude CLI
where claude >nul 2>&1
if errorlevel 1 (
    echo WARNING: Claude CLI not found in PATH.
    echo Install it from: https://docs.anthropic.com/en/docs/claude-code
    echo You can still install the scheduler and configure the CLI path later.
    echo.
)

echo Installing dependencies...
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

REM Generate icon
if not exist "app.ico" (
    echo Generating app icon...
    python gen_icon.py
)

echo.
echo === Installation Complete ===
echo.
echo To run the app:
echo   python main.py
echo.
echo To run in background (tray only):
echo   pythonw main.py --background
echo.
echo To build a standalone .exe:
echo   build.bat
echo.
pause
