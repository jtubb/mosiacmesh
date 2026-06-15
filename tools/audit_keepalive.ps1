<#
.SYNOPSIS
    Read-only keep-alive audit for an onboarded MosaicMesh iPad-1 fleet.

.DESCRIPTION
    For each device, over keyed SSH (no password, no prompts), it reports the
    device-side WiFi keep-alive posture that onboard_devices.ps1 sets up:

      REACHABLE   - did SSH connect at all? (an iPad in deep WiFi power-save or
                    powered off shows UNREACHABLE -- the headline keep-alive
                    failure mode).
      INSOMNIA    - the WiFi-keepalive tweak's preference state:
                      ON       plist present AND <key>Enabled</key><true/>
                      DISABLED plist present but not enabled (inert dylib)
                      MISSING  no plist -> Insomnia loads but does nothing
                    (plist: /var/mobile/Library/Preferences/com.malcolmhall.Insomnia.plist)
      INSDYLIB    - is the Insomnia MobileSubstrate dylib actually on disk?
      ALDAEMON    - is the boot autolock-off LaunchDaemon installed?
                    (/Library/LaunchDaemons/com.mosaicmesh.autolock-off.plist)
      UPTIME      - minutes since boot (sysctl kern.boottime); a recently
                    rebooted device may not have resprung Insomnia yet.
      DISPLAY     - best-effort: is the kiosk page process (Web.app/MobileSafari)
                    running? (needs `ps` on the device; DOWN may be a false
                    negative if ps isn't installed -- treat as a hint, not proof.)

    NOTHING IS CHANGED on any device -- this only reads files and queries state.

    A device is OK when REACHABLE + INSOMNIA=ON + INSDYLIB=YES + ALDAEMON=YES.
    Anything else is DEGRADED (and listed in the summary so you know exactly
    which screens to repair). UNREACHABLE is called out separately.

    Re-run this after every reflash or AP-saturation event -- per the project's
    standing note, Insomnia state drifts and silently leaves WiFi power-save on.

    Same SSH options as the server's device_scripts.py / onboard_devices.ps1:
    key auth only, legacy ssh-rsa enabled, accept-new host keys, BatchMode.

.PARAMETER Hosts
    Target IPs / hostnames; comma-separated or array. Combine with -HostFile.

.PARAMETER HostFile
    File with one target per line (# = comment). Defaults to tools/devices.txt
    next to this script when neither -Hosts nor -HostFile is given.

.PARAMETER Csv
    Emit the per-device rows as CSV to this path (in addition to the console
    table) so you can diff audits across time.

.EXAMPLE
    # Audit the whole fleet from the default device list
    .\tools\audit_keepalive.ps1

.EXAMPLE
    # Audit two specific devices
    .\tools\audit_keepalive.ps1 -Hosts 192.168.1.50,192.168.1.63

.EXAMPLE
    # Audit + save a timestamped CSV for trend comparison
    .\tools\audit_keepalive.ps1 -Csv .\keepalive-20260612.csv
#>
[CmdletBinding()]
param(
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [int]$Port = 22,
    [string]$KeyName = "mosaic_ipad",
    [string]$KeyPath = "",
    [int]$ConnectTimeout = 8,
    [string]$Csv = ""
)

$ErrorActionPreference = "Stop"

# --- locate tooling -------------------------------------------------------
$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
if (-not (Test-Path $ssh)) { throw "OpenSSH client (ssh.exe) not found." }

$sshKeygen = (Get-Command ssh-keygen -ErrorAction SilentlyContinue).Source
if (-not $sshKeygen) { $sshKeygen = "C:\Windows\System32\OpenSSH\ssh-keygen.exe" }
if (-not (Test-Path $sshKeygen)) { $sshKeygen = $null }

# --- resolve key ----------------------------------------------------------
if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
if (-not (Test-Path $KeyPath)) {
    throw "Private key not found: $KeyPath  (run tools\onboard_devices.ps1 first)"
}

# --- build the host list --------------------------------------------------
$targets = @()
if ($Hosts) { $targets += $Hosts }
if ($HostFile) {
    if (-not (Test-Path $HostFile)) { throw "HostFile not found: $HostFile" }
    $targets += Get-Content $HostFile
}
# Default to tools/devices.txt next to this script when nothing was given.
if (-not $targets) {
    $defaultList = Join-Path $PSScriptRoot 'devices.txt'
    if (-not (Test-Path $defaultList)) {
        throw "No hosts given and no default $defaultList. Use -Hosts or -HostFile."
    }
    $targets += Get-Content $defaultList
}
$targets = $targets |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith("#") } |
    Select-Object -Unique
if (-not $targets) { throw "Host list is empty after filtering comments/blanks." }

# Same legacy/keepalive options the server uses, plus LogLevel=ERROR to keep
# ssh.exe's known-hosts chatter out of the PowerShell output.
$sshOpts = @(
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=$ConnectTimeout",
    "-o", "ServerAliveInterval=5",
    "-o", "ServerAliveCountMax=2"
)

# --- the remote probe (single line; no double quotes -> survives the
#     PowerShell -> ssh.exe -> bash argv flattening that mangled quoted
#     heredocs elsewhere in this toolset). Emits KEY=VALUE lines + PROBE_OK.
#     Fixed paths have no spaces, so unquoted vars are safe; the empty-string
#     guards use `[ $VAR ]` (true iff non-empty for whitespace-free values).
#
#     iOS-5 shell caveats learned the hard way:
#       - `tr` is NOT installed (it silently no-ops), so Insomnia detection
#         uses two `grep -q` passes instead. The prefs file is a single-key
#         plist, so the only <true/> in it IS Enabled's value: key present +
#         a <true/> => ON; key present, no <true/> (e.g. <false/>) => DISABLED.
#         (A binary plist would read DISABLED -- ours is shipped as XML.)
#       - `sysctl -n` misbehaves; plain `sysctl kern.boottime` emits
#         `{ sec = <epoch>, usec = 0 } ...` which the sed parses cleanly.
$probe = @'
INSP=/var/mobile/Library/Preferences/com.malcolmhall.Insomnia.plist; if [ -f $INSP ]; then if grep -q Enabled $INSP 2>/dev/null && grep -q '<true/>' $INSP 2>/dev/null; then echo INSOMNIA=ON; else echo INSOMNIA=DISABLED; fi; else echo INSOMNIA=MISSING; fi; [ -f /Library/MobileSubstrate/DynamicLibraries/Insomnia.dylib ] && echo INSDYLIB=YES || echo INSDYLIB=NO; [ -f /Library/LaunchDaemons/com.mosaicmesh.autolock-off.plist ] && echo ALDAEMON=YES || echo ALDAEMON=NO; NOW=$(date +%s 2>/dev/null); BOOT=$(sysctl kern.boottime 2>/dev/null | sed -n 's/[^0-9]*\([0-9][0-9]*\).*/\1/p'); if [ $BOOT ] && [ $NOW ]; then echo UPTIME=$(( (NOW-BOOT)/60 ))m; else echo UPTIME=?; fi; (ps ax 2>/dev/null || ps -e 2>/dev/null) | grep -iE 'Web.app|MobileSafari' | grep -v grep >/dev/null 2>&1 && echo DISPLAY=UP || echo DISPLAY=DOWN; echo PROBE_OK
'@
$probe = $probe.Trim()

function Parse-Value([string]$text, [string]$key) {
    $m = [regex]::Match($text, "(?m)^$key=(.*)$")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return ""
}

Write-Host "Auditing $($targets.Count) device(s) -- read-only, nothing is changed.`n" -ForegroundColor Cyan

$rows = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }

    # Defensive: drop any stale OpenSSH known_hosts entry (reflashed iPads
    # rotate host keys). accept-new then re-learns it. No-op if absent.
    if ($sshKeygen) { & $sshKeygen -R $hostName 2>$null | Out-Null }

    $out = ""
    try {
        $out = (& $ssh -i $KeyPath -p $p @sshOpts "$User@$hostName" $probe 2>&1) | Out-String
    } catch {
        $out = "ssh-exception: $($_.Exception.Message)"
    }

    if ($out -match 'PROBE_OK') {
        $insomnia = Parse-Value $out 'INSOMNIA'
        $row = [pscustomobject]@{
            Host     = $hostName
            Reach    = 'YES'
            Insomnia = $insomnia
            Dylib    = Parse-Value $out 'INSDYLIB'
            ALDaemon = Parse-Value $out 'ALDAEMON'
            Uptime   = Parse-Value $out 'UPTIME'
            Display  = Parse-Value $out 'DISPLAY'
        }
        $ok = ($row.Insomnia -eq 'ON') -and ($row.Dylib -eq 'YES') -and ($row.ALDaemon -eq 'YES')
        $row | Add-Member -NotePropertyName Status -NotePropertyValue ($(if ($ok) { 'OK' } else { 'DEGRADED' }))
    } else {
        $reason = ($out.Trim() -replace '\s+', ' ')
        if (-not $reason) { $reason = 'no response' }
        $row = [pscustomobject]@{
            Host     = $hostName
            Reach    = 'NO'
            Insomnia = '-'
            Dylib    = '-'
            ALDaemon = '-'
            Uptime   = '-'
            Display  = '-'
            Status   = 'UNREACHABLE'
        }
        $row | Add-Member -NotePropertyName Detail -NotePropertyValue $reason
    }

    $color = switch ($row.Status) {
        'OK'          { 'Green' }
        'DEGRADED'    { 'Yellow' }
        'UNREACHABLE' { 'Red' }
        default       { 'Gray' }
    }
    $line = "{0,-26} {1,-11} reach={2,-3} insomnia={3,-9} dylib={4,-3} aldaemon={5,-3} up={6,-6} disp={7}" -f `
        $hostName, $row.Status, $row.Reach, $row.Insomnia, $row.Dylib, $row.ALDaemon, $row.Uptime, $row.Display
    Write-Host $line -ForegroundColor $color

    $rows += $row
}

# --- summary --------------------------------------------------------------
$okN   = ($rows | Where-Object { $_.Status -eq 'OK' }).Count
$degN  = ($rows | Where-Object { $_.Status -eq 'DEGRADED' }).Count
$unrN  = ($rows | Where-Object { $_.Status -eq 'UNREACHABLE' }).Count

Write-Host ("`n=== Keep-alive summary: {0} OK / {1} degraded / {2} unreachable (of {3}) ===" -f `
    $okN, $degN, $unrN, $rows.Count) -ForegroundColor Cyan

