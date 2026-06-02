import logging
import json
from enum import Enum
import os
import cv2 as cv
import numpy as np
from pathlib import Path
import time
import jsonpickle
import jsonpickle.ext.numpy as jsonpickle_numpy
import effects
jsonpickle_numpy.register_handlers()

import asyncio
import socket
import threading
from aiohttp import web

from device_detector import DeviceDetector

from beeprint import pp

import sockjs

import argparse
import hashlib
from functools import lru_cache
import uuid
import datetime
from dateutil import rrule as _rrule

# Coordinated-start constants
RELEASE_LEAD_MS = 750       # ms in the future the GO start epoch is set to
PREPARE_TIMEOUT_MS = 25000  # Safety-net timeout for SILENT/stuck clients only. A
                            # client that reported NEEDS_ARM (iOS-5 awaiting a human
                            # tap) is waited on indefinitely (_release_expired_prepares
                            # holds the GO while any online client is arm-pending), so
                            # the whole wall starts together once all are armed. This
                            # timeout only releases past clients that never responded.
AUTO_ARM = True             # server fires a Veency tap to arm un-armed iOS devices
VEENCY_PORT = 5900
VEENCY_PASSWORD = "mosaic"

# --- Device lifecycle automation -----------------------------------------
# The server runs per-device shell scripts over SSH (login/start/stop/reboot),
# using the passphrase-less key installed by tools/onboard_devices.ps1 and the
# same legacy-crypto flags (the iPad-1's OpenSSH only speaks SHA-1-era crypto).
# Client.{login,start,stop,reboot}Script default to None and are backfilled with
# DEFAULT_DEVICE_SCRIPTS (editable per device via the discovery configure API).
SSH_KEY_PATH = os.path.expanduser(os.path.join("~", ".ssh", "mosaic_ipad"))
SSH_USER = "root"
SSH_LEGACY_OPTS = ["-o", "HostKeyAlgorithms=+ssh-rsa",
                   "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
                   "-o", "IdentitiesOnly=yes",           # only -i key; old sshd low MaxAuthTries
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ConnectTimeout=10",
                   "-o", "BatchMode=yes"]                # never prompt (unattended)
# The wall's display page each device opens. Edit for your network.
DISPLAY_URL = "http://192.168.1.60:3000/"
DEFAULT_DEVICE_SCRIPTS = {
    # Wake + unlock + keep the screen lit, via Activator. State-independent
    # (safe to call regardless of current iPad state): lockscreen.dismiss
    # wakes the screen if asleep AND skips slide-to-unlock if locked AND
    # no-ops if already unlocked. The previous version also pressed the
    # home button, which had the destructive side effect of minimizing
    # Safari (kicking the wall display to the home screen) if the iPad
    # was already foregrounded on MosaicMesh -- removed so login is safe
    # to fire from any starting state. The SBSettings autolock switch off
    # prevents re-sleeping. Verified on iPad-1 / iOS 5.1.1.
    "loginScript":  "activator send libactivator.lockscreen.dismiss; sleep 1; "
                    "activator send switch-off.com.a3tweaks.switch.autolock; echo LOGIN_OK",
    # Open the display page in mobile Safari.
    "startScript":  "uiopen '" + DISPLAY_URL + "'; echo START_OK",
    # Open the display page with the ?tdbg query flag, which the client JS
    # uses to (1) draw an on-screen timing HUD with current playback frame /
    # offset / drift, and (2) stream debug state back to the server log so
    # operators can collect group-wide diagnostics without per-device touch.
    # Same wake-and-open path as startScript otherwise.
    "testScript":   "uiopen '" + DISPLAY_URL +
                    ("?tdbg" if "?" not in DISPLAY_URL else "&tdbg") +
                    "'; echo TEST_OK",
    # Close Safari (the display client), re-enable auto-lock (login disabled it to
    # keep the wall lit), and sleep the screen now via the sleep button. Symmetric
    # with login: stop -> screen off + allowed to stay asleep.
    "stopScript":   "killall MobileSafari 2>/dev/null; "
                    "activator send switch-on.com.a3tweaks.switch.autolock; "
                    "activator send libactivator.system.sleepbutton; echo STOP_OK",
    # Full device reboot.
    "rebootScript": "echo REBOOTING; reboot",
}

def _apply_default_scripts(client):
    """Backfill the lifecycle-script fields with fleet defaults where unset (None),
    so a freshly-registered/older device isn't left with null scripts. Never
    overrides a per-device script an operator has set."""
    for field, default in DEFAULT_DEVICE_SCRIPTS.items():
        if getattr(client, field, None) is None:
            setattr(client, field, default)

# Render encode note: segments use plain libx264 Constrained Baseline + CRF (NO VBV
# -maxrate/-bufsize, which injects HRD into the SPS that iOS-5 / Chrome-29 UIWebView
# reject with MEDIA_ERR_SRC_NOT_SUPPORTED), plus a REGULAR keyframe grid every
# KEYFRAME_GRID_SEC. iOS-5 seeks keyframe-accurately (currentTime snaps to a
# keyframe), so x264's default ragged scene-cut keyframes (1-10s apart) made
# mid-clip drift-correction snap unpredictably far. A fixed grid lets every client
# seek to the SAME grid keyframe (shared GoTime clock + shared grid => mutual sync).
# All-intra (-g 1) is still avoided: it blew the bitrate past the iPad-1 decoder.
# Denser grid => smaller snap: the iPad seek lands within +-KEYFRAME_GRID_SEC/2 of
# the clock, so a tighter grid both reduces the residual AND its run-to-run spread.
KEYFRAME_GRID_SEC = 0.25

def _keyframe_grid_args():
    """ffmpeg args for a regular keyframe grid: force a keyframe every
    KEYFRAME_GRID_SEC of OUTPUT time (fps-independent). Encoder-independent;
    the scene-cut-disable flag is encoder-specific and lives in
    _video_encoder_args() below."""
    return ["-force_key_frames", "expr:gte(t,n_forced*%s)" % KEYFRAME_GRID_SEC]


# Video encoder + concurrency configuration. Render time on a 24-iPad fleet
# was previously: 24 sequential ffmpeg invocations, each a software (libx264)
# encode on one CPU. With a modern GPU's hardware encoder (NVENC on NVIDIA,
# QSV on Intel iGPU, AMF on AMD) and bounded asyncio.gather concurrency,
# the same render runs ~20-50x faster end-to-end (NVENC alone is 5-10x per
# file; concurrency cuts the wall time by another ~4-8x on a 24-thread box).
#
# Both knobs are env-var overridable so an operator on a CPU-only machine
# (or one whose driver session limit differs from ours) can adjust without
# editing source. Defaults assume a single decent NVIDIA GPU.
#
#   MMRENDER_ENCODER:  h264_nvenc (default) | h264_qsv | h264_amf | libx264
#   MMRENDER_CONCURRENCY: max parallel ffmpegs (default 6; NVENC consumer
#                        sessions are typically capped at 8, headroom keeps
#                        other concurrent work from being starved)
_VIDEO_ENCODER = os.environ.get("MMRENDER_ENCODER") or "h264_nvenc"
_RENDER_CONCURRENCY = int(os.environ.get("MMRENDER_CONCURRENCY") or 6)
# Default OFF after empirical regression: enabling -hwaccel cuda with 12
# concurrent NVENC encodes ran the test fleet (24 iPads) at 397s vs 322s
# without. The PCIe round-trip (GPU decode -> CPU filter chain (no CUDA
# equivalent of `perspective`) -> GPU encode) + GPU memory contention at
# high concurrency outweighed the CPU decode savings on iPad-sized
# output. Worth re-enabling for 4K/high-bitrate sources where CPU decode
# is the real bottleneck. Override:
#   $env:MMRENDER_HWACCEL = "cuda"   (or "qsv", "d3d11va")
_VIDEO_HWACCEL = os.environ.get("MMRENDER_HWACCEL") or ""


def _video_input_args():
    """ffmpeg input-option args (go BEFORE -i). If MMRENDER_HWACCEL is set,
    emit `-hwaccel <value>` so the source video is decoded on the GPU.
    Default is OFF (CPU decode) -- see _VIDEO_HWACCEL comment for the
    empirical reasoning."""
    if _VIDEO_HWACCEL:
        return ["-hwaccel", _VIDEO_HWACCEL]
    return []


def _video_encoder_args():
    """Return ffmpeg encoder + preset args for the configured encoder, plus
    the encoder-appropriate "no scene-cut keyframes" flag (keyframe grid
    spacing must be uniform for client-side seek alignment, so any extra
    scene-detection keyframes break the grid).

    All encoder configs target iPad-1 compatible H.264 baseline @ ~CRF 23
    quality; this works for NVENC, QSV, AMF, and libx264.
    """
    enc = _VIDEO_ENCODER
    if enc == "h264_nvenc":
        # NVENC preset names: p1 (fastest) -> p7 (slowest). p4 ~= libx264
        # 'fast'. -rc vbr + -cq 23 mimics libx264 -crf 23 (constant quality
        # rate-control). -no-scenecut 1 disables scene-detection keyframes.
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                "-cq", "23", "-no-scenecut", "1"]
    if enc == "h264_qsv":
        # Intel Quick Sync (integrated GPU). -global_quality is the QSV
        # equivalent of CRF. No-scenecut not exposed; QSV's keyframe
        # behaviour respects -force_key_frames.
        return ["-c:v", "h264_qsv", "-preset", "veryfast",
                "-global_quality", "23"]
    if enc == "h264_amf":
        # AMD AMF (discrete or APU). -quality speed = fastest preset.
        # -rc cqp + -qp_i/-qp_p = constant quality.
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp",
                "-qp_i", "23", "-qp_p", "23"]
    # Fallback: software libx264. -x264-params scenecut=0 is libx264-
    # specific syntax for the same "no scene-cut keyframes" intent.
    return ["-c:v", "libx264", "-preset", "veryfast",
            "-x264-params", "scenecut=0"]


# File cache with modification time tracking
file_cache = {}
cache_stats = {'hits': 0, 'misses': 0}

# JSON response cache for common responses (will be initialized after jsonpickle import)
json_response_cache = {}

# File handle pool for range requests
file_handle_pool = {}
pool_max_size = 50

def get_pooled_file_handle(file_path, mode='rb'):
    """Get cached file handle from pool"""
    key = f"{file_path}:{mode}"
    if key not in file_handle_pool:
        if len(file_handle_pool) >= pool_max_size:
            # Close oldest handle
            oldest_key = next(iter(file_handle_pool))
            file_handle_pool[oldest_key].close()
            del file_handle_pool[oldest_key]
        file_handle_pool[key] = open(file_path, mode)
    return file_handle_pool[key]

def close_file_pool():
    """Close all pooled file handles and clear the file cache"""
    for handle in file_handle_pool.values():
        handle.close()
    file_handle_pool.clear()
    file_cache.clear()
    cache_stats['hits'] = 0
    cache_stats['misses'] = 0

def _send_to_session(session_id, encoded_message):
    """Look up a sockjs Session by its id and call .send() directly. Returns
    True if delivered, False if no such session.

    Why we need this: the previous broadcast_to_*() helpers called
    socketmanager.broadcast() once PER addressed client, which sent each
    message to ALL connected sessions and relied on the iPad-side DEST
    filter (index.html line 688: `if DEST == getUDID() || DEST == 'ALL'`).
    For a 24-iPad group on a 24-iPad fleet (each iPad ~~ 1-2 sockjs
    sessions due to xhr_streaming fallback), one logical PLAY/STOP/PAUSE
    command became 24 broadcasts x ~40 sessions = ~960 serialized socket
    writes through the event loop -- visible to operators as command lag.
    Targeted send via socketmanager.get(session_id) skips every session
    that isn't the intended recipient: O(N) instead of O(N*M)."""
    if socketmanager is None or not session_id:
        return False
    sess = socketmanager.get(session_id, default=None)
    if sess is None:
        return False  # session has since disconnected/expired
    try:
        sess.send(encoded_message)
        return True
    except Exception as e:
        logging.debug("_send_to_session(%s) failed: %s", session_id, e)
        return False


def broadcast_to_client(client_id, response_dict):
    """Send a message to a single client (identified by its clientKey, i.e.
    the cookie-based UDID). Routes to the iPad's specific sockjs session
    instead of broadcasting to all sessions and relying on DEST filtering."""
    client = settings.clients.get(client_id)
    if not client:
        return
    response_dict["DEST"] = client_id
    _send_to_session(getattr(client, "clientID", ""),
                     jsonpickle.encode(response_dict))


def broadcast_to_display_group(display_id, response_dict):
    """Send a per-client message to every client in a display group. Each
    iPad gets the message addressed to its own DEST (the contract the
    client-side filter expects). Targeted per-session -- N sends instead
    of the previous N*M broadcast-fanout."""
    if socketmanager is None:
        return
    for client_id, client in settings.clients.items():
        if client.displayID != display_id:
            continue
        response_dict["DEST"] = client_id
        _send_to_session(getattr(client, "clientID", ""),
                         jsonpickle.encode(response_dict))

def init_json_cache():
    """Initialize JSON response cache after imports are available"""
    global json_response_cache
    json_response_cache = {
        'success': jsonpickle.encode({"PAYLOAD": "SUCCESS"}),
        'ack': jsonpickle.encode({"PAYLOAD": "ACK"}),
        'synack': jsonpickle.encode({"PAYLOAD": "SYNACK"})
    }

def handle_client_disconnect(session_id):
    """Enhanced client disconnect handling"""
    # Find and update client last seen time
    for client_key, client in settings.clients.items():
        if client.clientID == session_id:
            client.lastSeen = time.time()
            client.isOnline = False
            client.synced = False
            client.ready = False
            logging.info(f"Client {client.friendlyName or client_key} disconnected")
            break

def auto_configure_client(client_key, client):
    """Automatically configure new clients based on device characteristics"""
    if client.autoConfigured:
        return
    
    # Auto-assign display group based on device type
    if client.deviceType == "smartphone":
        client.displayID = "Mobile"
        settings.displays.setdefault("Mobile", Display())
    elif client.deviceType == "tablet":
        client.displayID = "Tablet"
        settings.displays.setdefault("Tablet", Display())
    elif client.deviceType == "desktop":
        client.displayID = "Desktop" 
        settings.displays.setdefault("Desktop", Display())
    else:
        client.displayID = "Default"
    
    # Generate friendly name if not set
    if not client.friendlyName:
        device_name = client.deviceModel or client.deviceBrand or "Unknown"
        client.friendlyName = f"{device_name}_{client_key[:8]}"
    
    # Set capabilities based on device characteristics
    client.capabilities = []
    if client.deviceWidth >= 1920 and client.deviceHeight >= 1080:
        client.capabilities.append("HD")
    if client.deviceType in ["smartphone", "tablet"]:
        client.capabilities.append("touch")
        client.capabilities.append("mobile")
    if client.deviceType == "desktop":
        client.capabilities.append("keyboard")
        client.capabilities.append("mouse")
    
    client.autoConfigured = True
    client.discoverySource = "websocket"
    
    logging.info(f"Auto-configured client {client.friendlyName} -> {client.displayID}")

def get_discovered_devices():
    """Get all discovered devices with discovery metadata"""
    discovered = []
    current_time = time.time()
    
    for client_key, client in settings.clients.items():
        device_info = {
            "clientKey": client_key,
            "friendlyName": client.friendlyName,
            "displayID": client.displayID,
            "deviceType": client.deviceType,
            "deviceBrand": client.deviceBrand,
            "deviceModel": client.deviceModel,
            "resolution": f"{client.deviceWidth}x{client.deviceHeight}",
            "canvas": f"{getattr(client, 'canvasWidth', 0)}x{getattr(client, 'canvasHeight', 0)}",
            "ip": client.ip,
            "hostname": getattr(client, "hostname", ""),
            "osName": client.osName,
            "osVersion": client.osVersion,
            "engine": getattr(client, "engine", ""),
            "userAgent": getattr(client, "userAgent", ""),
            "discoveryTime": client.discoveryTime,
            "lastSeen": client.lastSeen,
            "isOnline": client.isOnline,
            "synced": client.synced,
            "readyToDisplay": client.ready,
            "timeSinceLastSeen": current_time - client.lastSeen,
            "capabilities": client.capabilities,
            "autoConfigured": client.autoConfigured,
            "discoverySource": client.discoverySource,
            "connectionCount": client.connectionCount
        }
        discovered.append(device_info)
    
    # Sort by most recently seen
    discovered.sort(key=lambda x: x["lastSeen"], reverse=True)
    return discovered

# Settings save optimization
last_settings_hash = None

def save_settings_incremental():
    """Save settings only if they have changed"""
    global last_settings_hash
    try:
        current_settings = jsonpickle.encode(settings, unpicklable=True)
        current_hash = hash(current_settings)
        
        if last_settings_hash != current_hash:
            with Path("settings.dat").open("w", encoding="utf-8") as f:
                f.write(current_settings)
            last_settings_hash = current_hash
            logging.debug("Settings saved (changed)")
        else:
            logging.debug("Settings save skipped (unchanged)")
    except Exception as e:
        logging.error(f"Failed to save settings: {e}")

def saveSettings():
    """Persist settings to disk (wrapper around save_settings_incremental)."""
    save_settings_incremental()

def cleanup_old_clients(max_age_seconds=24 * 3600):
    """Remove clients that have been offline longer than max_age_seconds.
    Persists only when something was actually removed. Returns the count."""
    current_time = time.time()
    stale_keys = [
        key for key, client in settings.clients.items()
        if not client.isOnline and (current_time - client.lastSeen) > max_age_seconds
    ]
    for key in stale_keys:
        del settings.clients[key]
        logging.info(f"Removed stale client {key}")
    if stale_keys:
        saveSettings()
    return len(stale_keys)

def playlist_index(elapsed_ms, durations, loop):
    """Given elapsed playback time and per-item durations (ms), return the
    current {'index', 'offsetMs'} or None when the playlist is empty/ended.

    This is the synchronization core: clients call the JS mirror of this with
    elapsed = GoTime.now() - startEpoch, so every display lands on the same
    item at the same instant.
    """
    total = 0
    for d in durations:
        total += d
    if total <= 0:
        return None
    if loop:
        elapsed_ms = elapsed_ms % total
    elif elapsed_ms >= total:
        return None
    if elapsed_ms < 0:
        elapsed_ms = 0
    cum = 0
    for i in range(len(durations)):
        if elapsed_ms < cum + durations[i]:
            return {"index": i, "offsetMs": elapsed_ms - cum}
        cum += durations[i]
    return {"index": len(durations) - 1, "offsetMs": durations[-1]}

def sync_new_client_to_group(client_key, client):
    """If the client's display group is currently playing, send that one client
    PRELOAD + PLAY so it joins the in-progress playlist in sync."""
    display = settings.displays.get(client.displayID)
    if not display or display.action != PlayState.PLAY or not display.mediaElements:
        return
    # Per-client URLs (this client's rendered segment), not the generic source —
    # else a reconnecting renderable client gets the undecodable full source.
    items = _per_client_items(display, client_key, client)
    broadcast_to_client(client_key, {"REQUEST": "PRELOAD", "PAYLOAD": {"items": items}})
    broadcast_to_client(client_key, {
        "REQUEST": "PLAY",
        "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop}
    })

def order_points(pts):
    """Reduce a set of quad points (Nx1x2 or Nx2) to 4 corners [TL, TR, BR, BL]."""
    pts = np.array(pts, dtype="float64").reshape(-1, 2)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    return np.array([
        pts[np.argmin(s)],   # TL: smallest x+y
        pts[np.argmax(d)],   # TR: largest x-y
        pts[np.argmax(s)],   # BR: largest x+y
        pts[np.argmin(d)],   # BL: smallest x-y
    ], dtype="float32")


