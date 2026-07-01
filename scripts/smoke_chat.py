"""End-to-end chat test: login → WS connect → send message → receive reply."""

import asyncio
import json
import time
import urllib.request

import websockets


async def test_chat():
    # 1. Login
    body = json.dumps({"email": "ss@ss", "password": "ssssss"}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:9555/api/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["access_token"]
    print(f"[chat] Login OK, token={token[:20]}...")

    # 2. Connect WS
    uri = f"ws://127.0.0.1:9555/ai-web/ws/coder-001?token={token}"
    print("[chat] Connecting WS...")
    async with websockets.connect(uri, max_size=2**20, ping_interval=20) as ws:
        print("[chat] WS connected!")

        # 3. Send message
        content = "你好，请回复一句话确认你能正常工作"
        msg = {"type": "chat", "content": content}
        await ws.send(json.dumps(msg))
        print(f"[chat] Sent: {content}")

        # 4. Receive responses
        deadline = time.time() + 30
        full_text = ""
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(raw)
                mtype = data.get("type", "")
                if mtype == "stream":
                    chunk = data.get("content", "")
                    full_text += chunk
                    print(chunk, end="", flush=True)
                elif mtype in ("done", "end"):
                    print("\n[chat] DONE")
                    break
                elif mtype == "error":
                    print(f"\n[chat] ERROR: {data}")
                    break
                elif mtype == "pong":
                    continue
                else:
                    preview = str(data)[:200]
                    print(f"\n[chat] [{mtype}] {preview}")
            except asyncio.TimeoutError:
                if full_text:
                    print(f"\n[chat] Stream ended (got {len(full_text)} chars)")
                    break
                continue

        if full_text:
            print(f"\n[chat] SUCCESS: Got response ({len(full_text)} chars)")
            return True
        else:
            print("[chat] FAIL: No text response received")
            return False


if __name__ == "__main__":
    result = asyncio.run(test_chat())
    exit(0 if result else 1)
