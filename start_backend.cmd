@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backend.ps1" %*
if errorlevel 1 (
  echo Backend command failed. See the message above.
  pause
  exit /b 1
)
