# -*- coding: utf-8 -*-
"""
External Adapter Test Client

Usage:
  python external/test_client.py --mode sync   --message "Hello"
  python external/test_client.py --mode stream --message "Hello"
  python external/test_client.py --mode async  --message "Hello"
  python external/test_client.py --mode ws     --message "Hello"

  Interactive WebSocket mode:
  python external/test_client.py --mode ws --interactive
"""

import argparse
import asyncio
import json
import sys
import time

# Requires: pip install httpx websockets
try:
    import httpx
except ImportError:
    print("Please install httpx first: pip install httpx")
    sys.exit(1)

try:
    import websockets
except ImportError:
    websockets = None


BASE_URL = "http://127.0.0.1:9700"
WS_URL = "ws://127.0.0.1:9700"
API_KEY = ""  # The key is printed to the console when the adapter starts; paste it here


def set_api_key(key: str):
    global API_KEY
    API_KEY = key


def headers():
    return {"X-API-Key": API_KEY}


# ── Mode 1: Sync ──

def test_sync(message: str, agent_id: str = "default-001"):
    """Sync mode test"""
    print(f"\n{'='*50}")
    print(f"  Sync mode - POST /api/chat")
    print(f"{'='*50}")
    print(f"  Sending: {message}")
    print(f"  Agent: {agent_id}")
    print(f"  Waiting for reply...\n")

    start = time.time()
    with httpx.Client(timeout=180) as client:
        resp = client.post(
            f"{BASE_URL}/api/chat",
            headers=headers(),
            json={
                "agent_id": agent_id,
                "message": message,
            },
        )

    elapsed = time.time() - start
    print(f"  HTTP {resp.status_code} ({elapsed:.1f}s)")

    if resp.status_code == 200:
        data = resp.json()
        print(f"\n  Reply: {data['message']}")
        if data.get("thoughts"):
            print(f"  Thoughts: {len(data['thoughts'])} steps")
        if data.get("tool_calls"):
            print(f"  Tools: {len(data['tool_calls'])} calls")
        print(f"  Duration: {data['duration_ms']}ms")
    else:
        print(f"  Error: {resp.text}")


# ── Mode 2: SSE Stream ──

def test_stream(message: str, agent_id: str = "default-001"):
    """SSE streaming mode test"""
    print(f"\n{'='*50}")
    print(f"  SSE streaming mode - POST /api/chat/stream")
    print(f"{'='*50}")
    print(f"  Sending: {message}")
    print(f"  Agent: {agent_id}")
    print()

    with httpx.Client(timeout=180) as client:
        with client.stream(
            "POST",
            f"{BASE_URL}/api/chat/stream",
            headers=headers(),
            json={
                "agent_id": agent_id,
                "message": message,
            },
        ) as resp:
            print(f"  HTTP {resp.status_code}")
            print(f"  --- Event stream ---")
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    try:
                        event = json.loads(payload)
                        etype = event.get("type", "?")
                        content = event.get("content", "")

                        if etype == "stream":
                            print(f"  [stream] {content}", end="", flush=True)
                        elif etype == "message":
                            print(f"\n  [done] {str(content)[:200]}")
                        elif etype == "thought":
                            print(f"  [thought] {str(content)[:100]}")
                        elif etype == "tool_call":
                            print(f"  [tool] {str(content)[:100]}")
                        elif etype == "tool_result":
                            print(f"  [result] {str(content)[:100]}")
                        elif etype == "done":
                            print(f"  --- End ---")
                        elif etype == "error":
                            print(f"  [error] {content}")
                        else:
                            print(f"  [{etype}] {str(content)[:100]}")
                    except json.JSONDecodeError:
                        print(f"  [raw] {payload}")


# ── Mode 3: Async ──

def test_async(message: str, agent_id: str = "default-001"):
    """Async mode test"""
    print(f"\n{'='*50}")
    print(f"  Async mode - POST /api/chat/async")
    print(f"{'='*50}")
    print(f"  Sending: {message}")
    print(f"  Agent: {agent_id}")
    print()

    with httpx.Client(timeout=180) as client:
        # Submit
        resp = client.post(
            f"{BASE_URL}/api/chat/async",
            headers=headers(),
            json={
                "agent_id": agent_id,
                "message": message,
            },
        )
        data = resp.json()
        task_id = data.get("task_id", "")
        print(f"  Submitted: task_id = {task_id}")

        # Poll
        print(f"  Polling", end="", flush=True)
        for _ in range(60):
            time.sleep(2)
            print(".", end="", flush=True)
            resp = client.get(
                f"{BASE_URL}/api/chat/result/{task_id}",
                headers=headers(),
            )
            result = resp.json()
            if result["status"] == "done":
                print()
                r = result["result"]
                print(f"\n  Reply: {r['message']}")
                print(f"  Duration: {r['duration_ms']}ms")
                return
            elif result["status"] == "error":
                print()
                print(f"\n  Error: {result['result']['message']}")
                return

        print("\n  Timed out!")


