<#
.SYNOPSIS
    Install Cydia/apt packages on one or more devices over key-based SSH, with
    an optional SpringBoard respring afterward (needed for MobileSubstrate
    tweaks like SimulateTouch to load).

.DESCRIPTION
    apt on these devices works over HTTP + GPG (no TLS), so this needs no cert
    fix. Per host: optional `apt-get update`, then `apt-get install -y <pkgs>`,
    then optional `killall SpringBoard` to load tweaks.

.EXAMPLE
    # Reinstall the tap/swipe tool (pairs with terminalactivator) on one device
    .\ipad_apt.ps1 -Hosts 192.168.1.50 -Package kr.iolate.simulatetouch -Update -Respring

.EXAMPLE
    # Fleet-wide
    .\ipad_apt.ps1 -HostFile .\devices.txt -Package kr.iolate.simulatetouch -Update -Respring
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string[]]$Package,
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    [switch]$Update,
    [switch]$Respring
)

$ErrorActionPreference = "Stop"

$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
if (-not (Test-Path $ssh)) { throw "ssh.exe not found." }

if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
if (-not (Test-Path $KeyPath)) { throw "Private key not found: $KeyPath" }

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
    "-o", "ConnectTimeout=15"
)

# apt writes progress/warnings to stderr (and dead repos warn loudly); don't let
# that abort the run now that setup/validation above is done.
$ErrorActionPreference = "Continue"

$pkgs = $Package -join " "
$parts = @()
# Tolerate dead repos during update so one stale source can't fail the whole run.
if ($Update) { $parts += "apt-get update -o Acquire::AllowInsecureRepositories=true || true" }
$parts += "apt-get install -y $pkgs"
$parts += "echo INSTALL_RC=`$?"
if ($Respring) { $parts += "killall SpringBoard 2>/dev/null; echo RESPRUNG" }
$remote = $parts -join "; "

Write-Host "Packages: $pkgs   Update:$Update  Respring:$Respring" -ForegroundColor Magenta

$results = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }
    Write-Host "`n=== $hostName`:$p ===" -ForegroundColor Cyan
    $status = "FAILED"; $detail = ""
    try {
        $out = (& $ssh @sshOpts -p $p "$User@$hostName" $remote 2>&1) | Out-String
        # Surface the apt summary lines + our markers
        ($out -split "`r?`n" | Where-Object {
            $_ -match 'INSTALL_RC=|RESPRUNG|Setting up|Unpacking|already the newest|Unable to locate|E:|Get:|newly installed'
        }) | ForEach-Object { Write-Host "  $($_.Trim())" -ForegroundColor DarkGray }

        if ($out -match "INSTALL_RC=0") {
            $status = "OK"; Write-Host "  install OK" -ForegroundColor Green
        } else {
            $m = [regex]::Match($out, "INSTALL_RC=\d+")
            $detail = if ($m.Success) { $m.Value } else { ($out.Trim() -replace "\s+"," ") }
            Write-Host "  install failed ($detail)" -ForegroundColor Yellow
        }
    } catch {
        $detail = $_.Exception.Message
        Write-Host "  error: $detail" -ForegroundColor Red
    }
    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
}

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$ok = @($results | Where-Object { $_.Status -eq "OK" }).Count
Write-Host "$ok / $($results.Count) device(s) installed [$pkgs]." -ForegroundColor Cyan
