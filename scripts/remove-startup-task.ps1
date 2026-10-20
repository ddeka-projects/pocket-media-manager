#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$TaskName = "Pocket Media Manager",
    [switch]$RemoveFirewall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($RemoveFirewall) {
    $CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $CurrentPrincipal = New-Object System.Security.Principal.WindowsPrincipal($CurrentIdentity)
    if (-not $CurrentPrincipal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "-RemoveFirewall needs an elevated PowerShell for the same Windows user. No changes were made."
    }
}

$taskNames = @($TaskName)
if ($TaskName -eq "Pocket Media Manager") { $taskNames += "Pocket Media Manager PC Helper" }
foreach ($name in $taskNames) {
    $Task = Get-ScheduledTask -TaskName $name -TaskPath "\" -ErrorAction SilentlyContinue
    if (-not $Task) { continue }
    Stop-ScheduledTask -TaskName $name -TaskPath "\"
    Unregister-ScheduledTask -TaskName $name -TaskPath "\" -Confirm:$false
    Write-Host "Stopped and removed scheduled task: $name"
}
if ($RemoveFirewall) {
    Get-NetFirewallRule -DisplayName "Pocket Media Manager (Private LAN)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Host "Removed the Pocket Media Manager firewall rule."
}
else {
    Write-Host "Firewall was left as configured. To also remove its rules, run this script elevated with -RemoveFirewall."
}