def _draw_fitted_label(image, text, marker_corners, color=(255, 0, 0),
                       font=cv.FONT_HERSHEY_SIMPLEX,
                       width_mult=1.5, gap_frac=0.15):
    """Draw `text` aligned to the marker's TL->TR edge -- i.e. in the same
    reading direction as the canvas's +x axis, regardless of how the iPad
    is oriented in the photo.

    Anchoring to the MARKER (not the screen quad) has two big wins:
      1. The marker's corners are detected directly from the photo with
         pattern-defined ordering, so they're robust even when band
         detection is poor or the screen quad is fiducial-only.
      2. The marker's TL->TR vector is the canvas's reading direction,
         so labels read the right way up on rotated panels (a 90deg-
         rotated iPad's label is rotated 90deg too -- looks correct
         from the panel's viewpoint).

    Position: just above the marker (outside the marker's top edge by
    gap_frac of the marker's height), centered on the TL->TR midpoint.
    Text is rotated to align with the TL->TR direction via warpAffine.

    Size: text width matches width_mult * marker edge length (default
    1.5x). Marker is rendered at 300px in canvas coords; the screen is
    typically 3-4x that on each side, so 1.5x-marker text reads as
    proportional without overflowing the screen edges in normal layouts.

    The text is rendered onto a small transparent-style buffer and warped
    in via cv.warpAffine. We use a single-channel mask to compose: the
    text writes only where the buffer is non-zero, leaving the photo
    untouched everywhere else."""
    mc = np.array(marker_corners, dtype="float32").reshape(4, 2)
    tl, tr = mc[0], mc[1]
    edge = tr - tl
    edge_len = float(np.linalg.norm(edge))
    if edge_len < 8:
        return
    # Reading direction (along TL->TR) and "up" relative to the marker
    # (out of the canvas, away from marker's center).
    dx, dy = edge / edge_len
    # "up" is perpendicular to edge, pointing away from the marker's
    # centroid (so text goes ABOVE the marker, not into it).
    centroid = mc.mean(axis=0)
    perp_a = np.array([-dy, dx])   # rotate edge 90deg CCW
    perp_b = np.array([dy, -dx])   # rotate edge 90deg CW
    # Pick whichever perp points AWAY from the marker centroid.
    tl_to_centroid = centroid - tl
    up = perp_a if float(np.dot(perp_a, tl_to_centroid)) < 0 else perp_b

    # Size the text to fit within width_mult * marker edge.
    target_w = edge_len * width_mult
    (tw1, th1), _ = cv.getTextSize(str(text), font, 1.0, 1)
    if tw1 <= 0 or th1 <= 0:
        return
    scale = target_w / tw1
    if scale < 0.3:
        return
    thickness = max(2, int(round(scale * 1.5)))
    (tw, th), baseline = cv.getTextSize(str(text), font, scale, thickness)

    # Render text into its own small buffer (BGR), then warpAffine it
    # into the main image at the rotated, translated position.
    pad = max(2, int(round(scale * 2)))
    buf_w = tw + 2 * pad
    buf_h = th + baseline + 2 * pad
    text_buf = np.zeros((buf_h, buf_w, 3), dtype=np.uint8)
    text_mask = np.zeros((buf_h, buf_w), dtype=np.uint8)
    # Baseline at (pad, pad + th); thickness drawn into both buffers.
    cv.putText(text_buf, str(text), (pad, pad + th), font, scale, color,
               thickness, cv.LINE_AA)
    cv.putText(text_mask, str(text), (pad, pad + th), font, scale, 255,
               thickness, cv.LINE_AA)

    # Place buffer in the image: TL of the BUFFER maps to a photo point
    # such that the BUFFER'S BOTTOM CENTRE is at the marker's top edge
    # midpoint, offset upward by gap_frac * edge_len. The buffer is
    # rotated so its X axis aligns with the marker's TL->TR direction.
    edge_mid = (tl + tr) / 2.0
    gap = edge_len * gap_frac
    # The text's "bottom centre" anchor in the photo (right at the gap
    # above the marker's TL->TR edge).
    photo_anchor = edge_mid + up * gap
    # Buffer-local point that should land at photo_anchor: (buf_w/2, buf_h - pad).
    # We define the affine M such that
    #   M @ (buf_w/2, buf_h - pad, 1) = photo_anchor
    # and M's linear part is rotation by angle theta = atan2(dy, dx).
    cos_t, sin_t = float(dx), float(dy)
    # Photo point of an offset (bx, by) from anchor: anchor + bx*[dx,dy] + by*(-up).
    # We need the affine that maps buffer coords (bx_, by_) -> photo coords.
    # bx, by relative to anchor = (bx_ - buf_w/2, by_ - (buf_h - pad)).
    # Photo coord = anchor + (bx_ - buf_w/2)*[dx,dy] + (by_ - (buf_h - pad))*[-up_x,-up_y]
    # In matrix form:
    #   [photo_x]   [ dx  -up_x ] [bx_]   [ tx ]
    #   [photo_y] = [ dy  -up_y ] [by_] + [ ty ]
    # where (tx, ty) = anchor - (buf_w/2)*[dx,dy] - (buf_h - pad)*(-up).
    ax, ay = float(photo_anchor[0]), float(photo_anchor[1])
    bcx, bcy = buf_w / 2.0, buf_h - pad
    tx_ = ax - bcx * dx - bcy * (-up[0])
    ty_ = ay - bcx * dy - bcy * (-up[1])
    M = np.array([[dx, -up[0], tx_],
                  [dy, -up[1], ty_]], dtype="float32")
    h, w = image.shape[:2]
    warped = cv.warpAffine(text_buf, M, (w, h), flags=cv.INTER_LINEAR,
                           borderValue=(0, 0, 0))
    warped_mask = cv.warpAffine(text_mask, M, (w, h), flags=cv.INTER_LINEAR,
                                borderValue=0)
    # Composite: image[mask>0] = warped[mask>0]. Use np.where on the mask.
    mask3 = warped_mask[:, :, None] > 0
    np.copyto(image, warped, where=mask3)


def group_bounding_box(quads):
    """Tight axis-aligned [x, y, w, h] enclosing all screen quads (photo coords)."""
    if not quads:
        return None
    allpts = np.concatenate([np.array(q, dtype="int32").reshape(-1, 2) for q in quads])
    x, y, w, h = cv.boundingRect(allpts)
    return [int(x), int(y), int(w), int(h)]


def reconstruct_screen_quad(marker_quad, cw, ch, marker_px=300):
    """Photo-space quad of the full screen, extrapolated from the centered,
    fixed-size ArUco marker (marker and screen are coplanar). marker_quad is
    [TL,TR,BR,BL] in photo px (ordered). Returns a (4,1,2) int32 array of the
    screen corners [TL,TR,BR,BL]."""
    cw = float(cw); ch = float(ch); h = marker_px / 2.0
    marker_canvas = np.array([
        [cw/2 - h, ch/2 - h], [cw/2 + h, ch/2 - h],
        [cw/2 + h, ch/2 + h], [cw/2 - h, ch/2 + h]], dtype="float32")
    dst = np.array(marker_quad, dtype="float32").reshape(4, 2)
    H = cv.getPerspectiveTransform(marker_canvas, dst)
    screen = np.array([[[0, 0]], [[cw, 0]], [[cw, ch]], [[0, ch]]], dtype="float32")
    return cv.perspectiveTransform(screen, H).astype("int32")


def _quad_box(contour):
    """Clean convex 4-corner box (minAreaRect) from any contour/quad, ordered."""
    pts = np.array(contour, dtype="float32").reshape(-1, 1, 2)
    return order_points(cv.boxPoints(cv.minAreaRect(pts)))


def _quad_iou(a, b):
    """Intersection-over-union of two convex quads (each (4,2) or (4,1,2))."""
    a = np.array(a, dtype="float32").reshape(-1, 2)
    b = np.array(b, dtype="float32").reshape(-1, 2)
    inter, _ = cv.intersectConvexConvex(a, b)
    union = cv.contourArea(a) + cv.contourArea(b) - inter
    return float(inter / union) if union > 0 else 0.0


def _quad_aspect(quad):
    """Width / height of a quad's axis-aligned bounding rect. Used as an
    orientation-only signal -- aspect is invariant to translation, scale, and
    the band's well-known ~10-15% per-side inward shrink, so it's a more
    robust rotation detector than absolute IoU."""
    pts = np.array(quad, dtype="float32").reshape(-1, 1, 2)
    x, y, w, h = cv.boundingRect(pts.astype(np.int32))
    return float(w) / max(1.0, float(h))


def _aspect_in_marker_frame(quad, marker_corners):
    """Aspect ratio (width/height) of `quad` measured in the marker's local
    coordinate frame, after un-warping the marker's perspective.

    Why this is better than `_quad_aspect`: that function uses the quad's
    photo-frame AABB, which is *not* invariant to perspective tilt. A 2:1
    rectangle tilted 45deg in photo has an AABB aspect of 1:1 -- the
    orientation info has been erased by the bounding-rect operation.

    This function computes the homography from photo coords back to the
    marker's intrinsic 300x300 frame (centered at origin), applies it to
    the quad's corners, then measures the quad's extent in that flat
    rectified frame. The marker is coplanar with the screen (both are
    rendered on the same canvas), so the rectification that flattens the
    marker also flattens the screen -- giving the screen's true aspect
    as if you were looking straight at it.

    Use this for "does the band match the reported canvas aspect?" -- a
    direct comparison of ratios in the marker frame, no perspective bias."""
    mp = np.array(marker_corners, dtype="float32").reshape(4, 2)
    # Marker's intrinsic frame: 300x300 square centered at origin.
    h = 150.0
    mc = np.array([[-h, -h], [h, -h], [h, h], [-h, h]], dtype="float32")
    # Homography from photo back to marker frame.
    H = cv.getPerspectiveTransform(mp, mc)
    pts = np.array(quad, dtype="float32").reshape(-1, 1, 2)
    in_marker = cv.perspectiveTransform(pts, H).reshape(-1, 2)
    xs = in_marker[:, 0]
    ys = in_marker[:, 1]
    width = float(xs.max() - xs.min())
    height = float(ys.max() - ys.min())
    if height < 1e-6:
        return 1.0
    return width / height


def reconcile_screen_quad(marker_quad, border_contour, cw, ch, marker_px=300, min_iou=0.5):
    """Choose the screen quad. The marker-derived fiducial is ALWAYS the output
    geometry; the detected band is used purely to VALIDATE the fiducial and
    to detect a stale mobile auto-rotation.

    Why not use the band as output (a previous attempt): on iPad-1 calibrate
    pages with an 8px CSS border and the iPad's own plastic bezel, the bright-
    region threshold detects the white *interior* of the screen, not the
    screen edge -- the bezel + border + JPEG edge blur shrink the detected
    contour by ~10-15% per side from the true panel edge. The fiducial
    extrapolates from the marker to the full canvas (which equals the html
    element extent = the panel) and is correct by construction; substituting
    band geometry made every screen render too small.

    Rotation detection: we want to catch the case where the iPad reported its
    canvas dims in one orientation but was photographed in the other (the
    canvas-resize event didn't make it to the server before calibration).
    Two independent signals decide:
      - IoU comparison: the swapped fiducial (cw<->ch) is closer to the band.
        High specificity but requires a usable band AND enough orientation
        difference to push IoU above min_iou.
      - Aspect comparison: the band's bounding-rect aspect is closer to the
        swapped fiducial's aspect than to the native fiducial's. This is
        invariant to the band's ~10-15% inward shrink (shrink affects width
        and height proportionally), so it works even when band IoU is below
        min_iou -- which is the common case for one-off rotated screens
        whose band is partially occluded or poorly thresholded.

    Returns (quad (4,1,2) int32, source) where source is one of:
      'fiducial'    -- fiducial, band-validated (high confidence)
      'rotated'     -- swapped-orientation fiducial (band confirmed rotation)
      'unverified'  -- fiducial, band didn't validate either orientation
                       (band may be noisy/degenerate; fiducial still trusted)
      'no-band'     -- fiducial, no band quad was provided to validate against
                       (the bright-region pipeline produced no quad for this
                       marker -- typically dim/glare iPads)"""
    fid = reconstruct_screen_quad(marker_quad, cw, ch, marker_px)
    fid_sw = reconstruct_screen_quad(marker_quad, ch, cw, marker_px)
    # Need a usable, non-degenerate band box to validate against.
    box = None
    if border_contour is not None and len(np.array(border_contour).reshape(-1, 2)) >= 3:
        b = _quad_box(border_contour)
        if cv.contourArea(b.astype("float32").reshape(-1, 1, 2)) > 0:
            box = b
    if box is None:
        return fid, "no-band"
    iou = _quad_iou(fid, box)
    iou_sw = _quad_iou(fid_sw, box)
    # Aspect comparison in the MARKER'S frame after perspective un-warp.
    # Both the marker and the screen are coplanar (rendered on the same
    # canvas) so the rectification that flattens the marker also flattens
    # the band, giving the band's true aspect as if seen straight-on.
    # The fiducials' aspect in marker frame IS cw/ch by construction.
    ba = _aspect_in_marker_frame(box, marker_quad)
    fa = float(cw) / max(1.0, float(ch))
    fa_sw = float(ch) / max(1.0, float(cw))

    # AUTO-SWAP CRITERIA: only swap when evidence is OVERWHELMING. Real
    # fleet photos have intra-screen brightness gradients that can pull
    # the band's measured aspect toward 1.0 (square), and a "barely
    # closer to swap than to native" heuristic over-fires on those.
    # Tight criteria below default to KEEP (trust the iPad's reported
    # canvas dims) unless every signal agrees:
    #   (a) band aspect is within 0.15 log units of the swap aspect
    #       (i.e., band shape matches swap shape within ~15%)
    #   (b) band aspect is at least 0.35 log units away from the native
    #       aspect (i.e., band is decisively NOT the reported shape)
    #   (c) IoU with the swapped fiducial corroborates: swapped IoU
    #       beats native IoU by at least 1.5x AND is >= min_iou
    # If any fails, keep the iPad's reported orientation; the user can
    # manually swap via the swap_orientation admin action if needed.
    log_ba = float(np.log(ba))
    log_native = float(np.log(fa))
    log_swap = float(np.log(fa_sw))
    aspect_matches_swap = abs(log_ba - log_swap) < 0.15
    aspect_far_from_native = abs(log_ba - log_native) > 0.35
    iou_corroborates_swap = (iou_sw >= min_iou and
                              iou_sw > iou * 1.5)
    if aspect_matches_swap and aspect_far_from_native and iou_corroborates_swap:
        return fid_sw, "rotated"
    # Distinguish "we checked and it agreed with reported" from "we couldn't
    # decide". Useful in the visualisation: green = checked, yellow = ambiguous.
    if iou >= min_iou and abs(log_ba - log_native) < 0.20:
        return fid, "fiducial"
    return fid, "unverified"


def _render_output_dims(client):
    """Per-screen render output size: the canvas/viewport ASPECT (true shape and
    orientation), scaled to FIT WITHIN the device's reported screen resolution so
    it stays displayable AND decodable on the panel — a 1st-gen iPad's H.264
    decoder maxes near its 768x1024 screen, and the viewport can't exceed the
    screen anyway. Returns even (w, h) for libx264."""
    aw = int(getattr(client, "canvasWidth", 0) or client.deviceWidth) or 1
    ah = int(getattr(client, "canvasHeight", 0) or client.deviceHeight) or 1
    dw = int(getattr(client, "deviceWidth", 0) or 0)
    dh = int(getattr(client, "deviceHeight", 0) or 0)
    if dw and dh:
        s = min(1.0, dw / float(aw), dh / float(ah))
        aw = int(round(aw * s)); ah = int(round(ah * s))
    return max(2, aw - aw % 2), max(2, ah - ah % 2)


def warp_image_for_screen(source_img, bbox, screen_quad, out_w, out_h):
    """Warp the region of source_img under a screen's quad onto that screen's
    pixel rect. bbox is the [x, y, w, h] region of the photo that the source image is stretched to fill
    (the group bbox for SEGMENT, the screen's own quad bbox for INDIVIDUAL); the full image is
    stretched to fill bbox, so the screen quad (photo coords) maps back into
    media coords, then a homography fits it to out_w x out_h."""
    h, w = source_img.shape[:2]
    bx, by, bw, bh = bbox
    # Use the quad in its STORED order (screen TL,TR,BR,BL from the marker), not a
    # geometric re-sort — otherwise a non-upright panel (e.g. 180°-mounted) flips.
    ordered = np.array(screen_quad, dtype="float32").reshape(-1, 2)
    src = np.array([[(px - bx) / bw * w, (py - by) / bh * h] for (px, py) in ordered], dtype="float32")
    dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], dtype="float32")
    m = cv.getPerspectiveTransform(src, dst)
    return cv.warpPerspective(source_img, m, (out_w, out_h))


def _hex_to_bgr(hexstr):
    """'#rrggbb' -> OpenCV (B, G, R) tuple; falls back to black."""
    h = (hexstr or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def letterbox_to_aspect(img, target_w, target_h, bg_bgr):
    """Scale img to fit within target_w x target_h preserving aspect, centered
    on a solid bg_bgr canvas of exactly that size."""
    target_w = max(1, int(target_w)); target_h = max(1, int(target_h))
    h, w = img.shape[:2]
    scale = min(target_w / float(w), target_h / float(h))
    nw = max(1, int(round(w * scale))); nh = max(1, int(round(h * scale)))
    resized = cv.resize(img, (nw, nh))
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    canvas[:] = bg_bgr
    x = (target_w - nw) // 2; y = (target_h - nh) // 2
    canvas[y:y + nh, x:x + nw] = resized
    return canvas


def assign_group_bounding_boxes():
    """Per display group, set boundingBox/boundingBoxCenter from the ArUco
    screens' quads (photo coords). Call after calibration."""
    groups = {}
    for key, client in settings.clients.items():
        if client.measuredPerimeter is not None and client.displayID:
            groups.setdefault(client.displayID, []).append(client.measuredPerimeter)
    for display_id, quads in groups.items():
        display = settings.displays.setdefault(display_id, Display())
        bbox = group_bounding_box(quads)
        display.boundingBox = bbox
        if bbox:
            display.boundingBoxCenter = [bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2]


def _group_clients(display_id):
    """Sorted [(clientKey, client)] for clients assigned to a display group."""
    return sorted([(k, c) for k, c in settings.clients.items() if c.displayID == display_id])


_FREQ_MAP = {"DAILY": _rrule.DAILY, "WEEKLY": _rrule.WEEKLY,
             "MONTHLY": _rrule.MONTHLY, "YEARLY": _rrule.YEARLY}


def _parse_date(s):
    y, m, d = [int(x) for x in str(s).split("-")]
    return datetime.datetime(y, m, d)


def _hhmm_to_min(s):
    hh, mm = [int(x) for x in str(s).split(":")]
    return hh * 60 + mm


def schedule_active_at(schedule, when):
    """True if `schedule` is active at datetime `when` (server-local): `when`'s
    date is an rrule occurrence (minus exdates) and the time is within the
    [startTime, endTime] window. Pure; ignores `enabled` (caller checks that)."""
    freq = _FREQ_MAP.get(getattr(schedule, "freq", None))
    if freq is None:
        return False
    try:
        dtstart = _parse_date(schedule.dtstart)
    except Exception:
        return False
    kw = {"dtstart": dtstart, "interval": max(1, int(getattr(schedule, "interval", 1) or 1))}
    end = getattr(schedule, "end", None) or {"type": "never"}
    if not isinstance(end, dict):
        end = {"type": "never"}
    if end.get("type") == "until" and end.get("untilDate"):
        try:
            u = _parse_date(end["untilDate"])
            kw["until"] = u.replace(hour=23, minute=59, second=59)
        except Exception:
            pass
    elif end.get("type") == "count" and end.get("count"):
        kw["count"] = int(end["count"])
    if getattr(schedule, "freq", None) == "WEEKLY" and getattr(schedule, "byweekday", None):
        kw["byweekday"] = [int(x) for x in schedule.byweekday]
    rset = _rrule.rruleset()
    rset.rrule(_rrule.rrule(freq, **kw))
    for ex in (getattr(schedule, "exdates", None) or []):
        try:
            rset.exdate(_parse_date(ex))
        except Exception:
            pass
    day_start = datetime.datetime(when.year, when.month, when.day)
    if not rset.between(day_start, day_start, inc=True):   # occurrences sit at midnight of each day
        return False
    now_min = when.hour * 60 + when.minute
    try:
        return _hhmm_to_min(schedule.startTime) <= now_min <= _hhmm_to_min(schedule.endTime)
    except Exception:
        return False


def compute_render_token(display_id):
    """Stable hash of the inputs that affect a per-screen render (SEGMENT or INDIVIDUAL): the playlist
    items, the group bounding box, and each client's resolution + measured quad.
    Rendered assets are valid only while this matches Display.renderedToken."""
    display = settings.displays.get(display_id)
    if not display:
        return ""
    items = []
    for me in display.mediaElements:
        pm = me.playmode.name if hasattr(me.playmode, "name") else str(me.playmode)
        items.append((me.id, me.file, me.duration, pm,
                      getattr(me, "backgroundColor", "#000000"),
                      getattr(me, "startEffect", None), getattr(me, "endEffect", None)))
    clients = []
    for key, c in _group_clients(display_id):
        perim = None
        if c.measuredPerimeter is not None:
            perim = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2).tolist()
        clients.append((key, c.deviceWidth, c.deviceHeight, perim))
    # Bump this when the encode settings change, to invalidate stale renders.
    encode_ver = "grid025-cbl-v5"
    raw = repr((items, display.boundingBox, clients, encode_ver))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _broadcast_render_status(display_id, status):
    if socketmanager is not None:
        socketmanager.broadcast(jsonpickle.encode(
            {"REQUEST": "RENDER_STATUS", "PAYLOAD": {"displayID": display_id, "status": status}}))


def _is_renderable(me):
    """SEGMENT and INDIVIDUAL items require a per-screen server render."""
    return me.playmode in (PlayMode.SEGMENT, PlayMode.INDIVIDUAL)


def _normalize_effect(field):
    """Tolerate an effect field as {name, params} | bare-string name | None."""
    if not field:
        return None
    if isinstance(field, str):
        return {"name": field, "params": {}}
    if isinstance(field, dict) and field.get("name"):
        return field
    return None


