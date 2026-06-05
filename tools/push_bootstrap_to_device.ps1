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
# Probe PATH first, then fall back to the bundled suite directory
# (tools/libimobile-suite-latest_w64) alongside this script. That lets the
# user drop the suite zip into tools/ and skip PATH munging entirely.
$BundledBin = Join-Path $PSScriptRoot 'libimobile-suite-latest_w64'

function Find-Tool {
    param([string]$Name, [switch]$Required)
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($ext in @('.exe', '')) {
        $bundled = Join-Path $BundledBin ("$Name$ext")
        if (Test-Path $bundled) { return $bundled }
    }
    if ($Required) {
        throw "Required tool not found on PATH or in $BundledBin : $Name"
    }
    return $null
}

$idevice_id        = Find-Tool 'idevice_id' -Required
$ideviceinfo       = Find-Tool 'ideviceinfo'                # optional but recommended
$idevicediagnostics = Find-Tool 'idevicediagnostics'         # required unless -NoRestart
if (-not $NoRestart -and -not $idevicediagnostics) {
    throw "idevicediagnostics not found (needed to reboot). Pass -NoRestart to skip the reboot."
}

# AFC push: afcclient accepts positional one-shot commands
# (afcclient -u UDID <command> [args]). --help doesn't document this, but
# `afcclient -u UDID help` reveals the command set.
$afcclient  = Find-Tool 'afcclient'
$idevicepair = Find-Tool 'idevicepair'    # needed for pair validation
$pymd3      = Find-Tool 'pymobiledevice3' # fallback if afcclient unavailable

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

# @(...) forces an array even when there's one element. Without it, PowerShell
# auto-unwraps a single-element pipeline into a bare string, so $devices[0]
# would be the first CHARACTER of the UDID instead of the UDID itself.
$devices = @(Get-ConnectedDevices)
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

# --- pair validation -----------------------------------------------------
# AMDS auto-pairs iOS devices on first plug, and modern libimobiledevice can
# READ those AMDS-created pair records but generates INCOMPATIBLE ones if you
# call `idevicepair pair`. So: NEVER auto-pair from this script. Just validate;
# if validation fails, that means either (a) AMDS hasn't paired yet (e.g.
# Setup Assistant blocking) or (b) a previous `idevicepair pair` left a bad
# Windows pair record. The recovery recipe is documented for the user.
if (-not $idevicepair) {
    Write-Warning "idevicepair not found -- skipping pair validation. AFC push may fail silently."
} else {
    $vOut = (& $idevicepair -u $UDID validate 2>&1) | Out-String
    if ($vOut -notmatch 'SUCCESS') {
        Write-Host "`nPair NOT valid:" -ForegroundColor Red
        Write-Host "  $($vOut.Trim())" -ForegroundColor DarkRed
        Write-Host "`nRecovery recipe (one-time per device, when this happens):" -ForegroundColor Yellow
        Write-Host "  1. Check the iPad screen -- if it shows Setup Assistant / 'Connect to iTunes'," -ForegroundColor Yellow
        Write-Host "     tap through it (or activate via iTunes) until you reach the home screen." -ForegroundColor Yellow
        Write-Host "  2. Remove the stale Windows pair record:" -ForegroundColor Yellow
        Write-Host "     Remove-Item `"C:\ProgramData\Apple\Lockdown\$UDID.plist`" -Force" -ForegroundColor Yellow
        Write-Host "  3. Unplug + replug the iPad -- AMDS will auto-pair with a fresh, compatible record." -ForegroundColor Yellow
        Write-Host "  4. Re-run this script. DO NOT run 'idevicepair pair' manually -- it writes" -ForegroundColor Yellow
        Write-Host "     iOS-5-incompatible pair records that break this same flow you're trying to recover." -ForegroundColor Yellow
        throw "Pair invalid; cannot push via AFC until pair is valid."
    }
    Write-Host "  pair:      validated" -ForegroundColor DarkGray
}

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
    <#  Returns $true on full success, $false otherwise.

        afcclient supports a one-shot positional invocation
        (afcclient -u UDID <cmd> [args]). --help only lists the connection
        OPTIONS, but `afcclient -u UDID help` reveals the command set --
        mkdir, put, ls, rm, info, stat, etc. We use that mode: one
        process per operation, silent success, error text on stderr.
        We verify with an `ls` afterwards: any .deb missing from the
        listing means the put silently failed.
        #>
    param([string]$Udid, [System.IO.FileInfo[]]$Files)

    if ($afcclient) {
        Write-Host "Pushing via afcclient (positional one-shot)..." -ForegroundColor DarkGray

        # Ensure /Cydia/AutoInstall exists. Errors here are non-fatal
        # ("file already exists" is the expected case after the first push).
        & $afcclient -u $Udid mkdir /Cydia          2>&1 | Out-Null
        & $afcclient -u $Udid mkdir /Cydia/AutoInstall 2>&1 | Out-Null

        $failed = @()
        foreach ($f in $Files) {
            $remote = "/Cydia/AutoInstall/$($f.Name)"
            $putOut = (& $afcclient -u $Udid put $f.FullName $remote 2>&1) | Out-String
            if ($LASTEXITCODE -ne 0 -or $putOut -match 'Error|Failed|ERROR') {
                Write-Host "  FAILED  $($f.Name)  -- $($putOut.Trim())" -ForegroundColor Red
                $failed += $f.Name
            } else {
                Write-Host "  pushed  $($f.Name)" -ForegroundColor Green
            }
        }

        # Verify with ls. `ls -l` doesn't work (the -l is parsed as a global
        # option, not as an arg to the ls command), so use plain ls and
        # check names by substring.
        $lsOut = (& $afcclient -u $Udid ls /Cydia/AutoInstall 2>&1) | Out-String
        $missing = @()
        foreach ($f in $Files) {
            if ($lsOut -notmatch [regex]::Escape($f.Name)) { $missing += $f.Name }
        }
        if ($failed.Count -eq 0 -and $missing.Count -eq 0) {
            Write-Host "  all $($Files.Count) .deb(s) confirmed present in /Cydia/AutoInstall" -ForegroundColor Green
            return $true
        }
        if ($missing.Count -gt 0) {
            Write-Host "  missing from ls: $($missing -join ', ')" -ForegroundColor Yellow
        }
        return $false
    }

    if ($pymd3) {
        Write-Host "Trying: pymobiledevice3 afc ..." -ForegroundColor DarkGray
        # pymobiledevice3 subcommand style; may or may not work on iOS 5.
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

    return $false
}

$pushed = $false
if (-not $NoAutoPush) {
    if (-not ($afcclient -or $pymd3)) {
        Write-Host "`nNo AFC-push CLI found (tried: afcclient, pymobiledevice3)." -ForegroundColor Yellow
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
