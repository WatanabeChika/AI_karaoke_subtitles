param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\AIKaraokeWebGUIOnline",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 7860
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectFiles = @(
    "main.py",
    "web_gui.py",
    "requirements.txt"
)
$projectDirectories = @("ai_karaoke")
$legacyFiles = @(
    "pipeline.py",
    "cli_output.py",
    "separator.py",
    "aligner.py",
    "ctc_alignment.py",
    "lyrics.py",
    "furigana.py",
    "kara_style.py"
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Resolve-SourceRoot {
    param([string]$ScriptDirectory, [string[]]$RequiredFiles)

    $candidates = @(
        $ScriptDirectory,
        (Resolve-Path (Join-Path $ScriptDirectory "..")).Path
    )

    foreach ($candidate in $candidates) {
        $ok = $true
        foreach ($f in $RequiredFiles) {
            if (-not (Test-Path (Join-Path $candidate $f))) {
                $ok = $false
                break
            }
        }
        if ($ok) {
            return $candidate
        }
    }
    throw "Cannot locate source files. Put installer under project root or project-root\windows_online_installer."
}

function Download-Micromamba {
    param([string]$OutputPath)

    $urls = @(
        "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64.exe",
        "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64"
    )

    foreach ($url in $urls) {
        try {
            Write-Host "Downloading micromamba from: $url"
            Invoke-WebRequest -Uri $url -OutFile $OutputPath -UseBasicParsing
            if ((Test-Path $OutputPath) -and ((Get-Item $OutputPath).Length -gt 1MB)) {
                return
            }
        } catch {
            Write-Host "Download failed from $url"
        }
    }
    throw "Failed to download micromamba."
}

function Assert-NativeSuccess {
    param([string]$Action)

    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE."
    }
}

function Install-PytorchRuntime {
    param(
        [string]$PythonExe,
        [bool]$HasNvidia
    )

    if ($HasNvidia) {
        foreach ($cudaTag in @("cu129", "cu128", "cu126")) {
            $indexUrl = "https://download.pytorch.org/whl/$cudaTag"
            Write-Host "Trying CUDA-enabled torch runtime: $cudaTag"
            & $PythonExe -m pip install --upgrade torch torchaudio torchcodec --index-url $indexUrl
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Installed CUDA-enabled torch runtime: $cudaTag"
                return
            }
            Write-Host "No compatible $cudaTag wheel was installed; trying the next supported index."
        }

        Write-Host "CUDA-specific wheels were unavailable; trying the default PyPI runtime."
        & $PythonExe -m pip install --upgrade torch torchaudio torchcodec
        Assert-NativeSuccess "PyTorch runtime installation"
        return
    }

    Write-Host "No NVIDIA GPU detected; installing the CPU torch runtime."
    & $PythonExe -m pip install --upgrade torch torchaudio torchcodec --index-url https://download.pytorch.org/whl/cpu
    Assert-NativeSuccess "CPU PyTorch runtime installation"
}

function Write-LauncherBat {
    param(
        [string]$LauncherPath,
        [string]$HostValue,
        [int]$PortValue
    )

    $content = @"
@echo off
setlocal
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "HOST=$HostValue"
set "PORT=$PortValue"
set "APP_CACHE=%ROOT%\cache"
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
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "PATH=%ROOT%\env;%ROOT%\env\Scripts;%ROOT%\env\Library\bin;%PATH%"
title AI Karaoke Web GUI Server

if not exist "%ROOT%\env\python.exe" (
  echo Runtime environment missing: %ROOT%\env
  pause
  exit /b 1
)
if not exist "%ROOT%\app\web_gui.py" (
  echo Application entry point missing: %ROOT%\app\web_gui.py
  pause
  exit /b 1
)

echo Starting AI Karaoke Web GUI on http://%HOST%:%PORT%
echo Checking torch CUDA status...
"%ROOT%\env\python.exe" -c "import torch; print('torch=', torch.__version__, ' cuda_available=', torch.cuda.is_available())"
start "" powershell -NoProfile -ExecutionPolicy Bypass -Command "`$u='http://%HOST%:%PORT%/healthz'; for(`$i=0;`$i -lt 60;`$i++){ try{ Invoke-WebRequest -UseBasicParsing -Uri `$u -TimeoutSec 1 | Out-Null; Start-Process 'http://%HOST%:%PORT%'; break } catch { Start-Sleep -Milliseconds 500 } }"
"%ROOT%\env\python.exe" "%ROOT%\app\web_gui.py" --host %HOST% --port %PORT%
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
  echo Web GUI server exited with error code %CODE%.
) else (
  echo Web GUI server exited.
)
pause
endlocal & exit /b %CODE%
"@
    Set-Content -LiteralPath $LauncherPath -Value $content -Encoding ASCII
}

Write-Host "AI Karaoke Web GUI Online Installer"
Write-Host "Install root: $InstallRoot"

$sourceRoot = Resolve-SourceRoot -ScriptDirectory $scriptDir -RequiredFiles ($projectFiles + $projectDirectories)

$appRoot = Join-Path $InstallRoot "app"
$binRoot = Join-Path $InstallRoot "bin"
$mambaRoot = Join-Path $InstallRoot "mamba_root"
$envRoot = Join-Path $InstallRoot "env"
$logsRoot = Join-Path $InstallRoot "logs"
$pipCacheRoot = Join-Path $InstallRoot "pip_cache"
$runtimeCacheRoot = Join-Path $InstallRoot "cache"
$manifestPath = Join-Path $InstallRoot "install_manifest.json"
$launcherBat = Join-Path $InstallRoot "launch_web_gui.bat"
$micromambaExe = Join-Path $binRoot "micromamba.exe"
$pythonExe = Join-Path $envRoot "python.exe"

