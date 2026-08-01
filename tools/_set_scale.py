"""Live-tune the mmvideo CMTimebase rate scale on a display group (no reload).
Sends SET_SCALE {value} -> server broadcasts to the group -> each client navs to
mmvideo://scale/<v>, intercepted by the mmvideo tweak. Usage:
    python tools/_set_scale.py "OEB Sign 1" 0.30
"""
import asyncio, json, sys, aiohttp
HOST = "127.0.0.1:3000"
async def main(did, value):
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    m = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SET_SCALE",
         "PAYLOAD": {"displayID": did, "value": float(value)}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(m)]))
            print("sent SET_SCALE displayID=%r value=%s" % (did, value))
            await asyncio.sleep(0.5)
asyncio.run(main(sys.argv[1], sys.argv[2]))
