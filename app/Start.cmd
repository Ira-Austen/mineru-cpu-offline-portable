@echo off
chcp 65001 >nul
setlocal
set "PYTHONHOME="
set "PYTHONPATH=%~dp0packages"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%~dp0lib;%~dp0runtime\python;%PATH%"
"%~dp0runtime\python\python.exe" -s "%~dp0launcher.py" %*
if errorlevel 1 pause
