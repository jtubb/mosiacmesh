<#
.SYNOPSIS
    Onboard jailbroken iOS display devices for MosaicMesh management by
    installing the automation SSH public key, using the factory default
    password once to bootstrap key auth.

.DESCRIPTION
    For each device it:
      1. Pushes the public key into /var/root/.ssh/authorized_keys over a
         password SSH session (plink -pw). Idempotent: skips if already
         present. With -ReplaceKeys it instead rewrites authorized_keys to
         contain ONLY the current automation key (clears stale lines).
      2. Verifies key-only auth works afterward (native ssh, no password).

    The key is chosen by -KeyName (default "mosaic_ipad", under ~/.ssh). If a
    key by that name exists it is reused; otherwise a passphrase-less RSA key
    is generated automatically (automation keys must be passphrase-less so the
    server can run device lifecycle scripts unattended).

    The device's old OpenSSH only speaks SHA-1-era crypto, so verification
    explicitly re-enables ssh-rsa host keys / pubkey signatures.

    SECURITY: the bootstrap password (default "alpine") is a known factory
    default. Rotate it on each device after onboarding (see -PostInstall).

.EXAMPLE
    # One device, default key (~/.ssh/mosaic_ipad), generated if missing
    .\tools\onboard_devices.ps1 -Hosts 192.168.1.50

.EXAMPLE
    # A whole batch from a file (one IP or host[:port] per line, # = comment)
    .\tools\onboard_devices.ps1 -HostFile .\tools\devices.txt

.EXAMPLE
    # Use/create a differently-named key and wipe any stale authorized_keys
    .\tools\onboard_devices.ps1 -HostFile .\tools\devices.txt -KeyName lobby_wall -ReplaceKeys

.EXAMPLE
    # Full fleet bring-up: clean key + correct the clock (cert fix) on each device
    .\tools\onboard_devices.ps1 -HostFile .\tools\devices.txt -ReplaceKeys -FixClock

.EXAMPLE
    # Reflashed-via-kit fleet: key + tweaks + timezone in one shot.
    # Skips FixClock because the kit-built IPSW + working Cydia gives a sane NTP-set clock.
    .\tools\onboard_devices.ps1 -HostFile .\tools\devices.txt -ReplaceKeys -InstallTweaks -Timezone 'America/New_York'

.EXAMPLE
    # Custom package list (e.g. add AppSync to the standard set):
    .\tools\onboard_devices.ps1 -Hosts 192.168.1.76 -ReplaceKeys -InstallTweaks -Packages 'ai.akemi.appsyncunified'
#>
[CmdletBinding()]
param(
    [string[]]$Hosts,
    [string]$HostFile,
    [string]$User = "root",
    [string]$Password = "alpine",
    [int]$Port = 22,
    # Name of the key under ~/.ssh (reused if present, generated if missing).
    [string]$KeyName = "mosaic_ipad",
    # Explicit full path to the private key; overrides -KeyName when set.
    [string]$KeyPath = "",
    # Rewrite authorized_keys to contain ONLY this key (clears stale lines).
    [switch]$ReplaceKeys,
    # Fix the device clock (NTP, else this machine's UTC). On dead-RTC devices
    # a wrong clock makes every TLS cert look invalid system-wide; correcting
    # the date is the cert fix for SecureTransport (Safari/Cydia/etc.).
    [switch]$FixClock,
    # Optional extra shell run on the device after the key is installed
    # (e.g. a password rotation). Runs over the freshly-verified key session.
    [string]$PostInstall = "",
    # Install the standard MosaicMesh tweak set via apt over the keyed session.
    # The default set ($DEFAULT_TWEAKS below) covers the packages our automation
    # actually depends on (libactivator + skiplock + veency + terminalactivator
    # + their dependencies). Combine with -Packages to add extras.
    [switch]$InstallTweaks,
    # Additional / replacement package list to apt-get install. If -InstallTweaks
    # is also set, the union of both lists is installed. If only -Packages is set,
    # only those packages are installed.
    [string[]]$Packages = @(),
    # IANA timezone name (e.g. "America/New_York", "Europe/London"). If set,
    # the script symlinks /etc/localtime to the matching zoneinfo file AND
    # writes AppleTimeZone into .GlobalPreferences so the iOS Settings UI
    # reflects it. iPad-1's RTC is correct (NTP) regardless of TZ; this
    # only affects how local time is rendered.
    [string]$Timezone = "",
    # Local path to apt7 .deb. Reflashed iPads from the Legacy-iOS-Kit IPSW
    # ship dpkg + apt7-lib but NOT the apt7 CLI front-end (so /usr/bin/apt-get
    # doesn't exist). If this file is present locally and apt-get is missing
    # on the target, SCP + dpkg -i it before running -Packages / -InstallTweaks.
    # Default: auto-discover apt7_*.deb in ..\bootstrap-debs\.
    [string]$AptDeb = ""
)