def _resolve_effect_filters(me, duration_ms, out_w, out_h):
    """Collect (video_fragments, audio_fragments) for an item's start/end effects."""
    vfs, afs = [], []
    ctx = {"duration_ms": duration_ms, "out_w": out_w, "out_h": out_h}
    for role, field in (("start", getattr(me, "startEffect", None)),
                        ("end", getattr(me, "endEffect", None))):
        spec = _normalize_effect(field)
        if not spec:
            continue
        eff = effects.get_effect(spec.get("name"))
        if eff is None:
            continue
        v, a = eff.video_filters(role, eff.resolve(spec.get("params")), ctx)
        vfs += v
        afs += a
    return vfs, afs


async def _run_ffmpeg(cmd, label, semaphore):
    """Run one ffmpeg command under the concurrency semaphore. Logs and
    raises with the last few lines of stderr on non-zero exit -- ffmpeg's
    final lines are where the actual error message lives (everything
    before is progress noise)."""
    async with semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _out, _err = await proc.communicate()
        if proc.returncode != 0:
            tail = (_err or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            logging.error("ffmpeg rc=%s (%s) cmd=%s\n  %s",
                          proc.returncode, label, " ".join(cmd), "\n  ".join(tail))
            raise RuntimeError("ffmpeg failed for " + label +
                               " (" + str(proc.returncode) + ")")


async def render_group_async(display_id):
    """Async render of a group's SEGMENT items.

    Strategy: build the FULL list of per-client ffmpeg commands first, then
    asyncio.gather them under a Semaphore(_RENDER_CONCURRENCY) so multiple
    encodes run in parallel. The previous implementation awaited each ffmpeg
    in a for-loop -- the async syntax was misleading, the actual execution
    was strictly sequential. On a 24-iPad fleet this is the difference
    between ~10 minutes and ~30 seconds total render time.

    Image warps still happen inline (OpenCV CPU warpPerspective). For a 24-
    iPad fleet at typical output sizes, the whole image-render pass is
    < 1 second; concurrency would buy ~nothing and just complicate the
    code path."""
    display = settings.displays.get(display_id)
    if not display:
        return {"status": "error"}
    display.renderStatus = "rendering"
    _broadcast_render_status(display_id, "rendering")
    token = compute_render_token(display_id)
    try:
        seg_items = [(i, me) for i, me in enumerate(display.mediaElements)
                     if _is_renderable(me)]
        clients = [(k, c) for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]
        # Pass 1: collect all video render commands. Pass 2: gather them.
        # This lets us see the total job count and parallelise everything
        # in a single batch (across items AND across clients).
        video_jobs = []   # list of (cmd, label)
        for i, me in seg_items:
            src_path = resolve_media_path(me.file)
            if isVideoItem(me.file):
                dims = get_video_dimensions(src_path) if src_path else None
                if not dims:
                    raise RuntimeError("cannot read source video: " + str(me.file))
                sw, sh = dims
                for key, c in clients:
                    out_dir = os.path.join("media", key, "videos")
                    Path(out_dir).mkdir(parents=True, exist_ok=True)
                    out_w, out_h = _render_output_dims(c)
                    # NOTE: ffmpeg fade st= is in SECONDS, so this passes the
                    # seconds-domain duration (the param name 'duration_ms' is a
                    # misnomer). Do NOT convert to ms here — only the client
                    # playback payload (_media_item_payload) needs ms.
                    evf, eaf = _resolve_effect_filters(me, me.duration,
                                                       out_w, out_h)
                    if me.playmode == PlayMode.INDIVIDUAL:
                        quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                        bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                        if bw <= 0 or bh <= 0 or cv.contourArea(np.array(c.measuredPerimeter, dtype="int32")) <= 0:
                            raise RuntimeError("degenerate screen quad for client " + str(key))
                        if sw * bh >= sh * bw:                 # source wider/equal -> pad height
                            pad_w = sw; pad_h = int(round(sw * bh / float(bw)))
                        else:                                  # source taller -> pad width
                            pad_h = sh; pad_w = int(round(sh * bw / float(bh)))
                        pad_x = (pad_w - sw) // 2; pad_y = (pad_h - sh) // 2
                        pts = quad_to_source_points([bx, by, bw, bh], c.measuredPerimeter, pad_w, pad_h)
                        out_path = os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".mp4")
                        cmd = build_ffmpeg_individual_cmd(src_path, out_path, pts,
                                                          out_w, out_h,
                                                          pad_w, pad_h, pad_x, pad_y,
                                                          getattr(me, "backgroundColor", "#000000"),
                                                          extra_video_filters=evf, extra_audio_filters=eaf)
                    else:
                        pts = quad_to_source_points(display.boundingBox, c.measuredPerimeter, sw, sh)
                        out_path = os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".mp4")
                        cmd = build_ffmpeg_perspective_cmd(src_path, out_path, pts,
                                                           out_w, out_h,
                                                           extra_video_filters=evf, extra_audio_filters=eaf)
                    video_jobs.append((cmd, key + "/" + str(i)))
            else:
                img = cv.imread(src_path) if src_path else None
                if img is None:
                    raise RuntimeError("cannot read source image: " + str(me.file))
                for key, c in clients:
                    out_dir = os.path.join("media", key, "images")
                    Path(out_dir).mkdir(parents=True, exist_ok=True)
                    # Output at the client's TRUE rendered viewport (canvas),
                    # falling back to reported device dims when canvas is 0/missing.
                    out_w, out_h = _render_output_dims(c)
                    if me.playmode == PlayMode.INDIVIDUAL:
                        quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                        bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                        if bw <= 0 or bh <= 0 or cv.contourArea(np.array(c.measuredPerimeter, dtype="int32")) <= 0:
                            raise RuntimeError("degenerate screen quad for client " + str(key))
                        bg = _hex_to_bgr(getattr(me, "backgroundColor", "#000000"))
                        canvas = letterbox_to_aspect(img, bw, bh, bg)
                        warped = warp_image_for_screen(canvas, [bx, by, bw, bh], c.measuredPerimeter,
                                                       out_w, out_h)
                        cv.imwrite(os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".png"), warped)
                    else:
                        warped = warp_image_for_screen(img, display.boundingBox, c.measuredPerimeter,
                                                       out_w, out_h)
                        cv.imwrite(os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".png"), warped)
        # Pass 2: fire all video ffmpeg jobs in parallel, capped at
        # _RENDER_CONCURRENCY. Any failure raises out of asyncio.gather
        # (return_exceptions=False default) and gets caught by the outer
        # try-except, which sets renderStatus='error' and broadcasts.
        if video_jobs:
            sem = asyncio.Semaphore(_RENDER_CONCURRENCY)
            logging.info("render: launching %d ffmpeg jobs concurrency=%d encoder=%s",
                         len(video_jobs), _RENDER_CONCURRENCY, _VIDEO_ENCODER)
            t0 = time.time()
            await asyncio.gather(*[_run_ffmpeg(cmd, lbl, sem) for cmd, lbl in video_jobs])
            logging.info("render: %d ffmpeg jobs done in %.1fs",
                         len(video_jobs), time.time() - t0)
        display.renderedToken = token
        display.renderStatus = "ready"
        _broadcast_render_status(display_id, "ready")
        return {"status": "ready", "token": token}
    except Exception as e:
        logging.error("render failed for %s: %s", display_id, e)
        display.renderStatus = "error"
        _broadcast_render_status(display_id, "error")
        return {"status": "error", "error": str(e)}


def _per_client_items(display, key, c):
    """Per-client playlist items: renderable items (SEGMENT/INDIVIDUAL) resolve to
    THIS client's warped file when calibrated, else the plain source. Shared by
    the PLAY (GO) and PREPARE paths so both hand a client the same playable URL."""
    token = display.renderedToken
    items = []
    for i, me in enumerate(display.mediaElements):
        if _is_renderable(me) and c.measuredPerimeter is not None:
            prefix = "ind_" if me.playmode == PlayMode.INDIVIDUAL else "seg_"
            ext = ".mp4" if isVideoItem(me.file) else ".png"
            f = "/media/" + key + "/" + prefix + token + "_" + str(i) + ext
        else:
            f = me.file  # FULL item, or uncalibrated fallback to full source
        item = _media_item_payload(me)
        item["file"] = f
        items.append(item)
    return items


def _broadcast_per_client_play(display_id, display):
    """Send each client its own PLAY with its per-client (warped) media URLs."""
    for key, c in _group_clients(display_id):
        broadcast_to_client(key, {"REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch,
                        "items": _per_client_items(display, key, c), "loop": display.loop}})


# Recognized video source extensions. SEGMENT/INDIVIDUAL items are transcoded
# to .mp4 by ffmpeg regardless of source; FULL items play directly in the
# browser (.mp4/.webm/.m4v are broadly playable, .mov needs h264/Safari/Chrome).
_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".ogv")


def isVideoItem(file):
    """True if a media file is a video, mirroring the client's isVideoItem.
    Tolerates a trailing ?query."""
    return str(file or "").lower().split("?")[0].endswith(_VIDEO_EXTS)


def quad_to_source_points(bbox, screen_quad, src_w, src_h):
    """Corners of the screen's quad expressed in source media pixel coords (the
    source is stretched to fill the group bbox). Uses the quad in its STORED
    order — reconstruct_screen_quad emits screen [TL,TR,BR,BL] in the panel's own
    orientation (from the marker). Re-ordering geometrically would discard that
    and flip a non-upright panel (e.g. a 180°-mounted screen)."""
    bx, by, bw, bh = bbox
    pts = np.array(screen_quad, dtype="float32").reshape(-1, 2)
    return [[(float(px) - bx) / bw * src_w, (float(py) - by) / bh * src_h] for (px, py) in pts]


