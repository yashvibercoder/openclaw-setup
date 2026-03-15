@echo off
setlocal
title OpenClaw Setup

:: 1. Check if running as Administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrative privileges...
    PowerShell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)

:: 2. Check if Python 3 is installed
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo Python 3 is not installed. Attempting to install via winget...
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if %errorLevel% neq 0 (
        echo.
        echo winget is not available or failed to install Python.
        echo Please install Python 3 from python.org manually.
        echo Press any key to open the download page...
        pause >nul
        start https://python.org/downloads
        exit /b 1
    )
    
    :: Update PATH to include Python
    for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do (
        set "SYSPATH=%%B"
    )
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do (
        set "USERPATH=%%B"
    )
    set "PATH=%USERPATH%;%SYSPATH%;%PATH%"
)

:: 3. Navigate to the script's own directory
cd /d "%~dp0"

:: 4. Run launch.py
python launch.py
if errorlevel 1 (
    echo.
    echo Setup failed. See error above.
    pause
    exit /b 1
)
