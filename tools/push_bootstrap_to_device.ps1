<#
.SYNOPSIS
    Push harvested bootstrap .debs (OpenSSH + tweaks) onto a USB-connected
    fresh iPad's /var/mobile/Media/Cydia/AutoInstall/ folder via AFC, then
    reboot the device. Cydia auto-installs the .debs at next boot, giving
    us SSH (and the other tweaks) without any network involvement.

.DESCRIPTION
    Per device, end-to-end:
      1. Discover plugged-in iPad via `idevice_id -l` (libimobiledevice).
      2. Verify it's an iPad on iOS 5.x via `ideviceinfo`.
      3. Push every .deb in $DebDir to /Cydia/AutoInstall/ via AFC. The
         script tries a few CLI variants (ideviceafc, afcclient,
         pymobiledevice3) in order; if NONE is available, it pauses with
         clear instructions for a manual GUI push (iFunBox / 3uTools /
         iMazing) and resumes on Enter.
      4. Trigger `idevicediagnostics restart` (unless -NoRestart).

    After the device reboots, Cydia's AutoInstall mechanism dpkg-installs
    the dropped .debs and OpenSSH starts. From that point, the regular
    onboard_devices.ps1 + sync_from_master.ps1 tooling can take over.

    NOTE on AFC: Cydia AutoInstall expects files at
    /var/mobile/Media/Cydia/AutoInstall/. The AFC service exposes
    /var/mobile/Media as its root, so the AFC path is /Cydia/AutoInstall/.

.PARAMETER DebDir
    Local folder holding the .debs to push (default: .\bootstrap-debs).
    Produced by harvest_bootstrap_debs.ps1.

.PARAMETER UDID
    Specific iPad UDID to target. If omitted and exactly one device is
    plugged in, that device is used; if multiple, the script lists them
    and exits so you can re-run with -UDID.

.PARAMETER ListOnly
    Just list plugged-in iOS devices (UDID + product type + iOS version)
    and exit. Useful for confirming what's connected before pushing.

.PARAMETER NoRestart
    Skip the reboot at the end. Use if you're staging multiple changes
    and want to reboot manually later. AutoInstall only fires at boot,
    so the device won't install the .debs until you do reboot.