def build_ffmpeg_perspective_cmd(src_path, out_path, src_points, out_w, out_h,
                                 extra_video_filters=None, extra_audio_filters=None):
    """ffmpeg arg list: perspective-warp the source quad to fill the frame, scale
    to the screen resolution, encode iPad-compatible H.264 + AAC audio.
    src_points is [TL, TR, BR, BL]; ffmpeg's perspective wants TL, TR, BL, BR.
    extra_video_filters append to -vf; extra_audio_filters add an -af when present."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = persp + ",scale=" + str(out_w) + ":" + str(out_h)
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y"] + _video_input_args() + ["-i", src_path, "-vf", vf]
    cmd += _video_encoder_args()
    cmd += ["-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += _keyframe_grid_args()
    cmd += ["-movflags", "+faststart", out_path]
    return cmd


def build_ffmpeg_individual_cmd(src_path, out_path, src_points, out_w, out_h,
                                pad_w, pad_h, pad_x, pad_y, bg_hex,
                                extra_video_filters=None, extra_audio_filters=None):
    """ffmpeg args for INDIVIDUAL: pad the source to the screen bbox aspect with
    backgroundColor, perspective-warp the whole padded frame to the screen quad,
    scale to the device resolution. src_points is [TL, TR, BR, BL]."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    _h = (bg_hex or "#000000").lstrip("#")
    if len(_h) != 6:
        _h = "000000"
    hexcol = "0x" + _h
    pad = ("pad=" + str(int(pad_w)) + ":" + str(int(pad_h)) + ":" +
           str(int(pad_x)) + ":" + str(int(pad_y)) + ":color=" + hexcol)
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = pad + "," + persp + ",scale=" + str(out_w) + ":" + str(out_h)
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y"] + _video_input_args() + ["-i", src_path, "-vf", vf]
    cmd += _video_encoder_args()
    cmd += ["-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += _keyframe_grid_args()
    cmd += ["-movflags", "+faststart", out_path]
    return cmd


def get_video_dimensions(path):
    """Return (width, height) of a video via OpenCV, or None if unreadable."""
    cap = cv.VideoCapture(path)
    try:
        w = int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if w <= 0 or h <= 0:
        return None
    return (w, h)


def resolve_media_path(file_url):
    """Map a media URL ('/media/<client>/<name>') to its on-disk path, matching
    media_handler's convention (images/ or videos/ by extension)."""
    parts = file_url.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "media":
        return None
    client = parts[1]
    name = parts[-1]
    subdir = "videos" if isVideoItem(name) else "images"
    return os.path.join("media", client, subdir, name)

def get_cached_file(file_path):
    """Get file content with caching based on modification time.

    Cache entries are stored as {'content': bytes, 'mtime': float}. This
    function is the only reader/writer of that value format.
    """
    if not os.path.exists(file_path):
        return None
    try:
        mod_time = os.path.getmtime(file_path)

        # Check if file is in cache and not modified
        cached = file_cache.get(file_path)
        if cached is not None and cached['mtime'] == mod_time:
            cache_stats['hits'] += 1
            return cached['content']

        # File not cached or modified - read from disk
        with open(file_path, 'rb') as f:
            data = f.read()
        cache_stats['misses'] += 1
        file_cache[file_path] = {'content': data, 'mtime': mod_time}

        # Limit cache size to prevent memory issues (simple FIFO)
        if len(file_cache) > 100:
            oldest_key = next(iter(file_cache))
            del file_cache[oldest_key]

        return data
    except (OSError, IOError):
        return None

def parse_args():
    """Parse CLI args. Called only from __main__ so that importing this module
    (e.g. from tests) has no argparse side effects."""
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--Port", help="Port to run server on")
    parser.add_argument("-v", "--Verbose", action='store_true', help="Verbose output")
    return parser.parse_args()

class Settings():
    def __init__(self):
        self.displays = {}
        self.scripts = {}
        self.clients = {}
        self.playlists = {}
        self.schedules = {}

class Scripts():
    def __init__(self):
        self.name = ''
        self.description = ''
        self.value = None
        self.status = None

class Display():
    def __init__(self):
        self.boundingBox = None
        self.boundingBoxCenter = None
        self.mediaElements = []
        self.loop = False
        self.currentFrame = 0
        self.action = PlayState.NOACTION
        self.playStartEpoch = 0   # server-time ms when playback last (re)started
        self.pauseOffset = 0      # ms into the playlist when paused
        self.renderedToken = ""   # token of the last successful SEGMENT render
        self.renderStatus = ""    # "" | "rendering" | "ready" | "error"
        self.defaultPlaylistName = None   # fallback playlist when no schedule is active
        self.scheduledEntryId = None      # transient: which schedule/"__default__" currently drives this group
        self.scheduledPlaying = False     # transient: have we issued PLAY for the current effective target
        self.prepareId = None
        self.readyClients = set()
        self.armPending = set()   # clients that sent NEEDS_ARM, awaiting a human tap
        self.prepareDeadline = 0

class PlayState(Enum):
    NOACTION = 0
    STOP = 1
    PLAY = 2
    PAUSE = 3
    PREPARING = 4

class MediaElement():
    def __init__(self):
        self.id = None
        self.file = None
        self.duration = None
        self.playmode = PlayMode.DEFAULT
        self.backgroundColor = "#000000"
        self.startEffect = None
        self.endEffect = None


class Playlist():
    def __init__(self):
        self.name = ""
        self.items = []      # list of item dicts: id, file, duration, playmode, backgroundColor, startEffect, endEffect
        self.loop = False

class Schedule():
    def __init__(self):
        self.id = ""
        self.name = ""
        self.playlistName = ""
        self.displayID = ""
        self.priority = 0
        self.enabled = True
        self.freq = "DAILY"          # DAILY | WEEKLY | MONTHLY | YEARLY
        self.interval = 1
        self.byweekday = []          # ints 0=Mon..6=Sun (WEEKLY)
        self.dtstart = ""            # "YYYY-MM-DD"
        self.end = {"type": "never"} # or {"type":"until","untilDate":...} / {"type":"count","count":N}
        self.exdates = []            # ["YYYY-MM-DD", ...]
        self.startTime = "00:00"
        self.endTime = "23:59"

class PlayMode(Enum):
    DEFAULT = 0
    FULL = 1
    SEGMENT = 2
    SCRIPT = 3
    INDIVIDUAL = 4

class Client():
    def __init__(self):
        self.friendlyName = None
        self.clientID = ""
        self.displayID = None
        self.arucoID = None
        self.deviceHeight = 0
        self.deviceWidth = 0
        self.canvasWidth = 0    # rendered viewport (innerWidth) — reflects actual
        self.canvasHeight = 0   # orientation; device* is the raw screen resolution
        self.measuredCenter = None
        self.measuredPerimeter = None
        self.userAgent = None
        self.ip = ""
        self.hostname = ""              # reverse-DNS (PTR) of ip, when resolvable
        self.hostnameResolved = False   # PTR lookup attempted (don't retry per ip)
        self.nameIsCustom = False       # user set friendlyName -> DNS won't override
        self.touch = False              # client reported touch support at REGISTER
        self.osName=""
        self.osVersion=""
        self.engine=""
        self.deviceBrand=""
        self.deviceModel=""
        self.deviceType=""
        self.loginScript = None
        self.startScript = None
        self.stopScript = None
        self.rebootScript = None
        self.testScript = None
        self.ready = False      # ready to display: media cached & client ready
        self.isOnline = False   # alive: connected / recent heartbeat
        self.synced = False     # SYN/SYNACK handshake (clock/group) complete
        # Enhanced discovery fields
        self.discoveryTime = time.time()
        self.lastSeen = time.time()
        self.connectionCount = 0
        self.capabilities = []
        self.autoConfigured = False
        self.discoverySource = "manual"  # manual, websocket, network

async def ws_handler(manager, session, msg):
    # sockjs >=0.12 handler signature: (manager, session, msg).
    # Message types are sockjs.MsgType.* and msg carries .type / .data.
    logging.debug("WS_HANDLER")
    if manager is None:
        return
    if msg.type == sockjs.MsgType.OPEN:
        # Enhanced discovery notification with client info
        client_info = {
            "sessionId": session.id,
            "ip": _client_ip(session.request) if hasattr(session, 'request') else "unknown",
            "userAgent": session.request.headers.get('User-Agent', '') if hasattr(session, 'request') else "",
            "timestamp": time.time()
        }
        discovery_announcement = {
            "REQUEST": "DEVICE_DISCOVERED", 
            "PAYLOAD": client_info
        }
        manager.broadcast(jsonpickle.encode(discovery_announcement))
        
        # Also send traditional JOIN for backward compatibility
        manager.broadcast(jsonpickle.encode({"REQUEST": "JOIN", "PAYLOAD":session.id}))

        # Replay current renderStatus to the newly-connected session for any
        # display with a non-empty status. Without this, an admin who
        # refreshes the playlist page during an in-flight render loses the
        # "rendering..." badge (the original broadcast happened before they
        # reconnected). Sent only to this session via session.send() to
        # avoid pestering already-connected clients.
        try:
            if settings is not None and getattr(settings, "displays", None):
                for _did, _disp in settings.displays.items():
                    _st = getattr(_disp, "renderStatus", "")
                    if _st:
                        session.send(jsonpickle.encode({
                            "REQUEST": "RENDER_STATUS",
                            "PAYLOAD": {"displayID": _did, "status": _st}}))
        except Exception as _e:
            logging.debug("ws OPEN: render-status replay failed: %s", _e)


    elif msg.type == sockjs.MsgType.MESSAGE:
        session.send(msg_response(jsonpickle.decode(msg.data),session))
    elif msg.type == sockjs.MsgType.CLOSED:
        # Enhanced disconnect notification
        handle_client_disconnect(session.id)
        manager.broadcast(jsonpickle.encode({"REQUEST": "DISC", "PAYLOAD":session.id}))

def _duration_ms(me):
    """Item duration in milliseconds. Durations are authored/stored in SECONDS
    (the editor's 'Duration (s)' field, default 5), but the client playback
    engine (playlistIndex vs GoTime ms, currentTime, msToNext) and the ffmpeg
    effect filters both consume MILLISECONDS — so convert at every boundary
    that leaves the seconds-domain."""
    try:
        return int(round(float(me.duration) * 1000))
    except (TypeError, ValueError):
        return 0


def _media_item_payload(me):
    """Per-item dict sent to clients in PLAY/PRELOAD. getattr guards items
    loaded from an older settings.dat that predate the newer fields. duration
    is emitted in MILLISECONDS (stored in seconds — see _duration_ms)."""
    return {"id": me.id, "file": me.file, "duration": _duration_ms(me),
            "playmode": me.playmode.name,
            "backgroundColor": getattr(me, "backgroundColor", "#000000"),
            "startEffect": getattr(me, "startEffect", None),
            "endEffect": getattr(me, "endEffect", None)}


def _build_media_elements(items):
    """Build MediaElement objects from a list of item dicts (SETPLAYLIST /
    ASSIGN_PLAYLIST share this). Maps the playmode string to the enum and
    applies field defaults."""
    elements = []
    for item in (items or []):
        me = MediaElement()
        me.id = item.get("id")
        me.file = item.get("file")
        me.duration = item.get("duration")
        _pm = item.get("playmode")
        me.playmode = (PlayMode.SEGMENT if _pm == "SEGMENT"
                       else PlayMode.SCRIPT if _pm == "SCRIPT"
                       else PlayMode.INDIVIDUAL if _pm == "INDIVIDUAL"
                       else PlayMode.FULL)
        me.backgroundColor = item.get("backgroundColor", "#000000")
        me.startEffect = item.get("startEffect")
        me.endEffect = item.get("endEffect")
        elements.append(me)
    return elements


def _apply_playlist(display_id, pl):
    """Copy a saved Playlist onto a group (mediaElements, loop, PRELOAD).

    Does NOT blank renderedToken eagerly: compute_render_token() is a stable
    hash of items + bounding box + per-client perimeters, so re-assigning
    the SAME playlist produces the same token and the existing render
    output is still valid. Blanking unconditionally forced a "needs render"
    state every time the user re-assigned a playlist they hadn't changed --
    deeply confusing because they could see the render had just completed.
    Let the natural token comparison decide."""
    display = settings.displays.setdefault(display_id, Display())
    display.mediaElements = _build_media_elements(pl.items)
    display.loop = bool(pl.loop)
    broadcast_to_display_group(display_id, {
        "REQUEST": "PRELOAD",
        "PAYLOAD": {"items": [_media_item_payload(me) for me in display.mediaElements]}})


def _start_group_playback(display_id, resume_epoch=None):
    """Set the group playing now and broadcast PLAY (per-client for renderable items,
    else group-wide). No render gating here — callers ensure render readiness."""
    display = settings.displays.get(display_id)
    if not display or not display.mediaElements:
        return
    now_ms = int(time.time() * 1000)
    if resume_epoch is None:
        resume_epoch = now_ms - display.pauseOffset if display.action == PlayState.PAUSE else now_ms
    display.playStartEpoch = resume_epoch
    display.action = PlayState.PLAY
    if any(_is_renderable(me) for me in display.mediaElements):
        _broadcast_per_client_play(display_id, display)
    else:
        items = [_media_item_payload(me) for me in display.mediaElements]
        broadcast_to_display_group(display_id, {
            "REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop}})


def _stop_group_playback(display_id):
    display = settings.displays.get(display_id)
    if display:
        display.action = PlayState.STOP
        display.currentFrame = 0
        # cancel any in-flight coordinated-start prepare (don't leave stale state)
        display.prepareId = None
        display.readyClients = set()
        display.armPending = set()
        display.prepareDeadline = 0
    broadcast_to_display_group(display_id, {"REQUEST": "STOP", "PAYLOAD": {"displayID": display_id}})


def _group_online_keys(display_id):
    return {k for k, c in settings.clients.items()
            if getattr(c, "displayID", None) == display_id and getattr(c, "isOnline", False)}


def _begin_prepare(display_id):
    """Phase 1: tell the group to buffer + hold frame 0 (don't start the clock)."""
    display = settings.displays.get(display_id)
    if not display or not display.mediaElements:
        return
    display.prepareId = uuid.uuid4().hex
    display.readyClients = set()
    display.armPending = set()
    display.prepareDeadline = int(time.time() * 1000) + PREPARE_TIMEOUT_MS
    display.action = PlayState.PREPARING
    # Per-client PREPARE: each client must buffer/arm with ITS OWN rendered
    # segment URL, not the generic source (a renderable client handed the 1080p
    # source can't decode it -> MEDIA_ERR_SRC_NOT_SUPPORTED). Same URLs as the GO.
    for key, c in _group_clients(display_id):
        broadcast_to_client(key, {
            "REQUEST": "PREPARE",
            "PAYLOAD": {"prepareId": display.prepareId,
                        "items": _per_client_items(display, key, c), "loop": display.loop}})


def _release_group(display_id):
    """Phase 2: pick a shared near-future start epoch and broadcast the GO."""
    display = settings.displays.get(display_id)
    if not display:
        return
    start_epoch = int(time.time() * 1000) + RELEASE_LEAD_MS
    display.prepareId = None
    display.prepareDeadline = 0
    _start_group_playback(display_id, start_epoch)


def _maybe_release(display_id):
    display = settings.displays.get(display_id)
    if not display or display.action != PlayState.PREPARING:
        return
    online = _group_online_keys(display_id)
    # Release only when there is at least one online client AND all of them are
    # ready. The leading `online and` is required: set().issubset(x) is True, so
    # without it an empty group would release immediately.
    if online and online.issubset(display.readyClients):
        _release_group(display_id)


def _release_expired_prepares():
    """Safety net: release a PREPARE only when the timeout has elapsed AND no online
    client is still awaiting a human arming tap. Clients that reported NEEDS_ARM
    (e.g. a 1st-gen iPad that needs a real finger to start its video) are waited on
    indefinitely so the whole wall starts together once every display is armed —
    the timeout then only covers clients that went silent/stuck (never responded)."""
    now = int(time.time() * 1000)
    for display_id, display in list(settings.displays.items()):
        if display.action != PlayState.PREPARING or not display.prepareDeadline:
            continue
        online = _group_online_keys(display_id)
        if getattr(display, "armPending", set()) & online:
            continue   # someone still needs a tap -> keep holding the GO
        if now > display.prepareDeadline:
            logging.warning("PREPARE timeout for %s; releasing without %s",
                            display_id, online - display.readyClients)
            _release_group(display_id)


async def _auto_arm_client(client_key):
    """Deliver one Veency VNC tap (screen centre) to arm an un-armed iOS device.
    Best-effort: missing vncdo / no IP / failure just logs — the PREPARE timeout
    covers a device that can't be armed."""
    if not AUTO_ARM:
        return
    client = settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        return
    cx = int((getattr(client, "deviceWidth", 0) or 1024) / 2)
    cy = int((getattr(client, "deviceHeight", 0) or 768) / 2)
    target = f"{client.ip}::{VEENCY_PORT}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "vncdo", "-s", target, "-p", VEENCY_PASSWORD,
            "move", str(cx), str(cy), "click", "1",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=10)
        logging.info("auto-arm: tapped %s at %d,%d", client_key, cx, cy)
    except Exception as e:  # noqa: BLE001
        logging.warning("auto-arm tap failed for %s: %s", client_key, e)


async def _run_device_script(client_key, which):
    """Run a device's lifecycle script (which in {login,start,stop,reboot}) over
    SSH, using the per-device field (or the fleet default). Best-effort: missing
    key / no IP / SSH failure just logs. Returns (rc, output) for the caller/log."""
    client = settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        logging.warning("run-script %s %s: no client/ip", client_key, which)
        return (None, "no-ip")
    field = which + "Script"
    script = getattr(client, field, None) or DEFAULT_DEVICE_SCRIPTS.get(field)
    if not script:
        logging.warning("run-script %s %s: no script", client_key, which)
        return (None, "no-script")
    cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS +
           ["%s@%s" % (SSH_USER, client.ip), script])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        text = (out or b"").decode("utf-8", "replace").strip()
        logging.warning("run-script %s %s rc=%s: %s", client_key, which,
                        proc.returncode, text.replace("\n", " ")[:300])
        return (proc.returncode, text)
    except Exception as e:  # noqa: BLE001
        logging.warning("run-script %s %s failed: %s", client_key, which, e)
        return (None, str(e))


def _client_ip(request):
    """Best-effort client IP. Honors the first X-Forwarded-For hop (when the
    client reaches us through a proxy/tunnel that sets it), else the socket peer.
    Note: a client connecting via localhost legitimately reports 127.0.0.1 — only
    a connection that actually originates from another host shows that host's IP."""
    try:
        xff = request.headers.get('X-Forwarded-For')
        if xff:
            return xff.split(',')[0].strip()
        return request.remote
    except Exception:
        return ""


def _engine_str(eng):
    """device_detector returns the engine as a dict ({'default':'WebKit'}) for some
    UAs and a plain string ('Blink') for others — normalize to a plain string."""
    if isinstance(eng, dict):
        return str(eng.get('default', '') or '')
    return str(eng or '')


def _device_type_str(dt):
    """device_detector returns device_type as a DeviceType enum for some UAs and a
    plain string for others — normalize to its string value (e.g. 'desktop',
    'tablet', 'smartphone') so storage, display, and grouping all match."""
    return str(getattr(dt, 'value', dt) or '')


# Known iPad logical screen sizes (screen.width x screen.height as iOS reports
# them — orientation-independent, so we compare as an unordered pair). A device
# that device_detector tags as "Apple + desktop" but which also reports touch
# support and one of these sizes is a legacy iPad presenting a Mac user-agent
# (Safari's "Request Desktop Website", or old iPad Safari builds). The iPad
# identity simply isn't in that UA string, so no parser can recover it — we
# recover it from the client-reported signals instead.
_IPAD_SCREEN_SIZES = frozenset([
    frozenset((768, 1024)),    # 1st-gen, iPad 2, mini 1, and most non-Pro iPads
    frozenset((810, 1080)),    # iPad 7th-9th gen (10.2")
    frozenset((820, 1180)),    # iPad Air 4/5, iPad 10th gen (10.9")
    frozenset((834, 1112)),    # iPad Pro 10.5" / Air 3
    frozenset((834, 1194)),    # iPad Pro 11"
    frozenset((744, 1133)),    # iPad mini 6 (8.3")
    frozenset((1024, 1366)),   # iPad Pro 12.9"
])


def _is_legacy_ipad_signal(brand, device_type, width, height, touch):
    """Strict heuristic: detect a legacy iPad that presents a Mac user-agent.

    All three signals must hold (to avoid mis-tagging a genuine Mac):
    device_detector parsed it as Apple + desktop, the client reports touch
    support, AND the reported screen size matches a known iPad size. Returns
    True when the device should be reclassified as a tablet/iPad."""
    if not touch:
        return False
    if str(brand or '').lower() != 'apple':
        return False
    if str(device_type or '').lower() != 'desktop':
        return False
    try:
        dims = frozenset((int(width), int(height)))
    except (TypeError, ValueError):
        return False
    return dims in _IPAD_SCREEN_SIZES


# clientKey -> epoch after which a still-nameless client may be re-tried. Lets a
# device that was asleep at registration (so it answered neither unicast nor
# mDNS) get picked up when it wakes, without re-querying every tick.
_hostname_next_retry = {}
_HOSTNAME_RETRY_SECONDS = 60

# Lazily-created, long-lived Zeroconf listener for the mDNS reverse fallback.
_zeroconf = None
_zeroconf_lock = threading.Lock()


def _reverse_dns(ip):
    """Blocking reverse-DNS (PTR) lookup. Returns the hostname, or "" when the
    IP has no PTR record / lookup fails. MUST be called via run_in_executor —
    socket.gethostbyaddr blocks and can hang for seconds on IPs with no record."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _short_hostname(ptr):
    """Friendly short name from a PTR: drop the trailing dot and the domain,
    keeping the first label. 'Jons-iPad.lan.' -> 'Jons-iPad'. "" -> ""."""
    if not ptr:
        return ""
    return str(ptr).rstrip('.').split('.')[0]


def _adopt_hostname_as_name(client, short_host):
    """True when a resolved short hostname should become the client's
    friendlyName: there is a hostname AND the user hasn't set a custom name.
    (migrate_client_objects marks pre-existing custom names so they're safe.)"""
    if not short_host:
        return False
    return not getattr(client, 'nameIsCustom', False)


def _is_private_ipv4(ip):
    """True for RFC1918 / link-local IPv4 — the only addresses worth an mDNS
    (multicast) reverse query. Avoids pointless multicast for public IPs."""
    parts = str(ip).split('.')
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) \
        or (a == 169 and b == 254)


def _in_addr_arpa(ip):
    """IPv4 -> reverse-DNS query name. '192.168.1.50' -> '50.1.168.192.in-addr.arpa.'"""
    return '.'.join(str(ip).split('.')[::-1]) + '.in-addr.arpa.'


def _get_zeroconf():
    """Lazily create one shared, long-lived Zeroconf listener (binds mDNS port
    5353 and joins the multicast group). Reused for every reverse query so we
    don't churn sockets. Returns None if zeroconf is unavailable."""
    global _zeroconf
    if _zeroconf is None:
        with _zeroconf_lock:
            if _zeroconf is None:
                from zeroconf import Zeroconf
                _zeroconf = Zeroconf()
    return _zeroconf


def _mdns_reverse(ip, wait=1.5):
    """mDNS reverse PTR lookup for a LAN IPv4 (Bonjour/.local devices that
    aren't in unicast DNS, e.g. Apple displays). Returns the hostname or "".
    Blocking (sleeps while awaiting the multicast reply) — call via
    run_in_executor. The device must be awake to answer (iOS goes quiet when
    asleep)."""
    if not _is_private_ipv4(ip):
        return ""
    try:
        from zeroconf import DNSOutgoing, DNSQuestion
        import zeroconf.const as zc_const
        name = _in_addr_arpa(ip)
        zc = _get_zeroconf()
        if zc is None:
            return ""
        # Maybe already heard passively; otherwise ask and wait for the reply.
        recs = zc.cache.get_all_by_details(name, zc_const._TYPE_PTR, zc_const._CLASS_IN)
        if not recs:
            out = DNSOutgoing(zc_const._FLAGS_QR_QUERY)
            out.add_question(DNSQuestion(name, zc_const._TYPE_PTR, zc_const._CLASS_IN))
            zc.send(out)
            time.sleep(wait)
            recs = zc.cache.get_all_by_details(name, zc_const._TYPE_PTR, zc_const._CLASS_IN)
        for r in recs:
            alias = getattr(r, 'alias', '') or ''
            if alias:
                return alias.rstrip('.')
        return ""
    except Exception as e:
        logging.debug("mDNS reverse lookup failed for %s: %s", ip, e)
        return ""


# Config carried from an old (offline) client onto the reconnecting one, so a
# device keeps its setup across a browser cache-clear (which yields a fresh
# client id). Live identity fields (clientID, ip, hostname, device*, online,
# connectionCount) are NOT copied — the new record keeps those.
_MERGE_FIELDS = ("displayID", "measuredCenter", "measuredPerimeter", "arucoID",
                 "capabilities", "loginScript", "startScript", "stopScript",
                 "rebootScript", "testScript")


def _merge_reconnected_client(new_key, new_client):
    """A device that clears its browser reconnects with a new client id. Once it
    resolves to the same hostname (and matching device attributes) as another
    client, fold that old client's config (group, calibration, custom name) onto
    the live new record and drop the old one. Returns the merged-away old key,
    or None. Match key is hostname + deviceType + resolution, regardless of the
    old client's online state (so a duplicate is collapsed immediately, without
    waiting for socket-close detection). Identity therefore rests on hostname
    uniqueness: two distinct devices that resolve to the SAME hostname with the
    same attributes would be treated as one."""
    host = (getattr(new_client, "hostname", "") or "").lower()
    if not host or not getattr(new_client, "isOnline", False):
        return None
    for old_key, old in list(settings.clients.items()):
        if old_key == new_key:
            continue
        if (getattr(old, "hostname", "") or "").lower() != host:
            continue
        if (old.deviceType != new_client.deviceType
                or old.deviceWidth != new_client.deviceWidth
                or old.deviceHeight != new_client.deviceHeight):
            continue
        for f in _MERGE_FIELDS:
            if hasattr(old, f):
                setattr(new_client, f, getattr(old, f))
        # Carry a user-set name; otherwise keep the new record's (DNS-derived) one.
        if getattr(old, "nameIsCustom", False) and old.friendlyName:
            new_client.friendlyName = old.friendlyName
            new_client.nameIsCustom = True
        new_client.discoveryTime = min(getattr(new_client, "discoveryTime", time.time()),
                                       getattr(old, "discoveryTime", time.time()))
        del settings.clients[old_key]
        logging.info("Merged reconnected client %s into prior %s (hostname %s)",
                     new_key, old_key, host)
        return old_key
    return None


async def resolve_client_hostnames(unicast_timeout=2.0, mdns_timeout=3.0):
    """Resolve every unresolved client IP off the event loop and adopt the short
    hostname as the friendlyName when it's still auto-generated. Tries unicast
    reverse DNS first (fast, authoritative); falls back to an mDNS reverse PTR
    for LAN devices not in unicast DNS (Bonjour/.local). One lookup per unique
    IP per pass; resolved clients short-circuit, still-nameless ones retry every
    ~60s (catches devices that were asleep). Persists only when a name changed."""
    loop = asyncio.get_event_loop()
    now = time.time()
    targets = []
    for key, client in list(settings.clients.items()):
        ip = getattr(client, 'ip', '') or ''
        if not ip or getattr(client, 'hostnameResolved', False):
            continue
        if now < _hostname_next_retry.get(key, 0):
            continue
        targets.append((key, client, ip))
    if not targets:
        return

    async def _resolve(ip):
        # 1) unicast reverse DNS — first choice
        try:
            host = await asyncio.wait_for(
                loop.run_in_executor(None, _reverse_dns, ip), unicast_timeout)
        except Exception:
            host = ""
        # 2) mDNS reverse fallback — for .local / Bonjour devices
        if not host:
            try:
                host = await asyncio.wait_for(
                    loop.run_in_executor(None, _mdns_reverse, ip), mdns_timeout)
            except Exception:
                host = ""
        return ip, host

    unique = {}
    for _, _, ip in targets:
        unique.setdefault(ip, "")
    for ip, host in await asyncio.gather(*[_resolve(ip) for ip in unique]):
        unique[ip] = host

    changed = False
    for key, client, ip in targets:
        host = unique.get(ip) or ""
        if host:
            client.hostname = host
            client.hostnameResolved = True
            _hostname_next_retry.pop(key, None)
            short = _short_hostname(host)
            if _adopt_hostname_as_name(client, short):
                client.friendlyName = short
                changed = True
            # Re-bind a browser-cache-cleared device to its prior record.
            merged_old = _merge_reconnected_client(key, client)
            if merged_old:
                changed = True
                try:
                    socketmanager.broadcast(jsonpickle.encode(
                        {"REQUEST": "DEVICE_REMOVED", "PAYLOAD": {"clientKey": merged_old}}))
                except Exception:
                    pass
        else:
            _hostname_next_retry[key] = now + _HOSTNAME_RETRY_SECONDS
    if changed:
        saveSettings()


def msg_response(msg,session):
    clientid = session.id
    logging.debug(session.request.headers['User-Agent'])
    
    response = {"DEST":clientid,"REQUEST": msg["REQUEST"], "PAYLOAD": {}}
    
    logging.debug(session.request.remote)
    logging.debug(msg["SRC"])
    
    if(msg["REQUEST"] == "SERVERTIME"):
        response["PAYLOAD"] = int(time.time()*1000)
        
    elif(msg["REQUEST"] == "DISPLAYS"):
        response["PAYLOAD"] = settings.displays
        
    elif(msg["REQUEST"] == "IDENTIFYDISPLAY"):
        identifyDisplays(msg["PAYLOAD"]['group'],msg["PAYLOAD"]['id'])
        
    elif(msg["REQUEST"] == "UPDATEDISPLAY"):
        # Cache client reference to avoid repeated dictionary lookups
        client_id = msg["PAYLOAD"]["clientID"]
        client = settings.clients.get(client_id)
        if client:
            if('friendlyName' in msg["PAYLOAD"]):
                client.friendlyName = msg["PAYLOAD"]["friendlyName"]
                client.nameIsCustom = True   # user-set name: DNS won't override it
            if('displayID' in msg["PAYLOAD"]):
                client.displayID = msg["PAYLOAD"]["displayID"]
    
    elif(msg["REQUEST"] == "UPDATEDISPLAYGROUP"):
        if(msg["PAYLOAD"]["newID"] != 'Default'):
            if(msg["PAYLOAD"]["newID"] is not None):
                settings.displays.setdefault(msg["PAYLOAD"]["newID"], Display())
                if(msg["PAYLOAD"]["oldID"] in settings.displays):
                    settings.displays[msg["PAYLOAD"]["newID"]] = settings.displays.pop(msg["PAYLOAD"]["oldID"])
            else:
                settings.displays.pop(msg["PAYLOAD"]["oldID"])
                for key in settings.clients.keys():
                    if(settings.clients[key].displayID == msg["PAYLOAD"]["oldID"]):
                        settings.clients[key].displayID = 'Default'
            
    elif(msg["REQUEST"] == "CLIENTS"):
        logging.debug(msg["PAYLOAD"])
        if 'PAYLOAD' not in msg:
            response["PAYLOAD"] = settings.clients[msg["PAYLOAD"]]
        else:
            response["PAYLOAD"] = settings.clients
            
    elif(msg["REQUEST"] == "SYN"):
        client = settings.clients.get(msg["PAYLOAD"])
        if client:
            client.synced = False
        response["PAYLOAD"] = "ACK"

    elif(msg["REQUEST"] == "SYNACK"):
        client = settings.clients.get(msg["PAYLOAD"])
        if client:
            client.synced = True
        response["PAYLOAD"] = "SYNACK"

    elif(msg["REQUEST"] == "REMOVE_CLIENT"):
        # Admin-initiated removal of a single device. The device re-registers
        # (as new) if it ever reconnects — this only clears the stale record.
        payload = msg.get("PAYLOAD") or {}
        target = payload.get("clientID") if isinstance(payload, dict) else payload
        removed = settings.clients.pop(target, None)
        response["PAYLOAD"] = {"removed": target if removed is not None else None}
        if removed is not None:
            saveSettings()
            logging.info(f"Removed client {target} (admin request)")
            socketmanager.broadcast(jsonpickle.encode(
                {"REQUEST": "DEVICE_REMOVED", "PAYLOAD": {"clientKey": target}}))

    elif(msg["REQUEST"] == "CLEAR_OFFLINE_CLIENTS"):
        # Bulk-purge every currently-offline device (max_age 0 = any age).
        count = cleanup_old_clients(max_age_seconds=0)
        response["PAYLOAD"] = {"removed": count}
        if count:
            socketmanager.broadcast(jsonpickle.encode(
                {"REQUEST": "DEVICE_REMOVED", "PAYLOAD": {"cleared": count}}))

    elif(msg["REQUEST"] == "REGISTER"):
        is_new_client = msg["SRC"] not in settings.clients
        settings.clients.setdefault(msg["SRC"], Client())
        # Cache client reference to avoid repeated dictionary lookups
        client = settings.clients[msg["SRC"]]
        
        # Enhanced registration with discovery tracking
        client.clientID = clientid
        client.userAgent = session.request.headers['User-Agent']
        client.deviceWidth = msg["PAYLOAD"]["width"]
        client.deviceHeight = msg["PAYLOAD"]["height"]
        # Rendered viewport (older clients omit it -> fall back to device res)
        client.canvasWidth = msg["PAYLOAD"].get("canvasWidth") or client.deviceWidth
        client.canvasHeight = msg["PAYLOAD"].get("canvasHeight") or client.deviceHeight
        _new_ip = _client_ip(session.request)
        if _new_ip != getattr(client, 'ip', ''):
            client.hostnameResolved = False   # new IP -> re-resolve its hostname
        client.ip = _new_ip
        client.lastSeen = time.time()
        client.isOnline = True
        client.connectionCount += 1
        
        # Device detection and fingerprinting
        device = DeviceDetector(session.request.headers['User-Agent']).parse()
        client.osName = device.os_name()
        client.osVersion = device.os_version()
        client.engine = _engine_str(device.engine())
        client.deviceBrand = device.device_brand()
        client.deviceModel = device.device_model()
        client.deviceType = _device_type_str(device.device_type())

        # Recover legacy iPads that present a Mac user-agent (e.g. Safari
        # "Request Desktop Website"). The iPad identity is absent from such a
        # UA, so we reclassify from client-reported touch + screen-size signals
        # BEFORE auto_configure_client runs (it groups by deviceType).
        client.touch = bool(msg["PAYLOAD"].get("touch", False))
        if _is_legacy_ipad_signal(client.deviceBrand, client.deviceType,
                                  client.deviceWidth, client.deviceHeight, client.touch):
            client.deviceType = "tablet"
            if not client.deviceModel:
                client.deviceModel = "iPad"
            logging.info(f"Reclassified {msg['SRC']} as iPad (Apple+desktop UA, "
                         f"touch, {client.deviceWidth}x{client.deviceHeight})")

        # Auto-configuration for new clients
        if is_new_client:
            client.discoveryTime = time.time()
            auto_configure_client(msg["SRC"], client)
            _apply_default_scripts(client)   # backfill login/start/stop/reboot defaults

            # Notify admin interface of new device
            new_device_notification = {
                "REQUEST": "NEW_DEVICE_CONFIGURED",
                "PAYLOAD": {
                    "clientKey": msg["SRC"],
                    "friendlyName": client.friendlyName,
                    "deviceType": client.deviceType,
                    "displayID": client.displayID,
                    "autoConfigured": True
                }
            }
            socketmanager.broadcast(jsonpickle.encode(new_device_notification))

        # Sync EVERY (re)connecting client to its group, not just first-timers: a
        # reload/reconnect mid-playback must resume (re-send PRELOAD + PLAY with the
        # in-progress epoch). Idempotent — no-op unless the group is currently PLAY.
        sync_new_client_to_group(msg["SRC"], client)

        # Enhanced success response with configuration info
        response["PAYLOAD"] = {
            "status": "SUCCESS",
            "displayID": client.displayID,
            "friendlyName": client.friendlyName,
            "autoConfigured": client.autoConfigured,
            "capabilities": client.capabilities
        }
        
    elif(msg["REQUEST"] == "UPDATECLIENT"):
        # Cache client reference to avoid repeated dictionary lookups
        client = settings.clients.get(msg["SRC"])
        if client:
            for settingKey in msg["PAYLOAD"]:
                setattr(client, settingKey, msg["PAYLOAD"][settingKey])
            client.clientID = clientid
            response["PAYLOAD"] = client
        
    elif(msg["REQUEST"] == "GENERATEARUCO"):
        generateAruco(msg["PAYLOAD"]["id"])
        
    elif(msg["REQUEST"] == "READY"):
        # Client signals its media is cached and it is ready to display
        client = settings.clients.get(msg["SRC"])
        if client:
            client.ready = True
        did = getattr(client, "displayID", None) if client else None
        display = settings.displays.get(did)
        if display and display.action == PlayState.PREPARING \
                and (msg.get("PAYLOAD") or {}).get("prepareId") == display.prepareId:
            display.readyClients.add(msg["SRC"])
            display.armPending.discard(msg["SRC"])   # armed now (was awaiting a tap)
            _maybe_release(did)
        response["PAYLOAD"]="SUCCESS"

    elif(msg["REQUEST"] == "NEEDS_ARM"):
        client = settings.clients.get(msg["SRC"])
        display = settings.displays.get(getattr(client, "displayID", None)) if client else None
        if display and display.action == PlayState.PREPARING \
                and (msg.get("PAYLOAD") or {}).get("prepareId") == display.prepareId:
            # Mark this client as awaiting a HUMAN arming tap so the GO timeout won't
            # release the wall without it (see _release_expired_prepares).
            display.armPending.add(msg["SRC"])
            asyncio.ensure_future(_auto_arm_client(msg["SRC"]))

    elif(msg["REQUEST"] == "CLIENTLOG"):
        # Client-side debug stream (opt-in via ?tdbg). Surfaced in the server log
        # so device state (video error/readyState/events) is visible without the
        # operator relaying an on-screen HUD. No state change.
        logging.warning("CLIENTLOG %s %s", msg.get("SRC"), msg.get("PAYLOAD"))

    elif(msg["REQUEST"] == "DISCOVERY_STATUS"):
        # Return discovery information for all clients
        response["PAYLOAD"] = get_discovered_devices()
        
    elif(msg["REQUEST"] == "RECONFIGURE_CLIENT"):
        # Force reconfiguration of a client
        client = settings.clients.get(msg["PAYLOAD"]["clientKey"])
        if client:
            client.autoConfigured = False
            auto_configure_client(msg["PAYLOAD"]["clientKey"], client)
            response["PAYLOAD"] = {"status": "SUCCESS", "reconfigured": True}
        else:
            response["PAYLOAD"] = {"status": "ERROR", "message": "Client not found"}
    
    elif(msg["REQUEST"] == "BULK_CONFIGURE"):
        # Configure multiple clients at once
        configured_count = 0
        for client_key in msg["PAYLOAD"]["clientKeys"]:
            client = settings.clients.get(client_key)
            if client:
                client.autoConfigured = False
                auto_configure_client(client_key, client)
                configured_count += 1
        response["PAYLOAD"] = {"status": "SUCCESS", "configured": configured_count}
        
    elif(msg["REQUEST"] == "REPORT_CANVAS"):
        # Client re-reporting its viewport size (e.g. after going full screen for
        # calibration). Keep canvasWidth/Height fresh so calibrate() reconstructs
        # the screen quad from the marker using the dims actually photographed.
        client = settings.clients.get(msg["SRC"])
        if client is not None:
            payload = msg.get("PAYLOAD") or {}
            cw = payload.get("canvasWidth")
            ch = payload.get("canvasHeight")
            if cw and ch:
                client.canvasWidth = int(cw)
                client.canvasHeight = int(ch)
                save_settings_incremental()

    elif(msg["REQUEST"] == "SETPLAYLIST"):
        payload = msg["PAYLOAD"]
        display_id = payload["displayID"]
        display = settings.displays.setdefault(display_id, Display())
        display.mediaElements = _build_media_elements(payload.get("items", []))
        display.loop = bool(payload.get("loop", False))
        display.renderedToken = ""  # playlist changed -> needs (re)render
        broadcast_to_display_group(display_id, {
            "REQUEST": "PRELOAD",
            "PAYLOAD": {"items": [_media_item_payload(me) for me in display.mediaElements]}
        })
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "PLAY"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if not display or not display.mediaElements:
            response["PAYLOAD"] = "SUCCESS"
        else:
            now_ms = int(time.time() * 1000)
            resume_epoch = now_ms - display.pauseOffset if display.action == PlayState.PAUSE else now_ms
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and display.renderStatus == "rendering":
                response["PAYLOAD"] = {"status": "RENDER_IN_PROGRESS", "displayID": display_id}
            elif has_renderable and compute_render_token(display_id) != display.renderedToken:
                response["PAYLOAD"] = {"status": "RENDER_REQUIRED", "displayID": display_id}
            else:
                if display.action == PlayState.PAUSE:
                    _start_group_playback(display_id, resume_epoch)   # resume: direct, today's path
                else:
                    _begin_prepare(display_id)                        # fresh start: coordinated
                response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "STOP"):
        display_id = msg["PAYLOAD"]["displayID"]
        _stop_group_playback(display_id)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "PAUSE"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if display and display.action == PlayState.PLAY:
            display.pauseOffset = int(time.time() * 1000) - display.playStartEpoch
            display.action = PlayState.PAUSE
        broadcast_to_display_group(display_id, {
            "REQUEST": "PAUSE", "PAYLOAD": {"displayID": display_id}
        })
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "RELOAD"):
        # Admin command: tell display clients to hard-reload so they pick up new
        # client JS/HTML. Scoped to one display group when PAYLOAD.displayID is
        # given (only that group's members reload), otherwise every connected
        # client via DEST="ALL". The client reloads only on a RELOAD addressed to
        # it or to "ALL", so the control console isn't reloaded by a group reload.
        payload = msg.get("PAYLOAD")
        display_id = payload.get("displayID") if isinstance(payload, dict) else None
        if display_id:
            broadcast_to_display_group(display_id, {"REQUEST": "RELOAD", "PAYLOAD": "NONE"})
        else:
            socketmanager.broadcast(jsonpickle.encode(
                {"DEST": "ALL", "REQUEST": "RELOAD", "PAYLOAD": "NONE"}))
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "RUN_SCRIPT"):
        # Admin command: run a device lifecycle script over SSH. PAYLOAD =
        # {script: "login"|"start"|"stop"|"reboot"} plus a target: {clientKey} for
        # one device, {displayID} for a whole group, or {all:true} for the fleet.
        payload = msg.get("PAYLOAD") or {}
        which = payload.get("script")
        if which not in ("login", "start", "stop", "reboot", "test"):
            response["PAYLOAD"] = {"status": "BAD_REQUEST"}
        else:
            ck = payload.get("clientKey")
            did = payload.get("displayID")
            if ck:
                keys = [ck] if ck in settings.clients else []
            elif did:
                keys = [k for k, c in settings.clients.items()
                        if getattr(c, "displayID", None) == did]
            elif payload.get("all"):
                keys = list(settings.clients.keys())
            else:
                keys = []
            for k in keys:
                asyncio.ensure_future(_run_device_script(k, which))
            logging.warning("RUN_SCRIPT %s -> %d device(s)", which, len(keys))
            response["PAYLOAD"] = {"status": "SUCCESS", "script": which, "count": len(keys)}

    elif(msg["REQUEST"] == "RENDER"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if not display or not display.mediaElements:
            response["PAYLOAD"] = {"status": "ERROR", "error": "no playlist"}
        elif not display.boundingBox:
            response["PAYLOAD"] = {"status": "ERROR", "error": "no calibration"}
        elif not any(_is_renderable(me) for me in display.mediaElements):
            response["PAYLOAD"] = {"status": "ERROR",
                                   "error": "nothing to render — Mirror/Animation play directly, just press Play"}
        elif not [c for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]:
            response["PAYLOAD"] = {"status": "ERROR", "error": "no calibrated screens"}
        elif display.renderStatus == "rendering":
            response["PAYLOAD"] = {"status": "rendering"}
        else:
            asyncio.ensure_future(render_group_async(display_id))
            response["PAYLOAD"] = {"status": "rendering"}

    elif(msg["REQUEST"] == "LIST_PLAYLISTS"):
        rows = []
        for name, pl in settings.playlists.items():
            items = pl.items or []
            has_segment = any(it.get("playmode") in ("SEGMENT", "INDIVIDUAL") for it in items)
            rows.append({"name": name, "itemCount": len(items),
                         "hasSegment": has_segment})
        response["PAYLOAD"] = rows

    elif(msg["REQUEST"] == "GET_PLAYLIST"):
        pl = settings.playlists.get(msg["PAYLOAD"].get("name"))
        if pl is None:
            response["PAYLOAD"] = {"error": "not found"}
        else:
            response["PAYLOAD"] = {"name": pl.name, "items": pl.items, "loop": pl.loop}

    elif(msg["REQUEST"] == "SAVE_PLAYLIST"):
        payload = msg["PAYLOAD"]
        name = (payload.get("name") or "").strip()
        if not name:
            response["PAYLOAD"] = {"error": "name required"}
        else:
            pl = settings.playlists.setdefault(name, Playlist())
            pl.name = name
            pl.items = payload.get("items", [])
            pl.loop = bool(payload.get("loop", False))
            response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "DELETE_PLAYLIST"):
        settings.playlists.pop(msg["PAYLOAD"].get("name"), None)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "ASSIGN_PLAYLIST"):
        payload = msg["PAYLOAD"]
        display_id = payload.get("displayID")
        pl = settings.playlists.get(payload.get("name"))
        if pl is None or display_id is None:
            response["PAYLOAD"] = {"status": "error", "displayID": display_id}
        else:
            _apply_playlist(display_id, pl)
            display = settings.displays.get(display_id)
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and not display.boundingBox:
                status = "NOT_CALIBRATED"
            elif has_renderable and compute_render_token(display_id) != display.renderedToken:
                status = "RENDER_REQUIRED"
            else:
                status = "ok"
            response["PAYLOAD"] = {"status": status, "displayID": display_id}

    elif(msg["REQUEST"] == "LIST_SCHEDULES"):
        now = datetime.datetime.now()
        rows = []
        for sid, s in settings.schedules.items():
            rows.append({"id": s.id, "name": s.name, "playlistName": s.playlistName,
                         "displayID": s.displayID, "priority": s.priority, "enabled": s.enabled,
                         "activeNow": bool(getattr(s, "enabled", True)) and schedule_active_at(s, now)})
        response["PAYLOAD"] = rows

    elif(msg["REQUEST"] == "GET_SCHEDULE"):
        s = settings.schedules.get(msg["PAYLOAD"].get("id"))
        if s is None:
            response["PAYLOAD"] = {"error": "not found"}
        else:
            response["PAYLOAD"] = {"id": s.id, "name": s.name, "playlistName": s.playlistName,
                                   "displayID": s.displayID, "priority": s.priority, "enabled": s.enabled,
                                   "freq": s.freq, "interval": s.interval, "byweekday": s.byweekday,
                                   "dtstart": s.dtstart, "end": s.end, "exdates": s.exdates,
                                   "startTime": s.startTime, "endTime": s.endTime}

    elif(msg["REQUEST"] == "SAVE_SCHEDULE"):
        p = msg["PAYLOAD"]
        ok = True; err = None
        try:
            if _hhmm_to_min(p.get("startTime", "00:00")) >= _hhmm_to_min(p.get("endTime", "23:59")):
                ok = False; err = "endTime must be after startTime"
        except Exception:
            ok = False; err = "bad time"
        if ok and p.get("freq") not in _FREQ_MAP:
            ok = False; err = "bad frequency"
        if ok and p.get("freq") == "WEEKLY" and not p.get("byweekday"):
            ok = False; err = "weekly schedule needs at least one weekday"
        if ok:
            try:
                _parse_date(p.get("dtstart"))
            except Exception:
                ok = False; err = "bad start date"
        if ok:
            probe = Schedule()
            for k in ("freq", "interval", "byweekday", "dtstart", "end"):
                setattr(probe, k, p.get(k, getattr(probe, k)))
            probe.startTime = p.get("startTime", "00:00"); probe.endTime = p.get("endTime", "23:59")
            try:
                schedule_active_at(probe, datetime.datetime.now())  # compiles the rrule
            except Exception as e:
                ok = False; err = "bad recurrence: " + str(e)
        if not ok:
            response["PAYLOAD"] = {"error": err}
        else:
            sid = p.get("id") or ("sch_" + uuid.uuid4().hex[:10])
            s = settings.schedules.setdefault(sid, Schedule())
            s.id = sid
            for k in ("name", "playlistName", "displayID", "priority", "enabled",
                      "freq", "interval", "byweekday", "dtstart", "end", "exdates",
                      "startTime", "endTime"):
                if k in p:
                    setattr(s, k, p[k])
            try:
                s.priority = int(s.priority)
            except (TypeError, ValueError):
                s.priority = 0
            response["PAYLOAD"] = {"id": sid}

    elif(msg["REQUEST"] == "DELETE_SCHEDULE"):
        settings.schedules.pop(msg["PAYLOAD"].get("id"), None)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "GET_GROUP_DEFAULTS"):
        response["PAYLOAD"] = [{"displayID": did, "defaultPlaylistName": getattr(d, "defaultPlaylistName", None)}
                               for did, d in settings.displays.items()]

    elif(msg["REQUEST"] == "SET_GROUP_DEFAULT"):
        p = msg["PAYLOAD"]
        display = settings.displays.get(p.get("displayID"))
        if display is not None:
            display.defaultPlaylistName = (p.get("playlistName") or "").strip() or None
        response["PAYLOAD"] = "SUCCESS"

    else:
        response["PAYLOAD"] = msg["PAYLOAD"]    #echo anything that isn't a registered command

    return jsonpickle.encode(response)

