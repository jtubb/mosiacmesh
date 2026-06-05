import logging
import json
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
from vncdotool import api

import sockjs

import argparse
import hashlib
from functools import lru_cache
import uuid
import datetime

# Data classes live in mosaicmesh.state; re-imported here so existing code
# (and tests that do `from server import Client`) keeps working.
from mosaicmesh.state import (
    Settings, Scripts, Display, PlayState, MediaElement,
    Playlist, Schedule, PlayMode, Client,
    _apply_default_scripts, migrate_client_objects,
)
from mosaicmesh.persistence import (
    save_settings_incremental, saveSettings, cleanup_old_clients,
)
from mosaicmesh.cache import (
    get_pooled_file_handle, close_file_pool,
    prewarm_static_cache, get_cached_file,
    file_cache, cache_stats,
)
from mosaicmesh.broadcast import (
    _send_to_session, _deliver,
    broadcast_to_client, broadcast_to_display_group,
)
from mosaicmesh.calibration import (
    order_points, _draw_fitted_label, group_bounding_box,
    reconstruct_screen_quad, _quad_box, _quad_aspect,
    _aspect_in_marker_frame, reconcile_screen_quad, _render_output_dims,
    warp_image_for_screen, _hex_to_bgr, letterbox_to_aspect,
    assign_group_bounding_boxes, _group_clients,
    find_squares, angle_cos,
)
from mosaicmesh.render import (
    _keyframe_grid_args, _video_input_args, _video_encoder_args,
    _get_push_sem, compute_render_token, _broadcast_render_status,
    _is_renderable, _normalize_effect, _resolve_effect_filters,
    _run_ffmpeg, render_group_async,
    _per_client_items, _broadcast_per_client_play, _broadcast_per_client_preload,
    isVideoItem, quad_to_source_points,
    build_ffmpeg_perspective_cmd, build_ffmpeg_individual_cmd,
    get_video_dimensions, resolve_media_path,
    _duration_ms, _media_item_payload,
    _resolve_media_url, _build_media_elements,
    _apply_playlist, _start_group_playback, _stop_group_playback,
    _group_online_keys, _begin_prepare, _prepare_unsynced_clients,
    _VIDEO_ENCODER, _RENDER_CONCURRENCY, _VIDEO_HWACCEL, _PUSH_CONCURRENCY,
    KEYFRAME_GRID_SEC, _VIDEO_EXTS, _SEG_FILE_RE,
)
from mosaicmesh.device_scripts import (
    SSH_KEY_PATH, SSH_USER, SSH_LEGACY_OPTS, DISPLAY_URL,
    WEBCLIP_BUNDLE_ID, WEBAPP_ICON_FBX, WEBAPP_ICON_FBY,
    DEFAULT_DEVICE_SCRIPTS,
    _launch_webapp_via_vnc, _run_device_script, _drop_pooled_vnc,
)
from mosaicmesh.scheduling import (
    _FREQ_MAP, playlist_index, _parse_date, _hhmm_to_min, schedule_active_at,
)
from mosaicmesh.api.discovery import (
    auto_configure_client, get_discovered_devices,
    _expected_seg_keys_for_display, _expected_segments_for_client,
    _propagation_percent_for_client, sync_new_client_to_group,
    api_discovery_devices, api_discovery_stats, api_discovery_configure,
)
from mosaicmesh.api.playlists import (
    api_playlists_list, api_playlists_create,
    api_playlists_update, api_playlists_delete,
)
from mosaicmesh.websocket.legacy import msg_response
# Re-exported for backward-compat: tests in test_websocket_handlers.py call
# server.handle_websocket_message(...) directly. The handler is also NOT YET
# wired into ws_handler (dispatch.py only dispatches to msg_response); when
# the typed protocol gets wired in, ws_handler should call it conditionally
# and this re-export becomes optional.
from mosaicmesh.websocket.typed import handle_websocket_message
from mosaicmesh.websocket.dispatch import ws_handler, handle_client_disconnect

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
# MUST match the password baked into the fleet's Veency plist by the
# onboarding script (tools/onboard_devices.ps1 -VncPassword). Default
# there is "mosaicmesh" so we match here; an operator who changed the
# onboarding -VncPassword needs to override this constant too (or set
# the MMVNCPW env var).
VEENCY_PASSWORD = os.environ.get("MMVNCPW") or "mosaicmesh"

