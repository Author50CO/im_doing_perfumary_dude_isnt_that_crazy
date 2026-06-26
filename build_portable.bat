@echo off
setlocal

cd /d "%~dp0"

echo ========================================
echo Building PerfumeCalculator portable app
echo ========================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_portable.ps1"

echo.
pause