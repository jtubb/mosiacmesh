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
    [string]$AptDeb = "",
    # Suppress the post-install respring. By default we killall SpringBoard
    # after a successful package install because MobileSubstrate only injects
    # tweaks at SpringBoard launch -- without a respring, freshly-installed
    # libactivator/veency/skiplock are inert (.dylibs on disk but not loaded).
    # Use this only if you're scripting multiple installs and want to defer
    # the respring to the end.
    [switch]$NoRespring,
    # Skip the autolock-disable step. By default, when -InstallTweaks is set
    # we permanently disable autolock (SBAutoLockTime = 0) so iOS 5 doesn't
    # sleep the screen + WiFi, which would make the iPad unreachable for
    # lifecycle scripts (login/start/stop/reboot can't deliver if WiFi is off).
    # Use this if you're onboarding a non-fleet iPad where you want normal
    # autolock behaviour.
    [switch]$KeepAutoLock,
    # The MosaicMesh display URL to open in Safari as the last onboarding
    # step. The websocket the page opens keeps the iPad's WiFi radio in
    # active mode (vs power-save) which is what makes the iPad reachable
    # for lifecycle scripts. Matches server.py's DISPLAY_URL by default.
    [string]$DisplayUrl = "http://192.168.1.60:3000/",
    # Skip the final "open Safari to DisplayUrl" step. Use if you want to
    # onboard without immediately joining the mesh (manual control over
    # when the device joins).
    [switch]$NoOpenDisplay,
    # Veency (VNC) configuration. By default the kit IPSW installs veency
    # with Prompt=true and no password -- meaning every incoming VNC
    # connection pops an "Accept / Reject" dialog on the iPad itself,
    # which you have to tap. Useless for a video wall. Setting -VncPassword
    # writes the veency plist with Prompt=false (auto-accept) AND sets
    # this password as VNC auth, so the prompt is bypassed but unauth'd
    # connections still can't get in. Empty string = disable the prompt
    # only, no password (LAN-trusted setup). Use -SkipVncConfig to leave
    # the defaults alone (you'll see the accept prompt every connection).
    [string]$VncPassword = "mosaicmesh",
    # Skip Veency plist write entirely (leave veency defaults: Prompt=true,
    # no password). Useful if you've already configured it manually.
    [switch]$SkipVncConfig
)