# Canonical MosaicMesh tweak set -- everything our scripts rely on plus the
# tweaks that make manual ops sane. All sourced from BigBoss (HTTP) so no TLS
# concerns. Edit here if the fleet's needs evolve.
$DEFAULT_TWEAKS = @(
    'libactivator',                  # Activator events used by login/start/stop/reboot scripts
    'com.fb.skiplock',                # passcode bypass for unattended display
    'veency',                         # VNC fallback (manual remote control)
    'kr.iolate.terminalactivator',    # uiopen-via-Activator (used by START script)
    'com.a3tweaks.flipswitch',        # toolkit required by skiplock
    'com.rpetrich.rocketbootstrap'    # common IPC tweak dep
)

$ErrorActionPreference = "Stop"

# --- locate tooling -------------------------------------------------------
$plink = (Get-Command plink -ErrorAction SilentlyContinue).Source
if (-not $plink) {
    foreach ($p in @("C:\Program Files\PuTTY\plink.exe", "C:\Program Files (x86)\PuTTY\plink.exe")) {
        if (Test-Path $p) { $plink = $p; break }
    }
}
if (-not $plink) { throw "plink.exe not found. Install PuTTY or add it to PATH." }

$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "C:\Windows\System32\OpenSSH\ssh.exe" }
if (-not (Test-Path $ssh)) { throw "OpenSSH client (ssh.exe) not found." }

$scp = (Get-Command scp -ErrorAction SilentlyContinue).Source
if (-not $scp) { $scp = "C:\Windows\System32\OpenSSH\scp.exe" }
# scp not strictly required (only used by apt7 bootstrap); warn rather than throw.
if (-not (Test-Path $scp)) { Write-Warning "scp.exe not found; apt7 bootstrap won't be possible." ; $scp = $null }

# Auto-discover apt7 .deb in ..\bootstrap-debs\ if -AptDeb not specified
if (-not $AptDeb) {
    $aptCandidates = Get-ChildItem -Path (Join-Path $PSScriptRoot '..\bootstrap-debs\apt7_*.deb') -ErrorAction SilentlyContinue
    if ($aptCandidates) { $AptDeb = ($aptCandidates | Sort-Object Name -Descending | Select-Object -First 1).FullName }
}
if ($AptDeb -and -not (Test-Path $AptDeb)) {
    Write-Warning "AptDeb path doesn't exist: $AptDeb -- apt7 bootstrap disabled"
    $AptDeb = ""
}

$sshKeygen = (Get-Command ssh-keygen -ErrorAction SilentlyContinue).Source
if (-not $sshKeygen) { $sshKeygen = "C:\Windows\System32\OpenSSH\ssh-keygen.exe" }
if (-not (Test-Path $sshKeygen)) { Write-Warning "ssh-keygen.exe not found; stale known_hosts entries can't be auto-cleared." ; $sshKeygen = $null }