# ── Mode 4: WebSocket ──

async def test_ws(message: str, agent_id: str = "default-001", interactive: bool = False):
    """WebSocket full-duplex mode test"""
    if websockets is None:
        print("Please install websockets first: pip install websockets")
        return

    print(f"\n{'='*50}")
    print(f"  WebSocket mode - /ws/chat")
    print(f"{'='*50}")

    url = f"{WS_URL}/ws/chat?agent_id={agent_id}&api_key={API_KEY}"
    print(f"  Connecting: {url}")

    async with websockets.connect(url) as ws:
        # Read connection confirmation
        raw = await ws.recv()
        data = json.loads(raw)
        print(f"  {data}")

        if data.get("type") == "error":
            return

        if interactive:
            print(f"\n  Interactive mode - type a message, type 'quit' to exit")
            while True:
                try:
                    user_input = input("\n  You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if user_input.lower() in ("quit", "exit", "q"):
                    break
                if not user_input:
                    continue

                await ws.send(json.dumps({"type": "chat", "message": user_input}))

                # Receive event stream
                while True:
                    raw = await ws.recv()
                    event = json.loads(raw)
                    etype = event.get("type", "?")
                    content = event.get("content", "")

                    if etype == "stream":
                        print(f"  ", end="", flush=True)
                        print(content, end="", flush=True)
                    elif etype == "message":
                        print(f"\n  Agent: {str(content)[:500]}")
                    elif etype == "thought":
                        print(f"  [thought] {str(content)[:100]}")
                    elif etype == "tool_call":
                        print(f"  [tool] {str(content)[:100]}")
                    elif etype == "turn_end":
                        break
                    elif etype == "error":
                        print(f"  [error] {content}")
                        break
                    else:
                        print(f"  [{etype}] {str(content)[:100]}")
        else:
            # Single send
            print(f"  Sending: {message}")
            await ws.send(json.dumps({"type": "chat", "message": message}))

            while True:
                raw = await ws.recv()
                event = json.loads(raw)
                etype = event.get("type", "?")
                content = event.get("content", "")

                if etype == "stream":
                    print(content, end="", flush=True)
                elif etype == "message":
                    print(f"\n  [done] {str(content)[:500]}")
                elif etype == "thought":
                    print(f"  [thought] {str(content)[:100]}")
                elif etype == "tool_call":
                    print(f"  [tool] {str(content)[:100]}")
                elif etype == "turn_end":
                    print(f"\n  --- End ---")
                    break
                elif etype == "error":
                    print(f"  [error] {content}")
                    break
                else:
                    print(f"  [{etype}] {str(content)[:100]}")

    print(f"\n  WebSocket disconnected")


# ── Entry point ──

def main():
    parser = argparse.ArgumentParser(description="External Adapter Test Client")
    parser.add_argument("--mode", choices=["sync", "stream", "async", "ws"], default="sync",
                        help="Communication mode")
    parser.add_argument("--message", "-m", default="Hello, please briefly introduce yourself",
                        help="Message to send")
    parser.add_argument("--agent", default="default-001",
                        help="Agent ID")
    parser.add_argument("--key", default="",
                        help="API Key")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="WebSocket interactive mode")

    args = parser.parse_args()

    if args.key:
        set_api_key(args.key)
    elif not API_KEY:
        key = input("Please enter API Key (printed to console when adapter starts): ").strip()
        set_api_key(key)

    if args.mode == "sync":
        test_sync(args.message, args.agent)
    elif args.mode == "stream":
        test_stream(args.message, args.agent)
    elif args.mode == "async":
        test_async(args.message, args.agent)
    elif args.mode == "ws":
        asyncio.run(test_ws(args.message, args.agent, args.interactive))


if __name__ == "__main__":
    main()
