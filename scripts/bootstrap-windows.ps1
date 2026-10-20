#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    try {
        # pip may write ordinary warnings to stderr in Windows PowerShell 5.1.
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $FilePath" }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        Invoke-Checked $launcher.Source @("-3", "-m", "venv", (Join-Path $Root ".venv"))
    }
    else {
        $launcher = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if ($null -eq $launcher) { throw "Install Python 3.11 or newer (python.org), then run this script again." }
        Invoke-Checked $launcher.Source @("-m", "venv", (Join-Path $Root ".venv"))
    }
}
Invoke-Checked $Python @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11 or newer is required.')")
Invoke-Checked $Python @("-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $Root "requirements.txt"))
$environmentFile = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $environmentFile)) {
    Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination $environmentFile
    Write-Host "Created .env. Set MEDIA_ROOT and PUBLIC_BASE_URL before starting." -ForegroundColor Yellow
}
Write-Host "Ready. Configuration: $environmentFile" -ForegroundColor Green
