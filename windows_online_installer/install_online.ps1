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
    "separator.py",
    "aligner.py",
    "furigana.py",
    "kara_style.py",
    "web_gui.py",
    "web_gui.html"
)

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
            if ((Test-Path $OutputPath) -and ((Get-Item $OutputPath).Length -gt 0)) {
                return
            }
        } catch {
            Write-Host "Download failed from $url"
        }
    }
    throw "Failed to download micromamba."
}

function Write-LauncherBat {
    param(
        [string]$LauncherPath,
        [string]$InstallPath,
        [string]$HostValue,
        [int]$PortValue
    )

    $content = @"
@echo off
setlocal
set "ROOT=$InstallPath"
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
title AI Karaoke Web GUI Server

if not exist "%ROOT%\env\python.exe" (
  echo Runtime environment missing: %ROOT%\env
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
endlocal
"@
    Set-Content -LiteralPath $LauncherPath -Value $content -Encoding ASCII
}

Write-Host "AI Karaoke Web GUI Online Installer"
Write-Host "Install root: $InstallRoot"

$sourceRoot = Resolve-SourceRoot -ScriptDirectory $scriptDir -RequiredFiles $projectFiles

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

foreach ($f in $projectFiles) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $f) -Destination (Join-Path $appRoot $f) -Force
}

if (-not (Test-Path $micromambaExe)) {
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

if (-not (Test-Path $pythonExe)) {
    Write-Host "Creating isolated runtime environment..."
    & $micromambaExe create --yes --root-prefix $mambaRoot --prefix $envRoot -c conda-forge python=3.10 ffmpeg
}

Write-Host "Installing Python dependencies (online)..."
& $pythonExe -m pip install --upgrade pip

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

if ($hasNvidia) {
    Write-Host "Installing CUDA-enabled torch/torchaudio/torchcodec (cu121)..."
    try {
        & $pythonExe -m pip install --upgrade torch torchaudio torchcodec --index-url https://download.pytorch.org/whl/cu121
    } catch {
        Write-Host "CUDA wheel install failed, falling back to default torch packages."
        & $pythonExe -m pip install --upgrade torch torchaudio torchcodec
    }
} else {
    Write-Host "No NVIDIA GPU detected, installing default torch packages."
    & $pythonExe -m pip install --upgrade torch torchaudio torchcodec
}

& $pythonExe -m pip install --upgrade stable-ts demucs librosa numpy scipy soundfile pykakasi

Write-Host "Verifying CUDA availability in installed runtime..."
& $pythonExe -c "import torch; print('torch=', torch.__version__, ' cuda_available=', torch.cuda.is_available())"

Write-LauncherBat -LauncherPath $launcherBat -InstallPath $InstallRoot -HostValue $BindHost -PortValue $Port

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
