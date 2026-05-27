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

def broadcast_to_client(client_id, response_dict):
    """Send message to specific client with caching"""
    response_dict["DEST"] = client_id
    socketmanager.broadcast(jsonpickle.encode(response_dict))

def broadcast_to_display_group(display_id, response_dict):
    """Send message to all clients in a display group"""
    for client_id, client in settings.clients.items():
        if client.displayID == display_id:
            response_dict["DEST"] = client_id  
            socketmanager.broadcast(jsonpickle.encode(response_dict))

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
    items = [_media_item_payload(me) for me in display.mediaElements]
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


def reconcile_screen_quad(marker_quad, border_contour, cw, ch, marker_px=300, min_iou=0.5):
    """Choose the screen quad: fiducial extrapolation, validated against the
    detected black-band outline. If the fiducial disagrees with the band but a
    cw<->ch swap agrees, a mobile auto-rotation is assumed (the reported canvas
    orientation was stale). Returns (quad (4,1,2) int32, source) where source is
    'fiducial' | 'rotated' | 'border'."""
    fid = reconstruct_screen_quad(marker_quad, cw, ch, marker_px)
    if border_contour is None or len(np.array(border_contour).reshape(-1, 2)) < 3:
        return fid, "fiducial"
    box = _quad_box(border_contour)
    iou = _quad_iou(fid, box)
    fid_sw = reconstruct_screen_quad(marker_quad, ch, cw, marker_px)
    iou_sw = _quad_iou(fid_sw, box)
    if iou >= iou_sw and iou >= min_iou:
        return fid, "fiducial"
    if iou_sw > iou and iou_sw >= min_iou:
        return fid_sw, "rotated"
    return box.astype("int32").reshape(4, 1, 2), "border"


def warp_image_for_screen(source_img, bbox, screen_quad, out_w, out_h):
    """Warp the region of source_img under a screen's quad onto that screen's
    pixel rect. bbox is the [x, y, w, h] region of the photo that the source image is stretched to fill
    (the group bbox for SEGMENT, the screen's own quad bbox for INDIVIDUAL); the full image is
    stretched to fill bbox, so the screen quad (photo coords) maps back into
    media coords, then a homography fits it to out_w x out_h."""
    h, w = source_img.shape[:2]
    bx, by, bw, bh = bbox
    ordered = order_points(screen_quad)  # [TL, TR, BR, BL] in photo coords
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
    raw = repr((items, display.boundingBox, clients))
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


async def render_group_async(display_id):
    """Async render of a group's SEGMENT items: images warped inline (OpenCV),
    videos warped by awaiting one ffmpeg subprocess per screen. Sets renderStatus
    and (on success) renderedToken; broadcasts RENDER_STATUS on each change."""
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
                    # Output at the client's TRUE rendered viewport (canvas),
                    # falling back to reported device dims when canvas is 0/missing.
                    out_w = int(getattr(c, "canvasWidth", 0) or c.deviceWidth) or 1
                    out_h = int(getattr(c, "canvasHeight", 0) or c.deviceHeight) or 1
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
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await proc.communicate()
                    if proc.returncode != 0:
                        raise RuntimeError("ffmpeg failed (" + str(proc.returncode) + ")")
            else:
                img = cv.imread(src_path) if src_path else None
                if img is None:
                    raise RuntimeError("cannot read source image: " + str(me.file))
                for key, c in clients:
                    out_dir = os.path.join("media", key, "images")
                    Path(out_dir).mkdir(parents=True, exist_ok=True)
                    # Output at the client's TRUE rendered viewport (canvas),
                    # falling back to reported device dims when canvas is 0/missing.
                    out_w = int(getattr(c, "canvasWidth", 0) or c.deviceWidth) or 1
                    out_h = int(getattr(c, "canvasHeight", 0) or c.deviceHeight) or 1
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
        display.renderedToken = token
        display.renderStatus = "ready"
        _broadcast_render_status(display_id, "ready")
        return {"status": "ready", "token": token}
    except Exception as e:
        logging.error("render failed for %s: %s", display_id, e)
        display.renderStatus = "error"
        _broadcast_render_status(display_id, "error")
        return {"status": "error", "error": str(e)}


