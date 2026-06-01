<#
.SYNOPSIS
    Routine maintenance for an already-onboarded MosaicMesh fleet: apt-get
    update + apt-get upgrade over keyed SSH, with optional dry-run preview
    and post-upgrade respring (tweaks reload).

.DESCRIPTION
    Per host:
      1. Clear stale OpenSSH known_hosts entry for the host (defensive --
         survives reflashes between maintenance runs).
      2. apt-get update with tight per-repo timeouts so graveyard sources
         (ModMyi/ZodTTD/MTMdev) can't hang the run.
      3. apt-get -y --force-yes upgrade  (-f install first to repair any
         half-installed state, same pattern as onboard_devices.ps1).
      4. Optional respring (killall SpringBoard) so any updated
         MobileSubstrate tweaks actually load -- they're inert on disk
         until SpringBoard relaunches.

    -DryRun lists pending upgrades without applying them (apt-get
    --simulate). Useful as the first pass to see what'd happen.

    -NoUpgrade just refreshes the indexes and stops (apt-get update only).
    Useful as a fleet health-check.

    Same SSH options as onboard_devices.ps1 / sync_from_master.ps1: key auth
    only, legacy ssh-rsa allowed, accept-new host keys, BatchMode (no prompts).

.PARAMETER Hosts
    Target IPs / hostnames; comma-separated or array. Combine with -HostFile.

.PARAMETER HostFile
    File with one target per line; lines starting with # are ignored.

.PARAMETER DryRun
    Run apt-get upgrade -s (simulate) to show what WOULD be upgraded.
    No actual install, no respring.

.PARAMETER NoUpgrade
    Run apt-get update only; skip the upgrade and respring steps.

.PARAMETER NoRespring
    Skip the post-upgrade killall SpringBoard. Use if you're scripting
    multiple runs and want to defer the respring.

.PARAMETER Packages
    Restrict the upgrade to specific packages (passed as args to
    apt-get install <pkg>=<latest>). If omitted, all upgradable packages
    are upgraded.

.EXAMPLE
    # Update + upgrade the entire fleet, with respring at the end:
    .\maintain_devices.ps1 -HostFile .\tools\devices.txt

.EXAMPLE
    # Preview what would change on one iPad without applying:
    .\maintain_devices.ps1 -Hosts 192.168.1.50 -DryRun

.EXAMPLE
    # Refresh indexes only (no upgrade) -- e.g. before sync_from_master:
    .\maintain_devices.ps1 -HostFile .\tools\devices.txt -NoUpgrade
#>
[CmdletBinding()]
param(
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    [switch]$DryRun,
    [switch]$NoUpgrade,
    [switch]$NoRespring,
    [string[]]$Packages = @()
)

$ErrorActionPreference = "Stop"

# --- locate tooling ------------------------------------------------------
$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
if (-not (Test-Path $ssh)) { throw "OpenSSH client (ssh.exe) not found." }

$sshKeygen = (Get-Command ssh-keygen -ErrorAction SilentlyContinue).Source
if (-not $sshKeygen) { $sshKeygen = "C:\Windows\System32\OpenSSH\ssh-keygen.exe" }
if (-not (Test-Path $sshKeygen)) { $sshKeygen = $null }

if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
if (-not (Test-Path $KeyPath)) { throw "Private key not found: $KeyPath  (run onboard_devices.ps1 first)" }

# --- host list -----------------------------------------------------------
$targets = @()
if ($Hosts)    { $targets += $Hosts }
if ($HostFile) {
    if (-not (Test-Path $HostFile)) { throw "HostFile not found: $HostFile" }
    $targets += Get-Content $HostFile | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
}
$targets = $targets | Select-Object -Unique
if (-not $targets) { throw "No hosts given. Use -Hosts or -HostFile." }

$sshLegacy = @(
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15",
    "-o", "BatchMode=yes"
)

# --- apt command construction -------------------------------------------
# Same flags as onboard_devices.ps1 / sync_from_master.ps1: tight per-repo
# timeouts so dead sources can't hang, AllowInsecureRepositories for stale
# GPG, --force-yes (legacy iOS apt 0.7.x equivalent of --allow-*).
$aptTimeouts = "-o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 " +
               "-o Acquire::Retries=0 -o Acquire::AllowInsecureRepositories=true"

