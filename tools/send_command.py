"""One-shot admin command sender over SockJS (raw websocket transport).

Sends a REQUEST to the server as if from the admin console. Useful to drive the
wall when the admin page itself can't send (e.g. its socket was nulled by the
orphaned-socket bug before the fixed client JS is reloaded), or for scripting.

Examples:
    python tools/send_command.py RELOAD                 # reload every client + admin
    python tools/send_command.py RELOAD "Desktop"       # reload only the Desktop group
    python tools/send_command.py STOP "Desktop" "Test Group"
    python tools/send_command.py PLAY "Test Group"

For RELOAD/STOP/PLAY the positional args after the REQUEST are display group IDs;
RELOAD with no group reloads all connected clients (DEST="ALL").
"""
import asyncio, json, sys, aiohttp

HOST = "127.0.0.1:3000"


async def main(request, groups):
    url = f"http://{HOST}/sockjs/000/claudecmd/websocket"
    async with aiohttp.ClientSession() as sess:
        async with sess.ws_connect(url) as ws:
            if request == "RELOAD" and not groups:
                # global reload: no displayID -> server broadcasts DEST="ALL"
                payloads = [{"SRC": "claude-admin", "DEST": "SRV",
                             "REQUEST": "RELOAD", "PAYLOAD": "NONE"}]
            else:
                payloads = [{"SRC": "claude-admin", "DEST": "SRV",
                             "REQUEST": request, "PAYLOAD": {"displayID": g}}
                            for g in groups]
            for p in payloads:
                await ws.send_str(json.dumps([json.dumps(p)]))
                print(f"sent {request} -> {p['PAYLOAD']}")
                await asyncio.sleep(0.3)
            try:
                for _ in range(5):
                    msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                    print("recv:", str(msg.data)[:140])
            except asyncio.TimeoutError:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
