"""Create a 'Scatter Demo' playlist: two plasma mesh items handing off via the
scatter transition (sprite=hop, wall). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def sc():
    return {"name": "scatter",
            "params": {"sprite": "hop", "scope": "wall", "count": 40,
                       "fillMs": 2500, "drainMs": 2500, "audioFade": True}}

ITEMS = [
    {"id": "sc-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": None, "endEffect": sc()},
    {"id": "sc-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": sc(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Scatter Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Scatter Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
