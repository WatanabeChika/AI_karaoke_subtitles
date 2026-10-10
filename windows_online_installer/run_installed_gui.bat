@echo off
setlocal

set "INSTALL_ROOT=%LOCALAPPDATA%\AIKaraokeWebGUIOnline"
set "LAUNCHER=%INSTALL_ROOT%\launch_web_gui.bat"

if not exist "%LAUNCHER%" (
  echo AI Karaoke Web GUI is not installed yet.
  echo Please run install_online.bat first.
  pause
  exit /b 1
)

call "%LAUNCHER%"
exit /b %ERRORLEVEL%
