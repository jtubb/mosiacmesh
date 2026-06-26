"""Create a 'Wheat Part Demo' playlist: two plasma mesh items handing off via the
wheatpart transition (golden wheat-texture backdrop + sparse foreground ear-stalks,
50% center dwell). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def wp():
    return {"name": "wheatpart",
            "params": {"tint": "golden", "sprite": "wheatfield", "density": 30,
                       "hold": 0.5, "scope": "wall", "duration": 4000, "audioFade": True}}

ITEMS = [
    {"id": "wp-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 12, "backgroundColor": "#0a0a0a", "startEffect": None, "endEffect": wp()},
    {"id": "wp-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 12, "backgroundColor": "#0a0a0a", "startEffect": wp(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Wheat Part Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Wheat Part Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
