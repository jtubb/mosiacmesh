<#
.SYNOPSIS
    Replicate one jailbroken iPad's Cydia repos + installed package set across
    the rest of the fleet. Step 4 of MosaicMesh onboarding (pairs with
    onboard_devices.ps1 for SSH + clock and install_truststore.ps1 for certs).

.DESCRIPTION
    Per target iPad:
      1. SCPs the master's user-managed Cydia sources file to the same path
         (/var/mobile/Library/Caches/com.saurik.Cydia/sources.list) and
         ensures the /etc/apt/sources.list.d/cydia.list symlink to it exists.
         This is how Cydia itself organises user-added repos -- doing the
         same keeps the device "Cydia-coherent" (the GUI sees its own list).
      2. apt-get update (tolerating dead repos so one stale source can't kill
         the run -- BigBoss is alive in 2026, but ModMyi/ZodTTD are graveyards
         that nonetheless still resolve apt metadata).
      3. apt-get install -y <master's full package list>. apt is idempotent:
         already-installed packages no-op, only missing ones download.
      4. killall SpringBoard so any new MobileSubstrate tweaks load.

    Pulls the master's sources.list and `dpkg --get-selections` over SSH at
    start (read-only); the rest is per-target writes.

.PARAMETER Master
    IP / hostname of the source iPad to clone from (e.g. 192.168.1.50).

.PARAMETER Hosts
    Target IPs / hostnames (mutually compatible with -HostFile; deduped).

.PARAMETER HostFile
    File with one target per line; lines starting with # are ignored. The
    master itself is auto-excluded from the target list (no point re-syncing
    a host to itself).

.PARAMETER NoRespring
    Skip the killall SpringBoard at the end. Useful if you don't want the
    fleet to flicker simultaneously (e.g., display-wall in use).

.PARAMETER DryRun
    Pull the master's config + package set and print what WOULD be pushed.
    No SSH writes to targets. Good for verifying the plan first.

.EXAMPLE
    # Standard fleet replication
    .\sync_from_master.ps1 -Master 192.168.1.50 -HostFile .\devices.txt

.EXAMPLE
    # Just see what it would do
    .\sync_from_master.ps1 -Master 192.168.1.50 -HostFile .\devices.txt -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Master,
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    [switch]$NoRespring,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# --- locate ssh/scp + key -------------------------------------------------
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

# --- target list (master auto-excluded) ----------------------------------
$targets = @()
if ($Hosts)    { $targets += $Hosts }
if ($HostFile) {
    if (-not (Test-Path $HostFile)) { throw "HostFile not found: $HostFile" }
    $targets += Get-Content $HostFile | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
}
$targets = $targets | Where-Object { $_ -ne $Master } | Select-Object -Unique
if (-not $targets) { throw "No targets (after excluding the master). Use -Hosts or -HostFile." }

# --- pull master config ---------------------------------------------------
Write-Host "Pulling config from master $Master ..." -ForegroundColor Cyan

# Cydia keeps user-added repos in this file, symlinked to from /etc/apt:
$srcPath = "/var/mobile/Library/Caches/com.saurik.Cydia/sources.list"
$srcDump = (& $ssh @sshOpts -p $Port "$User@$Master" "cat $srcPath" 2>&1) | Out-String
if ($LASTEXITCODE -ne 0 -or -not $srcDump.Trim()) {
    throw "Could not read $srcPath on master: $($srcDump.Trim())"
}

$pkgDump = (& $ssh @sshOpts -p $Port "$User@$Master" "dpkg --get-selections" 2>&1) | Out-String
if ($LASTEXITCODE -ne 0 -or -not $pkgDump.Trim()) {
    throw "Could not read dpkg --get-selections on master: $($pkgDump.Trim())"
}

# Parse: lines look like "<package>\t<install|deinstall>" -- keep installed only.
$packages = @()
foreach ($line in ($pkgDump -split "`r?`n")) {
    $line = $line.Trim()
    if (-not $line -or $line -match '^WARNING') { continue }
    $parts = $line -split '\s+'
    if ($parts.Count -ge 2 -and $parts[1] -eq 'install') { $packages += $parts[0] }
}
if (-not $packages) { throw "Parsed zero packages from master's dpkg output." }

