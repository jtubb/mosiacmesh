<#
.SYNOPSIS
    Lock down Windows-Update driver replacement so the redsn0w-compatible Apple
    mobile-device drivers don't get silently upgraded between jailbreak sessions.

.DESCRIPTION
    Idempotent. Re-run any time (e.g. after a Windows update). Two layers:

      1. Stop Windows Update from delivering ANY driver updates
         (HKLM\...\WindowsUpdate ExcludeWUDriversInQualityUpdate = 1). Equivalent
         to the GPO "Do not include drivers with Windows Updates" and the
         sysdm.cpl -> Hardware -> Device Installation Settings -> "No" toggle.

      2. Add specific Apple iOS-device USB IDs to the Device Installation
         Restrictions deny-list (DFU, recovery, common iPad/iPhone normal modes)
         so Windows cannot replace those drivers via WU or hot-plug. This is the
         surgical lock that protects the exact device redsn0w talks to.

    Both layers are policy-level (HKLM\SOFTWARE\Policies\...), survive reboots,
    and override most WU behaviour. The deny-list is NOT retroactive: it will
    not uninstall the driver currently bound to the device, only block
    future replacements. Install your known-good Apple Mobile Device USB Driver
    first, THEN run this to freeze it in place.

.PARAMETER Remove
    Revert all changes (restore default WU behaviour, clear the deny-list).

.PARAMETER Status
    Print the current state; make no changes.

.PARAMETER Extra
    Additional hardware IDs to append to the deny-list, e.g.
        -Extra 'USB\VID_05AC&PID_12AA','USB\VID_05AC&PID_12AB'

.EXAMPLE
    # Apply (default). Run from an elevated PowerShell.
    .\prevent_driver_updates.ps1

.EXAMPLE
    # See the current state without changing anything.
    .\prevent_driver_updates.ps1 -Status

.EXAMPLE
    # Undo everything.
    .\prevent_driver_updates.ps1 -Remove

.EXAMPLE
    # Apply, plus pin two extra device IDs (e.g. an iPhone you care about).
    .\prevent_driver_updates.ps1 -Extra 'USB\VID_05AC&PID_12AA'
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$Status,
    [string[]]$Extra
)

$ErrorActionPreference = "Stop"

# --- elevation check -------------------------------------------------------
function Test-IsAdmin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object System.Security.Principal.WindowsPrincipal($id)).IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-IsAdmin)) {
    throw "Run this in an elevated PowerShell (right-click PowerShell -> Run as Administrator)."
}

# --- policy registry locations --------------------------------------------
$WUKey   = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
$DIKey   = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions'
$DenyKey = "$DIKey\DenyDeviceIDs"

# Apple iOS USB IDs to pin. PIDs 1227 (DFU) and 1281 (Recovery) are universal
# across A4/A5 devices and are the ones redsn0w actually talks to -- the critical
# pair. Normal-mode PIDs vary by model; the included set covers iPad-1 / iPad-2 /
# iPhone-4/4S and the common 12A8 line. Add more with -Extra if you also handle
# newer iOS devices on this box.
$DeviceIds = @(
    'USB\VID_05AC&PID_1227',   # Apple Mobile Device - DFU mode (CRITICAL for redsn0w)
    'USB\VID_05AC&PID_1281',   # Apple Mobile Device - Recovery mode
    'USB\VID_05AC&PID_1222',   # iBoot / older DFU
    'USB\VID_05AC&PID_129A',   # iPad (1st gen) - normal mode
    'USB\VID_05AC&PID_129C',   # iPhone 4 / 4S - normal mode
    'USB\VID_05AC&PID_129E',   # iPad 2 - normal mode (common variant)
    'USB\VID_05AC&PID_12A8'    # iPad 3/4 / iPhone 5+ - normal mode
)
if ($Extra) { $DeviceIds += $Extra }
$DeviceIds = $DeviceIds | Select-Object -Unique

# --- helpers ---------------------------------------------------------------
function Ensure-Key([string]$path) {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
}