if ($degN) {
    Write-Host "`nDEGRADED (reachable but keep-alive incomplete):" -ForegroundColor Yellow
    foreach ($r in ($rows | Where-Object { $_.Status -eq 'DEGRADED' })) {
        $why = @()
        if ($r.Insomnia -ne 'ON')  { $why += "insomnia=$($r.Insomnia)" }
        if ($r.Dylib    -ne 'YES') { $why += "no-dylib" }
        if ($r.ALDaemon -ne 'YES') { $why += "no-autolock-daemon" }
        Write-Host ("  {0,-26} {1}" -f $r.Host, ($why -join ', ')) -ForegroundColor Yellow
    }
}
if ($unrN) {
    Write-Host "`nUNREACHABLE (offline or in WiFi power-save):" -ForegroundColor Red
    foreach ($r in ($rows | Where-Object { $_.Status -eq 'UNREACHABLE' })) {
        Write-Host ("  {0,-26} {1}" -f $r.Host, $r.Detail) -ForegroundColor Red
    }
}

if ($Csv) {
    $rows | Select-Object Host, Status, Reach, Insomnia, Dylib, ALDaemon, Uptime, Display |
        Export-Csv -NoTypeInformation -Path $Csv
    Write-Host "`nCSV written: $Csv" -ForegroundColor DarkGray
}
