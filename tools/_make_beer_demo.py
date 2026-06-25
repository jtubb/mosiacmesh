"""Create a 'Beer Demo' playlist: two plasma mesh items handing off via the
beerfill transition (fill out of A, drain into B). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def bf():
    return {"name": "beerfill",
            "params": {"beerType": "pale", "scope": "wall",
                       "duration": 2500, "audioFade": True}}

ITEMS = [
    {"id": "beer-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": None, "endEffect": bf()},
    {"id": "beer-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": bf(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Beer Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Beer Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
