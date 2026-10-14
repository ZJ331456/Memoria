@echo off
setlocal
cd /d "%~dp0..\.." || exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\backend.ps1" -Action Stop %*
if errorlevel 1 (
  echo Backend shutdown failed. See the message above.
  pause
  exit /b 1
)
exit /b 0