.PARAMETER NoAutoPush
    Skip the AFC push attempts entirely and go straight to the manual
    fallback (useful if you already know your CLI tools don't support AFC).

.EXAMPLE
    .\push_bootstrap_to_device.ps1 -ListOnly

.EXAMPLE
    # Standard run -- one iPad plugged in
    .\push_bootstrap_to_device.ps1

.EXAMPLE
    # Multi-device USB hub setup
    .\push_bootstrap_to_device.ps1 -UDID f1d2c3b4a5...
#>
[CmdletBinding()]
param(
    [string]$DebDir = ".\bootstrap-debs",
    [string]$UDID,
    [switch]$ListOnly,
    [switch]$NoRestart,
    [switch]$NoAutoPush
)

$ErrorActionPreference = "Stop"

# --- locate libimobiledevice tools ---------------------------------------
function Find-Tool {
    param([string]$Name, [switch]$Required)
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    if ($Required) {
        throw "Required tool not found on PATH: $Name`n" +
              "Install libimobiledevice for Windows (e.g. from imobiledevice-net releases) and add its bin/ to PATH."
    }
    return $null
}

$idevice_id        = Find-Tool 'idevice_id' -Required
$ideviceinfo       = Find-Tool 'ideviceinfo'                # optional but recommended
$idevicediagnostics = Find-Tool 'idevicediagnostics'         # required unless -NoRestart
if (-not $NoRestart -and -not $idevicediagnostics) {
    throw "idevicediagnostics not on PATH (needed to reboot). Pass -NoRestart to skip the reboot."
}

# AFC push candidates -- different libimobiledevice builds ship different
# tools, so we probe several and use the first one that exists.
$pymd3      = Find-Tool 'pymobiledevice3'
$ideviceafc = Find-Tool 'ideviceafc'
$afcclient  = Find-Tool 'afcclient'

# --- list devices --------------------------------------------------------
function Get-ConnectedDevices {
    $udids = (& $idevice_id -l 2>$null) -split "`r?`n" |
        ForEach-Object { $_.Trim() } | Where-Object { $_ }
    return $udids
}

function Get-DeviceInfo {
    param([string]$Udid, [string]$Key)
    if (-not $ideviceinfo) { return '?' }
    $v = (& $ideviceinfo -u $Udid -k $Key 2>$null) | Out-String
    return $v.Trim()
}

$devices = Get-ConnectedDevices
if (-not $devices) {
    throw "No iOS devices detected via USB. Plug in the iPad, accept any 'Trust' prompt if shown, and try again."
}

if ($ListOnly) {
    Write-Host "Connected iOS devices:" -ForegroundColor Cyan
    foreach ($u in $devices) {
        $pt = Get-DeviceInfo $u 'ProductType'
        $pv = Get-DeviceInfo $u 'ProductVersion'
        $dn = Get-DeviceInfo $u 'DeviceName'
        Write-Host ("  {0}  {1}  iOS {2}  ({3})" -f $u, $pt, $pv, $dn)
    }
    return
}

# --- pick target ---------------------------------------------------------
if (-not $UDID) {
    if ($devices.Count -ne 1) {
        Write-Host "Multiple devices connected -- specify one with -UDID:" -ForegroundColor Yellow
        foreach ($u in $devices) {
            $pt = Get-DeviceInfo $u 'ProductType'
            $pv = Get-DeviceInfo $u 'ProductVersion'
            Write-Host ("  {0}  {1}  iOS {2}" -f $u, $pt, $pv)
        }
        return
    }
    $UDID = $devices[0]
}
if ($devices -notcontains $UDID) {
    throw "UDID '$UDID' is not in the connected device list ($($devices -join ', '))."
}

$pt = Get-DeviceInfo $UDID 'ProductType'
$pv = Get-DeviceInfo $UDID 'ProductVersion'
$dn = Get-DeviceInfo $UDID 'DeviceName'
Write-Host "Target:      $UDID" -ForegroundColor Cyan
Write-Host "  product:   $pt" -ForegroundColor DarkGray
Write-Host "  ios:       $pv" -ForegroundColor DarkGray
Write-Host "  name:      $dn" -ForegroundColor DarkGray
if ($pt -and $pt -notmatch '^iPad') { Write-Warning "ProductType is '$pt' -- expected iPad. Proceeding anyway." }
if ($pv -and $pv -notmatch '^5\.')  { Write-Warning "ProductVersion is '$pv' -- this script targets iOS 5.x. AutoInstall path may differ on other versions." }

# --- validate DebDir ----------------------------------------------------
if (-not [System.IO.Path]::IsPathRooted($DebDir)) { $DebDir = Join-Path (Get-Location) $DebDir }
if (-not (Test-Path $DebDir)) { throw "DebDir not found: $DebDir  (run harvest_bootstrap_debs.ps1 first)" }
$debs = Get-ChildItem -Path $DebDir -Filter '*.deb' -File | Sort-Object Name
if (-not $debs) { throw "No .deb files in $DebDir." }

Write-Host "`nWill push $($debs.Count) .deb(s) to /var/mobile/Media/Cydia/AutoInstall/:" -ForegroundColor Cyan
$debs | ForEach-Object { Write-Host ("  {0,-50} {1,10:N0} bytes" -f $_.Name, $_.Length) -ForegroundColor DarkGray }

# --- AFC push ------------------------------------------------------------
# AFC's root = /var/mobile/Media, so AutoInstall is at /Cydia/AutoInstall.
$afcDir = "/Cydia/AutoInstall"

function Try-AfcPush {
    <#  Returns $true on full success, $false otherwise. Prints each attempt.  #>
    param([string]$Udid, [System.IO.FileInfo[]]$Files)

    if ($pymd3) {
        Write-Host "Trying: pymobiledevice3 afc ..." -ForegroundColor DarkGray
        # pymobiledevice3 doesn't always support iOS 5 cleanly, but worth a try.
        & $pymd3 afc --udid $Udid mkdir $afcDir 2>$null | Out-Null
        $ok = $true
        foreach ($f in $Files) {
            $rc = & $pymd3 afc --udid $Udid push $f.FullName "$afcDir/$($f.Name)" 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  push failed for $($f.Name): $rc" -ForegroundColor DarkYellow
                $ok = $false; break
            }
            Write-Host "  pymd3 pushed  $($f.Name)" -ForegroundColor Green
        }
        if ($ok) { return $true }
    }

    if ($ideviceafc) {
        Write-Host "Trying: ideviceafc ..." -ForegroundColor DarkGray
        & $ideviceafc -u $Udid mkdir $afcDir 2>$null | Out-Null
        $ok = $true
        foreach ($f in $Files) {
            & $ideviceafc -u $Udid put $f.FullName "$afcDir/$($f.Name)" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { $ok = $false; break }
            Write-Host "  ideviceafc pushed  $($f.Name)" -ForegroundColor Green
        }
        if ($ok) { return $true }
    }

    if ($afcclient) {
        Write-Host "Trying: afcclient ..." -ForegroundColor DarkGray
        & $afcclient -u $Udid mkdir $afcDir 2>$null | Out-Null
        $ok = $true
        foreach ($f in $Files) {
            & $afcclient -u $Udid put $f.FullName "$afcDir/$($f.Name)" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { $ok = $false; break }
            Write-Host "  afcclient pushed  $($f.Name)" -ForegroundColor Green
        }
        if ($ok) { return $true }
    }

    return $false
}