if ($DryRun) {
    # apt-get --simulate shows what would be done without doing it.
    # Use upgrade (not dist-upgrade) to keep behavior conservative.
    $aptCmd = "apt-get update $aptTimeouts 2>&1 || true; " +
              "apt-get -s upgrade $aptTimeouts 2>&1; echo APT_RC=`$?"
} elseif ($NoUpgrade) {
    $aptCmd = "apt-get update $aptTimeouts 2>&1; echo APT_RC=`$?"
} else {
    $upgradeTarget = if ($Packages) {
        # Targeted upgrade: install specified packages (upgrades to latest if
        # newer version available).
        "install $($Packages -join ' ')"
    } else {
        "upgrade"
    }
    $aptCmd = "apt-get update $aptTimeouts 2>&1 || true; " +
              "apt-get -f install -y --force-yes 2>&1; " +
              "apt-get -y --force-yes $aptTimeouts $upgradeTarget 2>&1; echo APT_RC=`$?"
}

# --- mode banner ---------------------------------------------------------
if ($DryRun)    { Write-Host "Mode: DRY-RUN (show upgrades, don't apply)" -ForegroundColor Magenta }
elseif ($NoUpgrade) { Write-Host "Mode: UPDATE-ONLY (refresh indexes, no upgrade)" -ForegroundColor Magenta }
elseif ($Packages)  { Write-Host "Mode: TARGETED ($($Packages -join ', '))" -ForegroundColor Magenta }
else            { Write-Host "Mode: FULL UPGRADE" -ForegroundColor Magenta }
if (-not $NoRespring -and -not $DryRun -and -not $NoUpgrade) {
    Write-Host "Mode: RESPRING after upgrade (tweaks will reload)" -ForegroundColor Magenta
}
Write-Host ""

# Switch EAP to Continue for the per-host loop: ssh writes warnings to
# stderr (e.g. "Permanently added to known hosts"), apt writes its progress
# there too -- both should NOT terminate the PowerShell script.
$ErrorActionPreference = "Continue"

# --- helper: clear stale host keys (defensive, like onboard_devices.ps1) -
function Clear-StaleHostKey {
    param([string]$HostName)
    if ($sshKeygen) { & $sshKeygen -R $HostName 2>$null | Out-Null }
}

# --- per-host loop -------------------------------------------------------
$results = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }
    Write-Host "=== $hostName`:$p ===" -ForegroundColor Cyan

    Clear-StaleHostKey -HostName $hostName

    $status = "FAILED"; $detail = ""
    try {
        $out = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $aptCmd 2>&1) | Out-String
    } catch {
        $detail = $_.Exception.Message
        Write-Host "  ssh exception: $detail" -ForegroundColor Red
        $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
        continue
    }

    # Surface meaningful lines: install actions, errors, dep info, and the RC marker.
    # Same wide filter as onboard_devices.ps1's apt step.
    ($out -split "`r?`n" | Where-Object {
        $_ -match '^(Get:|E:|W:|Inst |Conf |Setting up|Removing|Unpacking|Unable to locate|Need to get|already the newest|newly installed|Depends:|not going to be installed|unmet dependencies|APT_RC=|The following packages will be upgraded|^\d+ upgraded)'
    }) | Select-Object -First 60 | ForEach-Object {
        $line = $_.Trim()
        $color = if ($line -match '^E:|APT_RC=[^0]') { 'Yellow' } else { 'DarkGray' }
        Write-Host "  $line" -ForegroundColor $color
    }

    if ($out -match 'APT_RC=0') {
        $status = "OK"
        # Count what changed for the summary
        $upgraded = ([regex]::Match($out, '(\d+) upgraded')).Groups[1].Value
        $newlyInstalled = ([regex]::Match($out, '(\d+) newly installed')).Groups[1].Value
        $detail = if ($upgraded -or $newlyInstalled) { "$upgraded upgraded, $newlyInstalled new" } else { "no changes" }
        Write-Host "  $detail" -ForegroundColor Green
    } else {
        $rc = [regex]::Match($out, 'APT_RC=\d+').Value
        $detail = if ($rc) { $rc } else { "apt-failed" }
        Write-Host "  apt exited non-zero ($detail)" -ForegroundColor Yellow
    }

    # Respring after a successful real upgrade (not on dry-run / update-only).
    if ($status -eq "OK" -and -not $DryRun -and -not $NoUpgrade -and -not $NoRespring) {
        try {
            & $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" "killall SpringBoard 2>/dev/null; echo RESPRUNG" 2>&1 | Out-Null
            Write-Host "  respringed" -ForegroundColor Green
        } catch {
            Write-Host "  respring failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
    Write-Host ""
}

# --- summary -------------------------------------------------------------
Write-Host "===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$ok = @($results | Where-Object { $_.Status -eq "OK" }).Count
Write-Host "$ok / $($results.Count) device(s) maintained." -ForegroundColor Cyan
