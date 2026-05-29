<#
.SYNOPSIS
    Recover Apple iOS USB devices stuck in an Error state after a jailbreak or
    restore, without the manual "Device Manager -> Uninstall + Scan" dance.

.DESCRIPTION
    Scans present Apple (VID_05AC) USB devices, picks the ones whose Status is
    not OK, and rebinds them. Defaults to Disable + Enable (fast, non-destructive
    -- usually clears Code 10/43 left over from a USB-reset during restore).

    With -Reinstall it does the full pnputil remove-device + scan-devices, which
    is the scripted equivalent of "Uninstall device + Scan for hardware changes".

    With -Watch <seconds> it loops, recovering devices as they enter the error
    state -- handy for an unattended fleet jailbreak session so the operator
    doesn't have to babysit Device Manager between each iPad.

.PARAMETER Reinstall
    Use pnputil /remove-device + /scan-devices instead of Disable + Enable.
    Slower but matches Device Manager's Uninstall + Scan exactly. Use when the
    quick rebind doesn't clear the error.

.PARAMETER Watch
    Repeat every N seconds (default 0 = single pass). Ctrl+C to stop.

.EXAMPLE
    # Single pass -- run after each jailbreak finishes.
    .\recover_apple_driver.ps1

.EXAMPLE
    # Background watcher -- leave running during fleet bring-up.
    .\recover_apple_driver.ps1 -Watch 5

.EXAMPLE
    # Full reinstall path (equivalent to Device Manager Uninstall + Scan).
    .\recover_apple_driver.ps1 -Reinstall
#>
[CmdletBinding()]
param(
    [switch]$Reinstall,
    [int]$Watch = 0
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object System.Security.Principal.WindowsPrincipal($id)).IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-IsAdmin)) {
    throw "Run elevated (right-click PowerShell -> Run as Administrator)."
}

function Recover-One {
    param($dev)
    $tag = "$($dev.FriendlyName)  ($($dev.InstanceId))"
    Write-Host "[$($dev.Status)] $tag" -ForegroundColor Yellow
    try {
        if ($Reinstall) {
            # Full Device-Manager-equivalent: forget the device, rescan, let
            # Windows rebind from the local driver store. The deny-list policy
            # is still honored, so it won't pick a newer driver if you have
            # prevent_driver_updates.ps1 applied.
            & pnputil /remove-device "$($dev.InstanceId)" 2>$null | Out-Null
            & pnputil /scan-devices                       2>$null | Out-Null
            Write-Host "  -> pnputil remove + scan-devices" -ForegroundColor Green
        } else {
            # Fast non-destructive rebind: clears most "device cannot start" /
            # "reported problems" errors that come from a USB reset mid-flow.
            Disable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction Stop
            Start-Sleep -Milliseconds 500
            Enable-PnpDevice  -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction Stop
            Write-Host "  -> disabled + enabled (rebind)" -ForegroundColor Green
        }
    } catch {
        Write-Host "  -> recovery FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Scan-And-Recover {
    $bad = Get-PnpDevice -PresentOnly -ErrorAction Stop |
        Where-Object { $_.InstanceId -match 'VID_05AC' -and $_.Status -ne 'OK' }
    if ($bad) {
        foreach ($d in $bad) { Recover-One $d }
    } else {
        Write-Host "$(Get-Date -Format HH:mm:ss) -- no Apple device in error state" -ForegroundColor DarkGray
    }
}

if ($Watch -gt 0) {
    Write-Host "Watching every $Watch s (Ctrl+C to stop)..." -ForegroundColor Cyan
    while ($true) {
        try { Scan-And-Recover } catch { Write-Host "scan error: $($_.Exception.Message)" -ForegroundColor Red }
        Start-Sleep -Seconds $Watch
    }
} else {
    Scan-And-Recover
}