# Persistent VNC connections, keyed by client_key. Created lazily on
# first auto-arm; dropped from the pool when the client goes offline
# or on any per-tap failure. Each entry is a ThreadedVNCClientProxy
# from vncdotool.api -- vncdotool runs Twisted's reactor in a
# background thread, and proxy methods are thread-safe to call from
# the asyncio loop via run_in_executor. The lock guards cache
# read-modify-write; the proxy itself has its own internal queuing.
_veency_pool = {}
_veency_lock = asyncio.Lock()

# Render pipeline constants + functions live in mosaicmesh.render (imported above).
# Device lifecycle script constants + functions live in mosaicmesh.device_scripts (imported above).
# _PUSH_STALL_WINDOW_S and _PUSH_POLL_INTERVAL_S remain here because they are only
# used by _push_segment_to_cached_clients / _poll_push_progress which stay in server.py.
#
# Why STALL-based abort rather than a static timeout: legitimate cache pushes to
# iPad-1 over WiFi can run for many minutes on a fresh segment; a fixed timeout
# either over-aborts (kills slow but progressing transfers) or under-aborts (lets
# zombie SSH sessions hang for hours). Polling the iPad's destination file size
# every _PUSH_POLL_INTERVAL_S seconds and aborting only when no bytes flow for
# _PUSH_STALL_WINDOW_S seconds gives us "as long as it takes, but no longer."
# Empirically tuned: 30s stall window catches genuinely-dead transfers without
# spurious-aborting healthy-but-slow ones; 5s poll interval is the sweet spot
# between "responsive to stalls" and "not aggressively over-polling sshd on a
# resource-constrained iPad-1." Override via MMPUSH_STALL_S / MMPUSH_POLL_S
# env vars for tuning per-fleet without code changes.
_PUSH_STALL_WINDOW_S = int(os.environ.get("MMPUSH_STALL_S") or 30)
_PUSH_POLL_INTERVAL_S = float(os.environ.get("MMPUSH_POLL_S") or 5.0)


def parse_args():
    """Parse CLI args. Called only from __main__ so that importing this module
    (e.g. from tests) has no argparse side effects."""
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--Port", help="Port to run server on")
    parser.add_argument("-v", "--Verbose", action='store_true', help="Verbose output")
    return parser.parse_args()


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


def _do_tap(proxy, cx, cy):
    """Synchronous worker: move pointer + click button 1. Runs in
    the default ThreadPoolExecutor (offloaded from the asyncio loop
    by _auto_arm_client) because vncdotool's proxy methods block
    on the Twisted reactor's queue dispatch.

    After the click, park the pointer in the corner so the visible
    mouse cursor drawn by jp.ashikase.mousesupport (a MobileSubstrate
    tweak veency depends on) doesn't linger over the displayed video.
    (0, 0) lands the cursor in the top-left corner -- the least
    obtrusive on-screen position; full off-screen coords get clamped
    by Veency to the screen bounds, so this is as hidden as we get
    without a system-wide mousesupport plist change."""
    proxy.mouseMove(cx, cy)
    proxy.mousePress(1)
    try:
        proxy.mouseMove(0, 0)
    except Exception:
        pass


