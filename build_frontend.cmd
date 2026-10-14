@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\frontend.ps1" %*
if errorlevel 1 (
  echo Frontend build failed. See the message above.
  pause
  exit /b 1
)
