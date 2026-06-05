<#
.SYNOPSIS
    Drive a Veency device over VNC: capture the screen, or send a tap at (X,Y).

.DESCRIPTION
    Thin wrapper over vncdotool's `vncdo`. Veency listens on display 0 (port
    5900) and (with a password set) authenticates via VNC auth — no on-screen
    prompt. Use -Capture to grab a screenshot (handy for finding tap
    coordinates), or -X/-Y to send a single tap (which arms iOS-5 video
    playback).

.EXAMPLE
    # Screenshot the device (verifies VNC + shows where the overlay is)
    .\vnc_tap.ps1 -HostName 192.168.1.50 -Capture screen.png

.EXAMPLE
    # Tap the center of a 1024x768 landscape screen
    .\vnc_tap.ps1 -HostName 192.168.1.50 -X 512 -Y 384
#>
[CmdletBinding()]
param(
    [string]$HostName = "192.168.1.50",
    [string]$Password = "mosaic",
    [int]$VncPort = 5900,
    [int]$X = -1,
    [int]$Y = -1,
    [string]$Capture = ""
)

$vncdo = (Get-Command vncdo -ErrorAction SilentlyContinue).Source
if (-not $vncdo) {
    $cand = Join-Path (Split-Path (Get-Command python).Source) "Scripts\vncdo.exe"
    if (Test-Path $cand) { $vncdo = $cand }
}
if (-not $vncdo) { throw "vncdo not found. Install with: python -m pip install vncdotool" }

# vncdotool server spec: host::PORT  (double colon = absolute port)
$server = "$HostName::$VncPort"

if ($Capture) {
    Write-Host "Capturing $server -> $Capture" -ForegroundColor Cyan
    & $vncdo -s $server -p $Password capture $Capture
    if ($LASTEXITCODE -eq 0) { Write-Host "saved $Capture" -ForegroundColor Green }
    else { Write-Host "capture failed (rc=$LASTEXITCODE)" -ForegroundColor Yellow }
}
elseif ($X -ge 0 -and $Y -ge 0) {
    Write-Host "Tapping $server at ($X,$Y)" -ForegroundColor Cyan
    & $vncdo -s $server -p $Password move $X $Y click 1
    if ($LASTEXITCODE -eq 0) { Write-Host "tapped" -ForegroundColor Green }
    else { Write-Host "tap failed (rc=$LASTEXITCODE)" -ForegroundColor Yellow }
}
else {
    throw "Specify -Capture <file> or both -X and -Y."
}