# Canonical MosaicMesh tweak set -- everything our scripts rely on plus the
# tweaks that make manual ops sane. All sourced from BigBoss/Saurik/ModMyi (HTTP)
# so no TLS concerns. Edit here if the fleet's needs evolve.
#
# Includes explicit dependencies that aren't in the kit's IPSW baseline:
# apt-get usually auto-resolves these, but on a stripped-down IPSW it needs
# explicit pinning (otherwise it says "X but it is not going to be installed").
$DEFAULT_TWEAKS = @(
    # --- direct functional dependencies of our scripts ---
    'libactivator',                  # Activator events for login/start/stop/reboot scripts
    'com.fb.skiplock',                # passcode bypass for unattended display
    'veency',                         # VNC fallback (manual remote control)
    'kr.iolate.terminalactivator',    # uiopen-via-Activator (used by START script)
    'com.a3tweaks.flipswitch',        # toolkit required by skiplock
    'com.rpetrich.rocketbootstrap',   # common IPC tweak dep
    # --- WiFi keepalive (defends against iOS 5 power-save) ---
    # Insomnia v6 by imalc (BigBoss). MobileSubstrate hook into the
    # screen-lock handler that prevents the WiFi radio from entering its
    # power-save state when the screen is off (or the device is idle on
    # the index page with no recent input). Without this, iPads showing
    # 50%+ packet loss + 700ms RTT mid-day -- "request timed out" in
    # Safari, SSH "connect to host : Connection timed out" from the
    # server -- become unmanageable. Deps already in this list
    # (mobilesubstrate, preferenceloader, libstatusbar). Confirmed
    # compatible with iOS 5.x via the package's firmware<<7.0 bound.
    'com.imalc.insomnia',
    # --- transitive deps the kit IPSW left out ---
    'berkeleydb',                    # required by apt7 (the CLI we dpkg-bootstrap)
    'preferenceloader',              # required by libactivator/veency/terminalactivator
    'libstatusbar',                  # required by veency on iOS >= 4
    'jp.ashikase.mousesupport',      # required by veency on iOS >= 3
    'com.saurik.iphone.ske',         # required by veency on iOS < 7 (the "firmware fallback")
    # --- per-device media cache (2026-06-03) ---
    # lighttpd serves /var/mobile/Media/MosaicMeshCache/ at
    # http://127.0.0.1:8080/ -- per-iPad pre-rendered video segments
    # play from local disk instead of competing for shared WiFi
    # bandwidth. Deps (pcre, libxml2, sqlite3, bzip2) all resolve
    # from Saurik's repo which is already configured on these iPads.
    # Onboarding steps 5.4d/5.4e/5.4f below write the config plist,
    # the LaunchDaemon plist, and the server-side cacheMode flag.
    # See docs/superpowers/specs/2026-06-03-media-cache-design.md.
    'lighttpd'
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

function Cache-PuttyHostKey {
    <#  Pre-populate PuTTY's host-key registry cache for $HostName so plink
        doesn't prompt with "Store key in cache? y/n" on first contact.

        Why: modern plink reads that prompt from the Windows console
        (CONIN$), not stdin -- piping "y" doesn't reach it. Pre-populating
        the cache means plink finds a known key and never prompts.

        How: ssh-keyscan grabs the host's ssh-rsa public key (no auth needed,
        same legacy-crypto flags we use elsewhere), we parse the SSH wire
        format to extract exponent + modulus, convert to PuTTY's
        `0x<exp>,0x<mod>` registry value, and write it as `rsa2@<port>:<host>`.

        Returns $true on success, $false if ssh-keyscan can't reach the host
        or the format isn't parseable.  #>
    param([string]$HostName, [int]$Port = 22)

    $sshKeyscan = (Get-Command ssh-keyscan -ErrorAction SilentlyContinue).Source
    if (-not $sshKeyscan) { $sshKeyscan = "C:\Windows\System32\OpenSSH\ssh-keyscan.exe" }
    if (-not (Test-Path $sshKeyscan)) { return $false }

    # Windows OpenSSH ssh-keyscan doesn't support the long `-o option=value`
    # form (only `-O`, `-T`, `-t`, `-p`). It also doesn't need
    # HostKeyAlgorithms=+ssh-rsa because asking for `-t ssh-rsa` already
    # narrows the probe to the algorithm iOS 5 offers. -T is the connect
    # timeout (seconds).
    $keyOut = (& $sshKeyscan -t ssh-rsa -p $Port -T 10 $HostName 2>$null) | Out-String
    $line = ($keyOut -split "`r?`n" | Where-Object { $_ -match '\bssh-rsa\s+(\S+)' } | Select-Object -First 1)
    if (-not $line) { return $false }
    if ($line -notmatch '\bssh-rsa\s+(\S+)') { return $false }
    $b64 = $matches[1]

    try { $bytes = [Convert]::FromBase64String($b64) } catch { return $false }

    # SSH wire format for ssh-rsa pubkey:
    #   uint32 len | "ssh-rsa" | uint32 len | exponent | uint32 len | modulus
    # All length prefixes are big-endian. mpint values may have a leading 0x00
    # to indicate positive sign (strip when converting to PuTTY hex).
    function _be_uint32($b, $off) {
        return ([uint32]$b[$off] -shl 24) -bor ([uint32]$b[$off+1] -shl 16) `
            -bor ([uint32]$b[$off+2] -shl 8) -bor [uint32]$b[$off+3]
    }
    function _read_field([byte[]]$b, [ref]$off) {
        $len = _be_uint32 $b $off.Value
        $off.Value += 4
        $data = New-Object byte[] $len
        [Array]::Copy($b, $off.Value, $data, 0, $len)
        $off.Value += $len
        return ,$data
    }

    $off = 0
    try {
        $nameField = _read_field $bytes ([ref]$off)
        $expBytes  = _read_field $bytes ([ref]$off)
        $modBytes  = _read_field $bytes ([ref]$off)
    } catch { return $false }

    # Strip leading-zero sign byte from modulus (and exponent, defensively)
    while ($modBytes.Count -gt 1 -and $modBytes[0] -eq 0) {
        $modBytes = $modBytes[1..($modBytes.Count - 1)]
    }
    while ($expBytes.Count -gt 1 -and $expBytes[0] -eq 0) {
        $expBytes = $expBytes[1..($expBytes.Count - 1)]
    }

    $hex = { param($bs) -join ($bs | ForEach-Object { '{0:x2}' -f $_ }) }
    $expHex = (& $hex $expBytes).TrimStart('0'); if (-not $expHex) { $expHex = '0' }
    $modHex = (& $hex $modBytes).TrimStart('0'); if (-not $modHex) { $modHex = '0' }
    $value = "0x$expHex,0x$modHex"

    $puttyKey = "HKCU:\Software\SimonTatham\PuTTY\SshHostKeys"
    if (-not (Test-Path $puttyKey)) { New-Item -Path $puttyKey -Force | Out-Null }
    Set-ItemProperty -Path $puttyKey -Name "rsa2@${Port}:${HostName}" -Value $value
    return $true
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
if (-not $NoOpenDisplay -and $InstallTweaks) {
    Write-Host "Mode: OPEN-DISPLAY ($DisplayUrl) after onboarding" -ForegroundColor Magenta
}

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
if ($pkgsToInstall -and -not $SkipVncConfig) {
    $vncSummary = if ($VncPassword) { "veency prompt off + password='$VncPassword'" }
                  else { "veency prompt off (no password)" }
    Write-Host "Mode: CONFIGURE-VEENCY ($vncSummary)" -ForegroundColor Magenta
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

    # 0.5) pre-populate PuTTY's host-key cache via ssh-keyscan so plink doesn't
    #      prompt "Store key in cache? y/n" on first contact -- modern plink
    #      reads that prompt from CONIN$ (Windows console), so stdin pipes can't
    #      answer it. ssh-keyscan grabs the key without auth, we parse + write
    #      to the registry, plink finds the key cached and never prompts.
    if (Cache-PuttyHostKey -HostName $hostName -Port $p) {
        Write-Host "  host key cached" -ForegroundColor DarkGray
    } else {
        Write-Host "  host key cache failed (ssh-keyscan unreachable?) -- plink may prompt" -ForegroundColor DarkYellow
    }

    # 1) push key via password.
    #
    #    The host-key prompt ("Store key in cache? y/n") is answered via
    #    `echo y | plink` -- piped through cmd.exe rather than PowerShell.
    #    PowerShell's object-pipe (`"y" | & $plink`) does NOT reliably reach
    #    plink's stdin in modern PuTTY versions (stricter about
    #    pipe-vs-tty discrimination), so plink blocks at the prompt. cmd's
    #    native pipe handles it correctly across plink versions.
    #
    #    Retry up to 4 times -- iOS 5's WiFi power-save can drop the SSH
    #    banner handshake mid-exchange if the radio dozes off between
    #    TCP-accept and SSH negotiation. Each retry is ~5s pause so the
    #    radio gets a chance to be in active mode.
    $pushed = $false
    # Build the plink command line for cmd.exe. Quote the password (may contain
    # spaces). The remote shell command is the LAST positional arg.
    $plinkCmd = "echo y | `"$plink`" -ssh -P $p -pw `"$Password`" $User@$hostName `"$($remoteInstall -replace '"','\\""')`""
    for ($try = 1; $try -le 4; $try++) {
        try {
            $out = (& cmd /c "$plinkCmd 2>&1") | Out-String
            if ($out -match "KEY_INSTALLED") {
                $marker = if ($try -gt 1) { " (attempt $try)" } else { "" }
                Write-Host "  key pushed$marker" -ForegroundColor Green
                $pushed = $true; break
            } else {
                $reason = ($out.Trim() -replace "\s+", " ")
                Write-Host "  push attempt $try failed: $reason" -ForegroundColor DarkYellow
                $detail = "push: $reason"
            }
        } catch {
            $detail = "push exception: $($_.Exception.Message)"
            Write-Host "  push attempt $try exception: $($_.Exception.Message)" -ForegroundColor DarkYellow
        }
        if ($try -lt 4) { Start-Sleep -Seconds 5 }
    }
    if (-not $pushed) {
        Write-Host "  push failed after 4 attempts: $detail" -ForegroundColor Red
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

    # 4) set timezone. iOS uses /var/db/timezone/localtime as the PRIMARY tz
    #    lookup (not /etc/localtime -- standard Unix path is essentially
    #    ignored). We symlink BOTH so all date/time-aware tools find the right
    #    zone, AND try to write AppleTimeZone for iOS Settings UI display
    #    (the latter needs `defaults` which the kit IPSW doesn't ship, so it
    #    fails gracefully -- system clock is what matters for our use case).
    if ($status -eq "OK" -and $Timezone) {
        $tzCmd = (
            'ZONE=/usr/share/zoneinfo/__TZ__;' +
            ' if [ -f "$ZONE" ]; then' +
            '   ln -sf "$ZONE" /var/db/timezone/localtime;' +    # iOS primary tz path
            '   ln -sf "$ZONE" /etc/localtime;' +                # standard Unix path
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
        #
        # The `apt-get -f install` step repairs apt7's "installed but unconfigured"
        # state (left by the dpkg bootstrap when berkeleydb wasn't yet available)
        # and resolves any other broken deps before the main install.
        $aptCmd = "apt-get update -o Acquire::AllowInsecureRepositories=true -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 -o Acquire::Retries=0 2>/dev/null || true; " +
                  "apt-get -f install -y --force-yes 2>&1; " +
                  "apt-get install -y --force-yes $pkgArg; echo APT_RC=`$?"
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $a = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $aptCmd 2>&1) | Out-String
        } catch {
            $a = "exception: $($_.Exception.Message)"
        }
        $ErrorActionPreference = $prevEAP

        # Surface the meaningful lines + the install RC marker.
        # Include 'Depends:' / 'unmet' / 'not going to be installed' so dep-resolution
        # failures don't hide -- silent failure here was the bug we just fixed.
        ($a -split "`r?`n" | Where-Object {
            $_ -match '^(Get:|E:|W:|Unable to locate|Setting up|Need to get|already the newest|newly installed|Depends:|not going to be installed|unmet dependencies|APT_RC=)'
        }) | Select-Object -First 40 | ForEach-Object {
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

    # 5.4) disable autolock + auto-open MosaicMesh at every boot, via a
    #      LaunchDaemon that runs `activator send switch-off ... autolock`
    #      + `uiopen $DisplayUrl` ~30s after each boot.
    #
    #      We tried `defaults write SBAutoLockTime -int 0` here originally,
    #      but `defaults` doesn't ship on the Legacy-iOS-Kit IPSW (only
    #      `apt7-lib` etc., not the CLI front-end). So the plist write was
    #      a silent no-op and the only thing setting autolock=Never was
    #      manual Settings-UI taps -- meaning re-flashed iPads were going
    #      back to sleep on next reboot.
    #
    #      The LaunchDaemon approach uses activator (which IS installed,
    #      part of libactivator) for the autolock toggle, and uiopen
    #      (part of uikittools) to re-join the mesh. Both come up on
    #      every boot, no manual intervention required.
    if ($status -eq "OK" -and $pkgsToInstall -and -not $KeepAutoLock) {
        # Heredoc-write the plist, chown root:wheel, set 644 perms. launchctl
        # load won't work over SSH (Socket-not-connected mach-port issue --
        # SSH session isn't in the right launchd bootstrap context). That's
        # fine: launchd reads /Library/LaunchDaemons at every boot, so the
        # daemon registers automatically on next reboot. We also fire the
        # commands inline below so the iPad is in the right state NOW too.
        $plistPath = '/Library/LaunchDaemons/com.mosaicmesh.autolock-off.plist'
        # Prefer sbdidlaunch (launches the MosaicMesh webclip in WEBAPP
        # MODE -- chrome-less, fullscreen, no Safari URL bar) and fall
        # back to uiopen (Safari) if the webclip isn't installed or
        # sbdidlaunch isn't available on this device. The webclip's
        # bundle id is com.apple.webapp-<32-hex UUID>, and our
        # tools/mosaicmesh.webclip.Info.plist + step 5.4g pin a stable
        # UUID across the fleet so the daemon's command is identical
        # everywhere. See docs/superpowers/specs/2026-06-03-cache-
        # progress-and-propagation-ui.md "Known limitation" for why
        # webapp-mode matters for kiosk operation.
        $webclipBid = 'com.apple.webapp-4D6F736169634D6573684B696F736B31'
        $daemonScript = "sleep 30; /usr/bin/activator send switch-off.com.a3tweaks.switch.autolock; sleep 2; /usr/bin/sbdidlaunch $webclipBid 2>/dev/null || /usr/bin/uiopen $DisplayUrl"
        $writeDaemon = (
            "cat > $plistPath << 'PLIST'`n" +
            "<?xml version=`"1.0`" encoding=`"UTF-8`"?>`n" +
            "<!DOCTYPE plist PUBLIC `"-//Apple//DTD PLIST 1.0//EN`" `"http://www.apple.com/DTDs/PropertyList-1.0.dtd`">`n" +
            "<plist version=`"1.0`">`n" +
            "<dict>`n" +
            "    <key>Label</key><string>com.mosaicmesh.autolock-off</string>`n" +
            "    <key>ProgramArguments</key>`n" +
            "    <array>`n" +
            "        <string>/bin/sh</string>`n" +
            "        <string>-c</string>`n" +
            "        <string>$daemonScript</string>`n" +
            "    </array>`n" +
            "    <key>RunAtLoad</key><true/>`n" +
            "</dict>`n" +
            "</plist>`n" +
            "PLIST`n" +
            "chown root:wheel $plistPath; chmod 644 $plistPath;" +
            " ls -la $plistPath 2>&1; " +
            " /usr/bin/activator send switch-off.com.a3tweaks.switch.autolock 2>&1;" +
            " echo AUTOLOCK_DAEMON_INSTALLED"
        )
        try {
            $alOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $writeDaemon 2>&1) | Out-String
            if ($alOut -match 'AUTOLOCK_DAEMON_INSTALLED') {
                Write-Host "  autolock-off LaunchDaemon installed (fires every boot)" -ForegroundColor Green
            } else {
                Write-Host "  autolock-daemon write unexpected: $($alOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  autolock-daemon install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # 5.4b) configure Veency (the VNC tweak) BEFORE the respring so the new
    #       settings take effect when SpringBoard restarts and reloads
    #       MobileSubstrate-injected tweaks.
    #
    #       Veency's plist lives at
    #           /var/mobile/Library/Preferences/com.saurik.Veency.plist
    #       and is owned by mobile:mobile. Keys we care about:
    #         Enabled (bool)  -- VNC server on/off (default true after install)
    #         Prompt  (bool)  -- show Accept/Reject dialog on each connection
    #         Password (string) -- VNC auth password; empty = no auth
    #
    #       For a 24-iPad video wall Prompt=true is unusable (you'd have to
    #       walk over and tap accept on every device every connection). We
    #       always set Prompt=false here. If $VncPassword is non-empty we
    #       set the password too so unauth'd LAN clients still can't connect.
    if ($status -eq "OK" -and $pkgsToInstall -and -not $SkipVncConfig) {
        $veencyPlistPath = '/var/mobile/Library/Preferences/com.saurik.Veency.plist'
        # If $VncPassword is empty, we still write the plist but with no
        # <key>Password</key> entry -- veency reads it as "no password set".
        $passwordEntry = ''
        if ($VncPassword) {
            # Escape any XML-significant chars in the password so the plist
            # parses. Backslash and single/double quotes are also safe with
            # XML escaping (we don't shell-interpolate the password value;
            # it goes straight into the heredoc literally via cat).
            $pwXml = $VncPassword -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;'
            $passwordEntry = "    <key>Password</key><string>$pwXml</string>`n"
        }
        $writeVeency = (
            "cat > $veencyPlistPath << 'PLIST'`n" +
            "<?xml version=`"1.0`" encoding=`"UTF-8`"?>`n" +
            "<!DOCTYPE plist PUBLIC `"-//Apple//DTD PLIST 1.0//EN`" `"http://www.apple.com/DTDs/PropertyList-1.0.dtd`">`n" +
            "<plist version=`"1.0`">`n" +
            "<dict>`n" +
            "    <key>Enabled</key><true/>`n" +
            "    <key>Prompt</key><false/>`n" +
            $passwordEntry +
            "    <key>ShowCursor</key><true/>`n" +
            "</dict>`n" +
            "</plist>`n" +
            "PLIST`n" +
            "chown mobile:mobile $veencyPlistPath; chmod 644 $veencyPlistPath;" +
            " echo VEENCY_CONFIGURED"
        )
        try {
            $vOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $writeVeency 2>&1) | Out-String
            if ($vOut -match 'VEENCY_CONFIGURED') {
                $detailMsg = if ($VncPassword) { "veency: prompt off, password set" }
                             else { "veency: prompt off, no password" }
                Write-Host "  $detailMsg" -ForegroundColor Green
            } else {
                Write-Host "  veency config unexpected: $($vOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  veency config failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # 5.4c) configure Insomnia (the WiFi-keepalive tweak) BEFORE the
    #       respring so it loads ENABLED on the first SpringBoard launch.
    #
    #       Insomnia ships with NO defaults plist; without one the dylib
    #       loads but treats Enabled as nil (= false) and does nothing.
    #       Symptom: iPads' WiFi enters power-save when screen is off,
    #           ping shows 25-33% loss and 300-700ms RTT, GoTime probes
    #           drop, ProgrammableTimer.isSynced() never converges,
    #           the client never emits SYNACK, and the server's
    #           wait_for_reconnect gate times out at N/M iPads.
    #
    #       The plist lives at
    #           /var/mobile/Library/Preferences/com.malcolmhall.Insomnia.plist
    #       (note: bundle id is com.malcolmhall.Insomnia despite the
    #       Cydia package being com.imalc.insomnia -- the package author's
    #       imalc handle is the alias, malcolmhall is the actual identifier).
    #       The single key we need is `Enabled` = bool; extracted from
    #       strings on the dylib + InsomniaSettings prefs binary (2026-06-02).
    #       Owner: mobile:mobile, mode 644 (standard for user-domain prefs).
    #
    #       Deployed via scp from tools/com.malcolmhall.Insomnia.plist
    #       (same fix as 5.4d/5.4e): the earlier inline-heredoc with
    #       backtick-escaped double quotes intermittently lost quotes
    #       during PowerShell -> ssh -> bash argv flattening. An
    #       unquoted-attribute plist parses as nil in
    #       NSPropertyListSerialization, leaving Enabled=false and
    #       Insomnia inert. Confirmed on .69 (Jun 3 09:15 onboarding)
    #       which had a 213-byte plist with no quotes; subsequent
    #       passes that happened to keep quotes intact landed 223
    #       bytes correctly. Shipping the file removes the
    #       non-determinism.
    if ($status -eq "OK" -and $pkgsToInstall -and $scp) {
        $insomniaPlistPath = '/var/mobile/Library/Preferences/com.malcolmhall.Insomnia.plist'
        $insomniaPlistSrc = Join-Path $PSScriptRoot 'com.malcolmhall.Insomnia.plist'
        if (-not (Test-Path $insomniaPlistSrc)) {
            Write-Host "  insomnia plist: source file missing at $insomniaPlistSrc" -ForegroundColor Yellow
        } else {
            try {
                & $scp -i $KeyPath -P $p @sshLegacy $insomniaPlistSrc "${User}@${hostName}:$insomniaPlistPath" 2>&1 | Out-Null
                # Fix ownership (scp lands files as root:wheel; the
                # plist needs mobile:mobile so SpringBoard's
                # preference daemon can read+rewrite it as the
                # mobile user) and verify the quoted-attribute
                # signature on disk.
                # scp preserves byte-identical content, so file
                # presence + non-empty is a sufficient verify. We
                # deliberately avoid embedding double quotes in the
                # ssh command body here -- the whole point of this
                # refactor is to never send quotes through the
                # PowerShell -> ssh.exe -> bash pipeline again.
                $fixOwn = (
                    "chown mobile:mobile $insomniaPlistPath;" +
                    " chmod 644 $insomniaPlistPath;" +
                    " if [ -s $insomniaPlistPath ];" +
                    " then echo INSOMNIA_CONFIGURED;" +
                    " else echo INSOMNIA_EMPTY;" +
                    " fi"
                )
                $iOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $fixOwn 2>&1) | Out-String
                if ($iOut -match 'INSOMNIA_CONFIGURED') {
                    Write-Host "  insomnia: enabled (WiFi stays awake when screen is off)" -ForegroundColor Green
                } elseif ($iOut -match 'INSOMNIA_EMPTY') {
                    Write-Host "  insomnia: file empty after scp -- rerun" -ForegroundColor Yellow
                } else {
                    Write-Host "  insomnia config unexpected: $($iOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  insomnia config failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }

    # 5.4d) deploy /etc/lighttpd/lighttpd.conf via scp from the local
    #       repo (tools/lighttpd.conf). Earlier version of this step
    #       built the file via a PowerShell -> ssh -> bash heredoc, but
    #       PowerShell's backtick-escaping for inner double-quotes was
    #       stripped during ssh.exe argument parsing, so the file landed
    #       on the iPad with NO quotes around any string literal -> the
    #       lighttpd config parser failed -> daemon couldn't start.
    #       scp-from-local is the bullet-proof path: same content goes
    #       on disk byte-for-byte as what we ship in the repo.
    if ($status -eq "OK" -and $pkgsToInstall -and $scp) {
        $confSrc = Join-Path $PSScriptRoot 'lighttpd.conf'
        if (-not (Test-Path $confSrc)) {
            Write-Host "  lighttpd config: source file missing at $confSrc" -ForegroundColor Yellow
        } else {
            try {
                # Make the dirs + cache dir on the iPad first; then scp the
                # config. (lighttpd's config-validate happens at start time,
                # not at file-arrival time, so order matters only in that
                # the dirs must exist before lighttpd reads .pid-file etc.)
                $mkPrep = "mkdir -p /etc/lighttpd /var/log /var/run /var/mobile/Media/MosaicMeshCache; chown mobile:mobile /var/mobile/Media/MosaicMeshCache; echo LIGHTTPD_DIRS_OK"
                & $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $mkPrep 2>&1 | Out-Null
                # Push the config. -O disables the new sftp-based scp protocol
                # which OpenSSH 8.x defaults to but the iPad's old sshd may
                # not implement. (If our ssh build doesn't have -O it'll
                # silently complain; the fallback to legacy scp still works.)
                & $scp -i $KeyPath -P $p @sshLegacy $confSrc "${User}@${hostName}:/etc/lighttpd/lighttpd.conf" 2>&1 | Out-Null
                # Verify by counting expected string-literal lines on the iPad
                $verifyCmd = 'grep -c "^server\." /etc/lighttpd/lighttpd.conf'
                $vOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $verifyCmd 2>&1) | Out-String
                # Expect at least 6 server.* lines (modules, document-root,
                # bind, port, errorlog, pid-file). If we see them, the file
                # didn't get mangled.
                if ($vOut -match '\b([6-9]|[1-9][0-9])\b') {
                    Write-Host "  lighttpd config: pushed via scp ($confSrc)" -ForegroundColor Green
                } else {
                    Write-Host "  lighttpd config verification unexpected: $($vOut.Trim())" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  lighttpd config failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }

    # 5.4e) deploy the LaunchDaemon plist (scp from local) so lighttpd
    #       starts at every boot AND auto-respawns if killed, then exec
    #       lighttpd directly to make it available immediately for this
    #       onboarding pass.
    #
    #       Two bugs fixed here vs. the previous version:
    #
    #       (1) The plist used to be built with the same heredoc-with-
    #           backtick-escaped-quotes pattern as 5.4d and suffered
    #           the same quote-stripping problem (ssh.exe re-flattens
    #           argv and loses the backticks), so the file landed with
    #           broken XML. Fixed by shipping tools/com.mosaicmesh.
    #           lighttpd.plist and scp'ing it byte-for-byte.
    #
    #       (2) `launchctl load /Library/LaunchDaemons/...` was used
    #           to start the daemon immediately, but on iOS 5 launchctl-
    #           over-SSH fails with "launch_msg(): Socket is not
    #           connected" because the SSH session isn't connected to
    #           launchd's bootstrap domain. The plist still lands on
    #           disk for the boot-time path (launchd reads /Library/
    #           LaunchDaemons at boot, finds it, RunAtLoad fires it,
    #           KeepAlive respawns on crash). To start lighttpd RIGHT
    #           NOW we just exec the daemon directly. Without -D flag
    #           lighttpd daemonizes itself so the SSH command returns;
    #           the pid file confirms success.
    if ($status -eq "OK" -and $pkgsToInstall -and $scp) {
        $plistSrc = Join-Path $PSScriptRoot 'com.mosaicmesh.lighttpd.plist'
        if (-not (Test-Path $plistSrc)) {
            Write-Host "  lighttpd plist: source file missing at $plistSrc" -ForegroundColor Yellow
        } else {
            try {
                # /Library/LaunchDaemons always exists on iOS but be
                # defensive in case anyone forks this for another
                # Darwin variant.
                & $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" "mkdir -p /Library/LaunchDaemons" 2>&1 | Out-Null
                & $scp -i $KeyPath -P $p @sshLegacy $plistSrc "${User}@${hostName}:/Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist" 2>&1 | Out-Null
                # chmod + kill stale + start fresh + report pid. Kept
                # as one ssh round-trip to keep onboarding fast on
                # slow links.
                $startCmd = (
                    "chmod 644 /Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist;`n" +
                    "killall lighttpd 2>/dev/null;`n" +
                    "sleep 1;`n" +
                    "/usr/sbin/lighttpd -f /etc/lighttpd/lighttpd.conf;`n" +
                    "sleep 2;`n" +
                    "if [ -f /var/run/lighttpd.pid ]; then`n" +
                    "  echo LIGHTTPD_OK pid=`$(cat /var/run/lighttpd.pid);`n" +
                    "else`n" +
                    "  echo LIGHTTPD_NO_PID;`n" +
                    "fi"
                )
                $lOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $startCmd 2>&1) | Out-String
                if ($lOut -match 'LIGHTTPD_OK pid=(\d+)') {
                    Write-Host "  lighttpd: running pid=$($Matches[1]); LaunchDaemon plist set for boot" -ForegroundColor Green
                } else {
                    Write-Host "  lighttpd start: $($lOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  lighttpd LaunchDaemon failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }

    # 5.4f) mark this client as lighttpd-localhost cacheMode on the
    #       server side so future PLAY payloads route to the iPad's
    #       localhost lighttpd. Requires that the iPad has REGISTERed
    #       with the server at least once (so settings.clients has
    #       an entry for it). Onboarding usually triggers a REGISTER
    #       via step 7 (open MosaicMesh page); for fresh-imaged iPads
    #       you may need a second onboarding pass.
    if ($status -eq "OK" -and $pkgsToInstall) {
        try {
            # Look up the iPad's clientKey via /api/discovery/devices.
            # The API exposes each client by its registered IP (e.g.
            # "192.168.1.70"), so we have to translate $hostName ->
            # IP before the lookup. Without this, -HostFile entries
            # like "sign1screen4.home.lan" never matched the API's
            # numeric IP and every device printed a false
            # "no clientKey ... yet" warning even though it had
            # registered fine.
            #
            # Resolve via .NET DNS (works for mDNS .local/.home.lan
            # entries on Windows too). If $hostName was already an
            # IP, GetHostAddresses round-trips it through.
            $hostIP = $hostName
            try {
                $resolved = [System.Net.Dns]::GetHostAddresses($hostName) |
                    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                    Select-Object -First 1
                if ($resolved) { $hostIP = $resolved.IPAddressToString }
            } catch {
                # Resolution failed; fall through with $hostIP = $hostName
                # so the API match still works for raw-IP -Hosts callers.
            }

            $devs = Invoke-RestMethod -Uri "http://192.168.1.60:3000/api/discovery/devices" -TimeoutSec 5
            $devList = if ($devs.devices) { $devs.devices } else { $devs }
            $thisDev = $devList | Where-Object { $_.ip -eq $hostIP } | Select-Object -First 1
            if ($thisDev -and $thisDev.clientKey) {
                $body = @{
                    action = "set_cache_mode"
                    clientKey = $thisDev.clientKey
                    mode = "lighttpd-localhost"
                } | ConvertTo-Json -Compress
                $resp = Invoke-RestMethod -Uri "http://192.168.1.60:3000/api/discovery/configure" `
                    -Method POST -ContentType "application/json" -Body $body -TimeoutSec 5
                # api_discovery_configure returns {"success": true} on
                # every success branch (see server.py:4182). An earlier
                # version of this script checked $resp.status -eq
                # "SUCCESS" instead, which printed a false "unexpected"
                # warning even though the server-side mode update was
                # applied. Check the actual response shape.
                if ($resp.success -eq $true) {
                    Write-Host "  cacheMode: server marked $($thisDev.clientKey) as lighttpd-localhost" -ForegroundColor Green
                } else {
                    Write-Host "  cacheMode response unexpected: $($resp | ConvertTo-Json -Compress)" -ForegroundColor Yellow
                }

                # Also bring the server-side startScript in sync with
                # the webclip we just installed (step 5.4g). Without
                # this, existing clients keep whatever startScript was
                # baked in at their first onboarding -- typically the
                # old `uiopen ...; echo START_OK` form -- and the admin
                # "Start" action launches Safari instead of webapp
                # mode, even though the iPad has the webclip ready to
                # go. We push the explicit sbdidlaunch+uiopen-fallback
                # string here, matching server.py's
                # DEFAULT_DEVICE_SCRIPTS["startScript"]. Keeping it as a
                # PowerShell-side literal (not derived from the server)
                # so the script is self-contained for fleet migrations
                # without restarting the server.
                # startScript is the SSH-exec fallback when the
                # primary VNC-tap launch path fails (Veency
                # unreachable, screen unresponsive). The primary path
                # is server.py's _launch_webapp_via_vnc which taps
                # the home-screen icon (matches the only working
                # webclip-launch flow on iOS 5). See commit 5569318.
                $newStartScript = "sbdidlaunch 'com.apple.webapp-4D6F736169634D6573684B696F736B31' 2>/dev/null" +
                                  " || uiopen '$DisplayUrl'; echo START_OK"
                $startBody = @{
                    clientKey = $thisDev.clientKey
                    startScript = $newStartScript
                } | ConvertTo-Json -Compress
                $startResp = Invoke-RestMethod -Uri "http://192.168.1.60:3000/api/discovery/configure" `
                    -Method POST -ContentType "application/json" -Body $startBody -TimeoutSec 5
                if ($startResp.success -eq $true) {
                    Write-Host "  startScript: server-side updated to sbdidlaunch (webapp mode)" -ForegroundColor Green
                } else {
                    Write-Host "  startScript update unexpected: $($startResp | ConvertTo-Json -Compress)" -ForegroundColor Yellow
                }

                # stopScript: kill the Web.app webclip (the display
                # client since 2026-06-03's webapp-mode rollout) plus
                # MobileSafari (legacy / Safari-fallback path), then
                # autolock + sleep. Same belt-and-suspenders kill
                # pattern as server.py's DEFAULT_DEVICE_SCRIPTS, kept
                # PowerShell-side so the onboarding script is
                # self-contained.
                $newStopScript = "killall Web 2>/dev/null; " +
                                 "killall MobileSafari 2>/dev/null; " +
                                 "activator send switch-on.com.a3tweaks.switch.autolock; " +
                                 "activator send libactivator.system.sleepbutton; echo STOP_OK"
                $stopBody = @{
                    clientKey = $thisDev.clientKey
                    stopScript = $newStopScript
                } | ConvertTo-Json -Compress
                $stopResp = Invoke-RestMethod -Uri "http://192.168.1.60:3000/api/discovery/configure" `
                    -Method POST -ContentType "application/json" -Body $stopBody -TimeoutSec 5
                if ($stopResp.success -eq $true) {
                    Write-Host "  stopScript: server-side updated (kills Web + MobileSafari)" -ForegroundColor Green
                } else {
                    Write-Host "  stopScript update unexpected: $($stopResp | ConvertTo-Json -Compress)" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  cacheMode: no clientKey for ip=$hostIP (host=$hostName) in discovery API yet" -ForegroundColor DarkYellow
                Write-Host "                (run onboarding again after iPad first REGISTERs)" -ForegroundColor DarkYellow
            }
        } catch {
            Write-Host "  cacheMode set failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # 5.4g) install MosaicMesh as a home-screen webclip so the iPad
    #       can launch the display page in WEBAPP MODE (no Safari
    #       chrome, fullscreen across script + video transitions).
    #       SpringBoard auto-discovers webclips in /var/mobile/Library/
    #       WebClips on its next launch; step 5.5's killall SpringBoard
    #       below makes the new icon appear without an extra restart.
    #
    #       The Info.plist template (tools/mosaicmesh.webclip.Info.plist)
    #       has a __URL__ placeholder that we sed-substitute with the
    #       $DisplayUrl parameter on the iPad after scp -- same idiom
    #       as the lighttpd config (avoid the bash-heredoc quote-loss
    #       bug from earlier in the session).
    #
    #       Once installed, the icon appears but iOS does NOT auto-
    #       launch webclips; the operator (or end user) has to tap the
    #       home-screen icon once to enter webapp mode. Subsequent
    #       launches from the icon all use webapp mode. iOS 5's
    #       uiopen $DisplayUrl (used by 5.6 + the boot LaunchDaemon)
    #       launches Safari -- a separate code path from the webclip.
    if ($status -eq "OK" -and $pkgsToInstall -and $scp) {
        # Stable identifier so re-running the script overwrites the
        # existing webclip instead of creating duplicate icons.
        # iOS-5 webclip UUIDs are 32-hex-no-hyphens (confirmed from a
        # real Add-to-Home-Screen result on .70). Using a recognisable
        # ASCII pattern ("MosaicMeshKiosk1") so the resulting activator
        # listener id (com.apple.webapp-4D6F...3031) is grep-friendly.
        $webclipDir = "/var/mobile/Library/WebClips/4D6F736169634D6573684B696F736B31.webclip"
        $webclipSrc = Join-Path $PSScriptRoot 'mosaicmesh.webclip.Info.plist'
        if (-not (Test-Path $webclipSrc)) {
            Write-Host "  webclip: source plist missing at $webclipSrc" -ForegroundColor Yellow
        } else {
            try {
                # First sweep: remove any existing MosaicMesh-titled
                # webclips. The operator may have manually Added to
                # Home Screen during initial setup (variant titles
                # "Mosaicmesh" / "Mosaic Mesh" / "Mosaic mesh", each
                # with a different random UUID directory), and our
                # stable-UUID install creates a SEPARATE icon next to
                # those -- leaving stale duplicates. Match the Title
                # value (any <string> tag whose content begins with
                # "Mosaic" case-insensitive) and rm the whole .webclip
                # dir. Single-quoted bash pattern avoids the
                # PowerShell -> ssh.exe quote-stripping issue (the
                # same one that bit us in 5.4c/d/e earlier in the
                # session). Includes our own stable-UUID webclip so
                # the subsequent re-create always refreshes the
                # Info.plist content.
                # The project name is actually "mosiacmesh" (i-before-a) per
                # the repo + CLAUDE.md, but the page's <title>MosaicMesh</title>
                # is spelled correctly (a-before-i). Manual Add-to-Home-
                # Screen by an operator might capture either spelling
                # depending on whether they typed it themselves or copied
                # the page title. Match BOTH "Mosaic" and "Mosiac" so the
                # cleanup catches all variants.
                $cleanupCmd = 'for d in /var/mobile/Library/WebClips/*.webclip; do' +
                              ' [ -d $d ] || continue;' +
                              ' if grep -E -q -i ''<string>[Mm]os[ai][ai]c'' "$d/Info.plist" 2>/dev/null; then' +
                              '   rm -rf "$d";' +
                              ' fi;' +
                              ' done;' +
                              ' echo CLEANUP_DONE'
                $cOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $cleanupCmd 2>&1) | Out-String
                if ($cOut -match 'CLEANUP_DONE') {
                    # Quiet success; no log line unless something
                    # was actually removed (visible via ls below).
                } else {
                    Write-Host "  webclip cleanup unexpected: $($cOut.Trim() -replace '\s+',' ')" -ForegroundColor DarkYellow
                }

                # mkdir; scp; sed-substitute URL; chown/chmod.
                & $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" `
                    "mkdir -p '$webclipDir'" 2>&1 | Out-Null
                & $scp -i $KeyPath -P $p @sshLegacy $webclipSrc `
                    "${User}@${hostName}:$webclipDir/Info.plist" 2>&1 | Out-Null
                # Pipe-delimited sed so the URL's / characters don't
                # collide with sed's default delimiter. $DisplayUrl
                # doesn't contain pipes for any sane HTTP URL.
                $sedCmd = "sed -i 's|__URL__|$DisplayUrl|' '$webclipDir/Info.plist' && " +
                          "chown -R mobile:mobile '$webclipDir' && " +
                          "chmod 755 '$webclipDir' && " +
                          "chmod 644 '$webclipDir/Info.plist' && " +
                          "echo WEBCLIP_OK"
                $wOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $sedCmd 2>&1) | Out-String
                if ($wOut -match 'WEBCLIP_OK') {
                    Write-Host "  webclip: installed; step 7 below launches it via sbdidlaunch" -ForegroundColor Green
                } else {
                    Write-Host "  webclip install unexpected: $($wOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  webclip install failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }

    # 5.4h) pin the MosaicMesh webclip icon to the LEFTMOST dock slot
    #       in portrait orientation. The admin "Start" action drives
    #       a VNC tap at the framebuffer coordinate (945, 671) -- the
    #       only working webclip-launch path on iOS 5 (see commit
    #       5569318). That coordinate ONLY hits the icon if the icon
    #       is in dock slot 0 in portrait. Without this step, Start
    #       would tap an empty area on iPads where SpringBoard
    #       happened to place the icon elsewhere on the home screen.
    #
    #       Approach: scp IconState.plist down, edit with the local
    #       Python helper (handles dock-overflow + folder traversal),
    #       scp back. The next step's killall SpringBoard picks up
    #       the new icon layout.
    if ($status -eq "OK" -and $pkgsToInstall -and $scp) {
        $webclipBid = 'com.apple.webapp-4D6F736169634D6573684B696F736B31'
        $remotePath = '/var/mobile/Library/SpringBoard/IconState.plist'
        $localPlist = Join-Path ([System.IO.Path]::GetTempPath()) "mm-iconstate-$($hostName -replace '\.', '-').plist"
        $dockHelper = Join-Path $PSScriptRoot '_dock_webapp_icon.py'
        if (-not (Test-Path $dockHelper)) {
            Write-Host "  dock pin: helper script missing at $dockHelper" -ForegroundColor Yellow
        } else {
            try {
                # Pull, edit, push back, fix ownership.
                & $scp -i $KeyPath -P $p @sshLegacy "${User}@${hostName}:$remotePath" $localPlist 2>&1 | Out-Null
                $dockOut = (& python $dockHelper $localPlist $webclipBid 2>&1) | Out-String
                & $scp -i $KeyPath -P $p @sshLegacy $localPlist "${User}@${hostName}:$remotePath" 2>&1 | Out-Null
                & $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" `
                    "chown mobile:mobile '$remotePath'; chmod 600 '$remotePath'" 2>&1 | Out-Null
                Remove-Item $localPlist -Force -ErrorAction SilentlyContinue
                $dockTrim = $dockOut.Trim() -replace '\s+', ' '
                if ($dockTrim -match 'already at dock slot 0') {
                    Write-Host "  dock pin: icon already at slot 0 (no change)" -ForegroundColor DarkGreen
                } elseif ($dockTrim -match 'moved to dock slot 0') {
                    Write-Host "  dock pin: moved icon to leftmost dock slot" -ForegroundColor Green
                } else {
                    Write-Host "  dock pin unexpected: $dockTrim" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  dock pin failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }

    # 5.5) respring after successful tweak install -- MobileSubstrate only
    #      injects tweaks at SpringBoard launch, so without this the .dylibs
    #      are on disk but inert (activator listeners empty, send returns 255).
    #      Idempotent: killall returns non-zero if no SpringBoard but that's
    #      harmless. The screen flashes black for ~3s while SpringBoard restarts.
    if ($status -eq "OK" -and $pkgsToInstall -and -not $NoRespring) {
        try {
            & $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" "killall SpringBoard 2>/dev/null; echo RESPRUNG" 2>&1 | Out-String | Out-Null
            Write-Host "  respringed (tweaks now loaded)" -ForegroundColor Green
        } catch {
            Write-Host "  respring failed: $($_.Exception.Message)" -ForegroundColor Yellow
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

    # 7) launch the MosaicMesh display in WEBAPP MODE via the home-
    #    screen webclip we installed in step 5.4g. sbdidlaunch takes
    #    the webclip's bundle id (com.apple.webapp-<UUID>) and asks
    #    SpringBoard to launch it -- same result as the operator
    #    tapping the home-screen icon, but no physical interaction
    #    required. Falls back to uiopen (Safari) if sbdidlaunch fails,
    #    so iPads that somehow ended up without the webclip still
    #    join the mesh.
    #
    #    Why we need to launch at all: opens a websocket that keeps
    #    iPad-1's WiFi radio in active mode (vs power-save). Without
    #    this the iPad has everything installed but the radio idles,
    #    making it unreachable for lifecycle scripts until something
    #    else creates outbound traffic.
    #
    #    Default-on when -InstallTweaks; -NoOpenDisplay opts out.
    if ($status -eq "OK" -and $pkgsToInstall -and -not $NoOpenDisplay) {
        # Brief sleep so SpringBoard has time to finish respringing
        # before sbdidlaunch tries to launch the webclip; otherwise the
        # launch can race the SpringBoard relaunch and silently no-op.
        $webclipBid = 'com.apple.webapp-4D6F736169634D6573684B696F736B31'
        $openCmd = "sleep 4; " +
                   "if /usr/bin/sbdidlaunch '$webclipBid' 2>/dev/null; then " +
                   "  echo OPEN_RC=0 OPEN_MODE=webclip; " +
                   "else " +
                   "  uiopen '$DisplayUrl'; echo OPEN_RC=`$? OPEN_MODE=safari; " +
                   "fi"
        try {
            $oOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $openCmd 2>&1) | Out-String
            if ($oOut -match 'OPEN_RC=0 OPEN_MODE=webclip') {
                Write-Host "  display opened (webapp mode): $DisplayUrl" -ForegroundColor Green
            } elseif ($oOut -match 'OPEN_RC=0 OPEN_MODE=safari') {
                Write-Host "  display opened (Safari fallback): $DisplayUrl" -ForegroundColor DarkGreen
            } else {
                $rc = [regex]::Match($oOut, 'OPEN_RC=\S+').Value
                Write-Host "  display launch non-zero ($rc): $($oOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  open-display failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    $results += [pscustomobject]@{ Host = "$hostName`:$p"; Status = $status; Detail = $detail }
}

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$ok = @($results | Where-Object { $_.Status -eq "OK" }).Count
Write-Host "$ok / $($results.Count) device(s) onboarded with key '$KeyPath'." -ForegroundColor Cyan
