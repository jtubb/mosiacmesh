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
    quick rebind doesn't clear the error, OR when a fresh iPad shows up with
    NO driver bound (empty Driver / Service columns).

    Automatically lifts the prevent_driver_updates.ps1 deny-list policy around
    the rescan and restores it afterwards (via try/finally, so the restore
    happens even on error or Ctrl+C). Without this lift, the deny-list would
    block the fresh install on devices Windows has never bound a driver to.

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

# Path the prevent_driver_updates.ps1 sister-script writes to. The master switch
# DenyDeviceIDs (DWORD 0/1) gates the whole policy without losing the entries,
# so we just flip it for the duration of a rebind.
$DIKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions'

function With-PolicyLifted {
    <#  Temporarily set DenyDeviceIDs = 0, run $Block, restore the original
        value in a finally so we never strand the policy off. Returns whatever
        $Block returns. No-op if the policy was already off.  #>
    param([Parameter(Mandatory)][scriptblock]$Block)

    $original = $null
    $wasEnabled = $false
    try {
        $cur = Get-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -ErrorAction SilentlyContinue
        if ($cur -and $cur.DenyDeviceIDs -eq 1) {
            $wasEnabled = $true
            $original   = 1
            Set-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -Value 0 -Type DWord
            Write-Host "  ~ deny-list policy lifted for rebind" -ForegroundColor DarkYellow
        }
        & $Block
    } finally {
        if ($wasEnabled) {
            Set-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -Value $original -Type DWord
            Write-Host "  ~ deny-list policy restored" -ForegroundColor DarkYellow
        }
    }
}

function Wait-ForBinding {
    <#  Poll the device for up to $TimeoutSeconds, returning the bound Service
        name once Status flips to OK and Service is non-empty. Returns $null
        on timeout.  #>
    param(
        [Parameter(Mandatory)][string]$InstanceId,
        [int]$TimeoutSeconds = 10
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $dev = Get-PnpDevice -InstanceId $InstanceId -ErrorAction SilentlyContinue
        if ($dev -and $dev.Status -eq 'OK') {
            $svc = (Get-PnpDeviceProperty -InstanceId $InstanceId `
                -KeyName DEVPKEY_Device_Service -ErrorAction SilentlyContinue).Data
            if ($svc) { return $svc }
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Recover-One {
    param($dev)
    $tag = "$($dev.FriendlyName)  ($($dev.InstanceId))"
    Write-Host "[$($dev.Status)] $tag" -ForegroundColor Yellow
    try {
        if ($Reinstall) {
            # Full Device-Manager-equivalent: forget the device, rescan, let
            # Windows rebind from the local driver store. The deny-list policy
            # is temporarily lifted around the rescan so a fresh iPad (whose
            # binding got destroyed by remove-device) can actually be re-bound;
            # the lift is wrapped in try/finally so the policy is restored
            # even if PnP errors out or the user hits Ctrl+C.
            # With-PolicyLifted forwards the scriptblock's output; Wait-ForBinding
            # is the last expression and becomes the returned Service name (or $null).
            $svc = With-PolicyLifted {
                & pnputil /remove-device "$($dev.InstanceId)" 2>$null | Out-Null
                & pnputil /scan-devices                       2>$null | Out-Null
                Wait-ForBinding -InstanceId $dev.InstanceId -TimeoutSeconds 10
            }
            if ($svc) {
                Write-Host "  -> pnputil remove + scan-devices  (bound: $svc)" -ForegroundColor Green
            } else {
                Write-Host "  -> pnputil remove + scan-devices  (WARN: no driver bound after 10s)" -ForegroundColor Red
                Write-Host "     check 'pnputil /enum-drivers | findstr /i usbaapl' -- driver may not be in the store" -ForegroundColor DarkGray
            }
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
