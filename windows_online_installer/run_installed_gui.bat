@echo off
setlocal

set "INSTALL_ROOT=%LOCALAPPDATA%\AIKaraokeWebGUIOnline"
set "PYTHON_EXE=%INSTALL_ROOT%\env\python.exe"
set "APP_PY=%INSTALL_ROOT%\app\web_gui.py"
set "HOST=127.0.0.1"
set "PORT=7860"
set "APP_CACHE=%INSTALL_ROOT%\cache"
if not exist "%APP_CACHE%" mkdir "%APP_CACHE%"
if not exist "%APP_CACHE%\torch" mkdir "%APP_CACHE%\torch"
if not exist "%APP_CACHE%\huggingface" mkdir "%APP_CACHE%\huggingface"
if not exist "%APP_CACHE%\numba" mkdir "%APP_CACHE%\numba"
set "XDG_CACHE_HOME=%APP_CACHE%"
set "TORCH_HOME=%APP_CACHE%\torch"
set "HF_HOME=%APP_CACHE%\huggingface"
set "HF_HUB_CACHE=%APP_CACHE%\huggingface\hub"
set "TRANSFORMERS_CACHE=%APP_CACHE%\huggingface\transformers"
set "NUMBA_CACHE_DIR=%APP_CACHE%\numba"
title AI Karaoke Web GUI Server

if not exist "%PYTHON_EXE%" (
  echo AI Karaoke Web GUI is not installed yet.
  echo Please run install_online.bat first.
  pause
  exit /b 1
)

if not exist "%APP_PY%" (
  echo Missing app file: %APP_PY%
  echo Please reinstall with install_online.bat.
  pause
  exit /b 1
)

echo Starting AI Karaoke Web GUI on http://%HOST%:%PORT%
echo Checking torch CUDA status...
"%PYTHON_EXE%" -c "import torch; print('torch=', torch.__version__, ' cuda_available=', torch.cuda.is_available())"
start "" powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='http://%HOST%:%PORT%/healthz'; for($i=0;$i -lt 60;$i++){ try{ Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 1 | Out-Null; Start-Process 'http://%HOST%:%PORT%'; break } catch { Start-Sleep -Milliseconds 500 } }"
"%PYTHON_EXE%" "%APP_PY%" --host %HOST% --port %PORT%
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
  echo Web GUI server exited with error code %CODE%.
) else (
  echo Web GUI server exited.
)
pause
exit /b %CODE%
