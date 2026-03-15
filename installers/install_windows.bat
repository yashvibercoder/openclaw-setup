@echo off
setlocal EnableDelayedExpansion

:: 1. Check for admin rights — if not admin, relaunch self as admin
net session >nul 2>&1
if !errorLevel! neq 0 (
    echo [INFO] Requesting administrative privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo [OK] Running as Administrator

:: 2. Install Node.js >= 22 via winget
node -v >nul 2>&1
if !errorLevel! neq 0 (
    echo [INFO] Installing Node.js via winget...
    winget install --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
    if !errorLevel! neq 0 (
        echo [ERROR] winget is not available or failed. Please download Node.js from nodejs.org and run this script again.
        goto :error
    )
    echo [OK] Node.js installed.
) else (
    echo [OK] Node.js is already installed.
)

:: 3. Install Python 3 via winget
python -V >nul 2>&1
if !errorLevel! neq 0 (
    echo [INFO] Installing Python 3 via winget...
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    echo [OK] Python 3 installed.
) else (
    echo [OK] Python 3 is already installed.
)

:: 4. Refresh PATH (so node and python are usable in same session)
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do (
    set "SYSPATH=%%B"
)
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do (
    set "USERPATH=%%B"
)
set "PATH=%USERPATH%;%SYSPATH%;%PATH%"

:: 5. Install OpenClaw globally
echo [INFO] Installing OpenClaw globally...
call npm install -g openclaw
echo [OK] OpenClaw installed.

:: 6. Install Flask + requests
echo [INFO] Installing Flask and requests...
call pip install flask requests
echo [OK] Flask and requests installed.

:: 7. Create install directory C:\openclaw-setup and copy files
echo [INFO] Copying files to C:\openclaw-setup...
if not exist C:\openclaw-setup mkdir C:\openclaw-setup
xcopy /E /Y "%~dp0\..\*" "C:\openclaw-setup\"
echo [OK] Files copied.

:: 8. Install NSSM
if not exist C:\nssm\win64\nssm.exe (
    echo [INFO] Downloading NSSM...
    powershell -Command "Invoke-WebRequest -Uri https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip -OutFile %TEMP%\nssm.zip"
    powershell -Command "Expand-Archive -Path %TEMP%\nssm.zip -DestinationPath C:\ -Force"
    move "C:\nssm-2.24-101-g897c7ad" "C:\nssm"
    echo [OK] NSSM installed.
) else (
    echo [OK] NSSM is already installed.
)

:: 9. Register openclaw-setup as a Windows service via NSSM
echo [INFO] Registering OpenClaw Setup Server service...
C:\nssm\win64\nssm.exe install openclaw-setup "python" "C:\openclaw-setup\setup_server.py"
C:\nssm\win64\nssm.exe set openclaw-setup AppDirectory C:\openclaw-setup
net start openclaw-setup
echo [OK] Service registered and started.

:: 10. Open browser to setup wizard
echo [INFO] Setup wizard is opening in your browser!
timeout /t 3
start http://localhost:7070
exit /b

:error
echo [ERROR] Installation failed.
pause
exit /b
