@echo off

set "DEPTHVISTA_ROOT=%~dp0.."
for %%I in ("%DEPTHVISTA_ROOT%") do set "DEPTHVISTA_ROOT=%%~fI"

set "DEPTHVISTA_APP_DIR=%DEPTHVISTA_ROOT%\app"
set "DEPTHVISTA_RUNTIME_DIR=%DEPTHVISTA_ROOT%\runtime"
set "DEPTHVISTA_PYTHON_DIR=%DEPTHVISTA_RUNTIME_DIR%\python"

if not exist "%DEPTHVISTA_APP_DIR%\iw3\desktop\gui_dpg.py" (
  echo DepthVista XR: application files are missing.
  exit /b 1
)

if not exist "%DEPTHVISTA_PYTHON_DIR%\python.exe" (
  echo DepthVista XR: Python runtime is missing.
  exit /b 1
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "TQDM_ASCII=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONSTARTUP="
set "PYTHONUSERBASE="
set "PYTHONEXECUTABLE="
set "PIP_TARGET="
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0;%DEPTHVISTA_PYTHON_DIR%;%DEPTHVISTA_PYTHON_DIR%\Scripts"

exit /b 0
