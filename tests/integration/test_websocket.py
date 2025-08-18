#!/usr/bin/env python3
"""
Simple WebSocket client to test MosaicMesh discovery system
"""
import asyncio
import aiohttp
import json
import time
import uuid

async def test_client():
    session = aiohttp.ClientSession()
    
    try:
        # Connect to MosaicMesh WebSocket
        ws = await session.ws_connect('ws://localhost:8888/sockjs/websocket')
        print("-> Connected to MosaicMesh WebSocket")
        
        # Generate a unique client ID
        client_id = str(uuid.uuid4())[:12]
        
        # Send registration message
        register_msg = {
            "SRC": client_id,
            "DEST": "SRV", 
            "REQUEST": "REGISTER",
            "PAYLOAD": {
                "width": 1920,
                "height": 1080
            }
        }
        
        await ws.send_str(json.dumps(register_msg))
        print(f"-> Sent registration for client: {client_id}")
        
        # Listen for responses
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    response = json.loads(msg.data)
                    print(f"<- Received: {response}")
                    
                    # If registration successful, demonstrate we're alive
                    if response.get('REQUEST') == 'REGISTER' and response.get('PAYLOAD', {}).get('status') == 'SUCCESS':
                        print(f"SUCCESS: Auto-configured as: {response['PAYLOAD'].get('displayID')}")
                        print(f"CAPS: Capabilities detected: {response['PAYLOAD'].get('capabilities')}")
                        
                        # Keep connection alive for 30 seconds to test real-time updates
                        print("WAIT: Staying online for 30 seconds to test real-time status...")
                        await asyncio.sleep(30)
                        break
                        
                except json.JSONDecodeError:
                    print(f"ERROR: Failed to parse message: {msg.data}")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                print(f"ERROR: WebSocket error: {ws.exception()}")
                break
    
    except Exception as e:
        print(f"ERROR: Connection error: {e}")
    
    finally:
        await session.close()
        print("DONE: Disconnected from MosaicMesh")

if __name__ == "__main__":
    print("TESTING: MosaicMesh Discovery System")
    print("=" * 50)
    asyncio.run(test_client())