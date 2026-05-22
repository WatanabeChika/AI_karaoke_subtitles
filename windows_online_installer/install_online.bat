@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_online.ps1"
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" (
  echo Install failed with exit code %EXIT_CODE%.
) else (
  echo Install finished.
)
pause
exit /b %EXIT_CODE%
