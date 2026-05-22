param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\AIKaraokeWebGUIOnline"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "AI Karaoke Web GUI Uninstaller"
Write-Host "Target install root: $InstallRoot"

$manifestPath = Join-Path $InstallRoot "install_manifest.json"

if (Test-Path $manifestPath) {
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    } catch {
        Write-Host "Warning: failed to parse install manifest, continue with default cleanup."
    }
}

Write-Host "Stopping running web_gui processes..."
try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'"
    foreach ($p in $procs) {
        $cmd = [string]$p.CommandLine
        if ($cmd -and $cmd -match "web_gui\.py" -and $cmd -like "*$InstallRoot*") {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
} catch {
    Write-Host "Warning: process cleanup skipped."
}

if (Test-Path $InstallRoot) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    Write-Host "Removed install root: $InstallRoot"
} else {
    Write-Host "Install root not found, nothing to remove."
}

Write-Host ""
Write-Host "Uninstall completed."