# --- Structured async message protocol (intended replacement for msg_response) ---
# Existing JS clients still speak the REQUEST-based protocol above; this handler
# is additive for the newer 'type'-based clients until they fully migrate.

class _DeviceDetectorWrapper:
    """Adapts DeviceDetector to a parse(user_agent) call.

    Exposed as a module-level singleton (`device_detector`) so it can be
    injected/mocked in tests without constructing a DeviceDetector per call.
    """
    def parse(self, user_agent):
        return DeviceDetector(user_agent or "").parse()

device_detector = _DeviceDetectorWrapper()

def _device_field(value):
    """Resolve a DeviceDetector field (callable method or plain value),
    returning it only if it is a string. Guards against mocks / unexpected
    types leaking non-serializable objects into client state."""
    resolved = value() if callable(value) else value
    return resolved if isinstance(resolved, str) else None

async def handle_websocket_message(session, message_data):
    """Dispatch a structured ('type'-based) WebSocket message.

    Per-client delivery still flows through the central socketmanager + DEST
    routing used elsewhere; direct replies use session.send.
    """
    if not isinstance(message_data, dict):
        return  # ignore malformed frames without raising

    msg_type = message_data.get('type')

    if msg_type == 'clientInfo':
        client = settings.clients.setdefault(session.id, Client())
        client.clientID = session.id
        client.friendlyName = message_data.get('friendlyName', client.friendlyName)
        client.deviceWidth = message_data.get('deviceWidth', client.deviceWidth)
        client.deviceHeight = message_data.get('deviceHeight', client.deviceHeight)
        client.deviceType = message_data.get('deviceType', client.deviceType)
        client.userAgent = message_data.get('userAgent', client.userAgent)
        if getattr(session, 'request', None) is not None:
            client.ip = _client_ip(session.request)
        client.lastSeen = time.time()
        client.isOnline = True
        client.connectionCount += 1
        # Best-effort fingerprinting (fields may be methods or plain values)
        try:
            device = device_detector.parse(client.userAgent)
            client.osName = _device_field(device.os_name) or client.osName
            client.osVersion = _device_field(device.os_version) or client.osVersion
            client.deviceBrand = _device_field(device.device_brand) or client.deviceBrand
            client.deviceModel = _device_field(device.device_model) or client.deviceModel
            detected_type = _device_type_str(_device_field(device.device_type))
            if detected_type and not message_data.get('deviceType'):
                client.deviceType = detected_type
        except Exception as e:
            logging.debug(f"Device detection skipped: {e}")
        auto_configure_client(session.id, client)

    elif msg_type == 'ready':
        client = settings.clients.get(session.id)
        if client:
            client.ready = message_data.get('ready', True)
            client.lastSeen = time.time()

    elif msg_type == 'heartbeat':
        client = settings.clients.get(session.id)
        if client:
            client.lastSeen = time.time()
            client.isOnline = True
        await session.send(jsonpickle.encode({"REQUEST": "HEARTBEAT", "PAYLOAD": "ACK"}))

    elif msg_type == 'displayData':
        # Relay to peers in the same display group via the central manager
        display_id = message_data.get('displayID')
        if socketmanager is not None and display_id is not None:
            broadcast_to_display_group(display_id, {
                "REQUEST": "displayData",
                "PAYLOAD": message_data.get('data')
            })
        await session.send(jsonpickle.encode({"REQUEST": "displayData", "PAYLOAD": "ACK"}))

    else:
        logging.debug(f"Unknown websocket message type: {msg_type}")

