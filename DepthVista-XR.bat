@echo off
setlocal

call "%~dp0scripts\setup-env.bat"
if errorlevel 1 exit /b %errorlevel%

pushd "%DEPTHVISTA_APP_DIR%"
start "DepthVista XR" "%DEPTHVISTA_PYTHON_DIR%\pythonw.exe" -X utf8 -m iw3.desktop.gui_dpg
popd

exit /b 0
