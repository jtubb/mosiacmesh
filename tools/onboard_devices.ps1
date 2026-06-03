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
        $daemonScript = "sleep 30; /usr/bin/activator send switch-off.com.a3tweaks.switch.autolock; sleep 2; /usr/bin/uiopen $DisplayUrl"
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
    if ($status -eq "OK" -and $pkgsToInstall) {
        $insomniaPlistPath = '/var/mobile/Library/Preferences/com.malcolmhall.Insomnia.plist'
        $writeInsomnia = (
            "cat > $insomniaPlistPath << 'PLIST'`n" +
            "<?xml version=`"1.0`" encoding=`"UTF-8`"?>`n" +
            "<!DOCTYPE plist PUBLIC `"-//Apple//DTD PLIST 1.0//EN`" `"http://www.apple.com/DTDs/PropertyList-1.0.dtd`">`n" +
            "<plist version=`"1.0`">`n" +
            "<dict>`n" +
            "    <key>Enabled</key>`n" +
            "    <true/>`n" +
            "</dict>`n" +
            "</plist>`n" +
            "PLIST`n" +
            "chown mobile:mobile $insomniaPlistPath; chmod 644 $insomniaPlistPath;" +
            " echo INSOMNIA_CONFIGURED"
        )
        try {
            $iOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $writeInsomnia 2>&1) | Out-String
            if ($iOut -match 'INSOMNIA_CONFIGURED') {
                Write-Host "  insomnia: enabled (WiFi stays awake when screen is off)" -ForegroundColor Green
            } else {
                Write-Host "  insomnia config unexpected: $($iOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  insomnia config failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # 5.4d) write /etc/lighttpd/lighttpd.conf after tweaks are installed.
    #       Binds to 127.0.0.1:8080 only (never LAN-accessible), serves
    #       the cache directory, and sets correct MIME types for .mp4, .m4v,
    #       .mov, .jpg, .png, etc. Same heredoc pattern as 5.4b (Veency) /
    #       5.4c (Insomnia). lighttpd is lightweight and runs as mobile:mobile.
    if ($status -eq "OK" -and $pkgsToInstall) {
        $lighttpdConfig = (
            "mkdir -p /etc/lighttpd /var/log /var/run /var/mobile/Media/MosaicMeshCache;`n" +
            "chown mobile:mobile /var/mobile/Media/MosaicMeshCache;`n" +
            "cat > /etc/lighttpd/lighttpd.conf << 'CONF'`n" +
            "server.modules = ( `"mod_indexfile`", `"mod_dirlisting`", `"mod_staticfile`" )`n" +
            "server.document-root = `"/var/mobile/Media/MosaicMeshCache/`"`n" +
            "server.bind = `"127.0.0.1`"`n" +
            "server.port = 8080`n" +
            "server.errorlog = `"/var/log/lighttpd-error.log`"`n" +
            "server.pid-file = `"/var/run/lighttpd.pid`"`n" +
            "dir-listing.activate = `"disable`"`n" +
            "mimetype.assign = (`n" +
            "    `".mp4`"  => `"video/mp4`",`n" +
            "    `".m4v`"  => `"video/x-m4v`",`n" +
            "    `".mov`"  => `"video/quicktime`",`n" +
            "    `".jpg`"  => `"image/jpeg`",`n" +
            "    `".png`"  => `"image/png`",`n" +
            "    `".html`" => `"text/html`",`n" +
            "    `".js`"   => `"application/javascript`",`n" +
            "    `".css`"  => `"text/css`",`n" +
            "    `"`"      => `"application/octet-stream`"`n" +
            ")`n" +
            "index-file.names = ( `"index.html`" )`n" +
            "CONF`n" +
            "echo LIGHTTPD_CONF_OK"
        )
        try {
            $lOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $lighttpdConfig 2>&1) | Out-String
            if ($lOut -match 'LIGHTTPD_CONF_OK') {
                Write-Host "  lighttpd config: written to /etc/lighttpd/lighttpd.conf" -ForegroundColor Green
            } else {
                Write-Host "  lighttpd config unexpected: $($lOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  lighttpd config failed: $($_.Exception.Message)" -ForegroundColor Yellow
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

    # 7) open Safari to the MosaicMesh display URL -- joins the mesh and,
    #    importantly, opens a websocket that keeps iPad-1's WiFi radio in
    #    active mode (vs power-save). Without this final step the iPad has
    #    everything installed but the radio idles, making it unreachable
    #    for lifecycle scripts until something else creates outbound traffic.
    #    Default-on when -InstallTweaks; -NoOpenDisplay opts out.
    if ($status -eq "OK" -and $pkgsToInstall -and -not $NoOpenDisplay) {
        # Brief sleep so SpringBoard has time to finish respringing before
        # uiopen tries to launch Safari -- otherwise uiopen can race the
        # SpringBoard relaunch and the URL doesn't open.
        $openCmd = "sleep 4; uiopen '$DisplayUrl'; echo OPEN_RC=`$?"
        try {
            $oOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $openCmd 2>&1) | Out-String
            if ($oOut -match 'OPEN_RC=0') {
                Write-Host "  Safari opened: $DisplayUrl" -ForegroundColor Green
            } else {
                $rc = [regex]::Match($oOut, 'OPEN_RC=\d+').Value
                Write-Host "  uiopen non-zero ($rc): $($oOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
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
