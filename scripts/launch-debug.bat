@echo off
setlocal

call "%~dp0setup-env.bat"
if errorlevel 1 exit /b %errorlevel%

pushd "%DEPTHVISTA_APP_DIR%"
"%DEPTHVISTA_PYTHON_DIR%\python.exe" -X utf8 -m iw3.desktop.gui_dpg
set "EXIT_CODE=%errorlevel%"
popd

if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
