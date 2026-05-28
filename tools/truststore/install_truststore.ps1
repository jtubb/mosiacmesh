<#
.SYNOPSIS
    Install a rebuilt iOS TrustStore.sqlite3 (current root CAs) onto one or more
    jailbroken display devices over key-based SSH, with backup + rollback.

.DESCRIPTION
    Per device:
      1. Uploads the rebuilt trust store to a temp path (scp).
      2. Backs up the on-device TrustStore.sqlite3 to *.mmbak.
      3. Swaps in the new DB (preserving owner/perms), restarts securityd/ocspd.

    -Rollback restores the *.mmbak backup instead.

    The trust store is built by build_truststore.py (current Mozilla roots).
    Root certs are not device-specific, so the same file installs on every unit.

.EXAMPLE
    # Install on one device
    .\install_truststore.ps1 -Hosts 192.168.1.50

.EXAMPLE
    # Install across the fleet
    .\install_truststore.ps1 -HostFile ..\devices.txt

.EXAMPLE
    # Undo on one device
    .\install_truststore.ps1 -Hosts 192.168.1.50 -Rollback
#>
[CmdletBinding()]
param(
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    # Rebuilt trust store to push; defaults to TrustStore.new.sqlite3 beside this script.
    [string]$TrustStoreFile = "",
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"

$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
$scp = (Get-Command scp -ErrorAction SilentlyContinue).Source
if (-not $scp) { $scp = "C:\Windows\System32\OpenSSH\scp.exe" }
foreach ($t in @($ssh, $scp)) { if (-not (Test-Path $t)) { throw "Not found: $t" } }

if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
if (-not (Test-Path $KeyPath)) { throw "Private key not found: $KeyPath" }

if (-not $TrustStoreFile) { $TrustStoreFile = Join-Path $PSScriptRoot "TrustStore.new.sqlite3" }
if (-not $Rollback -and -not (Test-Path $TrustStoreFile)) {
    throw "Trust store file not found: $TrustStoreFile  (build it first: python build_truststore.py TrustStore.original.sqlite3 TrustStore.new.sqlite3)"
}

# --- host list ------------------------------------------------------------
$targets = @()
if ($Hosts)    { $targets += $Hosts }
if ($HostFile) {
    if (-not (Test-Path $HostFile)) { throw "HostFile not found: $HostFile" }
    $targets += Get-Content $HostFile | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
}
$targets = $targets | Select-Object -Unique
if (-not $targets) { throw "No hosts given. Use -Hosts or -HostFile." }

$sshOpts = @(
    "-i", $KeyPath,
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes",          # only the -i key; old sshd has low MaxAuthTries
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10"
)

$KS = "/private/var/Keychains"
# Back up ONLY if no backup exists yet, so re-running never overwrites the
# pristine original with an already-modified store.
$installCmd = "( [ -f $KS/TrustStore.sqlite3.mmbak ] || cp -p $KS/TrustStore.sqlite3 $KS/TrustStore.sqlite3.mmbak ) && " +
    "cat $KS/TrustStore.mm-new.sqlite3 > $KS/TrustStore.sqlite3 && " +
    "chown _securityd:wheel $KS/TrustStore.sqlite3 && chmod 600 $KS/TrustStore.sqlite3 && " +
    "rm -f $KS/TrustStore.mm-new.sqlite3 && " +
    "killall securityd 2>/dev/null; killall ocspd 2>/dev/null; echo INSTALLED"
$rollbackCmd = "test -f $KS/TrustStore.sqlite3.mmbak && cp -p $KS/TrustStore.sqlite3.mmbak $KS/TrustStore.sqlite3 && " +
    "killall securityd 2>/dev/null; killall ocspd 2>/dev/null; echo ROLLED_BACK || echo NO_BACKUP"

$results = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }
    Write-Host "`n=== $hostName`:$p ===" -ForegroundColor Cyan
    $status = "FAILED"; $detail = ""

    try {
        if ($Rollback) {
            $r = (& $ssh @sshOpts -p $p "$User@$hostName" $rollbackCmd 2>&1) | Out-String
            if ($r -match "ROLLED_BACK") { $status = "ROLLED_BACK"; Write-Host "  restored backup" -ForegroundColor Green }
            elseif ($r -match "NO_BACKUP") { $detail = "no .mmbak backup on device"; Write-Host "  $detail" -ForegroundColor Yellow }
            else { $detail = ($r.Trim() -replace "\s+", " "); Write-Host "  $detail" -ForegroundColor Yellow }
        } else {
            # 1) upload
            $up = (& $scp -O @sshOpts -P $p $TrustStoreFile "$User@$hostName`:$KS/TrustStore.mm-new.sqlite3" 2>&1) | Out-String
            if ($LASTEXITCODE -ne 0) { throw "scp failed: $($up.Trim())" }
            Write-Host "  uploaded" -ForegroundColor Green
            # 2) swap + restart
            $r = (& $ssh @sshOpts -p $p "$User@$hostName" $installCmd 2>&1) | Out-String
            if ($r -match "INSTALLED") { $status = "OK"; Write-Host "  installed + securityd reloaded" -ForegroundColor Green }
            else { $detail = ($r.Trim() -replace "\s+", " "); Write-Host "  install failed: $detail" -ForegroundColor Yellow }
        }
    } catch {
        $detail = $_.Exception.Message
        Write-Host "  error: $detail" -ForegroundColor Red
    }
    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
}

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$good = @($results | Where-Object { $_.Status -in @("OK", "ROLLED_BACK") }).Count
Write-Host "$good / $($results.Count) device(s) $([string]::Concat($(if($Rollback){'rolled back'}else{'installed'})))." -ForegroundColor Cyan
if (-not $Rollback) {
    Write-Host "Test: open Safari to https://www.google.com on the device, or toggle WiFi. Rollback: re-run with -Rollback." -ForegroundColor DarkGray
}