# Stash the master's sources.list to a local temp file we can scp to each target.
$localSrcFile = New-TemporaryFile
# Strip any incoming \r so the file is canonical LF (old apt is finicky).
($srcDump -replace "`r", "") | Out-File -FilePath $localSrcFile -Encoding ASCII -NoNewline

$repoLines = ($srcDump -split "`r?`n") | Where-Object { $_ -match '^\s*deb\s' }
Write-Host ""
Write-Host "Master repos ($($repoLines.Count)):" -ForegroundColor Cyan
$repoLines | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
Write-Host ""
Write-Host "Master package set: $($packages.Count) packages" -ForegroundColor Cyan
Write-Host "Targets ($($targets.Count)): $($targets -join ', ')" -ForegroundColor Cyan

if ($DryRun) {
    Write-Host ""
    Write-Host "DRY RUN -- packages that would be installed:" -ForegroundColor Yellow
    $packages | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Remove-Item $localSrcFile -ErrorAction SilentlyContinue
    return
}

# --- per-target sync ------------------------------------------------------
# We DON'T set $ErrorActionPreference back to Stop here -- apt-get reports
# benign warnings (dead repos, etc.) on stderr that we don't want to abort on.
$ErrorActionPreference = "Continue"

$pkgList    = $packages -join " "
$remoteCmds = @(
    "mkdir -p /var/mobile/Library/Caches/com.saurik.Cydia",
    "chown -R mobile:mobile /var/mobile/Library/Caches/com.saurik.Cydia",
    "ln -sf $srcPath /etc/apt/sources.list.d/cydia.list",
    "apt-get update -o Acquire::AllowInsecureRepositories=true 2>/dev/null || true",
    "apt-get install -y --force-yes $pkgList",
    "echo INSTALL_RC=`$?"
)
if (-not $NoRespring) { $remoteCmds += "killall SpringBoard 2>/dev/null; echo RESPRUNG" }
$remote = $remoteCmds -join "; "

$results = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }
    Write-Host "`n=== $hostName`:$p ===" -ForegroundColor Cyan

    $status = "FAILED"; $detail = ""

    # 1) push the sources.list
    $scpDest = "${User}@${hostName}:$srcPath"
    $scpOut = (& $scp @sshOpts -P $p $localSrcFile.FullName $scpDest 2>&1) | Out-String
    if ($LASTEXITCODE -ne 0) {
        $detail = "scp failed: " + ($scpOut.Trim() -replace "\s+", " ")
        Write-Host "  $detail" -ForegroundColor Red
        $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
        continue
    }
    Write-Host "  sources.list pushed" -ForegroundColor Green

    # 2) symlink + apt update + apt install + respring
    $out = (& $ssh @sshOpts -p $p "$User@$hostName" $remote 2>&1) | Out-String
    ($out -split "`r?`n" | Where-Object {
        $_ -match 'INSTALL_RC=|RESPRUNG|Setting up|Unpacking|already the newest|Unable to locate|^E:|newly installed'
    }) | Select-Object -First 25 | ForEach-Object {
        Write-Host "  $($_.Trim())" -ForegroundColor DarkGray
    }

    if ($out -match "INSTALL_RC=0") {
        $status = "OK"
        Write-Host "  install OK" -ForegroundColor Green
    } else {
        $m = [regex]::Match($out, "INSTALL_RC=\d+")
        $detail = if ($m.Success) { $m.Value } else { ($out.Trim() -replace "\s+", " ").Substring(0, [Math]::Min(200, $out.Length)) }
        Write-Host "  install non-zero ($detail)" -ForegroundColor Yellow
    }
    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
}

Remove-Item $localSrcFile -ErrorAction SilentlyContinue

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$ok = @($results | Where-Object { $_.Status -eq "OK" }).Count
Write-Host "$ok / $($results.Count) device(s) replicated from $Master." -ForegroundColor Cyan
