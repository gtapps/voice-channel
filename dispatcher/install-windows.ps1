# Install voice-dispatcher on Windows (acceptance-gated alternate).
# Run in PowerShell as the operator user (no elevation needed).
# Requires Python 3.11+ and internet access.
#
# Usage:  .\install-windows.ps1
#
# Note: Windows laptop support is acceptance-gated for v1.
# If this script fails on your hardware, use a macOS or Linux laptop instead.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir    = "$env:LOCALAPPDATA\voice-dispatcher\venv"
$ConfigDir  = "$env:APPDATA\voice-dispatcher"
$LogDir     = "$env:LOCALAPPDATA\voice-dispatcher\logs"
$TaskXml    = "$ScriptDir\install\voice-dispatcher.xml"
$TmpXml     = "$env:TEMP\voice-dispatcher.xml"

Write-Host "==> voice-dispatcher Windows install"

# Check Python
try {
    $pyver = python --version 2>&1
    Write-Host "--> $pyver"
} catch {
    Write-Error "Python not found. Install Python 3.11+ from https://python.org then re-run."
}

# Create venv
Write-Host "--> creating venv at $VenvDir"
New-Item -ItemType Directory -Force -Path (Split-Path $VenvDir) | Out-Null
python -m venv $VenvDir
& "$VenvDir\Scripts\pip" install --upgrade pip --quiet

Write-Host "--> installing voice-dispatcher"
& "$VenvDir\Scripts\pip" install --quiet $ScriptDir

# Voices directory
$VoicesDir = "$env:LOCALAPPDATA\voice-dispatcher\voices"
New-Item -ItemType Directory -Force -Path $VoicesDir | Out-Null
Write-Host "--> voice directory: $VoicesDir"
Write-Host "    Download .onnx voice files from:"
Write-Host "    https://github.com/rhasspy/piper/releases"

# Config skeleton
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
if (-not (Test-Path "$ConfigDir\config.yaml")) {
    Copy-Item "$ScriptDir\config.yaml.example" "$ConfigDir\config.yaml"
    Write-Host "--> config written to $ConfigDir\config.yaml  (edit before starting)"
}

# Log directory
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Register scheduled task
$PythonExe = "$VenvDir\Scripts\pythonw.exe"
(Get-Content $TaskXml) -replace 'VENV_PYTHON_PLACEHOLDER', $PythonExe | Out-File $TmpXml -Encoding Unicode

Write-Host "--> registering scheduled task"
schtasks /create /tn "voice-dispatcher" /xml $TmpXml /f | Out-Null
Remove-Item $TmpXml

Write-Host ""
Write-Host "✓ voice-dispatcher installed."
Write-Host ""
Write-Host "IMPORTANT — mic permission:"
Write-Host "  Windows may ask for microphone permission on first start."
Write-Host "  Allow it in Settings → Privacy & Security → Microphone."
Write-Host ""
Write-Host "IMPORTANT — Bonjour (mDNS):"
Write-Host "  voice-dispatcher announces itself via mDNS. If hermit containers"
Write-Host "  cannot reach laptop.local, use the laptop's LAN IP in /voice:configure:"
Write-Host "    ipconfig | findstr 'IPv4'"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit $ConfigDir\config.yaml"
Write-Host "  2. voice-dispatcher config add-hermit jarvis --triggers 'hey jarvis,hermit' --voice en_US-lessac-medium.onnx"
Write-Host "  3. Start: schtasks /run /tn 'voice-dispatcher'"
