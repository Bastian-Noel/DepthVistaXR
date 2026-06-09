@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-runtime.ps1"
if errorlevel 1 pause
exit /b %errorlevel%