$pushed = $false
if (-not $NoAutoPush) {
    if (-not ($pymd3 -or $ideviceafc -or $afcclient)) {
        Write-Host "`nNo AFC-push CLI found on PATH (tried: pymobiledevice3, ideviceafc, afcclient)." -ForegroundColor Yellow
    } else {
        $pushed = Try-AfcPush -Udid $UDID -Files $debs
    }
}

if (-not $pushed) {
    Write-Host "`n----- MANUAL PUSH REQUIRED -----" -ForegroundColor Yellow
    Write-Host "Open iFunBox / 3uTools / iMazing (any AFC-capable GUI tool)." -ForegroundColor Yellow
    Write-Host "Navigate the connected iPad's filesystem and drop ALL .debs from:" -ForegroundColor Yellow
    Write-Host "    $DebDir" -ForegroundColor Yellow
    Write-Host "into:" -ForegroundColor Yellow
    Write-Host "    /var/mobile/Media/Cydia/AutoInstall/" -ForegroundColor Yellow
    Write-Host "(in iFunBox this is usually shown as 'User Folder' -> 'Media' -> 'Cydia' -> 'AutoInstall')." -ForegroundColor Yellow
    Write-Host "Create the Cydia/AutoInstall folder if it doesn't exist yet." -ForegroundColor Yellow
    $ans = Read-Host "`nPress Enter when done (or 'q' to abort)"
    if ($ans -eq 'q') { Write-Host "Aborted -- no reboot." -ForegroundColor DarkGray; return }
}

# --- reboot --------------------------------------------------------------
if ($NoRestart) {
    Write-Host "`n-NoRestart given; skipping reboot. AutoInstall will fire at the next boot." -ForegroundColor Yellow
    return
}

Write-Host "`nRebooting $UDID ..." -ForegroundColor Cyan
$rebootOut = (& $idevicediagnostics -u $UDID restart 2>&1) | Out-String
Write-Host "  $($rebootOut.Trim())" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Device should reboot, Cydia AutoInstall runs the .debs at boot," -ForegroundColor Green
Write-Host "and OpenSSH starts. Allow ~60-90s, then onboard with key + clock:" -ForegroundColor Green
Write-Host "  .\onboard_devices.ps1 -Hosts <ipad-ip> -ReplaceKeys -FixClock" -ForegroundColor DarkGray
