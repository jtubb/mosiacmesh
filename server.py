import logging
from enum import Enum
import os
import cv2 as cv
import numpy as np
from pathlib import Path
import time
import jsonpickle
import jsonpickle.ext.numpy as jsonpickle_numpy
jsonpickle_numpy.register_handlers()

import asyncio
from aiohttp import web

from device_detector import DeviceDetector

from beeprint import pp

import sockjs

import argparse
from functools import lru_cache

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
    """Close all pooled file handles"""
    for handle in file_handle_pool.values():
        handle.close()
    file_handle_pool.clear()

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
            "isOnline": client.ready,
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

def get_cached_file(file_path):
    """Get file content with caching based on modification time"""
    try:
        stat = os.stat(file_path)
        mod_time = stat.st_mtime
        
        # Check if file is in cache and not modified
        if file_path in file_cache:
            cached_data, cached_time = file_cache[file_path]
            if cached_time == mod_time:
                cache_stats['hits'] += 1
                return cached_data
        
        # File not cached or modified - read from disk
        cache_stats['misses'] += 1
        data = Path(file_path).read_bytes()
        file_cache[file_path] = (data, mod_time)
        
        # Limit cache size to prevent memory issues
        if len(file_cache) > 100:
            # Remove oldest entries (simple FIFO)
            oldest_key = next(iter(file_cache))
            del file_cache[oldest_key]
            
        return data
    except (OSError, IOError):
        return None

# Initialize parser
parser = argparse.ArgumentParser()

# Adding optional argument
parser.add_argument("-p", "--Port", help = "Port to run server on")
parser.add_argument("-v", "--Verbose", action='store_true', help = "Verbose output")

# Read arguments from command line
args = parser.parse_args()

class Settings():
    def __init__(self):
        self.displays = {}
        self.scripts = {}
        self.clients = {}

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

class PlayState(Enum):
    NOACTION = 0
    STOP = 1
    PLAY = 2
    PAUSE = 3

class MediaElement():
    def __init(self):
        self.id = None
        self.file = None
        self.duration = None
        self.playmode = PlayMode.DEFAULT

class PlayMode(Enum):
    DEFAULT = 0
    FULL = 1
    SEGMENT = 2
    SCRIPT = 3

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
        self.ready = False
        # Enhanced discovery fields
        self.discoveryTime = time.time()
        self.lastSeen = time.time()
        self.connectionCount = 0
        self.capabilities = []
        self.autoConfigured = False
        self.discoverySource = "manual"  # manual, websocket, network

async def ws_handler(msg, session):
    logging.debug("WS_HANDLER")
    if session.manager is None:
        return
    if msg.type == sockjs.MSG_OPEN:
        # Enhanced discovery notification with client info
        client_info = {
            "sessionId": session.id,
            "ip": session.request.remote if hasattr(session, 'request') else "unknown",
            "userAgent": session.request.headers.get('User-Agent', '') if hasattr(session, 'request') else "",
            "timestamp": time.time()
        }
        discovery_announcement = {
            "REQUEST": "DEVICE_DISCOVERED", 
            "PAYLOAD": client_info
        }
        session.manager.broadcast(jsonpickle.encode(discovery_announcement))
        
        # Also send traditional JOIN for backward compatibility
        session.manager.broadcast(jsonpickle.encode({"REQUEST": "JOIN", "PAYLOAD":session.id}))
        
    elif msg.type == sockjs.MSG_MESSAGE:
        session.send(msg_response(jsonpickle.decode(msg.data),session))
    elif msg.type == sockjs.MSG_CLOSED:
        # Enhanced disconnect notification
        handle_client_disconnect(session.id)
        session.manager.broadcast(jsonpickle.encode({"REQUEST": "DISC", "PAYLOAD":session.id}))

def msg_response(msg,session):
    clientid = session.id
    logging.debug(session.manager[clientid].request.headers['User-Agent'])
    
    response = {"DEST":clientid,"REQUEST": msg["REQUEST"], "PAYLOAD": {}}
    
    logging.debug(session.manager[clientid].request.remote)
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
            client.ready = False
        response["PAYLOAD"] = "ACK"
        
    elif(msg["REQUEST"] == "SYNACK"):
        client = settings.clients.get(msg["PAYLOAD"])
        if client:
            client.ready = True
        response["PAYLOAD"] = "SYNACK"
        
    elif(msg["REQUEST"] == "REGISTER"):
        is_new_client = msg["SRC"] not in settings.clients
        settings.clients.setdefault(msg["SRC"], Client())
        # Cache client reference to avoid repeated dictionary lookups
        client = settings.clients[msg["SRC"]]
        
        # Enhanced registration with discovery tracking
        client.clientID = clientid
        client.userAgent = session.manager[clientid].request.headers['User-Agent']
        client.deviceWidth = msg["PAYLOAD"]["width"]
        client.deviceHeight = msg["PAYLOAD"]["height"]
        client.ip = session.manager[clientid].request.remote
        client.lastSeen = time.time()
        client.connectionCount += 1
        
        # Device detection and fingerprinting
        device = DeviceDetector(session.manager[clientid].request.headers['User-Agent']).parse()
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
        
    else:
        response["PAYLOAD"] = msg["PAYLOAD"]    #echo anything that isn't a registered command

    return jsonpickle.encode(response)

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
    settings.scripts.setdefault(scriptID, Script())
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
        response, ct = processVideo(path,filename)
    return web.Response(body=response, content_type=ct)