function Clear-StaleHostKeys {
    <#  Remove any cached host-key entry for $Host on both clients we use:
          - OpenSSH known_hosts (consulted by ssh.exe / scp.exe)
          - PuTTY's registry cache (consulted by plink.exe)
        Reflashed iPads generate a new RSA host key; without this clear,
        both clients refuse to connect on the grounds of "potential MITM".
        Onboarding is explicitly trusted so dropping the old fingerprint
        is the desired behaviour. Idempotent / no-op if nothing's cached.  #>
    param([string]$HostName)

    # 1) OpenSSH known_hosts
    if ($sshKeygen) { & $sshKeygen -R $HostName 2>$null | Out-Null }

    # 2) PuTTY/plink: registry entries look like "rsa2@22:<hostname>",
    #    "ssh-ed25519@22:<hostname>", etc. Match the trailing :<hostname>.
    $putty = "HKCU:\Software\SimonTatham\PuTTY\SshHostKeys"
    if (Test-Path $putty) {
        $stale = (Get-Item $putty).Property | Where-Object { $_ -match ":${HostName}$" }
        foreach ($n in $stale) {
            Remove-ItemProperty -Path $putty -Name $n -ErrorAction SilentlyContinue
        }
    }
}

# --- resolve / generate the key -------------------------------------------
if (-not $KeyPath) { $KeyPath = Join-Path $env:USERPROFILE ".ssh\$KeyName" }
$pubPath = "$KeyPath.pub"

function New-AutomationKey([string]$path) {
    # Passphrase-less RSA key. cmd /c handles the empty -N "" argument
    # reliably across both Windows PowerShell 5.1 and PowerShell 7 (where
    # native empty-string arg passing differs/breaks).
    $dir = Split-Path $path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $gen = 'ssh-keygen -t rsa -b 2048 -C mosaicmesh-automation -f "{0}" -N "" -q' -f $path
    cmd /c $gen | Out-Null
}

if ((Test-Path $KeyPath) -and (Test-Path $pubPath)) {
    Write-Host "Using existing key: $KeyPath" -ForegroundColor DarkGray
} elseif (Test-Path $KeyPath) {
    # Private key present but .pub missing — regenerate the public half.
    Write-Host "Private key found, regenerating public key: $pubPath" -ForegroundColor Yellow
    cmd /c ('ssh-keygen -y -f "{0}" -P "" > "{1}"' -f $KeyPath, $pubPath) | Out-Null
    if (-not (Test-Path $pubPath)) { throw "Could not derive public key (is $KeyPath passphrase-protected?)." }
} else {
    Write-Host "Generating new passphrase-less key: $KeyPath" -ForegroundColor Yellow
    New-AutomationKey $KeyPath
    if (-not (Test-Path $pubPath)) { throw "Key generation failed: $pubPath not created." }
}
$pubKey = (Get-Content $pubPath -Raw).Trim()

# --- build the host list --------------------------------------------------
$targets = @()
if ($Hosts)    { $targets += $Hosts }
if ($HostFile) {
    if (-not (Test-Path $HostFile)) { throw "HostFile not found: $HostFile" }
    $targets += Get-Content $HostFile |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
}
$targets = $targets | Select-Object -Unique
if (-not $targets) { throw "No hosts given. Use -Hosts or -HostFile." }

# --- remote install command -----------------------------------------------
# ReplaceKeys: truncate authorized_keys to just this key.
# Default:     idempotent append (grep -qF skips if already present).
if ($ReplaceKeys) {
    $remoteInstall = "mkdir -p /var/root/.ssh && chmod 700 /var/root/.ssh && echo '$pubKey' > /var/root/.ssh/authorized_keys && chmod 600 /var/root/.ssh/authorized_keys && echo KEY_INSTALLED"
} else {
    $remoteInstall = "mkdir -p /var/root/.ssh && chmod 700 /var/root/.ssh && touch /var/root/.ssh/authorized_keys && grep -qF '$pubKey' /var/root/.ssh/authorized_keys || echo '$pubKey' >> /var/root/.ssh/authorized_keys && chmod 600 /var/root/.ssh/authorized_keys && echo KEY_INSTALLED"
}

