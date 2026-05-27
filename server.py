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
            "ip": client.ip,
            "osName": client.osName,
            "osVersion": client.osVersion,
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
                    evf, eaf = _resolve_effect_filters(me, me.duration,
                                                       int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
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
                                                          int(c.deviceWidth) or 1, int(c.deviceHeight) or 1,
                                                          pad_w, pad_h, pad_x, pad_y,
                                                          getattr(me, "backgroundColor", "#000000"),
                                                          extra_video_filters=evf, extra_audio_filters=eaf)
                    else:
                        pts = quad_to_source_points(display.boundingBox, c.measuredPerimeter, sw, sh)
                        out_path = os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".mp4")
                        cmd = build_ffmpeg_perspective_cmd(src_path, out_path, pts,
                                                           int(c.deviceWidth) or 1, int(c.deviceHeight) or 1,
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
                    if me.playmode == PlayMode.INDIVIDUAL:
                        quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                        bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                        if bw <= 0 or bh <= 0 or cv.contourArea(np.array(c.measuredPerimeter, dtype="int32")) <= 0:
                            raise RuntimeError("degenerate screen quad for client " + str(key))
                        bg = _hex_to_bgr(getattr(me, "backgroundColor", "#000000"))
                        canvas = letterbox_to_aspect(img, bw, bh, bg)
                        warped = warp_image_for_screen(canvas, [bx, by, bw, bh], c.measuredPerimeter,
                                                       int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
                        cv.imwrite(os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".png"), warped)
                    else:
                        warped = warp_image_for_screen(img, display.boundingBox, c.measuredPerimeter,
                                                       int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
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


def isVideoItem(file):
    """True if a media file is a video (.mp4), mirroring the client's isVideoItem.
    Tolerates a trailing ?query."""
    return str(file or "").lower().split("?")[0].endswith(".mp4")


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
    subdir = "videos" if name.lower().endswith(".mp4") else "images"
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
        self.measuredCenter = None
        self.measuredPerimeter = None
        self.userAgent = None
        self.ip = ""
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

def _media_item_payload(me):
    """Per-item dict sent to clients in PLAY/PRELOAD. getattr guards items
    loaded from an older settings.dat that predate the newer fields."""
    return {"id": me.id, "file": me.file, "duration": me.duration,
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
        client.ip = _client_ip(session.request)
        client.lastSeen = time.time()
        client.isOnline = True
        client.connectionCount += 1
        
        # Device detection and fingerprinting
        device = DeviceDetector(session.request.headers['User-Agent']).parse()
        client.osName = device.os_name()
        client.osVersion = device.os_version()
        client.engine = device.engine()
        client.deviceBrand = device.device_brand()
        client.deviceModel = device.device_model()
        client.deviceType = device.device_type()
        
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
            response["PAYLOAD"] = {"status": "ERROR", "error": "no renderable items"}
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
            detected_type = _device_field(device.device_type)
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
    
    if(fileName.endswith('.jpg')):
        customHeaders = {'Content-Type':'image/jpeg'}
    elif(fileName.endswith('.png')):
        customHeaders = {'Content-Type':'image/png'}
    elif(fileName.endswith('.mp4')):
        customHeaders = {'Content-Type':'video/mp4'}
        subdir = "videos"
    else:
        customHeaders = {'Content-Type':'application/octet-stream'}
    
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
    i = 1
    for key in settings.clients.keys():
        id = settings.clients[key].arucoID or i
        if(not settings.clients[key].arucoID):
            settings.clients[key].arucoID = id
            i = i + 1
        # Generate the marker
        markerImage = np.zeros((300, 300), dtype=np.uint8)
        markerImage = cv.aruco.generateImageMarker(dictionary, settings.clients[key].arucoID, 300, markerImage, 1)
        Path("media/" + key + "/images").mkdir(parents=True, exist_ok=True)
        cv.imwrite("media/" + key + "/images/aruco.png", markerImage)
        if(displayID == None or settings.clients[key].displayID == displayID):
            #inform any client that they need to load aruco image
            response = {"DEST":key,"REQUEST": "CALIBRATE", "PAYLOAD": None}
            broadcast_to_client(key, response)

def identifyDisplays(isGroup,displayID):
    if(isGroup):
        response = {"REQUEST": "IDENTIFY", "PAYLOAD": ""}
        broadcast_to_display_group(displayID, response)
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
                # draw the ArUco marker ID on the image
                if(markerID >= len(settings.clients)):
                    break
                clientID = list(settings.clients.keys())[markerID-1]
                clientLabel = settings.clients[clientID].friendlyName or clientID
                cv.putText(image, str(clientLabel),(topLeft[0], topLeft[1] - 15), cv.FONT_HERSHEY_SIMPLEX,5, (255, 0, 0), 10)
                #Dictionary ordering is deterministic in python 3.7
                settings.clients[clientID].measuredCenter = [cX,cY]
                #find contours that enclose a marker - optimized with spatial indexing
                marker_bbox = (min(topLeft[0], bottomRight[0]), min(topLeft[1], bottomRight[1]),
                              max(topLeft[0], bottomRight[0]), max(topLeft[1], bottomRight[1]))
                
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
                        settings.clients[clientID].measuredPerimeter = approximatedShape
                        break

    # Handle case where there are no detected nodes
    x, y, w, h = cv.boundingRect(relevantContours)
    cX = int((x + (w / 2.0)))
    cY = int((y + (h / 2.0)))
    cv.circle(image, (cX, cY), 15, (0, 0, 255), -1)
    cv.rectangle(image, (x, y), (x+w, y+h), (0, 0, 255), 4)
    Path("media/displays/images").mkdir(parents=True, exist_ok=True)
    cv.imwrite("media/displays/images/calibration.png", image)
    
    # Clean up image memory
    del image, imgray, thresh
    cv.destroyAllWindows()

    assign_group_bounding_boxes()
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

async def api_media(request):
    """List the shared media library under media/server/{images,videos}."""
    def _list(sub):
        d = os.path.join("media", "server", sub)
        if not os.path.isdir(d):
            return []
        return ["/media/server/" + sub + "/" + f
                for f in sorted(os.listdir(d))
                if os.path.isfile(os.path.join(d, f))]
    body = json.dumps({"images": _list("images"), "videos": _list("videos")})
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

    saveSettings()
    return web.json_response({"success": True})

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