async def _get_pooled_vnc(client_key, ip):
    """Return a connected ThreadedVNCClientProxy for the given iPad,
    reusing a pooled connection if one exists. First-call cold path:
    full RFB handshake + auth (~1 s LAN). Subsequent calls: dict
    lookup (<1 ms)."""
    async with _veency_lock:
        proxy = _veency_pool.get(client_key)
        if proxy is not None:
            return proxy
    # Cold connect outside the lock so other clients aren't blocked
    # by this iPad's handshake.
    loop = asyncio.get_event_loop()
    proxy = await loop.run_in_executor(
        None,
        lambda: api.connect(f"{ip}::{VEENCY_PORT}",
                            password=VEENCY_PASSWORD,
                            timeout=5))
    async with _veency_lock:
        # Race: another coroutine may have populated the pool while
        # we were handshaking. Their proxy wins; discard ours.
        existing = _veency_pool.get(client_key)
        if existing is not None:
            try:
                await loop.run_in_executor(None, proxy.disconnect)
            except Exception:
                pass
            return existing
        _veency_pool[client_key] = proxy
    return proxy


async def _push_segment_to_cached_clients(client_key, segment_hash, segment_n):
    """Scp a freshly-rendered per-iPad mp4 to the iPad's lighttpd cache
    directory. Called from the render pipeline's success path for each
    Client with cacheMode == "lighttpd-localhost".

    Best-effort: a failed scp leaves the segment hash absent from
    Client.cachedSegments, which means _resolve_media_url will hand
    out the central-server URL for the next PLAY of this segment on
    this iPad. Operator sees the failure in server.err and can re-run.

    Spec: docs/superpowers/specs/2026-06-03-media-cache-design.md
    section 'Render-complete push hook'."""
    client = settings.clients.get(client_key)
    if not client:
        return
    if getattr(client, "cacheMode", "none") != "lighttpd-localhost":
        return
    if not getattr(client, "ip", ""):
        logging.warning("cache-push %s: no IP, skipping", client_key)
        return
    src = "media/%s/videos/seg_%s_%d.mp4" % (client_key, segment_hash, segment_n)
    dst = ("%s@%s:/var/mobile/Media/MosaicMeshCache/seg_%s_%d.mp4"
           % (SSH_USER, client.ip, segment_hash, segment_n))
    cmd = ["scp", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS + [src, dst]
    try:
        total_bytes = os.path.getsize(src)
    except OSError:
        # Source missing -- scp will fail with a clearer error than
        # we can produce here, so let it run and surface that via
        # the existing rc!=0 logging path. Default totalBytes to 0
        # so cachePushProgress is well-formed; percent will read 0
        # throughout, which is the truthful signal for a doomed push.
        total_bytes = 0

    # Throttle: 2026-06-03 production-load discovery -- firing 24
    # parallel scps over a single AP saturates the WiFi, all of them
    # crawl at ~100 KB/s, and they all hit a static timeout. We
    # serialise via _PUSH_CONCURRENCY, AND we now detect stalls by
    # polling the iPad-side destination file size (see
    # _poll_push_progress). A push that's making forward progress,
    # however slowly, runs to completion. Only a transfer that
    # genuinely stops moving bytes for _PUSH_STALL_WINDOW_S aborts.
    sem = _get_push_sem()
    seg_key = "%s_%d" % (segment_hash, segment_n)
    async with sem:
        now_ms = int(time.time() * 1000)
        client.cachePushProgress = {
            "token": segment_hash,
            "n": segment_n,
            "bytesSent": 0,
            "totalBytes": total_bytes,
            "startedMs": now_ms,
            "lastChangeMs": now_ms,
            "status": "pushing",
            "mbps": 0.0,
        }
        _broadcast_cache_progress(client_key, client)

        stall_event = asyncio.Event()
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        poller = asyncio.ensure_future(
            _poll_push_progress(client_key, client, stall_event, proc))
        communicate_task = asyncio.ensure_future(proc.communicate())
        stall_task = asyncio.ensure_future(stall_event.wait())
        try:
            done, pending = await asyncio.wait(
                {communicate_task, stall_task},
                return_when=asyncio.FIRST_COMPLETED)
            if stall_task in done and communicate_task not in done:
                # Stall path: poller signalled, scp hasn't finished.
                # Kill scp, then await the ORIGINAL communicate task
                # (now unblocked by SIGKILL on the child). Crucially
                # we DO NOT call proc.communicate() a second time here
                # -- asyncio's Process only allows one pending read at
                # a time, and a second call collides with
                # communicate_task with "read() called while another
                # coroutine is already waiting for incoming data".
                proc.kill()
                try:
                    await asyncio.wait_for(communicate_task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                sent = client.cachePushProgress.get("bytesSent", 0) \
                    if client.cachePushProgress else 0
                logging.warning(
                    "cache-push %s seg_%s_%d: stalled (no progress for %ds, "
                    "%d/%d bytes)",
                    client_key, segment_hash, segment_n,
                    _PUSH_STALL_WINDOW_S, sent, total_bytes)
                if client.cachePushProgress is not None:
                    client.cachePushProgress["status"] = "stalled"
                    _broadcast_cache_progress(client_key, client)
                return
            # scp finished first
            stall_task.cancel()
            _out, err = await communicate_task
            if proc.returncode == 0:
                client.cachedSegments.add(seg_key)
                if client.cachePushProgress is not None:
                    client.cachePushProgress["bytesSent"] = total_bytes
                    client.cachePushProgress["status"] = "cached"
                    _broadcast_cache_progress(client_key, client)
                logging.info("cache-push: %s seg_%s_%d -> %s",
                             client_key, segment_hash, segment_n, client.ip)
            else:
                tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-2:]
                logging.warning("cache-push rc=%s for %s seg_%s_%d: %s",
                                proc.returncode, client_key,
                                segment_hash, segment_n, " | ".join(tail))
        except Exception as e:  # noqa: BLE001
            logging.warning("cache-push exception for %s seg_%s_%d: %s",
                            client_key, segment_hash, segment_n, e)
        finally:
            poller.cancel()
            try:
                await poller
            except (asyncio.CancelledError, Exception):
                pass
            # Clear transient progress state. The cached-status broadcast
            # above (if applicable) is what consumers see; the in-memory
            # dict is reset to None so subsequent /api/discovery/devices
            # reads show idle.
            client.cachePushProgress = None


async def _poll_push_progress(client_key, client, stall_event, proc):
    """Sibling coroutine to _push_segment_to_cached_clients. Opens
    ONE long-running ssh connection that emits the destination
    file's size every _PUSH_POLL_INTERVAL_S seconds (via a shell
    loop on the iPad). Reads each line of stdout to update
    client.cachePushProgress, broadcast CACHE_PROGRESS, and detect
    stalls. Exits + closes its ssh when the enclosing push coroutine
    cancels us (success or stall path) or when scp finishes.

    Why one long-lived connection rather than per-poll connections:
    iPad-1 sshd has a tight concurrent-connection limit. Opening a
    fresh ssh every poll competes for handshake slots with the scp
    itself, which can starve the very transfer we're watching --
    bytes never move, the stall window fires erroneously, the
    push is aborted by us not by reality. One ssh per push keeps
    the poller's network footprint trivial relative to the
    transfer."""
    prog = client.cachePushProgress
    if prog is None:
        return
    seg_path = ("/var/mobile/Media/MosaicMeshCache/seg_%s_%d.mp4"
                % (prog["token"], prog["n"]))
    # The remote shell loop: print size (0 if file missing) every
    # _PUSH_POLL_INTERVAL_S seconds, forever. We rely on ssh process
    # cancellation (poller.cancel() in the push coroutine's finally
    # block) to close this when the push ends. The interval lives
    # on the iPad side so we don't pay handshake cost per sample.
    poll_script = (
        "F=%s; while true; do "
        "if [ -f $F ]; then stat -c%%s $F; else echo 0; fi; "
        "sleep %d; "
        "done"
    ) % (seg_path, int(_PUSH_POLL_INTERVAL_S))
    ssh_cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS
               + ["-T",   # disable TTY allocation; we're just streaming bytes
                  "%s@%s" % (SSH_USER, client.ip),
                  poll_script])
    poll_proc = None
    last_broadcast_bytes = -1
    seen_nonzero = False
    try:
        poll_proc = await asyncio.create_subprocess_exec(
            *ssh_cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        while proc.returncode is None:
            try:
                line = await asyncio.wait_for(
                    poll_proc.stdout.readline(),
                    timeout=_PUSH_POLL_INTERVAL_S + 5)
            except asyncio.TimeoutError:
                line = b""
            if proc.returncode is not None:
                return
            if client.cachePushProgress is None:
                return
            try:
                sz = int((line or b"0").strip() or 0)
            except ValueError:
                sz = 0
            now_ms = int(time.time() * 1000)
            if sz > client.cachePushProgress["bytesSent"]:
                if not seen_nonzero and sz > 0:
                    seen_nonzero = True
                elapsed_s = max(0.001,
                                (now_ms - client.cachePushProgress["startedMs"])
                                / 1000.0)
                client.cachePushProgress["bytesSent"] = sz
                client.cachePushProgress["lastChangeMs"] = now_ms
                client.cachePushProgress["mbps"] = round(
                    sz / 1024.0 / 1024.0 / elapsed_s, 2)
                if sz != last_broadcast_bytes:
                    _broadcast_cache_progress(client_key, client)
                    last_broadcast_bytes = sz
            else:
                # No new bytes since last lastChangeMs update. Only
                # treat this as a stall AFTER we've observed at least
                # one non-zero size -- before that, scp is legitimately
                # in setup (ssh handshake, key exchange, remote file
                # open). On iPad-1's SHA-1 sshd this can take 30s+ on
                # contended WiFi. Killing a transfer that hasn't
                # had a chance to start writing yet is a regression
                # vs. the static-timeout approach we replaced.
                if not seen_nonzero:
                    continue
                stalled_ms = now_ms - client.cachePushProgress["lastChangeMs"]
                if stalled_ms >= _PUSH_STALL_WINDOW_S * 1000:
                    stall_event.set()
                    return
    finally:
        if poll_proc is not None:
            try:
                poll_proc.kill()
                await poll_proc.wait()
            except Exception:
                pass


def _broadcast_cache_progress(client_key, client):
    """Emit a SockJS CACHE_PROGRESS message reflecting
    client.cachePushProgress. Broadcast DEST=ALL; admin.html listens
    for it, iPads have no handler and silently drop it. Safe to call
    when cachePushProgress is None (used as a clear/no-op ping)."""
    prog = client.cachePushProgress or {}
    total = prog.get("totalBytes", 0) or 0
    sent = prog.get("bytesSent", 0) or 0
    percent = (100.0 * sent / total) if total else 0.0
    payload = {
        "clientKey": client_key,
        "ip": getattr(client, "ip", ""),
        "displayID": getattr(client, "displayID", None),
        "token": prog.get("token"),
        "n": prog.get("n"),
        "bytesSent": sent,
        "totalBytes": total,
        "percent": round(percent, 1),
        "mbps": prog.get("mbps", 0.0),
        "status": prog.get("status", "cached"),
    }
    try:
        socketmanager.broadcast(jsonpickle.encode({
            "DEST": "ALL",
            "REQUEST": "CACHE_PROGRESS",
            "PAYLOAD": payload,
        }))
    except Exception as e:  # noqa: BLE001
        # SockJS broadcast errors are noisy and non-fatal -- we don't
        # want a misbehaving consumer to spam logs at poll rate.
        logging.debug("CACHE_PROGRESS broadcast failed for %s: %s",
                      client_key, e)


async def _reconcile_ipad_cache(client):
    """Remove cached segment files on this iPad that no longer
    correspond to any current playlist media element on this iPad's
    display group. Best-effort -- ssh failures just leave orphans
    on disk (cosmetic concern, recovered next reconciliation).

    Skips non-lighttpd-localhost clients (they have no on-device
    cache for us to clean)."""
    if getattr(client, "cacheMode", "none") != "lighttpd-localhost":
        return
    if not getattr(client, "ip", ""):
        return
    # Build set of seg_HASH_N keys currently referenced by this
    # client's display group's playlist.
    #
    # The cache key produced by _push_segment_to_cached_clients is
    # "<renderedToken>_<item_index>" -- so in-use entries derive from
    # the display's current renderedToken + the enumerated index of
    # each SEGMENT-mode MediaElement. (Older variants of this code
    # tried to parse seg_HASH from item.file via regex, but item.file
    # holds the SOURCE path like '/media/server/videos/<...>.mov'
    # rather than the rendered seg_HASH_N.mp4 filename -- that's set
    # per-client by _per_client_items at PRELOAD time, not on the
    # shared MediaElement. So the regex fallback never matched in
    # production and was deleting ALL cached segments as 'orphans'
    # right after every successful push.)
    in_use = set()
    did = getattr(client, "displayID", None)
    display = settings.displays.get(did) if did else None
    if display:
        token = getattr(display, "renderedToken", None)
        if token:
            for i, item in enumerate(getattr(display, "mediaElements", []) or []):
                pm = getattr(item, "playmode", None)
                pm_name = pm.name if hasattr(pm, "name") else (pm if isinstance(pm, str) else None)
                if pm_name == "SEGMENT":
                    in_use.add(f"{token}_{i}")
        # Test-stub fallback: some unit tests pass items with explicit
        # seg_hash/seg_n attributes (the _It stub in test_media_cache.py).
        # Honour those when present so the existing test contract holds.
        for item in (getattr(display, "mediaElements", []) or []):
            h = getattr(item, "seg_hash", None)
            n = getattr(item, "seg_n", None)
            if h is not None and n is not None:
                in_use.add(f"{h}_{n}")
    stale = set(client.cachedSegments) - in_use
    if not stale:
        return
    # Remove from server-side state immediately (the file deletes happen
    # async). Worst case a stale file lingers on disk after we forget
    # about it -- next reconciliation will retry the delete.
    for s in stale:
        client.cachedSegments.discard(s)
        cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS +
               [f"{SSH_USER}@{client.ip}",
                f"rm -f /var/mobile/Media/MosaicMeshCache/seg_{s}.mp4"])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(), timeout=15)
        except Exception as e:  # noqa: BLE001
            logging.debug("cache-reconcile rm failed for %s seg_%s: %s",
                          client.clientKey, s, e)


