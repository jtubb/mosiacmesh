<#
.SYNOPSIS
    Install the modified MobileSafari prefs plist (media autoplay enabled) onto
    device(s), with backup + rollback, then relaunch Safari. Optionally open a
    URL afterward to test.

.DESCRIPTION
    Per device: upload new plist to a temp path, back up the current one to
    *.mmbak (only if no backup exists yet), swap in the new one, restore
    mobile:mobile / 600, kill MobileSafari so it re-reads prefs on next launch.
    With -OpenUrl, runs `uiopen <url>` to launch Safari to the display client.

    -Rollback restores the *.mmbak backup instead.

.EXAMPLE
    # Install + immediately open the display client to test autoplay
    .\install_safari_plist.ps1 -Hosts 192.168.1.50 -OpenUrl http://192.168.1.60:3000

.EXAMPLE
    .\install_safari_plist.ps1 -Hosts 192.168.1.50 -Rollback
#>
[CmdletBinding()]
param(
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    [string]$PlistFile = "",   # default: com.apple.mobilesafari.new.plist beside this script
    [string]$OpenUrl = "",
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"

$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
$scp = (Get-Command scp -ErrorAction SilentlyContinue).Source
if (-not $scp) { $scp = "C:\Windows\System32\OpenSSH\scp.exe" }

if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
if (-not (Test-Path $KeyPath)) { throw "Private key not found: $KeyPath" }
if (-not $PlistFile) { $PlistFile = Join-Path $PSScriptRoot "com.apple.mobilesafari.new.plist" }
if (-not $Rollback -and -not (Test-Path $PlistFile)) { throw "Plist not found: $PlistFile" }

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
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10"
)

$P = "/var/mobile/Library/Preferences/com.apple.mobilesafari.plist"
$installCmd = "( [ -f $P.mmbak ] || cp -p $P $P.mmbak ) && " +
    "cat /tmp/mm-safari-new.plist > $P && chown mobile:mobile $P && chmod 600 $P && " +
    "rm -f /tmp/mm-safari-new.plist && killall MobileSafari 2>/dev/null; echo INSTALLED"
$rollbackCmd = "test -f $P.mmbak && cp -p $P.mmbak $P && chown mobile:mobile $P && " +
    "killall MobileSafari 2>/dev/null; echo ROLLED_BACK || echo NO_BACKUP"

$results = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }
    Write-Host "`n=== $hostName`:$p ===" -ForegroundColor Cyan
    $status = "FAILED"; $detail = ""
    try {
        if ($Rollback) {
            $r = (& $ssh @sshOpts -p $p "$User@$hostName" $rollbackCmd 2>&1) | Out-String
            if ($r -match "ROLLED_BACK") { $status = "ROLLED_BACK"; Write-Host "  restored backup + Safari killed" -ForegroundColor Green }
            elseif ($r -match "NO_BACKUP") { $detail = "no backup"; Write-Host "  no .mmbak backup" -ForegroundColor Yellow }
            else { $detail = ($r.Trim() -replace "\s+"," "); Write-Host "  $detail" -ForegroundColor Yellow }
        } else {
            $up = (& $scp -O @sshOpts -P $p $PlistFile "$User@$hostName`:/tmp/mm-safari-new.plist" 2>&1) | Out-String
            if ($LASTEXITCODE -ne 0) { throw "scp failed: $($up.Trim())" }
            Write-Host "  uploaded" -ForegroundColor Green
            $r = (& $ssh @sshOpts -p $p "$User@$hostName" $installCmd 2>&1) | Out-String
            if ($r -match "INSTALLED") { $status = "OK"; Write-Host "  installed + Safari killed" -ForegroundColor Green }
            else { $detail = ($r.Trim() -replace "\s+"," "); Write-Host "  install failed: $detail" -ForegroundColor Yellow }

            if ($status -eq "OK" -and $OpenUrl) {
                $o = (& $ssh @sshOpts -p $p "$User@$hostName" "uiopen '$OpenUrl'" 2>&1) | Out-String
                Write-Host "  opened $OpenUrl" -ForegroundColor Green
            }
        }
    } catch {
        $detail = $_.Exception.Message
        Write-Host "  error: $detail" -ForegroundColor Red
    }
    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
}

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$good = @($results | Where-Object { $_.Status -in @("OK","ROLLED_BACK") }).Count
Write-Host "$good / $($results.Count) device(s) done." -ForegroundColor Cyan
if (-not $Rollback) { Write-Host "Watch the iPad: video should now play WITHOUT a tap. If not, re-run with -Rollback." -ForegroundColor DarkGray }
