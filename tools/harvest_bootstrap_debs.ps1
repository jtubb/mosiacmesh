<#
.SYNOPSIS
    Harvest the .deb files needed to bootstrap fresh jailbroken iPads
    (OpenSSH + the standard tweaks) from a known-good master iPad's apt
    cache into a local folder, ready to push via AFC to each new iPad's
    /var/mobile/Media/Cydia/AutoInstall/ folder.

.DESCRIPTION
    Why bootstrap this way: iOS 5.1's TLS ceiling (TLS 1.0 max, see notes
    in the project README / memory) means Cydia GUI on a freshly-jailbroken
    iPad cannot reach modern HTTPS endpoints in 2026 -- so we can't install
    OpenSSH via Cydia. AutoInstall (a Cydia feature where .debs dropped in
    /var/mobile/Media/Cydia/AutoInstall/ get dpkg-installed at next boot)
    completely bypasses the network, so this is the only practical path.

    Per run, on the master iPad:
      1. apt-get update tolerating dead repos (AllowInsecureRepositories).
      2. apt-get install -y --reinstall --download-only <PackageSet>
         -- forces apt to download the .debs (and their dependencies) into
         /var/cache/apt/archives/ WITHOUT changing installed state.
      3. SCP everything in /var/cache/apt/archives/*.deb to local OutDir.

    The master must already be onboarded with key auth (see
    onboard_devices.ps1). This script makes no install state changes on
    the master -- only cache writes (which apt-get clean would clear).

.PARAMETER Master
    IP / hostname of the source iPad (e.g. 192.168.1.50). Must already
    accept key auth via $KeyName.

.PARAMETER OutDir
    Local folder for the harvested .debs (default: .\bootstrap-debs).

.PARAMETER PackageSet
    Packages to ensure get downloaded (apt resolves their dependencies
    automatically). Default is the standard MosaicMesh bootstrap kit:
    openssh + mobilesubstrate + libactivator + veency + com.fb.skiplock.

.PARAMETER CleanMaster
    Run "apt-get clean" on the master BEFORE fetching. Gives you a
    cache containing ONLY the bootstrap kit + its deps (no leftover
    .debs from past Cydia activity). Recommended for a clean bootstrap.

.PARAMETER SkipFetch
    Skip the apt-get update + --download-only step entirely; just SCP
    whatever's currently in the master's cache. Faster re-runs.

.PARAMETER Update
    Run apt-get update on the master before downloading. Off by default
    because the cached package indexes from Saurik + BigBoss are usually
    fresh enough, and graveyard repos (ModMyi, ZodTTD, MTMdev -- all
    still in Sign1Screen1's sources.list) can hang for minutes on dead
    HTTP servers. Use this only when you really need a refreshed index.
    Comes with strict per-repo timeouts so a dead source can't hang.

.PARAMETER Clean
    Clear OutDir before pulling (removes any *.deb already there).

.EXAMPLE
    # Standard run -- clean master cache, fetch bootstrap kit, pull locally
    .\harvest_bootstrap_debs.ps1 -Master 192.168.1.50 -CleanMaster -Clean

.EXAMPLE
    # Custom package set
    .\harvest_bootstrap_debs.ps1 -Master 192.168.1.50 -PackageSet openssh,kr.iolate.simulatetouch
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Master,
    [string]$OutDir = ".\bootstrap-debs",
    [string[]]$PackageSet = @('openssh','mobilesubstrate','libactivator','veency','com.fb.skiplock'),
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    [switch]$CleanMaster,
    [switch]$SkipFetch,
    [switch]$Update,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

# --- locate ssh / scp + key ----------------------------------------------
$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
$scp = (Get-Command scp -ErrorAction SilentlyContinue).Source
if (-not $scp) { $scp = "C:\Windows\System32\OpenSSH\scp.exe" }
foreach ($t in @($ssh, $scp)) { if (-not (Test-Path $t)) { throw "Not found: $t" } }

if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
if (-not (Test-Path $KeyPath)) { throw "Private key not found: $KeyPath" }

$sshOpts = @(
    "-i", $KeyPath,
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15"
)

# --- prep OutDir ----------------------------------------------------------
if (-not [System.IO.Path]::IsPathRooted($OutDir)) {
    $OutDir = Join-Path (Get-Location) $OutDir
}
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
if ($Clean) {
    $existing = Get-ChildItem -Path $OutDir -Filter '*.deb' -File -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Clearing $($existing.Count) existing .deb(s) from $OutDir ..." -ForegroundColor DarkYellow
        $existing | Remove-Item -Force
    }
}

Write-Host "Master:      $Master" -ForegroundColor Cyan
Write-Host "Output:      $OutDir" -ForegroundColor Cyan
Write-Host "PackageSet:  $($PackageSet -join ', ')" -ForegroundColor Cyan

# --- (optional) clean master cache first ---------------------------------
if ($CleanMaster -and -not $SkipFetch) {
    Write-Host "`nClearing master's apt cache (apt-get clean)..." -ForegroundColor DarkYellow
    & $ssh @sshOpts -p $Port "$User@$Master" "apt-get clean" 2>&1 | Out-Null
}

# --- fetch into master's cache -------------------------------------------
# --reinstall makes apt download even when already installed; --download-only
# means apt never tries to install/upgrade -- pure cache populate. We tolerate
# dead repos so one stale source can't fail the whole run.
if (-not $SkipFetch) {
    # Tight per-repo timeouts so a single dead source (ModMyi/ZodTTD/MTMdev)
    # can't make a multi-minute hang look like progress. Acquire::Retries=0
    # avoids the default 3-attempt loop on each failure.
    $aptTimeouts = "-o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 -o Acquire::Retries=0 -o Acquire::AllowInsecureRepositories=true"

    if ($Update) {
        Write-Host "`nRefreshing master's package indexes (apt-get update, 15s per-repo timeout)..." -ForegroundColor Cyan
        $u = (& $ssh @sshOpts -p $Port "$User@$Master" "apt-get update $aptTimeouts 2>&1 || true" 2>&1) | Out-String
        ($u -split "`r?`n" | Where-Object { $_ -match '^(Get:|E:|W:|Ign|Hit)' } | Select-Object -First 12) |
            ForEach-Object { Write-Host "  $($_.Trim())" -ForegroundColor DarkGray }
    } else {
        Write-Host "`nSkipping apt-get update (use -Update if you need fresh indexes; default is off to avoid graveyard-repo hangs)." -ForegroundColor DarkGray
    }

    Write-Host "`nFetching .debs into master's apt cache (download-only)..." -ForegroundColor Cyan
    # --force-yes overrides apt's safety bail on stale/unauthenticated entries
    # (the legacy iOS apt 0.7.x equivalent of modern --allow-*). Without it,
    # any stale signature in the cached indexes makes -y abort with
    # "There are problems and -y was used without --force-yes". We only
    # care about downloading -- not installing -- so the safeties don't matter.
    $pkgArg = $PackageSet -join ' '
    $aptCmd = "apt-get install -y --force-yes --reinstall --download-only $aptTimeouts $pkgArg; echo APT_RC=`$?"

    # Let apt write to stderr without PowerShell killing the run; capture the
    # full output so we can surface the meaningful lines AND any error reason
    # if the install bombs out.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = (& $ssh @sshOpts -p $Port "$User@$Master" $aptCmd 2>&1) | Out-String
    $ErrorActionPreference = $prevEAP

    ($out -split "`r?`n" | Where-Object {
        $_ -match '^(Get:|E:|W:|Unable to locate|Reading package|Need to get|already the newest|newly installed|APT_RC=)'
    }) | Select-Object -First 40 | ForEach-Object {
        $line = $_.Trim()
        $color = if ($line -match '^E:|APT_RC=[^0]') { 'Yellow' } else { 'DarkGray' }
        Write-Host "  $line" -ForegroundColor $color
    }
}

# --- SCP the cache locally -----------------------------------------------
Write-Host "`nListing master's apt cache..." -ForegroundColor Cyan
$listOut = (& $ssh @sshOpts -p $Port "$User@$Master" "ls /var/cache/apt/archives/" 2>&1) | Out-String
$debs = ($listOut -split "`r?`n") | ForEach-Object { $_.Trim() } |
    Where-Object { $_ -match '\.deb$' }
Write-Host "Found $($debs.Count) .deb(s) in master's cache" -ForegroundColor Cyan

if (-not $debs) {
    Write-Warning "No .debs in master's cache. If you used -SkipFetch, drop it; if not, the apt download likely failed -- check the apt output above."
    return
}

Write-Host "`nPulling .debs to $OutDir ..." -ForegroundColor Cyan
$pulled = 0; $failed = 0
foreach ($name in $debs) {
    $localPath = Join-Path $OutDir $name
    if ((Test-Path $localPath) -and -not $Clean) {
        # Already have it from a previous pull -- skip to save time.
        Write-Host "  (have)  $name" -ForegroundColor DarkGray
        $pulled++
        continue
    }
    $remote = "${User}@${Master}:/var/cache/apt/archives/$name"
    & $scp @sshOpts -P $Port $remote $localPath 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $localPath)) {
        Write-Host "  pulled  $name" -ForegroundColor Green
        $pulled++
    } else {
        Write-Host "  FAILED  $name" -ForegroundColor Red
        $failed++
    }
}

# --- summary --------------------------------------------------------------
$local = Get-ChildItem -Path $OutDir -Filter '*.deb' -File | Sort-Object Name
Write-Host "`n===== Local cache ($OutDir) =====" -ForegroundColor Cyan
$local | ForEach-Object {
    Write-Host ("  {0,-50} {1,10:N0} bytes" -f $_.Name, $_.Length) -ForegroundColor DarkGray
}
$total = ($local | Measure-Object Length -Sum).Sum
Write-Host ("`n$($local.Count) .deb(s) totalling {0:N2} MB.  pulled this run: $pulled, failed: $failed" -f ($total/1MB)) -ForegroundColor Cyan
Write-Host ""
Write-Host "Next: push these to each new iPad via AFC, then reboot." -ForegroundColor DarkGray
Write-Host "  .\push_bootstrap_to_device.ps1 -DebDir `"$OutDir`"" -ForegroundColor DarkGray
