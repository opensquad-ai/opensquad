#!/usr/bin/env python3
"""Smoke test: login + WS chat with coder-001 on local Gateway."""

from __future__ import annotations

import asyncio
import json
import sys
import time

import requests
import websockets

GATEWAY = "http://127.0.0.1:9555"
AGENT_ID = "coder-001"
TEST_MESSAGE = "你好，请用一句话回复：网页测试通过"
TIMEOUT_S = 120


def get_token() -> str:
    import os

    email = os.environ.get("SMOKE_EMAIL", "smoke@test.local")
    password = os.environ.get("SMOKE_PASSWORD", "smoke-pass-123")
    status = requests.get(f"{GATEWAY}/api/auth/registration-status", timeout=10).json()
    if status.get("registration_open") and email == "smoke@test.local":
        reg = requests.post(
            f"{GATEWAY}/api/auth/register",
            json={"email": email, "password": password, "name": "Smoke Tester"},
            timeout=15,
        )
        if reg.status_code not in (200, 201, 400):
            reg.raise_for_status()
    login = requests.post(
        f"{GATEWAY}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    if login.status_code == 401:
        raise SystemExit(f"Login failed for {email!r}. Check SMOKE_EMAIL/SMOKE_PASSWORD.")
    login.raise_for_status()
    return login.json()["access_token"]


async def chat_once(token: str) -> str:
    ws_url = f"ws://127.0.0.1:9555/ai-web/ws/{AGENT_ID}?token={token}"
    final_chunks: list[str] = []
    async with websockets.connect(ws_url, open_timeout=15, proxy=None) as ws:
        # Wait for connected + optional history
        deadline = time.time() + 10
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("type") == "connected":
                break

        await ws.send(json.dumps({"type": "chat", "content": TEST_MESSAGE, "channel": "web"}))

        end = time.time() + TIMEOUT_S
        while time.time() < end:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(30, end - time.time()))
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "stream":
                chunk = msg.get("content") or msg.get("data") or ""
                if isinstance(chunk, dict):
                    chunk = chunk.get("text") or chunk.get("content") or ""
                if chunk:
                    final_chunks.append(str(chunk))
            elif mtype in ("message", "response"):
                content = msg.get("content") or msg.get("data") or ""
                if isinstance(content, dict):
                    content = content.get("text") or content.get("content") or ""
                if content and msg.get("role") != "user":
                    return str(content)
            elif mtype == "error":
                raise RuntimeError(msg.get("message") or str(msg))

    joined = "".join(final_chunks).strip()
    if joined.startswith("[Error:"):
        raise RuntimeError(joined)
    if joined:
        return joined
    raise RuntimeError("No assistant reply within timeout")


def main() -> int:
    import os

    global GATEWAY, AGENT_ID, TEST_MESSAGE
    GATEWAY = os.environ.get("SMOKE_GATEWAY", GATEWAY)
    AGENT_ID = os.environ.get("SMOKE_AGENT_ID", AGENT_ID)
    TEST_MESSAGE = os.environ.get("SMOKE_MESSAGE", TEST_MESSAGE)

    token = get_token()
    print(f"[smoke] token ok, chatting with {AGENT_ID}...")
    reply = asyncio.run(chat_once(token))
    print(f"[smoke] assistant reply: {reply[:500]}")
    if not reply.strip():
        return 1
    print("[smoke] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
