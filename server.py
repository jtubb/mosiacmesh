import logging
import os
import cv2 as cv
import numpy as np
from pathlib import Path
import time
import jsonpickle
import jsonpickle.ext.numpy as jsonpickle_numpy
jsonpickle_numpy.register_handlers()

from aiohttp import web

from device_detector import DeviceDetector

from beeprint import pp

import sockjs

import argparse

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

class Client():
    def __init__(self):
        self.friendlyName = None
        self.clientID = ""
        self.displayID = None
        self.deviceHeight = 0
        self.deviceWidth = 0
        self.measuredCenter = None
        self.measuredPerimeter = None
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

async def ws_handler(msg, session):
    logging.debug("WS_HANDLER")
    if session.manager is None:
        return
    if msg.type == sockjs.MSG_OPEN:
        session.manager.broadcast(jsonpickle.encode({"SRC": "SRV", "DEST": "ALL", "REQUEST": "JOIN", "PAYLOAD":session.id}))
    elif msg.type == sockjs.MSG_MESSAGE:
        session.manager.broadcast(msg_response(jsonpickle.decode(msg.data),session))
    elif msg.type == sockjs.MSG_CLOSED:
        session.manager.broadcast(jsonpickle.encode({"SRC": "SRV", "DEST": "ALL", "REQUEST": "DISC", "PAYLOAD":session.id}))

def msg_response(msg,session):
    clientid = session.id
    logging.debug(session.manager[clientid].request.headers['User-Agent'])
    
    response = {"SRC": "SRV", "DEST" : msg["SRC"], "REQUEST": msg["REQUEST"], "PAYLOAD": {}}
    
    logging.debug(session.manager[clientid].request.remote)
    logging.debug(msg["SRC"])
    
    if(msg["REQUEST"] == "TIME"):
        response["PAYLOAD"] = int(time.time()*1000)
    elif(msg["REQUEST"] == "DISPLAYS"):
        response["DEST"] = "ALL"
        response["PAYLOAD"] = settings.displays
    elif(msg["REQUEST"] == "CLIENTS"):
        response["DEST"] = "ALL"
        logging.debug(msg["PAYLOAD"])
        if 'PAYLOAD' not in msg:
            response["PAYLOAD"] = settings.clients[msg["PAYLOAD"]]
        else:
            response["PAYLOAD"] = settings.clients
    elif(msg["REQUEST"] == "SYN"):
        settings.clients[msg["PAYLOAD"]]["ready"]=False
        response["PAYLOAD"] = "ACK"
    elif(msg["REQUEST"] == "SYNACK"):
        settings.clients[msg["PAYLOAD"]]["ready"]=True
        response["PAYLOAD"] = "SYNACK"
    elif(msg["REQUEST"] == "REGISTER"):
        settings.clients.setdefault(msg["SRC"], Client())
        settings.clients[msg["SRC"]].clientID = clientid
        if(settings.clients[msg["SRC"]].displayID == None):
            settings.clients[msg["SRC"]].displayID = "Default"
        settings.clients[msg["SRC"]].width = msg["PAYLOAD"]["width"]
        settings.clients[msg["SRC"]].height = msg["PAYLOAD"]["height"]
        settings.clients[msg["SRC"]].ip = session.manager[clientid].request.remote
        device = DeviceDetector(session.manager[clientid].request.headers['User-Agent']).parse()
        settings.clients[msg["SRC"]].osName = device.os_name()
        settings.clients[msg["SRC"]].osVersion = device.os_version()
        settings.clients[msg["SRC"]].engine = device.engine()
        settings.clients[msg["SRC"]].deviceBrand = device.device_brand()
        settings.clients[msg["SRC"]].deviceModel = device.device_model()
        settings.clients[msg["SRC"]].deviceType = device.device_type()
        response["DEST"]=msg["SRC"]
        response["PAYLOAD"]="SUCCESS"
    elif(msg["REQUEST"] == "UPDATECLIENT"):
        for settingKey in msg["PAYLOAD"]:
            setattr(settings.clients[msg["SRC"]],settingKey,msg["PAYLOAD"][settingKey])
        settings.clients[msg["SRC"]].clientID = clientid
        response["DEST"] = "ALL"
        response["PAYLOAD"] = settings.clients[msg["PAYLOAD"]]
    elif(msg["REQUEST"] == "CALIBRATE"):
        generateAruco()
        response["DEST"]="ALL"
    elif(msg["REQUEST"] == "READY"):
        response["DEST"]=msg["PAYLOAD"]
        response["PAYLOAD"]="SUCCESS"
    else:
        response["PAYLOAD"] = msg["PAYLOAD"]

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

    if( os.path.isfile(fileName)):
        data = Path(fileName).read_bytes()
        if(fileName.endswith('.html')):
            ct = 'text/html'
        elif(fileName.endswith('.js')):
            ct = 'application/javascript'
        elif(fileName.endswith('.css')):
            ct = 'text/css'
        elif(fileName.endswith('.jpg')):
            ct = 'image/jpeg'
        elif(fileName.endswith('.png')):
            ct = 'image/png'
        elif(fileName.endswith('.mp4')):
            ct = 'video/mp4'
        else:
            ct = 'application/octet-stream'
    
    return web.Response(body=data,content_type=ct)

async def image_handler(request):
    logging.debug("IMAGE_HANDLER")
    fileName = request.match_info.get('src')
    fileName = os.path.join('images',fileName)
    
    if( os.path.isfile(fileName)):
        data = Path(fileName).read_bytes()
        if(fileName.endswith('.ico')):
            ct = 'image/ico'
        elif(fileName.endswith('.jpg')):
            ct = 'image/jpeg'
        elif(fileName.endswith('.png')):
            ct = 'image/png'
        else:
            ct = 'application/octet-stream'
    
    return web.Response(body=data,content_type=ct)