async def index_handler(request):
    logging.debug("INDEX_HANDLER")
    fileName = request.match_info.get('page', "index.html")
    
    data = '404 Not Found'
    
    if(fileName == "time"):
        return web.Response(body=str(int(time.time()*1000)), content_type='text/html')
    
    root, ext = os.path.splitext(fileName)
    if not ext:
        fileName = fileName+'.html'

    logging.debug(fileName)

    ct = 'application/octet-stream'
    
    if( os.path.isfile(fileName)):
        cached_data = get_cached_file(fileName)
        if cached_data is not None:
            data = cached_data
            if(fileName.endswith('.html')):
                ct = 'text/html'
            elif(fileName.endswith('.js')):
                ct = 'application/javascript'
            elif(fileName.endswith('.css')):
                ct = 'text/css'

    logging.debug(ct)
    return web.Response(body=data,content_type=ct)

async def image_handler(request):
    logging.debug("IMAGE_HANDLER")
    fileName = request.match_info.get('src')
    fileName = os.path.join('images',fileName)
    
    data = None
    ct = 'application/octet-stream'
    
    if( os.path.isfile(fileName)):
        data = get_cached_file(fileName)
        if data is not None:
            if(fileName.endswith('.ico')):
                ct = 'image/ico'
            elif(fileName.endswith('.jpg')):
                ct = 'image/jpeg'
            elif(fileName.endswith('.png')):
                ct = 'image/png'
    
    if data is None:
        return web.Response(status=404, reason='NOT FOUND')
        
    return web.Response(body=data,content_type=ct)

async def media_handler(request):
    logging.debug("MEDIA_HANDLER")
    
    client = request.match_info.get('client') or "common"
    fileName = request.match_info.get('file')
    
    subdir = "images"
    
    customHeaders = None
    customStatus = 200
    data = None
    
    _fn = fileName.lower()
    _video_ct = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.m4v': 'video/x-m4v',
                 '.webm': 'video/webm', '.ogv': 'video/ogg'}
    _vext = next((e for e in _video_ct if _fn.endswith(e)), None)
    if(_fn.endswith('.jpg') or _fn.endswith('.jpeg')):
        customHeaders = {'Content-Type':'image/jpeg'}
    elif(_fn.endswith('.png')):
        customHeaders = {'Content-Type':'image/png'}
    elif(_vext):
        customHeaders = {'Content-Type': _video_ct[_vext]}
        subdir = "videos"
    else:
        customHeaders = {'Content-Type':'application/octet-stream'}

    # Three-segment form /media/<client>/<sub>/<file> carries the subdir
    # explicitly (that's how the media library URLs from /api/media look, e.g.
    # /media/server/videos/clip.mov). Honor it over the extension guess so those
    # source URLs resolve instead of 404ing.
    _sub = request.match_info.get('sub')
    if _sub:
        subdir = _sub

    logging.debug("media/"+client+"/"+subdir+"/"+fileName)
    
    if(not os.path.isfile("media/"+client+"/"+subdir+"/"+fileName)):
        response = web.Response(
            status=404,
            reason='NOT FOUND'
        )
        return response
    file_path = f"media/{client}/{subdir}/{fileName}"
    logging.debug(request.http_range)
    
    try:
        _rng = request.http_range
        if _rng.start is not None or _rng.stop is not None:
            # Honor BOTH bounded ("bytes=0-1023") and OPEN-ENDED ("bytes=512-")
            # ranges. Browsers seek with open-ended ranges (stop=None); the old
            # `if request.http_range.stop:` missed those and returned the whole
            # file as 200 from byte 0 -> the client (Chrome) treats a seek as a
            # full reload and restarts playback at 0. We must answer 206.
            file_size = os.path.getsize(file_path)
            start = _rng.start
            stop = _rng.stop
            if start is None:                      # suffix range "bytes=-N" (last N bytes)
                start = max(0, file_size - (stop or 0))
                stop = file_size
            else:
                if start < 0:
                    start = 0
                if stop is None or stop > file_size:
                    stop = file_size               # open-ended -> read to EOF
            # NB: no chunk cap. Returning fewer bytes than an open-ended range
            # requested makes Chrome-for-iOS (UIWebView) treat the 206 as a
            # truncated file -> MEDIA_ERR_SRC_NOT_SUPPORTED. Segments are a few MB
            # to ~80MB (no all-intra), so reading to EOF is acceptable.
            logging.debug(f'Range {start}-{stop-1}/{file_size}')
            customHeaders['Accept-Ranges'] = 'bytes'
            customHeaders['Content-Range'] = f'bytes {start}-{stop-1}/{file_size}'
            customStatus = 206
            # Use pooled file handle for better performance
            handle = get_pooled_file_handle(file_path, 'rb')
            handle.seek(start)
            data = handle.read(stop - start)
        else:
            # For small files, use caching; for large video files, stream directly
            if subdir == "images" or os.path.getsize(file_path) < 10 * 1024 * 1024:  # 10MB threshold
                data = get_cached_file(file_path)
            else:
                handle = get_pooled_file_handle(file_path, 'rb')
                handle.seek(0)
                data = handle.read()
    except (OSError, IOError) as e:
        logging.error(f"Error reading file {file_path}: {e}")
        return web.Response(status=500, reason='Internal Server Error')
            
    response = web.Response(
        status=customStatus,
        reason='OK',
        headers=customHeaders,
        body = data
    )
    
    return response

async def javascript_handler(request):
    logging.debug("JAVASCRIPT_HANDLER")
    fileName = request.match_info.get('src')
    logging.debug(fileName)
    file_path = 'js/' + fileName
    
    if( os.path.isfile(file_path)):
        data = get_cached_file(file_path)
        if data is not None:
            return web.Response(body=data, content_type='text/javascript')
    
    return web.Response(status=404, reason='NOT FOUND')

def generateAruco(displayID = None):
    # Load the predefined dictionary
    dictionary = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_6X6_50)
    # Assign each client a GLOBALLY-UNIQUE arucoID. A client keeps its existing
    # id unless that id is already taken by an earlier client (resolves
    # collisions left by the old counter, which handed out duplicates across
    # runs as clients reconnected/merged). calibrate() maps markers back by this
    # id, so uniqueness is what lets each screen be identified.
    taken = set()
    for key in settings.clients.keys():
        client = settings.clients[key]
        aid = client.arucoID
        if aid is None or aid in taken:
            n = 1
            while n in taken:
                n += 1
            client.arucoID = n
            aid = n
        taken.add(aid)
        # Generate the marker
        markerImage = np.zeros((300, 300), dtype=np.uint8)
        markerImage = cv.aruco.generateImageMarker(dictionary, client.arucoID, 300, markerImage, 1)
        Path("media/" + key + "/images").mkdir(parents=True, exist_ok=True)
        cv.imwrite("media/" + key + "/images/aruco.png", markerImage)
        if(displayID == None or client.displayID == displayID):
            #inform any client that they need to load aruco image
            response = {"DEST":key,"REQUEST": "CALIBRATE", "PAYLOAD": None}
            broadcast_to_client(key, response)

def identifyDisplays(isGroup,displayID):
    if(isGroup):
        # Send each client in the group ITS OWN label — a single group-wide
        # broadcast can only carry one payload, so it showed blank on every
        # screen. Per-client so each display shows its own name.
        for key, client in settings.clients.items():
            if client.displayID == displayID:
                response = {"REQUEST": "IDENTIFY", "PAYLOAD": client.friendlyName or key}
                broadcast_to_client(key, response)
    else:
        client = settings.clients.get(displayID)
        if client:
            response = {"REQUEST": "IDENTIFY", "PAYLOAD": client.friendlyName or displayID}
            broadcast_to_client(displayID, response)
    

def createScript(scriptID,value):
    settings.scripts.setdefault(scriptID, Scripts())
    settings.scripts[scriptID].value = value

def runScript(scriptID):
    settings.scripts[scriptID].status = os.system(settings.scripts[scriptID].value)

def deleteScript(scriptID):
    del settings.scripts[scriptID]

async def upload_handler(request):
    logging.debug("UPLOAD_HANDLER")
    uploadDest = request.match_info.get('dest')
    logging.debug(uploadDest)
    reader = await request.multipart()
    # reader.next() will `yield` the fields of your form
    field = await reader.next()
    logging.debug(field.name)
    filename = field.filename
    # You cannot rely on Content-Length if transfer is chunked.
    size = 0
    path = os.path.join('cache')
    if not os.path.exists(path):
        os.mkdir(path)
    with open(os.path.join(path,filename), 'wb') as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)
    
    response = "none"
    ct = 'application/octet-stream'
    
    if(uploadDest == "calibrate"):
        response, ct = calibrate(os.path.join(path,filename))
    elif(uploadDest == "image"):
        response, ct = processImage(path,filename)
    elif(uploadDest == "video"):
        response, ct = processVideo(path, filename)
    return web.Response(body=response, content_type=ct)

def processImage(path,filename):
    logging.debug("processImage")
    imgDir = "media/server/images"
    Path(imgDir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(path,filename)).rename(os.path.join(imgDir,filename))
    return "success", "text/html"

def processVideo(path, filename):
    logging.debug("processVideo")
    vidDir = "media/server/videos"
    Path(vidDir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(path, filename)).rename(os.path.join(vidDir, filename))
    return "success", "text/html"

def angle_cos(p0, p1, p2):
    d1, d2 = (p0-p1).astype('float'), (p2-p1).astype('float')
    return abs( np.dot(d1, d2) / np.sqrt( np.dot(d1, d1)*np.dot(d2, d2) ) )

# ---------------------------------------------------------------------------
# Screen-quad detection: a four-stage pipeline that survives the cluttered,
# unevenly-lit calibration photos a real installation produces (the previous
# raw-findContours approach drew long compound polygons across multiple iPads
# and picked up cables/carpet as miniature screens).
#
#   1. find_screen_quads_bright -- adaptive-threshold the image so only iPad
#      screens (bright interiors against dark bezels/background) survive into
#      the contour pass. Eliminates background clutter directly.
#   2. _select_per_marker_quads -- for each ArUco marker, take the SMALLEST
#      quad that encloses its center. Compound polygons (spanning >1 iPad)
#      have larger area than the individual iPad screens, so smallest-
#      enclosing reliably picks the per-iPad outline.
#   3. _filter_outlier_area -- discard quads whose area is far from the
#      median (all iPads are the same physical size; outliers are wrong).
#   4. _drop_overlapping -- discard quad pairs that overlap heavily (the
#      compound-spanning case that survived stages 2-3 by luck); keep the
#      smaller of each overlapping pair.
# ---------------------------------------------------------------------------

def find_screen_quads_bright(image, min_area=1000, max_area_frac=0.3):
    """Find iPad-screen quadrilateral candidates using multiple thresholding
    strategies, then filter to convex 4-point polygons with near-90deg
    corners.

    A single thresholding strategy fails on real fleet photos because
    lighting is uneven across the array: any one threshold value (or even
    adaptive-with-fixed-block) catches the screens in its operating band
    and misses the rest. Three passes in parallel cover the range:

      - Canny edge detection: catches faint screen edges in dim/shadow
        regions where threshold-based methods see uniform local pixels.
      - Multiple fixed thresholds (60/120/180): each captures screens at
        a particular brightness band; combined, they cover dim through
        bright. Same multi-pass idea as the existing find_squares.
      - Adaptive threshold with a moderate C value: picks up screens
        whose local-mean-relative brightness varies with lighting.

    Quads are bounded:
      - Below min_area (pixels): tiny noise polygons (cable crossings,
        carpet weave knots, marker pattern fragments).
      - Above max_area_frac * image-area: whole-photo spanning compounds.
        For a typical fleet shot, each iPad is ~1/24 to 1/8 of frame
        area; anything past 30% is the find-contours-confused-by-the-
        whole-cluster artefact we saw poisoning per-marker selection.
        Stages 2-4 of the pipeline DO catch these later via the area
        outlier filter, but rejecting them here keeps the candidate
        pool clean enough that stage 2's smallest-enclosing rule has
        a real choice for the iPads in dim regions.

    Returns convex 4-point polygons (Nx1x2 OpenCV format) with max
    corner-angle-cosine < 0.15 (~81-99deg)."""
    gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    # CLAHE locally equalizes brightness so dim iPads (against shadow/couch)
    # get the same intra-screen contrast as bright iPads (under direct
    # lighting). Without this, threshold passes that work for bright screens
    # leave dim screens as one solid black blob (no detectable perimeter
    # contour). CLAHE's tile_size needs to be small enough to operate
    # locally (per-iPad) but large enough not to amplify noise -- 8x8 is
    # OpenCV's recommended default and matches well to a 24-iPad photo
    # where each iPad occupies ~10% of frame width.
    clahe = cv.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    gray = cv.GaussianBlur(gray, (5, 5), 0)
    max_area = float(gray.shape[0] * gray.shape[1]) * max_area_frac
    # Morphology kernels for bridging faint/broken edges. A 3x3 close fills
    # 1-2 px gaps without merging neighbours; the larger 5x5 close handles
    # the dim iPads whose screen-perimeter band has spots of low contrast.
    _k3 = cv.getStructuringElement(cv.MORPH_RECT, (3, 3))
    _k5 = cv.getStructuringElement(cv.MORPH_RECT, (5, 5))

    def _quads_from_mask(mask):
        out = []
        contours, _ = cv.findContours(mask, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            peri = cv.arcLength(cnt, True)
            # Slightly looser epsilon (0.025 vs 0.02) tolerates the small
            # rounding-error wobble on dim/low-contrast edge contours, so
            # they reduce to 4 points instead of 5-6 and survive the
            # len()==4 gate. Doesn't admit obviously non-quad shapes.
            approx = cv.approxPolyDP(cnt, 0.025 * peri, True)
            if len(approx) != 4 or not cv.isContourConvex(approx):
                continue
            pts = approx.reshape(-1, 2)
            max_cos = float(np.max([angle_cos(pts[i], pts[(i+1) % 4], pts[(i+2) % 4])
                                    for i in range(4)]))
            # 0.20 ~= 78-102deg (was 0.15 ~= 81-99deg). Foreshortening on
            # the near/far rows of a 6-wide array can take corner angles
            # past the 0.15 limit on real photos.
            if max_cos < 0.20:
                out.append(approx)
        return out

    quads = []
    # Pass 1: Canny (catches dim-region edges). Dilate then close to bridge
    # broken segments on faint screen edges before contour-finding.
    edges = cv.Canny(gray, 0, 50, apertureSize=5)
    edges = cv.dilate(edges, _k3)
    edges = cv.morphologyEx(edges, cv.MORPH_CLOSE, _k3)
    quads.extend(_quads_from_mask(edges))
    # Pass 2: multiple fixed luminance thresholds spanning the brightness
    # range. Wider span (40 catches very dim iPads; 220 catches bright/glare
    # iPads where 180 lumps screen + reflection into one blob).
    for thrs in (40, 60, 90, 120, 150, 180, 220):
        _, mask = cv.threshold(gray, thrs, 255, cv.THRESH_BINARY)
        # Close gaps in the perimeter band where lighting glare interrupts it.
        mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, _k3)
        quads.extend(_quads_from_mask(mask))
    # Pass 2b: Otsu threshold -- auto-selects a globally optimal threshold
    # based on the image's bimodal histogram. For shots where the screens
    # and background are well-separated in brightness, Otsu hits the right
    # cutoff without needing us to enumerate. Two morphology variants so
    # subtle gaps (3x3) and big gaps (7x7) both get bridged.
    _, otsu = cv.threshold(gray, 0, 255, cv.THRESH_BINARY + cv.THRESH_OTSU)
    quads.extend(_quads_from_mask(cv.morphologyEx(otsu, cv.MORPH_CLOSE, _k3)))
    _k7 = cv.getStructuringElement(cv.MORPH_RECT, (7, 7))
    quads.extend(_quads_from_mask(cv.morphologyEx(otsu, cv.MORPH_CLOSE, _k7)))
    # Pass 3: adaptive threshold at multiple block sizes -- the small block
    # responds to fine-grained per-iPad lighting, the large block tolerates
    # uniform-luminance screens that the small block would chop into noise.
    for block, C in ((31, -8), (51, -10), (101, -12), (151, -14)):
        mask = cv.adaptiveThreshold(gray, 255, cv.ADAPTIVE_THRESH_MEAN_C,
                                    cv.THRESH_BINARY, block, C)
        mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, _k5)
        quads.extend(_quads_from_mask(mask))
    return quads


def _quad_contains_point(quad, point):
    """True iff (x,y) lies inside the 4-point quad. Uses OpenCV's
    pointPolygonTest which is robust to point order and quad orientation."""
    contour = quad.reshape(-1, 1, 2).astype(np.float32)
    return cv.pointPolygonTest(contour, tuple(point), False) >= 0


def _quad_iou(q1, q2):
    """Intersection-over-union of two convex quads. Uses the axis-aligned
    bounding-box approximation -- fast and good enough for the "did these
    two quads accidentally trace the same compound region?" decision."""
    x1, y1, w1, h1 = cv.boundingRect(q1)
    x2, y2, w2, h2 = cv.boundingRect(q2)
    ix = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    iy = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    if ix == 0 or iy == 0:
        return 0.0
    inter = float(ix * iy)
    union = float(w1 * h1) + float(w2 * h2) - inter
    return inter / union if union > 0 else 0.0


