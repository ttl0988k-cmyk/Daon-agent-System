@echo off
chcp 65001 >nul
echo ======================================================================
echo    DAON Agent System - Unified One-Click Build & Auto-Sync Pipeline
echo ======================================================================
cd /d "%~dp0"

echo.
echo [1/3] Step 1: Syncing source to dist_new mirror (_sync_build.py)...
python _sync_build.py
if errorlevel 1 (
    echo [ERROR] _sync_build.py failed! Aborting build.
    exit /b 1
)

echo.
echo [2/3] Step 2: Rebuilding backend PyInstaller binary (daon-server.spec)...
python -m PyInstaller daon-server.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed! Aborting build.
    exit /b 1
)

echo.
echo [3/3] Step 3: Packaging Electron and auto-syncing to installed apps (after-pack.js)...
call npx electron-builder
if errorlevel 1 (
    echo [ERROR] electron-builder failed! Aborting build.
    exit /b 1
)

echo.
echo ======================================================================
echo    [SUCCESS] Build and Auto-Sync Completed Successfully!
echo    - dist/server.exe updated
echo    - dist/win-unpacked updated
echo    - Local installed app (resources/) auto-synced via after-pack.js
echo ======================================================================
