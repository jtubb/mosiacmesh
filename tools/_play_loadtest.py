"""One-shot SockJS admin client: ASSIGN_PLAYLIST(Demo) + PLAY a group, to load-test the
mmwsc native-ws transplant under the coordinated PREPARE/SYN/GO message burst. Gitignored (_).

Usage: python tools/_play_loadtest.py "OEB Sign 1" Demo [play|stop]
"""
import aiohttp, asyncio, json, random, string, sys

BASE_WS = "ws://192.168.1.60:3000"
GROUP = sys.argv[1] if len(sys.argv) > 1 else "OEB Sign 1"
PLAYLIST = sys.argv[2] if len(sys.argv) > 2 else "Demo"
ACTION = sys.argv[3] if len(sys.argv) > 3 else "play"


def genmsg(dest, req, payload):
    return json.dumps({"SRC": "loadtest", "DEST": dest, "REQUEST": req, "PAYLOAD": payload})


async def send(ws, dest, req, payload):
    # SockJS websocket frame: a JSON array of message strings
    await ws.send_str(json.dumps([genmsg(dest, req, payload)]))
    print(f"  -> {req} {payload}")


async def drain(ws, secs, label):
    """read + print server frames for a window"""
    end = asyncio.get_event_loop().time() + secs
    while asyncio.get_event_loop().time() < end:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=max(0.1, end - asyncio.get_event_loop().time()))
        except asyncio.TimeoutError:
            break
        if msg.type == aiohttp.WSMsgType.TEXT:
            d = msg.data
            if d and d[0] == "a":  # a[...] messages array
                for m in json.loads(d[1:]):
                    try:
                        mm = json.loads(m)
                        print(f"  <- {label}: REQ={mm.get('REQUEST')} PAYLOAD={str(mm.get('PAYLOAD'))[:80]}")
                    except Exception:
                        print(f"  <- {label}: {m[:80]}")
            elif d and d[0] == "o":
                print("  <- open")
            elif d and d[0] == "c":
                print(f"  <- close {d}")
                return
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            return


async def main():
    srv = str(random.randint(100, 999))
    sess = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    url = f"{BASE_WS}/sockjs/{srv}/{sess}/websocket"
    print(f"connecting {url}\n  group='{GROUP}' playlist='{PLAYLIST}' action={ACTION}")
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url, heartbeat=None) as ws:
            await drain(ws, 1.0, "open")
            if ACTION == "stop":
                await send(ws, "SRV", "STOP", {"displayID": GROUP})
                await drain(ws, 2.0, "stop")
                return
            if ACTION == "reload":
                await send(ws, "SRV", "RELOAD", {"displayID": GROUP})
                await drain(ws, 2.0, "reload")
                return
            await send(ws, "SRV", "ASSIGN_PLAYLIST", {"displayID": GROUP, "name": PLAYLIST})
            await drain(ws, 2.5, "assign")
            await send(ws, "SRV", "PLAY", {"displayID": GROUP})
            await drain(ws, 4.0, "play")
    print("done.")


asyncio.run(main())