def _broadcast_per_client_play(display_id, display):
    """Send each client its own PLAY: renderable items (SEGMENT/INDIVIDUAL) use
    that client's warped file when calibrated, otherwise the plain source."""
    token = display.renderedToken
    for key, c in _group_clients(display_id):
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
        broadcast_to_client(key, {"REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop}})


# Recognized video source extensions. SEGMENT/INDIVIDUAL items are transcoded
# to .mp4 by ffmpeg regardless of source; FULL items play directly in the
# browser (.mp4/.webm/.m4v are broadly playable, .mov needs h264/Safari/Chrome).
_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".ogv")


def isVideoItem(file):
    """True if a media file is a video, mirroring the client's isVideoItem.
    Tolerates a trailing ?query."""
    return str(file or "").lower().split("?")[0].endswith(_VIDEO_EXTS)


def quad_to_source_points(bbox, screen_quad, src_w, src_h):
    """Ordered [TL, TR, BR, BL] corners of the screen's quad expressed in source
    media pixel coords (the source is stretched to fill the group bbox)."""
    bx, by, bw, bh = bbox
    ordered = order_points(screen_quad)  # [TL, TR, BR, BL] in photo coords
    return [[(float(px) - bx) / bw * src_w, (float(py) - by) / bh * src_h] for (px, py) in ordered]


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
    cmd = ["ffmpeg", "-y", "-i", src_path,
           "-vf", vf,
           "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += ["-preset", "veryfast", "-movflags", "+faststart", out_path]
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
    cmd = ["ffmpeg", "-y", "-i", src_path,
           "-vf", vf,
           "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += ["-preset", "veryfast", "-movflags", "+faststart", out_path]
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

class PlayState(Enum):
    NOACTION = 0
    STOP = 1
    PLAY = 2
    PAUSE = 3

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
    """Copy a saved Playlist onto a group (mediaElements, loop, reset token, PRELOAD)."""
    display = settings.displays.setdefault(display_id, Display())
    display.mediaElements = _build_media_elements(pl.items)
    display.loop = bool(pl.loop)
    display.renderedToken = ""
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
    broadcast_to_display_group(display_id, {"REQUEST": "STOP", "PAYLOAD": {"displayID": display_id}})


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
                 "rebootScript")


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
            sync_new_client_to_group(msg["SRC"], client)

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
        response["PAYLOAD"]="SUCCESS"
        
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
                _start_group_playback(display_id, resume_epoch)
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
        if request.http_range.stop:
            logging.debug(f'Range Start {request.http_range.start} - Stop {request.http_range.stop-1}')
            customHeaders['Accept-Ranges'] = 'bytes'
            file_size = os.path.getsize(file_path)
            customHeaders['Content-Range'] = f'bytes {request.http_range.start}-{request.http_range.stop-1}/{file_size}'
            customStatus = 206
            # Use pooled file handle for better performance
            handle = get_pooled_file_handle(file_path, 'rb')
            handle.seek(request.http_range.start)
            data = handle.read(request.http_range.stop-request.http_range.start)
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
    """Return (dictionary, parameters) for 6x6 ArUco marker detection."""
    dictionary = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_6X6_50)
    parameters = cv.aruco.DetectorParameters()
    return dictionary, parameters

def detect_aruco_markers(image):
    """Detect ArUco markers in an image. Returns (corners, ids, rejected)."""
    dictionary, parameters = setup_aruco_detector()
    detector = cv.aruco.ArucoDetector(dictionary, parameters)
    return detector.detectMarkers(image)

def calibrate(filename):
    logging.debug(filename)
    image = cv.imread(filename)

    imgray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
    ret, thresh = cv.threshold(imgray, 127, 255, 0)

    (corners, ids, rejected) = detect_aruco_markers(image)

    contours, hierarchy = cv.findContours(thresh, cv.RETR_TREE, cv.CHAIN_APPROX_SIMPLE)
    #cv.drawContours(image, contours, -1, (0, 0, 255), 2)
    
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
                cv.putText(image, str(clientLabel),(topLeft[0], topLeft[1] - 15), cv.FONT_HERSHEY_SIMPLEX,5, (255, 0, 0), 10)
                #Dictionary ordering is deterministic in python 3.7
                settings.clients[clientID].measuredCenter = [cX,cY]
                #find contours that enclose a marker - optimized with spatial indexing
                marker_bbox = (min(topLeft[0], bottomRight[0]), min(topLeft[1], bottomRight[1]),
                              max(topLeft[0], bottomRight[0]), max(topLeft[1], bottomRight[1]))

                # The enclosing/band contour (screen's black border) validates the
                # fiducial extrapolation. None if nothing encloses the marker.
                border_contour = None
                for contour in contours:
                    # Quick bounding box check before expensive polygon test
                    x, y, w, h = cv.boundingRect(contour)
                    if not (x <= marker_bbox[0] and y <= marker_bbox[1] and
                           x + w >= marker_bbox[2] and y + h >= marker_bbox[3]):
                        continue

                    # Only do expensive polygon tests on spatially relevant contours
                    result1 = cv.pointPolygonTest(contour, topLeft, False)
                    result2 = cv.pointPolygonTest(contour, bottomRight, False)
                    if(result1 == 1 and result2 == 1):
                        perimeter = cv.arcLength(contour, True)
                        approximatedShape = cv.approxPolyDP(contour, 0.01 * perimeter, True)
                        if(len(relevantContours) == 0):
                            relevantContours = approximatedShape
                        else:
                            relevantContours = np.concatenate((relevantContours,approximatedShape))
                        for i in range(len(approximatedShape)-1):
                            cv.line(image, approximatedShape[i][0], approximatedShape[i+1][0], (0, 255, 0), 4)
                        cv.line(image, approximatedShape[len(approximatedShape)-1][0], approximatedShape[0][0], (0, 255, 0), 4)
                        border_contour = approximatedShape
                        break

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
                elif source == "border":
                    logging.warning("calibrate: fiducial/band mismatch for %s; using band outline", clientID)
                _cli.measuredPerimeter = quad
                # Visualize the reconciled quad when it differs from the raw band.
                if source != "fiducial":
                    qpts = quad.reshape(4, 2)
                    for i in range(4):
                        cv.line(image, tuple(qpts[i]), tuple(qpts[(i + 1) % 4]), (0, 255, 0), 4)

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
    del image, imgray, thresh

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
    """REST: configure client(s). Supports three payload styles:

      - {"clientKey", "displayID"?, "friendlyName"?}      -> update fields
      - {"action": "reconfigure", "clientKey"}            -> re-run auto-config
      - {"action": "bulk_reconfigure", "clientKeys": [...]}-> re-run for many

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
    else:
        if "displayID" in data:
            client.displayID = data["displayID"]
        if "friendlyName" in data:
            client.friendlyName = data["friendlyName"]
            client.nameIsCustom = True   # user-set name: DNS won't override it

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
                site = web.TCPSite(runner=runner, host=None, port=args.Port or 3000)
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

