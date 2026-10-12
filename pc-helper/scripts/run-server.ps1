$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Root ".env"

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$DefaultValue
    )

    if (-not (Test-Path $EnvFile)) {
        return $DefaultValue
    }

    $line = Get-Content $EnvFile |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } |
        Select-Object -Last 1

    if (-not $line) {
        return $DefaultValue
    }

    $value = ($line -split "=", 2)[1].Trim()
    if ($value.Length -ge 2) {
        $first = $value.Substring(0, 1)
        $last = $value.Substring($value.Length - 1, 1)
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }

    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }

    return $value
}

if (-not (Test-Path $Python)) {
    throw "Virtual environment not found. Run ./scripts/bootstrap.sh from Git Bash first."
}

$HostName = Get-DotEnvValue -Name "SERVER_HOST" -DefaultValue "0.0.0.0"
$Port = Get-DotEnvValue -Name "SERVER_PORT" -DefaultValue "8787"

$MediaRootValue = if ($env:MEDIA_ROOT) { $env:MEDIA_ROOT } else {
    Get-DotEnvValue -Name "MEDIA_ROOT" -DefaultValue "E:\Hobby Disk"
}
if (-not [System.IO.Path]::IsPathRooted($MediaRootValue)) {
    $MediaRootValue = Join-Path $Root $MediaRootValue
}
$MediaRootDirectory = [System.IO.Path]::GetFullPath($MediaRootValue)
if (-not (Test-Path -LiteralPath $MediaRootDirectory -PathType Container)) {
    throw "MEDIA_ROOT is not an available directory: $MediaRootDirectory"
}
$MetadataDirectory = Join-Path $MediaRootDirectory ".pocket-media-manager"
$LogDir = Join-Path $MetadataDirectory "logs"
$LogFile = Join-Path $LogDir "server.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Push-Location $Root
try {
    Start-Transcript -Path $LogFile | Out-Null
    Write-Host "Starting Pocket Media Manager PC Helper on ${HostName}:${Port}"
    & $Python -m uvicorn app.main:app --host $HostName --port $Port
}
finally {
    try {
        Stop-Transcript | Out-Null
    }
    catch {
    }
    Pop-Location
}