def processImage(path,filename):
    logging.debug("processImage")
    imgDir = "media/server/images"
    Path(imgDir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(path,filename)).rename(os.path.join(imgDir,filename))
    return "success", "text/html"
    
def processVideo(path):
    logging.debug("processVideo")
    vidDir = "media/server/videos"
    Path(imgDir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(path,filename)).rename(os.path.join(imgDir,filename))
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
        
def calibrate(filename):
    logging.debug(filename)
    image = cv.imread(filename)

    imgray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
    ret, thresh = cv.threshold(imgray, 127, 255, 0)
    
    arucoDict = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_6X6_50)
    arucoParams = cv.aruco.DetectorParameters()
    detector = cv.aruco.ArucoDetector(arucoDict, arucoParams)
    (corners, ids, rejected) = detector.detectMarkers(image)

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

async def discovery_api_handler(request):
    """REST API endpoint for device discovery management"""
    method = request.method
    path_parts = request.path.split('/')
    
    if method == "GET":
        if "devices" in path_parts:
            # Get all discovered devices
            devices = get_discovered_devices()
            return web.json_response({
                "success": True,
                "devices": devices,
                "total": len(devices),
                "online": len([d for d in devices if d["isOnline"]])
            })
        elif "stats" in path_parts:
            # Discovery statistics
            devices = get_discovered_devices()
            stats = {
                "totalDevices": len(devices),
                "onlineDevices": len([d for d in devices if d["isOnline"]]),
                "deviceTypes": {},
                "displayGroups": {},
                "autoConfiguredCount": len([d for d in devices if d["autoConfigured"]])
            }
            
            for device in devices:
                # Count by device type
                device_type = device["deviceType"] or "unknown"
                stats["deviceTypes"][device_type] = stats["deviceTypes"].get(device_type, 0) + 1
                
                # Count by display group
                display_id = device["displayID"] or "default"
                stats["displayGroups"][display_id] = stats["displayGroups"].get(display_id, 0) + 1
            
            return web.json_response({"success": True, "stats": stats})
    
    elif method == "POST":
        try:
            data = await request.json()
            action = data.get("action")
            
            if action == "reconfigure":
                client_key = data.get("clientKey")
                client = settings.clients.get(client_key)
                if client:
                    client.autoConfigured = False
                    auto_configure_client(client_key, client)
                    return web.json_response({"success": True, "message": "Client reconfigured"})
                else:
                    return web.json_response({"success": False, "error": "Client not found"}, status=404)
                    
            elif action == "bulk_reconfigure":
                client_keys = data.get("clientKeys", [])
                configured_count = 0
                for client_key in client_keys:
                    client = settings.clients.get(client_key)
                    if client:
                        client.autoConfigured = False
                        auto_configure_client(client_key, client)
                        configured_count += 1
                        
                return web.json_response({
                    "success": True, 
                    "message": f"Reconfigured {configured_count} clients",
                    "configured": configured_count
                })
                
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=400)
    
    return web.json_response({"success": False, "error": "Invalid request"}, status=400)

def migrate_client_objects():
    """Migrate old client objects to include new discovery fields"""
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

async def process():
    """Enhanced periodic processing with device health monitoring"""
    global last_discovery_scan
    current_time = time.time()
    
    # Update last seen times for all active clients
    for client_key, client in settings.clients.items():
        if client.ready:
            client.lastSeen = current_time
    
    # Check for stale clients (offline for more than 60 seconds)
    stale_clients = []
    for client_key, client in settings.clients.items():
        if (current_time - client.lastSeen) > 60 and client.ready:
            client.ready = False
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
                "onlineClients": len([c for c in settings.clients.values() if c.ready]),
                "timestamp": current_time
            }
        }
        socketmanager.broadcast(jsonpickle.encode(discovery_summary))
        process.last_announcement = current_time
    
    #response = {"DEST":"ALL","REQUEST": "TEST", "PAYLOAD": "NONE"}
    #socketmanager.broadcast(jsonpickle.encode(response))
    ##logging.debug('Test broadcast')

socketmanager = None

if __name__ == '__main__':
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
        # Discovery API endpoints
        app.router.add_route('GET', '/api/discovery/devices', discovery_api_handler)
        app.router.add_route('GET', '/api/discovery/stats', discovery_api_handler)
        app.router.add_route('POST', '/api/discovery/configure', discovery_api_handler)
        sockjs.add_endpoint(app, ws_handler, name='mosiacmesh', prefix='/sockjs/')
        
        asyncio.run(run_server())
        
    finally:
        # Use incremental save and cleanup resources
        save_settings_incremental()
        close_file_pool()

