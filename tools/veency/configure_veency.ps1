<#
.SYNOPSIS
    Push a headless Veency config (password set => no on-screen accept prompt)
    to device(s) and reload Veency without a respring.

.DESCRIPTION
    Veency shows an on-screen "accept connection?" prompt UNLESS a password is
    set (per its own settings footer). This pushes com.saurik.Veency.plist with
    Enabled=true, ShowCursor=false, Password=<pw>, fixes mobile:mobile/600, and
    posts the Veency reload notifications via notify_post.

    Build the plist first (done once):
      python - <<'PY'
      import plistlib
      plistlib.dump({"Enabled":True,"ShowCursor":False,"Password":"mosaic"},
                    open("tools/veency/com.saurik.Veency.plist","wb"),
                    fmt=plistlib.FMT_BINARY)
      PY

.EXAMPLE
    .\configure_veency.ps1 -Hosts 192.168.1.50
    .\configure_veency.ps1 -HostFile ..\devices.txt
#>
[CmdletBinding()]
param(
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    [string]$PlistFile = ""   # default: com.saurik.Veency.plist beside this script
)

$ErrorActionPreference = "Stop"
$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source; if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
$scp = (Get-Command scp -ErrorAction SilentlyContinue).Source; if (-not $scp) { $scp = "C:\Windows\System32\OpenSSH\scp.exe" }
if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
if (-not (Test-Path $KeyPath)) { throw "Private key not found: $KeyPath" }
if (-not $PlistFile) { $PlistFile = Join-Path $PSScriptRoot "com.saurik.Veency.plist" }
if (-not (Test-Path $PlistFile)) { throw "Veency plist not found: $PlistFile" }

$targets = @()
if ($Hosts) { $targets += $Hosts }
if ($HostFile) {
    if (-not (Test-Path $HostFile)) { throw "HostFile not found: $HostFile" }
    $targets += Get-Content $HostFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") }
}
$targets = $targets | Select-Object -Unique
if (-not $targets) { throw "No hosts given." }

$sshOpts = @("-i", $KeyPath, "-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10")

$P = "/var/mobile/Library/Preferences/com.saurik.Veency.plist"
# Install prefs, fix ownership, and nudge Veency to reload settings.
$installCmd = "cat /tmp/mm-veency.plist > $P && chown mobile:mobile $P && chmod 600 $P && rm -f /tmp/mm-veency.plist && " +
    "notify_post com.saurik.Veency-Settings; notify_post com.saurik.Veency-Enabled; echo CONFIGURED"

$ErrorActionPreference = "Continue"
$results = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }
    Write-Host "`n=== $hostName`:$p ===" -ForegroundColor Cyan
    $status = "FAILED"; $detail = ""
    try {
        $up = (& $scp -O @sshOpts -P $p $PlistFile "$User@$hostName`:/tmp/mm-veency.plist" 2>&1) | Out-String
        if ($LASTEXITCODE -ne 0) { throw "scp failed: $($up.Trim())" }
        $r = (& $ssh @sshOpts -p $p "$User@$hostName" $installCmd 2>&1) | Out-String
        if ($r -match "CONFIGURED") { $status = "OK"; Write-Host "  Veency configured (password set, prompt off)" -ForegroundColor Green }
        else { $detail = ($r.Trim() -replace "\s+"," "); Write-Host "  failed: $detail" -ForegroundColor Yellow }
    } catch { $detail = $_.Exception.Message; Write-Host "  error: $detail" -ForegroundColor Red }
    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
}
Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$ok = @($results | Where-Object { $_.Status -eq "OK" }).Count
Write-Host "$ok / $($results.Count) device(s) configured. VNC: <host>:5900 password 'mosaic'." -ForegroundColor Cyan
