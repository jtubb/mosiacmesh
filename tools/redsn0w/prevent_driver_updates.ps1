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

.PARAMETER AutoDiscover
    Scan currently-plugged Apple (VID_05AC) devices and append THEIR exact
    Hardware IDs to the deny-list (catches device-specific REV / MI_xx variants
    that the generic entries don't list). Plug each state you care about (normal
    / recovery / DFU) before running with this switch.

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
    [string[]]$Extra,
    [switch]$AutoDiscover
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

# Apple iOS USB IDs to pin -- comprehensive coverage. For restore-based flows,
# Windows binds a separate driver to (a) each boot mode the device passes through,
# (b) the composite parent in normal mode, AND (c) each interface (MI_xx) child
# of the composite -- PnP can update any of those independently, so all three
# layers need to be on the deny-list.

# --- Boot-mode PIDs (universal across iOS devices in these modes) ---
$BootPids = @(
    '1222',  # iBoot / older DFU
    '1227',  # DFU mode (CRITICAL for redsn0w + iTunes restore handoff)
    '1280',  # WTF / various legacy boot
    '1281'   # Recovery mode (during iTunes restore)
)

# --- Normal-mode composite parent PIDs across iPad / iPhone / iPod touch ---
# Spans original iPhone through iPhone 5 / iPad-4 generation; the modern Apple
# Mobile Device driver also reports under several of these IDs.
$NormalPids = @(
    '1290','1291','1292','1293','1294','1296','1297','1299',           # iPhone original-4 / iPod 1-3
    '129A',                                                            # iPad (1st gen)
    '129B','129C','129D',                                              # iPod 4 / iPhone 4S
    '129E','129F',                                                     # iPad 2 / iPad 3
    '12A0','12A1','12A2','12A3','12A4','12A5','12A6','12A7','12A8',    # iPhone 5 / iPad 4 family + variants
    '12A9','12AA','12AB','12AC','12AD','12AE','12AF'                   # later iPad / iPod variants
)

# --- Composite-interface child devices (MI_00 .. MI_03) ---
# Windows installs/updates a driver per interface on the composite parent
# (e.g. WUDFWpdMtp for MI_00 camera/MTP, Apple usb-multifunction for others).
$Interfaces = @('MI_00','MI_01','MI_02','MI_03','MI_04','MI_05')

$DeviceIds = New-Object System.Collections.Generic.List[string]
foreach ($p in $BootPids)   { $DeviceIds.Add("USB\VID_05AC&PID_$p") }
foreach ($p in $NormalPids) {
    $DeviceIds.Add("USB\VID_05AC&PID_$p")
    foreach ($mi in $Interfaces) { $DeviceIds.Add("USB\VID_05AC&PID_$p&$mi") }
}

# -AutoDiscover: scan currently-plugged Apple devices and add their EXACT
# Hardware IDs (catches device-specific REV/MI variants the generic list misses).
if ($AutoDiscover) {
    try {
        Get-PnpDevice -PresentOnly -ErrorAction Stop |
            Where-Object { $_.InstanceId -match 'VID_05AC' } |
            ForEach-Object {
                $hwids = (Get-PnpDeviceProperty -InstanceId $_.InstanceId `
                    -KeyName DEVPKEY_Device_HardwareIds -ErrorAction SilentlyContinue).Data
                foreach ($h in $hwids) {
                    if ($h -and $h.StartsWith('USB\VID_05AC')) { $DeviceIds.Add($h) }
                }
            }
    } catch {
        Write-Warning "AutoDiscover failed: $($_.Exception.Message)"
    }
}

if ($Extra) { foreach ($e in $Extra) { $DeviceIds.Add($e) } }
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
