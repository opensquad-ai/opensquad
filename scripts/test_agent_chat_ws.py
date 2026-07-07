"""Send a test chat message to agent301 via Gateway WebSocket."""
import asyncio
import json
import os
import sys

try:
    import websockets
except ImportError:
    print("websockets not installed")
    sys.exit(1)

TOKEN = os.environ.get("GW_TOKEN", "")
AGENT_ID = "agent301-001"
USER_ID = "217223"
WS_URL = f"ws://127.0.0.1:9555/ai-web/ws/{AGENT_ID}?token={TOKEN}"


async def main():
    if not TOKEN:
        print("Set GW_TOKEN env var")
        sys.exit(1)

    async with websockets.connect(WS_URL, open_timeout=15) as ws:
        # Wait for connected handshake
        for _ in range(5):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            print(f"recv: {msg.get('type', msg)}")
            if msg.get("type") == "connected":
                break

        chat_msg = {
            "type": "chat",
            "content": "请用 system.run_session_job 执行 cd 命令，只回复当前工作目录路径",
            "channel": "web",
        }
        await ws.send(json.dumps(chat_msg))
        print("sent chat message")

        # Collect responses for up to 90s
        deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
            except TimeoutError:
                print("(waiting...)")
                continue
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype in ("message", "thought", "stream", "error"):
                preview = str(msg.get("content", msg.get("message", "")))[:300]
                print(f"[{mtype}] {preview}")
            if mtype == "message" and msg.get("role") == "assistant":
                content = str(msg.get("content", ""))
                if "game2" in content.lower() or "desktop" in content.lower() or "opensquad" in content.lower():
                    print(f"\nCWD HINT IN RESPONSE: {content[:500]}")
                if msg.get("final", True):
                    break


if __name__ == "__main__":
    asyncio.run(main())
