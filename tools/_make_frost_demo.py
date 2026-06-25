"""Create a 'Frost Creep Demo' playlist: two plasma mesh items handing off via the
frostcreep transition (tint=frost, wall). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def fc():
    return {"name": "frostcreep",
            "params": {"tint": "frost", "scope": "wall", "duration": 2200, "audioFade": True}}

ITEMS = [
    {"id": "fc-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#06121a", "startEffect": None, "endEffect": fc()},
    {"id": "fc-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#06121a", "startEffect": fc(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Frost Creep Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Frost Creep Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