async def _auto_arm_client(client_key):
    """Deliver one Veency VNC tap (screen centre) to arm an un-armed
    iOS device. Holds one persistent VNC connection per iPad in
    _veency_pool: first tap to an iPad pays the handshake cost
    (~1 s LAN); subsequent taps reuse the open socket
    (~5-20 ms -- single PointerEvent write). On any error the
    pooled connection is dropped and the next attempt
    re-handshakes.

    Replaces the previous vncdo-subprocess implementation
    (~1-3 s/tap regardless of pooling). The user-gesture gate on
    iOS 5 Safari still requires a tap; we just made the tap
    cheap. See docs/superpowers/plans/2026-06-02-veency-connection-pool.md
    for the design.

    Best-effort: missing IP / handshake failure / runtime tap
    failure all just log -- the PREPARE timeout covers a device
    that can't be armed."""
    if not AUTO_ARM:
        return
    client = settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        return
    cx = int((getattr(client, "deviceWidth", 0) or 1024) / 2)
    cy = int((getattr(client, "deviceHeight", 0) or 768) / 2)
    loop = asyncio.get_event_loop()
    try:
        proxy = await _get_pooled_vnc(client_key, client.ip)
        await loop.run_in_executor(None, _do_tap, proxy, cx, cy)
        logging.info("auto-arm: tapped %s at %d,%d (pooled)",
                     client_key, cx, cy)
    except Exception as e:  # noqa: BLE001
        # Drop the bad connection so the next attempt re-handshakes.
        await _drop_pooled_vnc(client_key)
        logging.warning("auto-arm tap failed for %s: %s", client_key, e)


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
    # no-cache so a RELOAD broadcast actually delivers updated HTML/JS
    # to iPad-1 Safari. Without this header, iPad-1's aggressive disk
    # cache happily served stale index.html / inline scripts even after
    # a sock.send(RELOAD) -- a real bug we hit during the 2026-06-03
    # client-UX rollout (audio + fullscreen edits invisible until
    # explicitly reloaded again with no-cache). The actual byte cost
    # is negligible (a fleet RELOAD is rare) and these are small files.
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate",
               "Pragma": "no-cache", "Expires": "0"}
    return web.Response(body=data, content_type=ct, headers=headers)

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
            # See index_handler comment: iPad-1 needs explicit no-cache
            # so a RELOAD actually delivers updated JS (mosiacmesh.js,
            # GoTime.js). The server-side get_cached_file still caches
            # the file contents in memory.
            headers = {"Cache-Control": "no-cache, no-store, must-revalidate",
                       "Pragma": "no-cache", "Expires": "0"}
            return web.Response(body=data, content_type='text/javascript',
                                headers=headers)

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
    two quads accidentally trace the same compound region?" decision.

    NOTE: mosaicmesh.calibration ALSO defines _quad_iou, but with the
    PRECISE convex intersection (cv.intersectConvexConvex) for the
    marker/border reconciliation pipeline. The two are intentionally
    different algorithms for different call sites; do not merge them.
    This AABB version is local to server.py because it's only used by
    the _drop_overlapping helper in the screen-quad detection pipeline.
    server.py does NOT re-export _quad_iou from mosaicmesh.calibration
    (the calibration version stays internal to that module).
    """
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

    # Cache reconciliation: sweep orphans from each cached iPad's local
    # MosaicMeshCache/ dir. Fires every process() tick (~5s) but the
    # helper is a no-op for iPads whose cachedSegments already match
    # the current playlist (the common case), so the cost is just a
    # set difference per cached client.
    for _c in list(settings.clients.values()):
        if getattr(_c, "cacheMode", "none") == "lighttpd-localhost" and getattr(_c, "isOnline", False):
            asyncio.ensure_future(_reconcile_ipad_cache(_c))

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
                # backlog=4096: aiohttp's default of 128 is the kernel listen
                # queue size. During a fleet-wide Start All burst, each iPad's
                # Safari opens ~6 parallel sockets (HTML + 2 JS files + SockJS
                # xhr_streaming + xhr_send + /time), so 24 iPads ≈ 144 SYNs
                # arriving in <1s, plus the admin browser and sockets still
                # tearing down from the previous load. Overflowing 128 causes
                # Windows to silently DROP the SYN (not RST) -- Safari then
                # retransmits with 3s/6s/12s/24s backoff and eventually shows
                # "server did not respond". 4096 is well beyond any realistic
                # connect-burst and Windows' SOMAXCONN accepts it.
                site = web.TCPSite(runner=runner,
                                    host=["0.0.0.0", "::"],
                                    port=args.Port or 3000,
                                    backlog=4096)
                await site.start()
                
                logging.debug('Started webapp')
                
                # Set up socket manager
                socketmanager = sockjs.get_manager(app=app,name='mosiacmesh')

                # Pre-warm the static-file cache so a fleet-wide Start burst
                # doesn't block the event loop on cold disk reads.
                prewarm_static_cache()
                
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
        app.router.add_get('/api/playlists', api_playlists_list)
        app.router.add_post('/api/playlists', api_playlists_create)
        app.router.add_put('/api/playlists/{name}', api_playlists_update)
        app.router.add_delete('/api/playlists/{name}', api_playlists_delete)
        sockjs.add_endpoint(app, ws_handler, name='mosiacmesh', prefix='/sockjs/')
        
        asyncio.run(run_server())
        
    finally:
        # Use incremental save and cleanup resources
        save_settings_incremental()
        close_file_pool()

