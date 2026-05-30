<#
.SYNOPSIS
    Pin the legacy 2012-era USBAAPL64 Apple Mobile Device USB Driver as the
    only Apple driver in the local driver store, so iPads auto-bind to the
    "USBAAPL64-at-parent" single-device model required by restore-based
    jailbreak workflows.

.DESCRIPTION
    Modern Apple Mobile Device USB Driver packages (v500+, dated 2023 or
    later) install a composite-device INF: the iPad's USB parent gets bound
    to Microsoft's generic usbccgp, and only the MI_00 (camera/MTP) child
    gets a real driver (WUDFWpdMtp). That breaks restore-based jailbreak
    flows that need USBAAPL64 directly at the parent.

    Windows' driver-ranking system always prefers the newest INF when more
    than one matches, and there is no pnputil flag to override that.  The
    reliable fix is to remove the newer Apple INFs from the store entirely.
    With only the legacy USBAAPL64 INF (12/11/2012, v6.0.9999.65) left,
    /scan-devices binds it -- and the deny-list pins it.

    Removed packages are backed up to disk first so they can be re-added
    later with a single pnputil /add-driver call (handy if something else
    on this box ever needs the modern driver).

    Workflow per box (one time):
        1. .\pin_legacy_apple_driver.ps1 -Status        (see what's there)
        2. .\pin_legacy_apple_driver.ps1                (back up + remove + rebind)
        3. .\prevent_driver_updates.ps1 -AutoDiscover   (pin the legacy bind)

.PARAMETER BackupTo
    Directory to back up removed driver packages to.
    Default: <script-folder>\apple-driver-backup

.PARAMETER Force
    Skip the "proceed?" confirmation prompt.

.PARAMETER Status
    List Apple Mobile Device USB Driver packages in the store and exit.
    No changes.

.EXAMPLE
    .\pin_legacy_apple_driver.ps1 -Status
    # Just inspect the driver store.

.EXAMPLE
    .\pin_legacy_apple_driver.ps1
    # Back up + delete newer Apple INFs + lift policy + /scan-devices + report.

.EXAMPLE
    # Restore a backed-up modern driver later:
    pnputil /add-driver "<BackupTo>\<oem-folder>\usbaapl64.inf" /install
#>
[CmdletBinding()]
param(
    [string]$BackupTo = (Join-Path $PSScriptRoot 'apple-driver-backup'),
    [switch]$Force,
    [switch]$Status
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

$DIKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions'
$LegacyVersion = '6.0.9999.65'   # the redsn0w-era USBAAPL64

# ---------------------------------------------------------------------------

function Get-AppleDrivers {
    # Get-WindowsDriver returns structured records from DISM (cleaner than
    # parsing pnputil text). Inbox=True entries are Windows-bundled and
    # cannot/should not be deleted.
    Get-WindowsDriver -Online -ErrorAction Stop |
        Where-Object {
            ($_.ProviderName -eq 'Apple, Inc.' -or $_.OriginalFileName -match 'usbaapl(64)?\.inf') -and
            -not $_.Inbox
        } |
        Sort-Object Date
}

function Show-AppleDrivers {
    param($drivers)
    if (-not $drivers) {
        Write-Host "No removable Apple Mobile Device USB Driver packages in the store." -ForegroundColor Yellow
        Write-Host "Install iTunes (or just Apple Mobile Device Support) to populate it." -ForegroundColor DarkGray
        return
    }
    Write-Host "Apple drivers in local store (oldest -> newest):" -ForegroundColor Cyan
    $legacy = $drivers | Where-Object { $_.Version -eq $LegacyVersion } | Select-Object -First 1
    foreach ($d in $drivers) {
        $tag = if ($d -eq $legacy) { ' [LEGACY -- KEEP]' }
               elseif (-not $legacy -and $d -eq $drivers[0]) { ' [OLDEST -- KEEP]' }
               else { ' [WOULD REMOVE]' }
        Write-Host ("  {0,-12} {1}  v{2,-14} ({3}){4}" -f `
            $d.Driver, $d.Date.ToString('yyyy-MM-dd'), $d.Version, $d.OriginalFileName, $tag)
    }
}

function Backup-DriverPackage {
    param([string]$OemInf, [string]$DestRoot)
    # Find the package folder under DriverStore\FileRepository by locating
    # the folder that contains the published-name INF.
    $repo = Join-Path $env:SystemRoot 'System32\DriverStore\FileRepository'
    $folder = Get-ChildItem -Path $repo -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName $OemInf) } |
        Select-Object -First 1
    if (-not $folder) {
        Write-Host "  (no FileRepository folder found for $OemInf -- backup skipped)" -ForegroundColor DarkYellow
        return $null
    }
    if (-not (Test-Path $DestRoot)) { New-Item -ItemType Directory -Path $DestRoot -Force | Out-Null }
    $dest = Join-Path $DestRoot $folder.Name
    if (Test-Path $dest) { Remove-Item -Path $dest -Recurse -Force }
    Copy-Item -Path $folder.FullName -Destination $dest -Recurse -Force
    Write-Host "  backed up: $dest" -ForegroundColor DarkGray
    return $dest
}

function Remove-DriverPackage {
    param([string]$OemInf)
    # /force = also uninstall from any device currently using it. The device
    # will be left without a driver until /scan-devices runs below.
    $result = & pnputil /delete-driver $OemInf /force 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARN: pnputil /delete-driver $OemInf /force exited $LASTEXITCODE" -ForegroundColor Red
        $result | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
        return $false
    }
    return $true
}

function With-PolicyLifted {
    # Same lift/restore pattern as recover_apple_driver.ps1 -- toggle the
    # master DenyDeviceIDs DWORD and always restore via finally.
    param([Parameter(Mandatory)][scriptblock]$Block)
    $wasEnabled = $false
    try {
        $cur = Get-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -ErrorAction SilentlyContinue
        if ($cur -and $cur.DenyDeviceIDs -eq 1) {
            $wasEnabled = $true
            Set-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -Value 0 -Type DWord
            Write-Host "  ~ deny-list policy lifted" -ForegroundColor DarkYellow
        }
        & $Block
    } finally {
        if ($wasEnabled) {
            Set-ItemProperty -Path $DIKey -Name 'DenyDeviceIDs' -Value 1 -Type DWord
            Write-Host "  ~ deny-list policy restored" -ForegroundColor DarkYellow
        }
    }
}

# --- dispatch --------------------------------------------------------------
$drivers = Get-AppleDrivers
Show-AppleDrivers $drivers
if ($Status) { return }
if (-not $drivers -or $drivers.Count -lt 2) {
    Write-Host "`nNothing to do (zero or one removable Apple driver in store)." -ForegroundColor Green
    return
}

# Decide which to keep: prefer the explicit 6.0.9999.65 (the known-good
# redsn0w-era driver). Fall back to the oldest if that exact version isn't
# present -- but warn, because we may not actually be on the right rev.
$keep = $drivers | Where-Object { $_.Version -eq $LegacyVersion } | Select-Object -First 1
if (-not $keep) {
    $keep = $drivers[0]
    Write-Host "`nWARN: didn't find USBAAPL64 v$LegacyVersion in the store; falling back to oldest:" -ForegroundColor Yellow
    Write-Host "      $($keep.Driver)  $($keep.Date.ToString('yyyy-MM-dd'))  v$($keep.Version)" -ForegroundColor Yellow
}
$remove = $drivers | Where-Object { $_.Driver -ne $keep.Driver }

Write-Host ""
Write-Host "Keep:    $($keep.Driver)   ($($keep.Date.ToString('yyyy-MM-dd'))  v$($keep.Version))" -ForegroundColor Green
Write-Host "Remove:  $($remove.Count) package(s) -- backed up first to:" -ForegroundColor Yellow
Write-Host "         $BackupTo" -ForegroundColor Yellow

if (-not $Force) {
    $ans = Read-Host "`nProceed? (y/N)"
    if ($ans -notin 'y','Y') {
        Write-Host "Aborted." -ForegroundColor DarkGray
        return
    }
}

foreach ($d in $remove) {
    Write-Host "`n[$($d.Driver)]  $($d.Date.ToString('yyyy-MM-dd'))  v$($d.Version)" -ForegroundColor Cyan
    Backup-DriverPackage -OemInf $d.Driver -DestRoot $BackupTo | Out-Null
    if (Remove-DriverPackage -OemInf $d.Driver) {
        Write-Host "  -> removed from store" -ForegroundColor Green
    }
}

Write-Host "`nRebinding present Apple devices..." -ForegroundColor Cyan
With-PolicyLifted {
    & pnputil /scan-devices 2>$null | Out-Null
    Start-Sleep -Seconds 3
}

Write-Host ""
Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -match 'VID_05AC&PID_129[A-F]' } |
    ForEach-Object {
        $svc = (Get-PnpDeviceProperty -InstanceId $_.InstanceId `
            -KeyName DEVPKEY_Device_Service -ErrorAction SilentlyContinue).Data
        $color = if ($svc -eq 'USBAAPL64') { 'Green' }
                 elseif ($svc) { 'Yellow' }
                 else { 'Red' }
        Write-Host ("  [{0}] {1,-44} Service={2}" -f $_.Status, $_.FriendlyName, $svc) -ForegroundColor $color
    }

Write-Host "`nNext:" -ForegroundColor DarkGray
Write-Host "  .\prevent_driver_updates.ps1 -AutoDiscover" -ForegroundColor DarkGray
Write-Host "    (re-pin the deny-list around the now-bound legacy driver)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "To restore the removed modern driver later:" -ForegroundColor DarkGray
Write-Host "  pnputil /add-driver `"$BackupTo\<folder>\usbaapl64.inf`" /install" -ForegroundColor DarkGray
