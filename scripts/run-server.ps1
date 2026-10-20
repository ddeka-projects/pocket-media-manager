#requires -Version 5.1
[CmdletBinding()]
param([switch]$Reload)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Virtual environment not found. Run scripts\bootstrap-windows.ps1 first."
}

Push-Location -LiteralPath $Root
try {
    # Use the app's configuration so the listener, logs, and firewall setup agree,
    # including real environment variables overriding .env.
    $configuration = & $Python -c "import json; from pocket_media_manager.config import get_settings; s=get_settings(); print(json.dumps(dict(host=s.server_host, port=s.server_port, media_root=str(s.media_root))))"
    if ($LASTEXITCODE -ne 0) { throw "Correct .env before starting the server." }
    $settings = ($configuration | Select-Object -Last 1) | ConvertFrom-Json
    $MediaRoot = [string]$settings.media_root
    if (-not (Test-Path -LiteralPath $MediaRoot -PathType Container)) {
        throw "MEDIA_ROOT is not an available directory: $MediaRoot"
    }
    $LogDir = Join-Path $MediaRoot ".pocket-media-manager\logs"
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Start-Transcript -Path (Join-Path $LogDir "server.log") | Out-Null
    Write-Host "Starting Pocket Media Manager on $($settings.host):$($settings.port)"
    $serverArguments = @("-u", "-m", "uvicorn", "pocket_media_manager.main:app", "--host", [string]$settings.host, "--port", [string]$settings.port)
    if ($Reload) { $serverArguments += @("--reload", "--reload-dir", (Join-Path $Root "pocket_media_manager")) }
    # Windows PowerShell 5.1 can classify ordinary native stderr logs as errors.
    $ErrorActionPreference = "Continue"
    & $Python @serverArguments
    $serverExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = "Stop"
    try { Stop-Transcript | Out-Null } catch { }
    Pop-Location
}
exit $serverExitCode