function Apply-Policy {
    # 1) WU: exclude drivers from quality updates
    Ensure-Key $WUKey
    New-ItemProperty -Path $WUKey -Name 'ExcludeWUDriversInQualityUpdate' `
        -PropertyType DWord -Value 1 -Force | Out-Null
    Write-Host "[OK] Windows Update: drivers excluded from quality updates" -ForegroundColor Green

    # 2) Device Installation Restrictions: enable + populate the deny-list
    Ensure-Key $DIKey
    New-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' `
        -PropertyType DWord -Value 1 -Force | Out-Null
    # Do NOT apply retroactively: leave the currently-bound driver alone, just
    # block future replacements. Flip to 1 if you also want to uninstall any
    # already-installed driver matching the IDs (usually not what you want here).
    New-ItemProperty -Path $DIKey -Name 'DenyDeviceIDsRetroactive' `
        -PropertyType DWord -Value 0 -Force | Out-Null

    Ensure-Key $DenyKey
    # Rewrite the list canonically: clear stale entries, then append our IDs.
    Get-Item $DenyKey | Select-Object -ExpandProperty Property | ForEach-Object {
        Remove-ItemProperty -Path $DenyKey -Name $_ -ErrorAction SilentlyContinue
    }
    $i = 1
    foreach ($id in $DeviceIds) {
        New-ItemProperty -Path $DenyKey -Name "$i" -PropertyType String -Value $id -Force | Out-Null
        $i++
    }
    Write-Host "[OK] Device-install deny-list: $($DeviceIds.Count) Apple iOS device ID(s) pinned" -ForegroundColor Green
    $DeviceIds | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }

    Write-Host ""
    Write-Host "Applied. Notes:" -ForegroundColor Yellow
    Write-Host "  - Install the known-good Apple Mobile Device USB driver BEFORE running this;" -ForegroundColor Yellow
    Write-Host "    the deny-list only blocks future swaps, it does not install the right driver." -ForegroundColor Yellow
    Write-Host "  - Re-run after any Windows update; HKLM policies are the source of truth." -ForegroundColor Yellow
    Write-Host "  - Run with -Status to verify, -Remove to undo." -ForegroundColor Yellow
}

function Remove-Policy {
    if (Test-Path $WUKey) {
        Remove-ItemProperty -Path $WUKey -Name 'ExcludeWUDriversInQualityUpdate' `
            -ErrorAction SilentlyContinue
        Write-Host "[OK] Windows Update: driver-exclusion removed" -ForegroundColor Green
    }
    if (Test-Path $DenyKey) {
        Remove-Item -Path $DenyKey -Recurse -Force
        Write-Host "[OK] Device-install deny-list cleared" -ForegroundColor Green
    }
    if (Test-Path $DIKey) {
        Remove-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -ErrorAction SilentlyContinue
        Remove-ItemProperty -Path $DIKey -Name 'DenyDeviceIDsRetroactive' -ErrorAction SilentlyContinue
    }
    Write-Host "Reverted to defaults (drivers may again be replaced via Windows Update)." -ForegroundColor Yellow
}

function Show-Status {
    $wu = (Get-ItemProperty -Path $WUKey -Name 'ExcludeWUDriversInQualityUpdate' -ErrorAction SilentlyContinue).ExcludeWUDriversInQualityUpdate
    Write-Host "WU drivers excluded:  " -NoNewline
    if ($wu -eq 1) { Write-Host "YES" -ForegroundColor Green }
    else           { Write-Host "no"  -ForegroundColor DarkGray }

    $deny = (Get-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -ErrorAction SilentlyContinue).DenyDeviceIDs
    Write-Host "Device deny-list:     " -NoNewline
    if ($deny -eq 1 -and (Test-Path $DenyKey)) {
        $names = Get-Item $DenyKey | Select-Object -ExpandProperty Property
        Write-Host "ENABLED ($($names.Count) entries)" -ForegroundColor Green
        foreach ($n in $names) {
            $v = (Get-ItemProperty -Path $DenyKey -Name $n).$n
            Write-Host "    $v" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "off" -ForegroundColor DarkGray
    }
}

# --- dispatch --------------------------------------------------------------
if     ($Status) { Show-Status }
elseif ($Remove) { Remove-Policy }
else             { Apply-Policy }