def _band_from_marker_floodfill(image, marker_corners,
                                 brightness_min=140, fill_tolerance=70,
                                 min_quad_to_marker_area_ratio=3.0,
                                 max_quad_to_marker_area_ratio=60.0):
    """Find the screen's bright interior by flood-filling outward from a
    seed point just outside the marker. Returns a 4-point quad (the
    minAreaRect of the fill region) anchored to that iPad's screen, or
    None.

    Why this beats thresholding: the screen interior is GUARANTEED to be
    a single connected bright region (the calibrate page sets the
    background to #ffffff and the marker sits on top of it). The
    boundary to the dark surround (CSS black border + iPad bezel) is
    sharp. Flood-fill follows pixel connectivity within a brightness
    tolerance, so it traces the screen's true outline regardless of the
    absolute brightness level. Dim iPads against dark couches and bright
    iPads in direct light both get found because they're each handled
    by their own local fill, not by a global threshold.

    Seed selection: walk outward from the marker centre along each of
    the four canvas axes (via the marker homography) until the first
    sufficiently-bright pixel. This guarantees seeds land right at the
    edge of the marker -- on the iPad's own white interior -- even when
    the canvas's axes project to unexpected photo directions (a
    physically-rotated iPad). A fixed seed offset would risk landing
    on a neighbouring iPad's screen in that case.

    Sanity check: the fill bbox must contain the marker centre.
    Otherwise the fill leaked from the seed into a neighbour without
    touching the marker -- a clear failure.

    Area bounds (relative to marker area): [3x, 60x] catches
    dim/foreshortened screens without admitting multi-screen compounds."""
    mc = np.array(marker_corners, dtype="float32").reshape(4, 2)
    marker_area = float(cv.contourArea(mc))
    if marker_area < 16:
        return None
    marker_center = mc.mean(axis=0)
    # Homography from marker's intrinsic 300x300 frame (centered at origin)
    # to the marker's detected photo corners.
    h_half = 150.0
    marker_frame = np.array([[-h_half, -h_half], [h_half, -h_half],
                             [h_half, h_half], [-h_half, h_half]], dtype="float32")
    H = cv.getPerspectiveTransform(marker_frame, mc)

    gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    img_h, img_w = gray.shape[:2]
    min_area = marker_area * min_quad_to_marker_area_ratio
    max_area = marker_area * max_quad_to_marker_area_ratio

    # Walk outward from the marker centre along each of the four canvas
    # axes (via the marker homography) and pick the FIRST sufficiently
    # bright pixel as a seed. This guarantees seeds land on white pixels
    # adjacent to the marker -- not on a neighbouring iPad's screen which
    # happens to lie in the canvas-axis direction for a physically-rotated
    # iPad. Walk stops at half a marker-edge past the marker boundary; if
    # no white pixel exists in that span, that direction's seed is skipped.
    walk_max = h_half * 2.0   # up to 1.0 marker-edge from centre
    walk_step = max(2.0, h_half * 0.05)
    seeds_photo = []
    for direction in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
        for r in np.arange(h_half * 1.05, walk_max, walk_step):
            local = np.array([[[direction[0] * r, direction[1] * r]]], dtype="float32")
            photo = cv.perspectiveTransform(local, H).reshape(2)
            px, py = int(round(photo[0])), int(round(photo[1]))
            if not (0 <= px < img_w and 0 <= py < img_h):
                break
            if int(gray[py, px]) >= brightness_min:
                seeds_photo.append((px, py))
                break

    best_quad = None
    best_area = 0.0
    for sx, sy in seeds_photo:
        # Try a sweep of fill tolerances and keep the LARGEST valid fill
        # under that seed. Single tolerance can't satisfy all iPads --
        # tight tolerances stop at intra-screen gradients, loose ones
        # leak through bezels.
        mask = np.zeros((img_h + 2, img_w + 2), dtype=np.uint8)
        flood = gray.copy()
        cv.floodFill(flood, mask, (sx, sy), 200,
                     loDiff=fill_tolerance, upDiff=fill_tolerance,
                     flags=cv.FLOODFILL_FIXED_RANGE | (255 << 8))
        fill = mask[1:-1, 1:-1]
        area = float(fill.sum()) / 255.0   # mask values are 0 or 255
        if area < min_area or area > max_area:
            continue
        # Get the fill region's contour, then a 4-point quad via
        # minAreaRect (robust to noisy contour edges). Use
        # RETR_EXTERNAL so an internal hole (the marker pattern's
        # black squares) doesn't become its own contour.
        contours, _ = cv.findContours(fill, cv.RETR_EXTERNAL,
                                       cv.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        biggest = max(contours, key=cv.contourArea)
        contour_area = float(cv.contourArea(biggest))
        if contour_area < min_area or contour_area > max_area:
            continue
        rect = cv.minAreaRect(biggest)
        quad = cv.boxPoints(rect).astype(np.int32).reshape(-1, 1, 2)
        # Reject if the minAreaRect itself blew past the ceiling: the
        # contour may be irregular (L-shape from a partial leak) so
        # its tight bounding rect is much bigger than the contour area.
        quad_area = float(cv.contourArea(quad.reshape(-1, 2)))
        if quad_area > max_area:
            continue
        # Solidity check: contour-area / minAreaRect-area. A clean
        # screen fill is a near-rectangle so solidity is close to 1. A
        # leak through a corner produces an L-shape with solidity
        # ~0.5, and we should reject those even if their absolute area
        # fits.
        if quad_area > 0 and (contour_area / quad_area) < 0.7:
            continue
        # The marker MUST sit inside the band -- the band is the
        # screen's white interior and the marker is rendered at the
        # screen centre, so any valid fill encloses the marker.
        if not _quad_contains_point(quad,
                                     (float(marker_center[0]),
                                      float(marker_center[1]))):
            continue
        if area > best_area:
            best_quad = quad
            best_area = area
    return best_quad


def _per_marker_fallback_search(image, marker_corners, marker_id,
                                 candidates_radius_mult=8.0,
                                 min_quad_to_marker_area_ratio=3.0,
                                 max_quad_to_marker_area_ratio=60.0):
    """Last-chance band search for an individual marker whose iPad's screen
    wasn't picked up by the fleet-wide pipeline. Crops the image to a
    region around the marker (radius = candidates_radius_mult * marker
    edge length) and runs the same threshold pipeline.

    Why localized: the fleet pipeline filters by 5x-marker-area, which is
    correct for most iPads but conservatively rejects screens close to
    that floor (glare-shrunk band quads, partial occlusions). A per-marker
    fallback with a 3x floor catches those without re-poisoning the
    fleet's median area filter.

    Area bounds (relative to marker area):
      - Floor: 3x  (vs fleet's 5x). Catches dim/glare iPads whose visible
        bright region is smaller than the canonical 5x.
      - Ceiling: 40x. iPad-1 screens are ~13x marker area in canvas
        coords; perspective foreshortening at the near rows of a fleet
        photo can stretch this to ~30x. 40x leaves headroom without
        admitting multi-screen compound polygons.

    Returns the smallest-area quad in the search region that encloses
    the marker center, or None."""
    mc = np.array(marker_corners, dtype="float32").reshape(4, 2)
    marker_area = float(cv.contourArea(mc))
    marker_edge = float(np.linalg.norm(mc[1] - mc[0]))
    if marker_edge < 4:
        return None
    cx = float(np.mean(mc[:, 0]))
    cy = float(np.mean(mc[:, 1]))
    radius = marker_edge * candidates_radius_mult
    h, w = image.shape[:2]
    x0 = max(0, int(cx - radius))
    y0 = max(0, int(cy - radius))
    x1 = min(w, int(cx + radius))
    y1 = min(h, int(cy + radius))
    if x1 - x0 < 50 or y1 - y0 < 50:
        return None
    sub = image[y0:y1, x0:x1]
    # max_area_frac=0.4 in the sub-image still allows up to ~100x marker
    # area in pixels, but we filter against an explicit max-multiplier
    # below to reject multi-screen compounds.
    sub_quads = find_screen_quads_bright(sub, min_area=int(marker_area),
                                          max_area_frac=0.5)
    if not sub_quads:
        return None
    # Translate quad coords back to image space.
    full_quads = []
    for q in sub_quads:
        q2 = q.copy()
        q2[:, 0, 0] += x0
        q2[:, 0, 1] += y0
        full_quads.append(q2)
    min_area = marker_area * min_quad_to_marker_area_ratio
    max_area = marker_area * max_quad_to_marker_area_ratio
    enclosing = [q for q in full_quads
                 if min_area <= cv.contourArea(q) <= max_area
                 and _quad_contains_point(q, (cx, cy))]
    if not enclosing:
        return None
    return min(enclosing, key=cv.contourArea)


def _select_per_marker_quads(quads, marker_list, min_quad_to_marker_area_ratio=5.0):
    """For each (corners, id) in marker_list, find the smallest-area quad
    from `quads` that encloses the marker's center point AND is at least
    `min_quad_to_marker_area_ratio` times larger than the marker itself.

    Returns a dict {marker_id: quad}.

    Smallest-enclosing is the key trick for rejecting compound polygons that
    span multiple iPads (those have larger area than the actual iPad screen).
    But the smallest-enclosing rule has a failure mode the bright-region
    detector exposes: the ArUco marker's OWN black outline is itself a clean
    4-point convex quad that encloses the marker's center, and it's smaller
    than the iPad screen. Without a lower bound on quad area, "smallest
    enclosing" would always pick the marker's outline -- which then poisons
    the median used by _filter_outlier_area, making the real screen quads
    look like over-sized outliers. The fix: require quads to be substantially
    bigger than the marker before considering them. iPad screens are
    typically 30-100x the marker area; a 5x floor keeps the iPad's screen
    quad in the running while reliably rejecting the marker's own quad."""
    result = {}
    for marker_corners, marker_id in marker_list:
        marker_area = float(cv.contourArea(marker_corners.astype(np.float32)))
        min_area = marker_area * min_quad_to_marker_area_ratio
        cx = float(np.mean(marker_corners[:, 0]))
        cy = float(np.mean(marker_corners[:, 1]))
        candidates = [q for q in quads
                      if cv.contourArea(q) >= min_area
                      and _quad_contains_point(q, (cx, cy))]
        if not candidates:
            continue
        result[int(marker_id)] = min(candidates, key=cv.contourArea)
    return result


def _filter_outlier_area(marker_to_quad, max_ratio=3.0):
    """Reject per-marker quads whose area is way off the median.

    All iPads in a calibration shot are the same physical size, but
    perspective in a single-viewpoint photo of an N x M array can make
    the apparent area vary 3-4x between the near-camera and far-camera
    iPads. A symmetric +/-tolerance filter has to be very loose to keep
    those perspective extremes, which then lets compound polygons slip
    through. Use a max-ratio bound instead: keep quads with area in
    [median / max_ratio, median * max_ratio]. max_ratio=3.0 catches the
    typical perspective range without admitting the obvious compounds
    (those are usually 10x+ the median -- a whole-array spanning
    polygon, for example).
    """
    if not marker_to_quad:
        return marker_to_quad
    areas = [cv.contourArea(q) for q in marker_to_quad.values()]
    median = float(np.median(areas))
    if median <= 0:
        return marker_to_quad
    low, high = median / max_ratio, median * max_ratio
    return {mid: q for mid, q in marker_to_quad.items()
            if low <= cv.contourArea(q) <= high}


def _marker_angle(marker_corners):
    """Angle (radians, -pi..pi) of the marker's TL->TR edge in photo coords.
    ArUco's detector returns corners in pattern-intrinsic order, so this is
    always the projection of the canvas's +x axis -- independent of how the
    marker is photographed. Two iPads with the same physical orientation
    will have very close angles; an iPad rotated 90deg in software will
    have an angle ~pi/2 off the others."""
    pts = np.array(marker_corners, dtype="float32").reshape(4, 2)
    tl, tr = pts[0], pts[1]
    return float(np.arctan2(tr[1] - tl[1], tr[0] - tl[0]))


def _circular_median(angles):
    """Median direction of a set of angles (radians). Uses the sum-of-unit-
    vectors method -- robust to wrap-around (a 179deg vector and a -179deg
    vector aren't 358deg apart, they're 2deg apart). Returns the angle of
    the resultant vector."""
    if not angles:
        return 0.0
    s = float(np.sum(np.sin(angles)))
    c = float(np.sum(np.cos(angles)))
    return float(np.arctan2(s, c))


def detect_fleet_rotations(marker_list, rotation_threshold_deg=45.0):
    """Per-marker rotation flag based on FLEET-WIDE angle consistency.

    For each marker_corners in marker_list (zip of corners + ids), compute
    its TL->TR angle. Take the circular median across all markers as the
    fleet-native orientation. A marker whose angle differs from the median
    by more than rotation_threshold_deg (default 45deg) is flagged as
    rotated -- its iPad is in a different physical orientation than the
    majority.

    This catches the case calibrate's per-iPad band+aspect detector misses:
    a no-band iPad (band detection failed) whose stale-canvas-dims caused
    the wrong fiducial orientation. The marker is detected for every iPad
    that we calibrate, so this signal is always available -- it doesn't
    depend on band quality.

    Returns dict {marker_id: True/False} where True = rotated relative to
    fleet. Empty marker_list -> empty dict (no fleet to compare against).

    Threshold notes:
      - 45deg splits the angle space cleanly into "same orientation as
        fleet" (within 45deg of median) vs "90deg-rotated" (45-135deg off).
      - We don't distinguish 90 from 180 from 270deg -- all are "the iPad
        is sitting differently than the fleet". Any of those misalignments
        causes the same wrong-aspect problem and needs a canvas dim swap."""
    if not marker_list:
        return {}
    angles = [_marker_angle(c) for c, _ in marker_list]
    fleet_angle = _circular_median(angles)
    threshold = np.radians(rotation_threshold_deg)
    out = {}
    for (_, mid), ang in zip(marker_list, angles):
        # Signed wrap-around difference, then take absolute value: how
        # different is this marker's angle from the fleet median?
        diff = np.arctan2(np.sin(ang - fleet_angle), np.cos(ang - fleet_angle))
        out[int(mid)] = abs(diff) > threshold
    return out


def _drop_overlapping(marker_to_quad, iou_threshold=0.3):
    """When two markers' selected quads overlap by >iou_threshold, drop the
    larger one (the compound spanning into the other). Iterates pairs once;
    O(n^2) in marker count but the fleet is small."""
    keep = dict(marker_to_quad)
    items = list(marker_to_quad.items())
    for i, (mid_a, qa) in enumerate(items):
        if mid_a not in keep:
            continue
        for mid_b, qb in items[i+1:]:
            if mid_b not in keep:
                continue
            if _quad_iou(qa, qb) > iou_threshold:
                drop = mid_a if cv.contourArea(qa) > cv.contourArea(qb) else mid_b
                keep.pop(drop, None)
                if drop == mid_a:
                    break   # this i is gone, move to next
    return keep


def find_squares(img):
    # Optimize: Convert to grayscale once instead of processing all channels
    gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    gray = cv.GaussianBlur(gray, (5, 5), 0)
    squares = []
    
    # Optimize: Reduce threshold iterations and use more efficient range
    for thrs in range(0, 255, 52):  # Reduced iterations from 10 to 5
        if thrs == 0:
            bin = cv.Canny(gray, 0, 50, apertureSize=5)
            bin = cv.dilate(bin, None)
        else:
            _retval, bin = cv.threshold(gray, thrs, 255, cv.THRESH_BINARY)
        
        contours, _hierarchy = cv.findContours(bin, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            # Early area check to avoid expensive operations on small contours
            area = cv.contourArea(cnt)
            if area < 1000:
                continue
                
            cnt_len = cv.arcLength(cnt, True)
            cnt = cv.approxPolyDP(cnt, 0.02*cnt_len, True)
            if len(cnt) == 4 and cv.isContourConvex(cnt):
                cnt = cnt.reshape(-1, 2)
                max_cos = np.max([angle_cos( cnt[i], cnt[(i+1) % 4], cnt[(i+2) % 4] ) for i in range(4)])
                if max_cos < 0.1:
                    squares.append(cnt)
    return squares
        
def setup_aruco_detector():
    """Return (dictionary, parameters) for 6x6 ArUco marker detection.

    Subpixel corner refinement is enabled: by default OpenCV's ArUco
    detector returns corners at integer-pixel precision, which is fine
    for IDENTIFICATION but bad for the FIDUCIAL EXTRAPOLATION we do --
    the marker is ~300px (canvas) projected to ~85px (photo), but the
    screen is ~3-4x larger, so any 1-pixel error in the marker corner
    becomes a 3-4 px error at the screen edge. CORNER_REFINE_SUBPIX
    runs cv.cornerSubPix internally, taking corners from integer
    precision to ~0.1 px which is well below the noise floor of our
    other measurements. Visible effect: screen polygons sit more
    squarely on the iPad edges (no more "pitched" appearance on
    perspective-tilted screens). Cost: a few ms per marker, negligible
    on a 24-iPad calibration."""
    dictionary = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_6X6_50)
    parameters = cv.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv.aruco.CORNER_REFINE_SUBPIX
    return dictionary, parameters

def detect_aruco_markers(image):
    """Detect ArUco markers in an image. Returns (corners, ids, rejected)."""
    dictionary, parameters = setup_aruco_detector()
    detector = cv.aruco.ArucoDetector(dictionary, parameters)
    return detector.detectMarkers(image)

def calibrate(filename):
    logging.debug(filename)
    image = cv.imread(filename)

    (corners, ids, rejected) = detect_aruco_markers(image)

    # Find candidate screen quadrilaterals using the bright-region pipeline
    # (see find_screen_quads_bright). This replaces the previous raw
    # findContours pass that picked up compound polygons spanning multiple
    # iPads + cable/carpet noise.
    candidate_quads = find_screen_quads_bright(image)

    # Pre-compute the best (smallest-enclosing) quad per ArUco marker, then
    # filter outliers and overlapping pairs. After this, marker_to_quad has
    # at most one quad per marker, and each surviving quad is reasonably
    # confident to be that iPad's actual screen outline (not a compound).
    marker_to_quad = {}
    if len(corners) > 0:
        marker_list = []
        for marker_corners, marker_id in zip(corners, ids.flatten()):
            marker_list.append((marker_corners.reshape(4, 2), marker_id))
        # PRIMARY band detection: per-marker flood fill from a seed point
        # just outside the marker, in the screen's white interior. The
        # screen is GUARANTEED to be a connected bright region in
        # calibrate mode (page background is forced white), so each
        # marker has a deterministic "follow this region's pixels"
        # signal -- no global threshold tuning, no fleet-wide candidate
        # pool. This handles dim iPads and bright iPads identically
        # because the fill is local and follows connectivity, not
        # absolute brightness levels.
        marker_to_quad = {}
        for marker_corners, marker_id in marker_list:
            q = _band_from_marker_floodfill(image, marker_corners)
            if q is not None:
                marker_to_quad[int(marker_id)] = q
        n_floodfill = len(marker_to_quad)
        # SECONDARY: the threshold-based fleet pipeline as a fallback for
        # any marker whose flood fill failed (seed landed off-image,
        # screen interior wasn't bright enough at the seed, etc.).
        n_threshold = 0
        if n_floodfill < len(marker_list):
            from_threshold = _select_per_marker_quads(candidate_quads, marker_list)
            from_threshold = _filter_outlier_area(from_threshold, max_ratio=3.0)
            from_threshold = _drop_overlapping(from_threshold, iou_threshold=0.3)
            for mid, q in from_threshold.items():
                if mid not in marker_to_quad:
                    marker_to_quad[mid] = q
                    n_threshold += 1
        # TERTIARY: per-marker localized threshold search for any marker
        # still without a band quad.
        n_fallback = 0
        for marker_corners, marker_id in marker_list:
            if int(marker_id) in marker_to_quad:
                continue
            q = _per_marker_fallback_search(image, marker_corners, marker_id)
            if q is not None:
                marker_to_quad[int(marker_id)] = q
                n_fallback += 1
        logging.info("calibrate: %d markers detected -> %d band quads "
                     "(%d flood-fill + %d threshold + %d fallback)",
                     len(marker_list), len(marker_to_quad),
                     n_floodfill, n_threshold, n_fallback)

    relevantContours = []

    if len(corners) > 0:
        # flatten the ArUco IDs list
        ids = ids.flatten()
        # loop over the detected ArUCo corners
        for (markerCorner, markerID) in zip(corners, ids):
                # extract the marker corners (which are always returned in
                # top-left, top-right, bottom-right, and bottom-left order)
                corners = markerCorner.reshape((4, 2))
                (topLeft, topRight, bottomRight, bottomLeft) = corners
                # convert each of the (x, y)-coordinate pairs to integers
                topRight = (int(topRight[0]), int(topRight[1]))
                bottomRight = (int(bottomRight[0]), int(bottomRight[1]))
                bottomLeft = (int(bottomLeft[0]), int(bottomLeft[1]))
                topLeft = (int(topLeft[0]), int(topLeft[1]))
                # draw the bounding box of the ArUCo detection
                cv.line(image, topLeft, topRight, (255, 0, 0), 4)
                cv.line(image, topRight, bottomRight, (255, 0, 0), 4)
                cv.line(image, bottomRight, bottomLeft, (255, 0, 0), 4)
                cv.line(image, bottomLeft, topLeft, (255, 0, 0), 4)
                # compute and draw the center (x, y)-coordinates of the ArUco
                # marker
                cX = int((topLeft[0] + bottomRight[0]) / 2.0)
                cY = int((topLeft[1] + bottomRight[1]) / 2.0)
                cv.circle(image, (cX, cY), 10, (255, 0, 0), -1)
                # Map the detected marker to the client that OWNS that arucoID
                # (assigned + stored in generateAruco). Mapping by client list
                # position breaks whenever the client set/order changes between
                # generate and calibrate — e.g. a device reconnecting with a new
                # id — leaving most screens uncalibrated.
                clientID = next((k for k, c in settings.clients.items()
                                 if getattr(c, "arucoID", None) == markerID), None)
                if clientID is None:
                    continue  # marker for a client we no longer have
                clientLabel = settings.clients[clientID].friendlyName or clientID
                # Label is drawn AFTER quad reconciliation below (we need the
                # screen bbox to size the font correctly). See _draw_fitted_label.
                #Dictionary ordering is deterministic in python 3.7
                settings.clients[clientID].measuredCenter = [cX,cY]
                # Look up the per-marker quad selected by the bright/median/IoU
                # pipeline above. If a quad survived all three filters, hand it
                # to reconcile_screen_quad as the screen's "band" contour;
                # otherwise pass None and reconcile_screen_quad will
                # extrapolate from the marker corners + canvas aspect ratio
                # (the "fiducial" path). The drawing + relevantContours
                # accumulation happens BELOW once we have the reconciled
                # quad -- whichever path it came from -- so we never display
                # a raw band that the reconcile step later decided was wrong.
                quad_candidate = marker_to_quad.get(int(markerID))
                border_contour = None
                if quad_candidate is not None:
                    border_contour = quad_candidate.reshape(-1, 1, 2)

                # Prefer the fiducial extrapolation of the full screen quad over
                # the messy band contour; reconcile against the band outline and
                # auto-correct stale mobile orientation.
                _cli = settings.clients[clientID]
                cw = getattr(_cli, "canvasWidth", 0) or _cli.deviceWidth
                ch = getattr(_cli, "canvasHeight", 0) or _cli.deviceHeight
                quad, source = reconcile_screen_quad(markerCorner.reshape(4, 2), border_contour, cw, ch)
                if source == "rotated":
                    _cli.canvasWidth, _cli.canvasHeight = ch, cw   # reported orientation was stale
                    logging.info("calibrate: detected rotation for %s; swapped canvas to %sx%s", clientID, ch, cw)
                elif source == "unverified":
                    logging.warning("calibrate: couldn't validate %s against its black band; using marker fiducial", clientID)
                _cli.measuredPerimeter = quad
                # Visualize the reconciled quad for EVERY screen, including the
                # fiducial-only fallbacks (iPads where the bright-region pipeline
                # didn't find a band contour and reconcile_screen_quad
                # extrapolated from the marker corners + canvas aspect ratio).
                # Previously this was gated on source != "fiducial", which left
                # the fallbacks visually unbounded -- looking to the operator
                # like "this iPad didn't get a bounding box" even though the
                # measuredPerimeter was set correctly. Green for band-detected,
                # yellow for fiducial-extrapolated (so the operator can see
                # which iPads needed the fallback, but every iPad is bounded).
                qpts = quad.reshape(4, 2).astype(int)
                # Geometry is the fiducial in all cases (correct by construction
                # from the marker + canvas dims). The colour reflects how
                # confident we are about it:
                #   Green  = band-validated (band matched fiducial >= min_iou)
                #            -- the iPad's true orientation is also confirmed.
                #   Yellow = no band found / band didn't validate. Geometry is
                #            still trusted (marker-anchored), but orientation
                #            isn't independently confirmed.
                colour = (0, 255, 0) if source in ("fiducial", "rotated") else (0, 255, 255)
                for i in range(4):
                    cv.line(image, tuple(qpts[i]), tuple(qpts[(i + 1) % 4]), colour, 4)
                # Label the screen with its client name, oriented along the
                # marker's TL->TR direction (the canvas's reading direction).
                _draw_fitted_label(image, clientLabel, markerCorner.reshape(4, 2))
                # Ensure the reconciled quad participates in the overall
                # bounding box too -- it's the iPad's actual screen extent,
                # whether band-detected or fiducial-extrapolated.
                quad_contour = quad.reshape(-1, 1, 2).astype(np.int32)
                if len(relevantContours) == 0:
                    relevantContours = quad_contour
                else:
                    relevantContours = np.concatenate((relevantContours, quad_contour))

    # Draw the overall bounding box only if we actually found marker contours.
    # With no detectable ArUco markers, relevantContours stays an empty list and
    # cv.boundingRect() raises — skip it and still return the (annotated) image
    # so the user gets visual feedback instead of a 500.
    if len(relevantContours) > 0:
        x, y, w, h = cv.boundingRect(relevantContours)
        cX = int((x + (w / 2.0)))
        cY = int((y + (h / 2.0)))
        cv.circle(image, (cX, cY), 15, (0, 0, 255), -1)
        cv.rectangle(image, (x, y), (x+w, y+h), (0, 0, 255), 4)
    else:
        logging.info("calibrate: no ArUco markers detected in uploaded image")

    Path("media/displays/images").mkdir(parents=True, exist_ok=True)
    cv.imwrite("media/displays/images/calibration.png", image)

    # Clean up image memory. (No cv.destroyAllWindows() — this is a headless
    # server; that GUI call raises on OpenCV builds without highgui support.)
    # imgray/thresh from the old findContours path are gone now; the new
    # bright-region pipeline (find_screen_quads_bright) does its own
    # grayscale conversion internally and lets the temp go out of scope.
    del image, candidate_quads, marker_to_quad

    assign_group_bounding_boxes()
    # Return the *URL* (not the disk path): media_handler serves
    # /media/<client>/<file> by inserting the images/ subdir, so the file
    # written to media/displays/images/calibration.png is fetched as
    # /media/displays/calibration.png.
    return "media/displays/calibration.png","text/html"

async def cache_stats_handler(request):
    """Debug endpoint to view cache performance"""
    stats = {
        'hits': cache_stats['hits'],
        'misses': cache_stats['misses'],
        'cached_files': len(file_cache),
        'hit_ratio': cache_stats['hits'] / (cache_stats['hits'] + cache_stats['misses']) if (cache_stats['hits'] + cache_stats['misses']) > 0 else 0
    }
    return web.json_response(stats)

# --- Granular discovery REST endpoints (one handler per resource) ---

# (path, mtime) -> duration in seconds (or None). Avoids re-probing unchanged
# files on every /api/media call.
_video_duration_cache = {}


async def get_video_duration(path):
    """Video length in seconds via ffprobe, or None on failure. Cached by
    (path, mtime). Async (create_subprocess_exec) so it never blocks the loop."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    ckey = (path, mtime)
    if ckey in _video_duration_cache:
        return _video_duration_cache[ckey]
    dur = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        val = (out or b"").decode("utf-8", "replace").strip()
        if val and val != "N/A":
            dur = float(val)
    except Exception as e:
        logging.debug("ffprobe duration failed for %s: %s", path, e)
    _video_duration_cache[ckey] = dur
    return dur


async def api_media(request):
    """List the shared media library under media/server/{images,videos}, plus
    per-video durations (seconds) so the playlist editor can offer 'full length'."""
    def _list(sub):
        d = os.path.join("media", "server", sub)
        if not os.path.isdir(d):
            return []
        return ["/media/server/" + sub + "/" + f
                for f in sorted(os.listdir(d))
                if os.path.isfile(os.path.join(d, f))]
    videos = _list("videos")
    durations = {}
    for url in videos:
        disk = os.path.join("media", "server", "videos", os.path.basename(url))
        d = await get_video_duration(disk)
        if d is not None:
            durations[url] = round(d, 1)
    body = json.dumps({"images": _list("images"), "videos": videos,
                       "videoDurations": durations})
    return web.Response(text=body, content_type="application/json")

async def api_effects(request):
    """List the registered transition effects and their parameter schemas."""
    return web.Response(text=json.dumps({"effects": effects.effect_catalog()}),
                        content_type="application/json")

async def api_discovery_devices(request):
    """REST: list all discovered devices."""
    devices = get_discovered_devices()
    return web.json_response({
        "success": True,
        "devices": devices,
        "total": len(devices),
        "online": len([d for d in devices if d["isOnline"]]),
    })

async def api_discovery_stats(request):
    """REST: aggregate discovery + cache statistics."""
    devices = get_discovered_devices()
    display_groups = {}
    for d in devices:
        gid = d["displayID"] or "default"
        display_groups[gid] = display_groups.get(gid, 0) + 1
    total = cache_stats['hits'] + cache_stats['misses']
    return web.json_response({
        "success": True,
        "totalDevices": len(devices),
        "onlineDevices": len([d for d in devices if d["isOnline"]]),
        "autoConfiguredDevices": len([d for d in devices if d["autoConfigured"]]),
        "displayGroups": display_groups,
        "cacheStats": {
            "hits": cache_stats['hits'],
            "misses": cache_stats['misses'],
            "cachedFiles": len(file_cache),
            "hitRatio": (cache_stats['hits'] / total) if total else 0,
        },
    })

async def api_discovery_configure(request):
    """REST: configure client(s). Supports four payload styles:

      - {"clientKey", "displayID"?, "friendlyName"?}      -> update fields
      - {"action": "reconfigure", "clientKey"}            -> re-run auto-config
      - {"action": "bulk_reconfigure", "clientKeys": [...]}-> re-run for many
      - {"action": "swap_orientation", "clientKey"}        -> swap canvas dims +
        clear measuredPerimeter (force a re-calibrate at the new orientation)

    (The action-based forms preserve the contract that discovery.html uses.)
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    action = data.get("action")

    if action == "bulk_reconfigure":
        configured = 0
        for key in data.get("clientKeys", []):
            client = settings.clients.get(key)
            if client:
                client.autoConfigured = False
                auto_configure_client(key, client)
                configured += 1
        saveSettings()
        return web.json_response({"success": True, "configured": configured})

    client_key = data.get("clientKey")
    if not client_key:
        return web.json_response({"success": False, "error": "clientKey required"}, status=400)
    client = settings.clients.get(client_key)
    if not client:
        return web.json_response({"success": False, "error": "Client not found"}, status=404)

    if action == "reconfigure":
        client.autoConfigured = False
        auto_configure_client(client_key, client)
    elif action == "swap_orientation":
        # Manual override for cases where calibrate's auto-rotation detection
        # got it wrong (e.g. an iPad whose band quad wasn't detected so neither
        # the IoU nor aspect signal had a chance to fire, or borderline cases
        # where both signals were noisy). Swaps reported canvas dims AND
        # clears measuredPerimeter so the next calibration photo re-projects
        # the screen with the corrected orientation. The user is expected to
        # re-upload a calibration image after calling this.
        cw = int(getattr(client, "canvasWidth", 0) or 0)
        ch = int(getattr(client, "canvasHeight", 0) or 0)
        client.canvasWidth, client.canvasHeight = ch, cw
        client.measuredPerimeter = None
        logging.info("swap_orientation: %s canvas %sx%s -> %sx%s",
                     client_key, cw, ch, ch, cw)
    else:
        if "displayID" in data:
            client.displayID = data["displayID"]
        if "friendlyName" in data:
            client.friendlyName = data["friendlyName"]
            client.nameIsCustom = True   # user-set name: DNS won't override it
        # Per-device lifecycle scripts (login/start/stop/reboot/test). ""
        # clears back to the fleet default on next backfill; a non-empty
        # string overrides it.
        for sf in ("loginScript", "startScript", "stopScript", "rebootScript",
                   "testScript"):
            if sf in data:
                setattr(client, sf, data[sf] if data[sf] else None)

    saveSettings()
    return web.json_response({"success": True})

def sanitize_display_groups():
    """Self-heal display-group keys that captured HTML markup.

    An older rename bug could round-trip a node's decoration badges into the
    group name, persisting a phantom key like
    'Default <span class="badge">…</span>' that renders as a duplicate group.
    Recover the real name (everything before the first '<'), merge the entry
    back into the clean key (keeping an existing clean group if present), and
    repoint any clients that referenced the bad key. Idempotent; returns the
    number of keys fixed."""
    if not isinstance(getattr(settings, 'displays', None), dict):
        return 0
    fixed = 0
    for bad_key in [k for k in list(settings.displays.keys()) if '<' in str(k)]:
        clean = str(bad_key).split('<')[0].strip() or 'Default'
        group = settings.displays.pop(bad_key)
        settings.displays.setdefault(clean, group)
        for c in settings.clients.values():
            if getattr(c, 'displayID', None) == bad_key:
                c.displayID = clean
        fixed += 1
    if fixed:
        logging.info(f"Sanitized {fixed} display group key(s) containing HTML")
    return fixed


def migrate_client_objects():
    """Migrate old client objects to include new discovery fields"""
    if not hasattr(settings, 'playlists'):
        settings.playlists = {}
    if not hasattr(settings, 'schedules'):
        settings.schedules = {}
    for _disp in settings.displays.values():
        if not hasattr(_disp, 'defaultPlaylistName'):
            _disp.defaultPlaylistName = None
        _disp.scheduledEntryId = None      # transient — reset on startup
        _disp.scheduledPlaying = False
        # coordinated-start fields: backfill onto older Displays AND reset the
        # transient prepare state (a restart cancels any in-flight prepare).
        _disp.prepareId = None
        _disp.readyClients = set()
        _disp.armPending = set()
        _disp.prepareDeadline = 0
    current_time = time.time()
    for client_key, client in settings.clients.items():
        if not hasattr(client, 'discoveryTime'):
            client.discoveryTime = current_time
        if not hasattr(client, 'lastSeen'):
            client.lastSeen = current_time
        if not hasattr(client, 'connectionCount'):
            client.connectionCount = 1
        if not hasattr(client, 'capabilities'):
            client.capabilities = []
        if not hasattr(client, 'autoConfigured'):
            client.autoConfigured = False
        if not hasattr(client, 'discoverySource'):
            client.discoverySource = "existing"
        if not hasattr(client, 'isOnline'):
            client.isOnline = False
        if not hasattr(client, 'synced'):
            client.synced = False
        if not hasattr(client, 'hostname'):
            client.hostname = ""
        if not hasattr(client, 'hostnameResolved'):
            client.hostnameResolved = False
        if not hasattr(client, 'touch'):
            client.touch = False
        if not hasattr(client, 'canvasWidth'):
            client.canvasWidth = getattr(client, 'deviceWidth', 0)
        if not hasattr(client, 'canvasHeight'):
            client.canvasHeight = getattr(client, 'deviceHeight', 0)
        if not hasattr(client, 'nameIsCustom'):
            # Protect pre-existing custom names: a name that is NOT the
            # auto-generated '<device>_<key[:8]>' form is treated as user-set,
            # so reverse-DNS won't clobber it.
            fn = client.friendlyName or ""
            client.nameIsCustom = bool(fn) and not fn.endswith('_' + client_key[:8])
        # Backfill lifecycle-script defaults onto devices registered before the
        # automation existed (their fields are absent/None -> show as null).
        _apply_default_scripts(client)
        # Re-attempt resolution for clients that never got a hostname (e.g.
        # resolved blank before DNS was fixed / before the mDNS fallback). The
        # 60s retry throttle keeps perpetually-nameless devices from churning.
        if not getattr(client, 'hostname', ''):
            client.hostnameResolved = False

def evaluate_schedules(now=None):
    """Per group with a schedule or a default playlist: pick the effective target
    (highest-priority active schedule, else group default, else nothing) and drive
    assign -> auto-render -> play / stop. Called every process() tick."""
    if now is None:
        now = datetime.datetime.now()
    group_ids = set(s.displayID for s in settings.schedules.values())
    for did, d in settings.displays.items():
        if getattr(d, "defaultPlaylistName", None):
            group_ids.add(did)
    for display_id in group_ids:
        try:
            display = settings.displays.get(display_id)
            if display is None:
                continue
            winner = None
            for s in settings.schedules.values():
                if s.displayID != display_id or not getattr(s, "enabled", True):
                    continue
                if schedule_active_at(s, now):
                    if (winner is None or s.priority > winner.priority
                            or (s.priority == winner.priority and s.id < winner.id)):
                        winner = s
            if winner is not None:
                key, playlist_name = winner.id, winner.playlistName
            elif getattr(display, "defaultPlaylistName", None):
                key, playlist_name = "__default__:" + display.defaultPlaylistName, display.defaultPlaylistName
            else:
                key, playlist_name = None, None

            prev = getattr(display, "scheduledEntryId", None)
            if key is None:
                if prev is not None:
                    _stop_group_playback(display_id)
                    display.scheduledEntryId = None
                    display.scheduledPlaying = False
                continue
            if key != prev:
                pl = settings.playlists.get(playlist_name)
                if pl is None:
                    if prev is not None:
                        _stop_group_playback(display_id)
                    display.scheduledEntryId = None
                    display.scheduledPlaying = False
                    continue
                _apply_playlist(display_id, pl)
                display.scheduledEntryId = key
                display.scheduledPlaying = False
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and compute_render_token(display_id) != display.renderedToken:
                if display.renderStatus != "rendering":
                    asyncio.ensure_future(render_group_async(display_id))
                    display.scheduledPlaying = False
            elif not getattr(display, "scheduledPlaying", False):
                _start_group_playback(display_id)
                display.scheduledPlaying = True
        except Exception as e:
            logging.error("evaluate_schedules: group %s failed: %s", display_id, e)


async def process():
    """Enhanced periodic processing with device health monitoring"""
    current_time = time.time()

    try:
        evaluate_schedules()
    except Exception as e:
        logging.error("schedule evaluation failed: %s", e)

    # Resolve client hostnames (reverse DNS) off the event loop
    try:
        await resolve_client_hostnames()
    except Exception as e:
        logging.error("hostname resolution failed: %s", e)

    # Update last seen times for all active clients
    for client_key, client in settings.clients.items():
        if client.isOnline:
            client.lastSeen = current_time

    # Check for stale clients (no activity for more than 60 seconds)
    stale_clients = []
    for client_key, client in settings.clients.items():
        if (current_time - client.lastSeen) > 60 and client.isOnline:
            client.isOnline = False
            stale_clients.append({
                "clientKey": client_key,
                "friendlyName": client.friendlyName,
                "lastSeen": client.lastSeen
            })
    
    # Notify about stale clients
    if stale_clients:
        stale_notification = {
            "REQUEST": "CLIENTS_WENT_OFFLINE",
            "PAYLOAD": stale_clients
        }
        socketmanager.broadcast(jsonpickle.encode(stale_notification))
        logging.info(f"{len(stale_clients)} clients went offline")

    # Release any PREPARING groups whose timeout has elapsed
    try:
        _release_expired_prepares()
    except Exception as e:
        logging.error("_release_expired_prepares failed: %s", e)
    
    # Periodic discovery announcements (every 30 seconds)
    if not hasattr(process, 'last_announcement') or (current_time - process.last_announcement) > 30:
        discovery_summary = {
            "REQUEST": "DISCOVERY_HEARTBEAT",
            "PAYLOAD": {
                "totalClients": len(settings.clients),
                "onlineClients": len([c for c in settings.clients.values() if c.isOnline]),
                "timestamp": current_time
            }
        }
        socketmanager.broadcast(jsonpickle.encode(discovery_summary))
        process.last_announcement = current_time

    # Prune clients offline for >24h (checked hourly, persists only on change)
    if not hasattr(process, 'last_cleanup') or (current_time - process.last_cleanup) > 3600:
        cleanup_old_clients()
        process.last_cleanup = current_time

    #response = {"DEST":"ALL","REQUEST": "TEST", "PAYLOAD": "NONE"}
    #socketmanager.broadcast(jsonpickle.encode(response))
    ##logging.debug('Test broadcast')

socketmanager = None

if __name__ == '__main__':
    args = parse_args()
    settings = Settings()
    runner = None

    try:
        if args.Verbose:
            logging.basicConfig(level=logging.DEBUG,format='%(asctime)s %(levelname)s %(message)s')

        if( os.path.isfile('settings.dat')):
            data = Path('settings.dat').read_text(encoding="utf-8")
            settings = jsonpickle.decode(data)
            # Migrate old client objects to include new discovery fields
            migrate_client_objects()
            # Remove any HTML-corrupted display group keys (phantom duplicates)
            sanitize_display_groups()
            # Reset stale renderStatus: a previous server run that was
            # interrupted mid-render leaves the display with renderStatus
            # = "rendering" on disk. After restart there's no ffmpeg
            # actually running, but the render trigger silently returns
            # "already rendering" (msg_response / RENDER handler) and the
            # display is permanently stuck. Clearing here lets the next
            # render request fire normally.
            for _did, _disp in (settings.displays or {}).items():
                if getattr(_disp, "renderStatus", "") == "rendering":
                    logging.info("startup: clearing stale renderStatus='rendering' on %s", _did)
                    _disp.renderStatus = ""
        else:
            settings.displays.setdefault("Default", Display())
        
        for display in settings.displays:
            if(settings.displays[display].action != PlayState.NOACTION):
                #Send stop command
                print("Send stop command")
        
        # Run the async main function
        async def run_server():
            global runner, socketmanager
            try:
                runner = web.AppRunner(app)
                await runner.setup()
                # Bind explicitly to BOTH IPv4 and IPv6 wildcards. host=None
                # delegates the choice to getaddrinfo, which on Windows can
                # return IPv6-only (LocalAddress "::") -- and Windows IPv6
                # sockets are NOT dual-stack by default, so IPv4 connections
                # to the LAN address ("http://192.168.x.y:3000/") get
                # "actively refused" while ::1 still works. Explicit list
                # forces aiohttp to bind both wildcards so iPads (IPv4-only)
                # and modern browsers (often IPv4 first via DNS) can both
                # reach the server.
                site = web.TCPSite(runner=runner,
                                    host=["0.0.0.0", "::"],
                                    port=args.Port or 3000)
                await site.start()
                
                logging.debug('Started webapp')
                
                # Set up socket manager
                socketmanager = sockjs.get_manager(app=app,name='mosiacmesh')
                
                # Initialize JSON response cache now that jsonpickle is available
                init_json_cache()
                
                oneshot = True
                save_counter = 0

                try:
                    while True:
                        await process()
                        await asyncio.sleep(5)
                        
                        # Periodic settings save every 10 cycles (50 seconds)
                        save_counter += 1
                        if save_counter >= 10:
                            save_settings_incremental()
                            save_counter = 0
                        
                        if oneshot:
                            #inform any active clients that they need to reload
                            response = {"DEST":"ALL","REQUEST": "RELOAD", "PAYLOAD": "NONE"}
                            socketmanager.broadcast(jsonpickle.encode(response))
                            oneshot = False
                except Exception as e:
                    print(f"Server error: {e}")
                    
            finally:
                if runner:
                    await runner.cleanup()
        
        # Create the app before running
        app = web.Application()
        app.router.add_route('GET', '/', index_handler)
        app.router.add_route('GET', '/{page:[^{}/]+}', index_handler) #[^sockjs/]+
        app.router.add_route('GET', '/js/{src}', javascript_handler)
        app.router.add_route('GET', '/images/{src}', image_handler)
        app.router.add_route('GET', '/media/{file}', media_handler),
        app.router.add_route('GET', '/media/{client}/{file}', media_handler),
        app.router.add_route('GET', '/media/{client}/{sub}/{file}', media_handler),
        app.router.add_route('POST', '/upload/{dest}', upload_handler),
        app.router.add_route('GET', '/debug/cache', cache_stats_handler)
        # Discovery API endpoints (granular handlers)
        app.router.add_route('GET', '/api/media', api_media)
        app.router.add_route('GET', '/api/effects', api_effects)
        app.router.add_route('GET', '/api/discovery/devices', api_discovery_devices)
        app.router.add_route('GET', '/api/discovery/stats', api_discovery_stats)
        app.router.add_route('POST', '/api/discovery/configure', api_discovery_configure)
        sockjs.add_endpoint(app, ws_handler, name='mosiacmesh', prefix='/sockjs/')
        
        asyncio.run(run_server())
        
    finally:
        # Use incremental save and cleanup resources
        save_settings_incremental()
        close_file_pool()

