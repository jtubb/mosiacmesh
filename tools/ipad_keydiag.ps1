<#
.SYNOPSIS
    Diagnose why key-based SSH auth is failing on a device, over the password
    channel (which still works). Dumps what sshd's pubkey + StrictModes checks
    care about: root's real home, .ssh / authorized_keys perms and ownership,
    the key contents, and relevant sshd_config directives.

    The remote script is sent via `plink -m <file>` so PowerShell never mangles
    its quotes/parens/pipes.

.EXAMPLE
    .\tools\ipad_keydiag.ps1
    .\tools\ipad_keydiag.ps1 -HostName 192.168.1.51
#>
[CmdletBinding()]
param(
    [string]$HostName = "192.168.1.50",
    [string]$User = "root",
    [string]$Password = "alpine",
    [int]$Port = 22
)

$plink = (Get-Command plink -ErrorAction SilentlyContinue).Source
if (-not $plink) {
    foreach ($p in @("C:\Program Files\PuTTY\plink.exe", "C:\Program Files (x86)\PuTTY\plink.exe")) {
        if (Test-Path $p) { $plink = $p; break }
    }
}
if (-not $plink) { throw "plink.exe not found." }

# Remote shell script. Quotes/parens/pipes are safe here because it goes to a
# file read by `plink -m`, not through PowerShell argument parsing.
$script = @'
echo "=== whoami ==="; id
echo "=== root home (passwd) ==="; grep "^root:" /etc/passwd
echo "=== home dir perms ==="; ls -ld /var/root
echo "=== .ssh perms ==="; ls -ld /var/root/.ssh
echo "=== authorized_keys (stat) ==="; ls -l /var/root/.ssh/authorized_keys
echo "=== authorized_keys (contents) ==="; cat /var/root/.ssh/authorized_keys
echo "=== sshd_config (key bits) ==="
for f in /etc/ssh/sshd_config /etc/sshd_config; do
  if [ -f "$f" ]; then
    echo "@ $f"
    grep -iE "strictmode|authorizedkeysfile|pubkeyauth|permitroot" "$f"
  fi
done
echo "=== /var free space ==="; df -h /var 2>/dev/null
echo "=== DONE ==="
'@

# Write with LF line endings (the device shell chokes on CRLF).
$tmp = [IO.Path]::GetTempFileName()
[IO.File]::WriteAllText($tmp, ($script -replace "`r`n", "`n"))

Write-Host "Diagnosing $User@$HostName`:$Port ..." -ForegroundColor Cyan
try {
    "y" | & $plink -ssh -P $Port -pw $Password -m $tmp "$User@$HostName" 2>&1
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}