$sshLegacy = @(
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes",          # only the -i key; old sshd has low MaxAuthTries
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10"
)

# --- clock / cert fix command ---------------------------------------------
# Prefer NTP (UDP, needs no valid certs); fall back to this machine's UTC.
# Handles both GNU date (`-s "ISO"`) and BSD/iOS date (positional MMDDhhmmYYYY.ss).
# Then probes CLI TLS so we can see whether cert validation recovered.
$isoUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
$bsdUtc = (Get-Date).ToUniversalTime().ToString("MMddHHmmyyyy.ss")
$clockCmd = (
    'echo WAS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u);' +
    ' SRC=;' +
    ' if command -v sntp >/dev/null 2>&1; then sntp -sS time.apple.com >/dev/null 2>&1 && SRC=sntp; fi;' +
    ' if [ -z "$SRC" ] && command -v ntpdate >/dev/null 2>&1; then ntpdate -u time.apple.com >/dev/null 2>&1 && SRC=ntpdate; fi;' +
    ' if [ -z "$SRC" ]; then ( date -u -s "__ISO__" >/dev/null 2>&1 || date -u __BSD__ >/dev/null 2>&1 ) && SRC=manual; fi;' +
    ' echo NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u) SRC=${SRC:-FAILED};' +
    ' if command -v curl >/dev/null 2>&1; then curl -sS -m 10 -o /dev/null https://www.apple.com >/dev/null 2>&1 && echo CERT=OK || echo CERT=FAIL; else echo CERT=SKIP_no_curl; fi'
) -replace '__ISO__', $isoUtc -replace '__BSD__', $bsdUtc

if ($ReplaceKeys)    { Write-Host "Mode: REPLACE (authorized_keys will contain only this key)" -ForegroundColor Magenta }
if ($FixClock)       { Write-Host "Mode: FIX-CLOCK (set time + cert probe per device)" -ForegroundColor Magenta }
if ($Timezone)       { Write-Host "Mode: SET-TIMEZONE ($Timezone)" -ForegroundColor Magenta }

# Switch to Continue from here down: native exes writing warnings to stderr
# (ssh's "Permanently added to known hosts", apt's stale-repo warnings, etc.)
# should NOT trigger terminating PowerShell errors. We check exit codes and
# parse output markers ourselves for actual success/failure.
$ErrorActionPreference = "Continue"

# Compute the package list once -- union of -Packages and (when -InstallTweaks) $DEFAULT_TWEAKS.
$pkgsToInstall = @()
if ($InstallTweaks) { $pkgsToInstall += $DEFAULT_TWEAKS }
if ($Packages)      { $pkgsToInstall += $Packages }
$pkgsToInstall = $pkgsToInstall | Select-Object -Unique
if ($pkgsToInstall) {
    Write-Host "Mode: INSTALL-PACKAGES ($($pkgsToInstall.Count): $($pkgsToInstall -join ', '))" -ForegroundColor Magenta
}