async def media_handler(request):
    logging.debug("MEDIA_HANDLER")
    client = request.match_info.get('client')
    fileName = request.match_info.get('file')
    
    logging.debug("media/"+client+"/"+fileName)
    subdir = "images"
    
    if(fileName.endswith('.jpg')):
            ct = 'image/jpeg'
    elif(fileName.endswith('.png')):
        ct = 'image/png'
    elif(fileName.endswith('.mp4')):
        ct = 'video/mp4'
        subdir = "videos"
    else:
        ct = 'application/octet-stream'
    
    if( os.path.isfile("media/"+client+"/"+subdir+"/"+fileName)):
        data = Path("media/"+client+"/"+subdir+"/"+fileName).read_bytes()
    
    return web.Response(body=data,content_type=ct)

async def javascript_handler(request):
    logging.debug("JAVASCRIPT_HANDLER")
    fileName = request.match_info.get('src')
    logging.debug(fileName)
    data = '404 Not Found'
    if( os.path.isfile('js/' + fileName)):
        data = Path('js/' + fileName).read_bytes()
    return web.Response(body=data, content_type='text/javascript')

def generateAruco():
    # Load the predefined dictionary
    dictionary = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_6X6_50)
    id = 1
    for key in settings.clients.keys():
        # Generate the marker
        markerImage = np.zeros((300, 300), dtype=np.uint8)
        markerImage = cv.aruco.generateImageMarker(dictionary, id, 300, markerImage, 1)
        id = id + 1
        Path("media/" + key+"/images").mkdir(parents=True, exist_ok=True)
        cv.imwrite("media/" + key + "/images/aruco.png", markerImage)

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
    img = cv.GaussianBlur(img, (5, 5), 0)
    squares = []
    for gray in cv.split(img):
        for thrs in range(0, 255, 26):
            if thrs == 0:
                bin = cv.Canny(gray, 0, 50, apertureSize=5)
                bin = cv.dilate(bin, None)
            else:
                _retval, bin = cv.threshold(gray, thrs, 255, cv.THRESH_BINARY)
            contours, _hierarchy = cv.findContours(bin, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                cnt_len = cv.arcLength(cnt, True)
                cnt = cv.approxPolyDP(cnt, 0.02*cnt_len, True)
                if len(cnt) == 4 and cv.contourArea(cnt) > 1000 and cv.isContourConvex(cnt):
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
                cv.putText(image, str(markerID),(topLeft[0], topLeft[1] - 15), cv.FONT_HERSHEY_SIMPLEX,5, (255, 0, 0), 10)
                if(markerID >= len(settings.clients)):
                    break
                clientID = list(settings.clients.keys())[markerID]
                #Dictionary ordering is deterministic in python 3.7
                settings.clients[clientID].measuredCenter = [cX,cY]
                #find contours that enclose a marker
                for contour in contours:
                    perimeter = cv.arcLength(contour,True)
                    approximatedShape = cv.approxPolyDP(contour, 0.01 * perimeter, True)
                    result1 = cv.pointPolygonTest(contour, topLeft, False)
                    result2 = cv.pointPolygonTest(contour, bottomRight, False)
                    if(result1 == 1 and result2 ==1):
                        if(len(relevantContours) == 0):
                            relevantContours = approximatedShape
                        else:
                            relevantContours = np.concatenate((relevantContours,approximatedShape))
                        for i in range(len(approximatedShape)-1):
                            cv.line(image, approximatedShape[i][0], approximatedShape[i+1][0], (0, 255, 0), 4)
                        cv.line(image, approximatedShape[len(approximatedShape)-1][0], approximatedShape[0][0], (0, 255, 0), 4)
                        settings.clients[clientID].measuredPerimeter = approximatedShape
                        break

    x, y, w, h = cv.boundingRect(relevantContours)
    cX = int((x + (w / 2.0)))
    cY = int((y + (h / 2.0)))
    cv.circle(image, (cX, cY), 15, (0, 0, 255), -1)
    cv.rectangle(image, (x, y), (x+w, y+h), (0, 0, 255), 4)
    Path("media/displays/images").mkdir(parents=True, exist_ok=True)
    cv.imwrite("media/displays/images/calibration.png", image)
   
    return "media/displays/calibration.png","text/html"

    
if __name__ == '__main__':
    """Simple sockjs chat."""
    settings = Settings()
    try:
        if args.Verbose:
            logging.basicConfig(level=logging.DEBUG,format='%(asctime)s %(levelname)s %(message)s')

        if( os.path.isfile('settings.dat')):
            data = Path('settings.dat').read_text(encoding="utf-8")
            settings = jsonpickle.decode(data)
        else:
            settings.displays.setdefault("Default", Display())
        
        app = web.Application()
        app.router.add_route('GET', '/', index_handler)
        app.router.add_route('GET', '/{page:[^{}/][^sockjs/]+}', index_handler)
        app.router.add_route('GET', '/js/{src}', javascript_handler)
        app.router.add_route('GET', '/images/{src}', image_handler)
        app.router.add_route('GET', '/media/{client}/{file}', media_handler),
        app.router.add_route('POST', '/upload/{dest}', upload_handler),
        sockjs.add_endpoint(app, ws_handler, name='mosiacmesh', prefix='/sockjs/')

        web.run_app(app, port=args.Port or 3000)
    finally:
        jsonObj = jsonpickle.encode(settings, unpicklable=True)
        with Path("settings.dat").open("w", encoding ="utf-8") as f:
            f.write(jsonObj)

