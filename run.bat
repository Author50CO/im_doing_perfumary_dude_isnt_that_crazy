@echo off
setlocal
cd /d "%~dp0"

echo.
echo ========================================
echo  PerfumeStudio Portable ZIP Builder
echo ========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_portable.ps1"
if errorlevel 1 (
    echo.
    echo BUILD FAILED. See the error above.
    echo.
    pause
    exit /b 1
)

echo.
echo Portable ZIP created in:
echo %~dp0release\PerfumeStudio_Portable.zip
echo.
start "" explorer.exe "%~dp0release"
pause
exit /b 0
