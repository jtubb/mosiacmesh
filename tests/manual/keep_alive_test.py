#!/usr/bin/env python3
"""
Keep-alive client to test real-time green status indicators
"""
import asyncio
import aiohttp
import json
import uuid

async def stay_online():
    session = aiohttp.ClientSession()
    client_id = f"test_{uuid.uuid4().hex[:8]}"
    
    try:
        # Connect via raw WebSocket (not SockJS)
        print(f"Testing live connection for: {client_id}")
        print("This will show as ACTIVE (green) in the discovery dashboard")
        print("Open http://localhost:8888/discovery in browser to see real-time status")
        print("Press Ctrl+C to disconnect")
        
        # Use SockJS format
        ws = await session.ws_connect('ws://localhost:8888/sockjs/websocket')
        
        # Send registration
        register_msg = json.dumps({
            "SRC": client_id,
            "DEST": "SRV", 
            "REQUEST": "REGISTER",
            "PAYLOAD": {"width": 1600, "height": 900}
        })
        
        await ws.send_str(register_msg)
        print(f"-> Registered client: {client_id}")
        
        # Send periodic heartbeats to stay active
        heartbeat_count = 0
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    response = json.loads(msg.data)
                    if 'NEW_DEVICE_CONFIGURED' in response.get('REQUEST', ''):
                        print(f"<- Auto-configured successfully!")
                    elif 'REGISTER' in response.get('REQUEST', ''):
                        print(f"<- Registration response: {response.get('PAYLOAD', {}).get('status', 'unknown')}")
                        
                except json.JSONDecodeError:
                    pass
            
            # Send heartbeat every 10 seconds
            heartbeat_count += 1
            if heartbeat_count % 50 == 0:  # Roughly every 10 seconds
                heartbeat_msg = json.dumps({
                    "SRC": client_id,
                    "DEST": "SRV",
                    "REQUEST": "READY",
                    "PAYLOAD": "alive"
                })
                await ws.send_str(heartbeat_msg)
                print(f"-> Heartbeat sent (staying active)")
                
            await asyncio.sleep(0.2)  # Small delay
        
    except KeyboardInterrupt:
        print(f"\\n-> Disconnecting {client_id}...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await session.close()
        print("-> Disconnected - should show as OFFLINE in dashboard")

if __name__ == "__main__":
    asyncio.run(stay_online())