$results = @()
foreach ($h in $targets) {
    $hostName = $h; $p = $Port
    if ($h -match "^(.*):(\d+)$") { $hostName = $Matches[1]; $p = [int]$Matches[2] }

    Write-Host "`n=== $hostName`:$p ===" -ForegroundColor Cyan
    $status = "FAILED"; $detail = ""

    # 0) clear any stale host-key cache for this host (OpenSSH known_hosts +
    #    PuTTY registry). Reflashed iPads change host keys; without this both
    #    clients would refuse to connect on MITM grounds.
    Clear-StaleHostKeys -HostName $hostName

    # 1) push key via password (pipe 'y' to auto-cache host key on first contact)
    try {
        $out = ("y" | & $plink -ssh -P $p -pw $Password "$User@$hostName" $remoteInstall 2>&1) | Out-String
        if ($out -match "KEY_INSTALLED") {
            Write-Host "  key pushed" -ForegroundColor Green
        } else {
            $detail = "push: " + ($out.Trim() -replace "\s+", " ")
            Write-Host "  push failed: $detail" -ForegroundColor Yellow
            $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
            continue
        }
    } catch {
        $detail = "push exception: $($_.Exception.Message)"
        Write-Host "  $detail" -ForegroundColor Red
        $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
        continue
    }

    # 2) verify key-only auth
    try {
        $v = (& $ssh -i $KeyPath -p $p @sshLegacy -o BatchMode=yes "$User@$hostName" "echo KEYOK" 2>&1) | Out-String
        if ($v -match "KEYOK") {
            $status = "OK"
            Write-Host "  key auth verified" -ForegroundColor Green
        } else {
            $detail = "verify: " + ($v.Trim() -replace "\s+", " ")
            Write-Host "  verify failed: $detail" -ForegroundColor Yellow
        }
    } catch {
        $detail = "verify exception: $($_.Exception.Message)"
        Write-Host "  $detail" -ForegroundColor Red
    }

    # 3) clock / cert fix over the keyed session
    if ($status -eq "OK" -and $FixClock) {
        try {
            $c = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $clockCmd 2>&1) | Out-String
            $clockLines = $c -split "`r?`n" | Where-Object { $_ -match '^(WAS|NOW|CERT)=' }
            foreach ($line in $clockLines) {
                $color = if ($line -match 'CERT=FAIL|SRC=FAILED') { "Yellow" } else { "Green" }
                Write-Host "  $($line.Trim())" -ForegroundColor $color
            }
            $certLine = ($clockLines | Where-Object { $_ -match '^CERT=' }) -join ""
            if ($certLine) { $detail = ($detail + " " + $certLine.Trim()).Trim() }
        } catch {
            Write-Host "  clock/cert fix failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # 4) set timezone (symlink /etc/localtime + AppleTimeZone preference)
    if ($status -eq "OK" -and $Timezone) {
        # Validate the zone file exists on the device, symlink /etc/localtime to
        # it, AND write AppleTimeZone into .GlobalPreferences so the iOS
        # Settings UI reflects the change at next respring/boot. `defaults` ships
        # with iOS so this works without extra tools.
        $tzCmd = (
            'ZONE=/usr/share/zoneinfo/__TZ__;' +
            ' if [ -f "$ZONE" ]; then' +
            '   ln -sf "$ZONE" /etc/localtime;' +
            '   defaults write /var/mobile/Library/Preferences/.GlobalPreferences AppleTimeZone -string "__TZ__" 2>/dev/null;' +
            '   chown mobile:mobile /var/mobile/Library/Preferences/.GlobalPreferences.plist 2>/dev/null;' +
            '   echo TZ=OK NOW=$(date "+%Y-%m-%dT%H:%M:%S%z");' +
            ' else echo TZ=NOT_FOUND zone=__TZ__; fi'
        ) -replace '__TZ__', $Timezone
        try {
            $tzOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $tzCmd 2>&1) | Out-String
            $tzLines = $tzOut -split "`r?`n" | Where-Object { $_ -match '^TZ=' }
            foreach ($l in $tzLines) {
                $color = if ($l -match 'TZ=OK') { 'Green' } else { 'Yellow' }
                Write-Host "  $($l.Trim())" -ForegroundColor $color
            }
            if ($tzLines -join '' -notmatch 'TZ=OK') {
                $detail = ($detail + " tz-failed").Trim()
            }
        } catch {
            Write-Host "  timezone set failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # 4.5) bootstrap apt7 if /usr/bin/apt-get is missing (kit-IPSW omission).
    #      Check first, only install if needed -- avoids unnecessary work on
    #      iPads that already have it.
    if ($status -eq "OK" -and $AptDeb -and $scp -and $pkgsToInstall) {
        $check = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" "test -x /usr/bin/apt-get && echo APT_PRESENT || echo APT_MISSING" 2>&1) | Out-String
        if ($check -match 'APT_MISSING') {
            Write-Host "  apt-get missing -- bootstrapping apt7 via scp+dpkg" -ForegroundColor Yellow
            $remoteDeb = "/tmp/" + (Split-Path $AptDeb -Leaf)
            try {
                # scp the .deb to /tmp on the device
                & $scp -i $KeyPath -P $p @sshLegacy $AptDeb "${User}@${hostName}:$remoteDeb" 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "scp returned $LASTEXITCODE" }
                # dpkg -i it, then rm the staged file
                $dpkgOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" "dpkg -i $remoteDeb 2>&1; echo DPKG_RC=`$?; rm -f $remoteDeb" 2>&1) | Out-String
                if ($dpkgOut -match 'DPKG_RC=0') {
                    Write-Host "  apt7 installed (dpkg)" -ForegroundColor Green
                } else {
                    $rc = [regex]::Match($dpkgOut, 'DPKG_RC=\d+')
                    Write-Host "  apt7 install non-zero ($($rc.Value))" -ForegroundColor Yellow
                    $detail = ($detail + " apt7-bootstrap-failed").Trim()
                }
            } catch {
                Write-Host "  apt7 bootstrap failed: $($_.Exception.Message)" -ForegroundColor Red
                $detail = ($detail + " apt7-bootstrap-error").Trim()
            }
        } else {
            Write-Host "  apt-get already present" -ForegroundColor DarkGray
        }
    }

    # 5) install packages (Activator/Veency/SkipLock/etc.) via apt over the keyed session
    if ($status -eq "OK" -and $pkgsToInstall) {
        $pkgArg = $pkgsToInstall -join ' '
        # Same flags as sync_from_master.ps1 / ipad_apt.ps1: AllowInsecureRepositories
        # for stale-GPG forgiveness, tight per-repo timeouts so graveyard sources
        # (ModMyi, ZodTTD) can't hang the run, --force-yes for the same reason.
        $aptCmd = "apt-get update -o Acquire::AllowInsecureRepositories=true -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 -o Acquire::Retries=0 2>/dev/null || true; " +
                  "apt-get install -y --force-yes $pkgArg; echo APT_RC=`$?"
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $a = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $aptCmd 2>&1) | Out-String
        } catch {
            $a = "exception: $($_.Exception.Message)"
        }
        $ErrorActionPreference = $prevEAP

        # Surface the meaningful lines + the install RC marker
        ($a -split "`r?`n" | Where-Object {
            $_ -match '^(Get:|E:|Unable to locate|Setting up|Need to get|already the newest|newly installed|APT_RC=)'
        }) | Select-Object -First 20 | ForEach-Object {
            $line = $_.Trim()
            $color = if ($line -match '^E:|APT_RC=[^0]') { 'Yellow' } else { 'DarkGray' }
            Write-Host "  $line" -ForegroundColor $color
        }

        if ($a -match 'APT_RC=0') {
            Write-Host "  packages installed" -ForegroundColor Green
        } else {
            $rcMatch = [regex]::Match($a, 'APT_RC=\d+')
            $detail = ($detail + " " + $(if ($rcMatch.Success) { $rcMatch.Value } else { 'apt-failed' })).Trim()
            Write-Host "  package install non-zero" -ForegroundColor Yellow
        }
    }

    # 6) optional post-install (e.g. password rotation) over the keyed session
    if ($status -eq "OK" -and $PostInstall) {
        try {
            $pi = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $PostInstall 2>&1) | Out-String
            Write-Host "  post-install: $($pi.Trim())" -ForegroundColor Green
        } catch {
            Write-Host "  post-install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
}

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$ok = @($results | Where-Object { $_.Status -eq "OK" }).Count
Write-Host "$ok / $($results.Count) device(s) onboarded with key '$KeyPath'." -ForegroundColor Cyan
