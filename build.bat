@echo off
REM ============================================================
REM  Build Claude Code Scheduler as a standalone Windows .exe
REM  Requires: pip install pyinstaller
REM ============================================================

echo.
echo === Claude Code Scheduler - Build Script ===
echo.

REM Check pyinstaller
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Generate icon if missing
if not exist "app.ico" (
    echo Generating app icon...
    python gen_icon.py
)

echo.
echo Building executable...
echo.

pyinstaller ^
    --name "ClaudeCodeScheduler" ^
    --onedir ^
    --windowed ^
    --icon "app.ico" ^
    --add-data "app.ico;." ^
    --hidden-import "pystray._win32" ^
    --noconfirm ^
    --clean ^
    main.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED
    pause
    exit /b 1
)

echo.
echo === Build Complete ===
echo Output: dist\ClaudeCodeScheduler\ClaudeCodeScheduler.exe
echo.
echo To create a Windows installer, install Inno Setup and run:
echo   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
echo.
pause
