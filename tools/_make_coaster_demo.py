"""Create a 'Coaster Flip Demo' playlist: two plasma mesh items handing off via the
coasterflip v2 tumbling coaster (axis=horizontal, kraft, coaster back-face PNG,
5 flips). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def cf():
    return {"name": "coasterflip",
            "params": {"axis": "horizontal", "coaster": "kraft", "sprite": "coaster",
                       "flips": 5, "scope": "wall", "duration": 1800, "audioFade": True}}

ITEMS = [
    {"id": "cf-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 12, "backgroundColor": "#0a0a0a", "startEffect": None, "endEffect": cf()},
    {"id": "cf-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 12, "backgroundColor": "#0a0a0a", "startEffect": cf(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Coaster Flip Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Coaster Flip Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
