@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_online.ps1"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Uninstall failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
