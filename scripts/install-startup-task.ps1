#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$TaskName = "Pocket Media Manager",
    [switch]$ConfigureFirewall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$RunScript = Join-Path $PSScriptRoot "run-server.ps1"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$firewallRuleName = "Pocket Media Manager (Private LAN)"

$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$CurrentPrincipal = New-Object System.Security.Principal.WindowsPrincipal($CurrentIdentity)
if ($ConfigureFirewall -and -not $CurrentPrincipal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "-ConfigureFirewall needs an elevated PowerShell for the same Windows user. Omit it if the rule is already configured."
}

if (-not (Test-Path $RunScript)) {
    throw "Could not find $RunScript"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Run scripts\bootstrap-windows.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $Root ".env") -PathType Leaf)) {
    throw "Configure .env in the repository root first."
}

Push-Location -LiteralPath $Root
try {
    $configuredPort = & $Python -c "from pocket_media_manager.config import get_settings; s=get_settings(); assert s.media_root.is_dir(), 'MEDIA_ROOT must be an existing directory'; print(s.server_port)"
    if ($LASTEXITCODE -ne 0) { throw "Correct .env and MEDIA_ROOT before installing startup." }
}
finally {
    Pop-Location
}
$configuredPort = [int](($configuredPort | Select-Object -Last 1).Trim())
if ($configuredPort -lt 1 -or $configuredPort -gt 65535) { throw "SERVER_PORT must be between 1 and 65535." }

$CurrentUser = $CurrentIdentity.Name

$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$taskNames = @($TaskName)
if ($TaskName -eq "Pocket Media Manager") { $taskNames += "Pocket Media Manager PC Helper" }
foreach ($name in $taskNames) {
    $existingTask = Get-ScheduledTask -TaskName $name -TaskPath "\" -ErrorAction SilentlyContinue
    if ($null -eq $existingTask) { continue }
    Stop-ScheduledTask -TaskName $name -TaskPath "\"
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $existingTask = Get-ScheduledTask -TaskName $name -TaskPath "\"
        if ($existingTask.State -notin @("Running", "Queued")) { break }
        if ((Get-Date) -ge $deadline) { throw "Timed out stopping '$name'. No replacement server was started." }
        Start-Sleep -Milliseconds 250
    } while ($true)
    if ($name -ne $TaskName) {
        Unregister-ScheduledTask -TaskName $name -TaskPath "\" -Confirm:$false
        Write-Host "Removed legacy scheduled task: $name"
    }
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath "\" `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Runs Pocket Media Manager when $CurrentUser signs in." `
    -Force | Out-Null

if ($ConfigureFirewall) {
    Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $firewallRuleName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $configuredPort -Profile Private -RemoteAddress LocalSubnet | Out-Null
}

Start-ScheduledTask -TaskName $TaskName -TaskPath "\"

Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "The helper will start automatically when $CurrentUser signs in."
Write-Host "The task runs with limited privileges and no runtime limit."
Write-Host "Programs launched by the helper (such as mpv) will appear on your desktop."
if ($ConfigureFirewall) {
    Write-Host "Allowed TCP port $configuredPort only from the local subnet on Private networks."
}
else {
    Write-Host "Firewall was left as configured. To add Private/LocalSubnet rules, run this script elevated with -ConfigureFirewall."
}