New-Item -ItemType Directory -Force -Path $InstallRoot, $appRoot, $binRoot, $logsRoot, $pipCacheRoot, $runtimeCacheRoot | Out-Null

if (Test-Path (Join-Path $appRoot "ai_karaoke")) {
    Remove-Item -LiteralPath (Join-Path $appRoot "ai_karaoke") -Recurse -Force
}
if (Test-Path (Join-Path $appRoot "web_gui")) {
    Remove-Item -LiteralPath (Join-Path $appRoot "web_gui") -Recurse -Force
}
foreach ($legacyFile in $legacyFiles) {
    $legacyPath = Join-Path $appRoot $legacyFile
    if (Test-Path $legacyPath) {
        Remove-Item -LiteralPath $legacyPath -Force
    }
}
foreach ($f in $projectFiles) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $f) -Destination (Join-Path $appRoot $f) -Force
}
foreach ($directory in $projectDirectories) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $directory) -Destination $appRoot -Recurse -Force
}

if ((-not (Test-Path $micromambaExe)) -or ((Get-Item $micromambaExe).Length -le 1MB)) {
    Download-Micromamba -OutputPath $micromambaExe
}

$env:MAMBA_ROOT_PREFIX = $mambaRoot
$env:PIP_CACHE_DIR = $pipCacheRoot
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:XDG_CACHE_HOME = $runtimeCacheRoot
$env:TORCH_HOME = (Join-Path $runtimeCacheRoot "torch")
$env:HF_HOME = (Join-Path $runtimeCacheRoot "huggingface")
$env:HF_HUB_CACHE = (Join-Path $runtimeCacheRoot "huggingface\hub")
$env:TRANSFORMERS_CACHE = (Join-Path $runtimeCacheRoot "huggingface\transformers")
$env:NUMBA_CACHE_DIR = (Join-Path $runtimeCacheRoot "numba")
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:PYTHONUTF8 = "1"
$env:PIP_NO_INPUT = "1"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Creating isolated runtime environment..."
    & $micromambaExe create --yes --root-prefix $mambaRoot --prefix $envRoot -c conda-forge python=3.10 ffmpeg git
    Assert-NativeSuccess "Runtime environment creation"
} else {
    Write-Host "Refreshing FFmpeg and Git in the existing runtime..."
    & $micromambaExe install --yes --root-prefix $mambaRoot --prefix $envRoot -c conda-forge ffmpeg git
    Assert-NativeSuccess "Runtime dependency refresh"
}

$env:PATH = "$envRoot;$envRoot\Scripts;$envRoot\Library\bin;$env:PATH"

Write-Host "Installing Python dependencies (online)..."
& $pythonExe -m pip install --upgrade pip
Assert-NativeSuccess "pip upgrade"

Write-Host "Removing packages from retired alignment and separation backends..."
& $pythonExe -m pip uninstall --yes qwen-asr qwen-fa-ultra demucs
if ($LASTEXITCODE -ne 0) {
    Write-Host "Obsolete package cleanup reported a warning; continuing with the fixed pipeline."
}
$huggingFaceHubCache = Join-Path $runtimeCacheRoot "huggingface\hub"
if (Test-Path $huggingFaceHubCache) {
    Get-ChildItem -LiteralPath $huggingFaceHubCache -Directory -Filter "models--Qwen--*" |
        Remove-Item -Recurse -Force
}

$hasNvidia = $false
try {
    $nvidiaCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidiaCmd) {
        $gpuInfo = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null) -join ""
        if ($gpuInfo -and $gpuInfo.Trim().Length -gt 0) {
            $hasNvidia = $true
            Write-Host "Detected NVIDIA GPU: $gpuInfo"
        }
    }
} catch {
    $hasNvidia = $false
}

Install-PytorchRuntime -PythonExe $pythonExe -HasNvidia $hasNvidia

Write-Host "Installing AI Karaoke runtime requirements..."
& $pythonExe -m pip install --upgrade -r (Join-Path $appRoot "requirements.txt")
Assert-NativeSuccess "AI Karaoke dependency installation"

Write-Host "Verifying the installed application..."
& $pythonExe -m compileall -q $appRoot
Assert-NativeSuccess "Application bytecode check"
Push-Location $appRoot
try {
    & $pythonExe -c "import ai_karaoke, torch, torchaudio, torchcodec, stable_whisper, ctc_forced_aligner, librosa, soundfile, kanjiconv, pykakasi, transformers, mel_band_roformer; from ai_karaoke.web.handler import KaraokeWebHandler; print('torch=', torch.__version__, 'cuda_available=', torch.cuda.is_available(), 'imports=ok')"
    Assert-NativeSuccess "Application import check"
} finally {
    Pop-Location
}

Write-LauncherBat -LauncherPath $launcherBat -HostValue $BindHost -PortValue $Port

$manifest = [ordered]@{
    install_root = $InstallRoot
    app_root = $appRoot
    env_root = $envRoot
    mamba_root = $mambaRoot
    pip_cache_root = $pipCacheRoot
    runtime_cache_root = $runtimeCacheRoot
    launcher_bat = $launcherBat
    desktop_shortcuts = @()
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host ""
Write-Host "Install completed."
Write-Host "Run: $InstallRoot\launch_web_gui.bat"
