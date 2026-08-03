#!/usr/bin/env python3
"""Timed first-round chat: login -> WS connect -> send -> measure TTFT / full turn."""

import asyncio
import json
import os
import sys
import time

import requests
import websockets

GATEWAY = os.environ.get("GW", "http://127.0.0.1:9555")
AGENT_ID = os.environ.get("AGENT_ID", "agent301-001")
MESSAGE = os.environ.get("MSG", "你好，请用一句话回复：启动联通性测试通过")
TIMEOUT_S = 120


def now() -> float:
    return time.perf_counter()


def login() -> str:
    email = os.environ.get("SMOKE_EMAIL", "smoke@test.local")
    password = os.environ.get("SMOKE_PASSWORD", "smoke-pass-123")
    t0 = now()
    status = requests.get(f"{GATEWAY}/api/auth/registration-status", timeout=10).json()
    now() - t0
    t0 = now()
    if status.get("registration_open") and email == "smoke@test.local":
        r = requests.post(
            f"{GATEWAY}/api/auth/register",
            json={"email": email, "password": password, "name": "Smoke Tester"},
            timeout=15,
        )
        t_reg = now() - t0
        print(f"[timing] register: {t_reg * 1000:.0f} ms (status={r.status_code})")
    else:
        t_reg = now() - t0
        print(f"[timing] register skipped (open={status.get('registration_open')}) {t_reg * 1000:.0f} ms")
    t0 = now()
    login = requests.post(f"{GATEWAY}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    t_login = now() - t0
    print(f"[timing] login: {t_login * 1000:.0f} ms (status={login.status_code})")
    login.raise_for_status()
    return login.json()["access_token"]


async def chat(token: str):
    ws_url = f"ws://127.0.0.1:9555/ai-web/ws/{AGENT_ID}?token={token}"
    t0 = now()
    async with websockets.connect(ws_url, open_timeout=15, proxy=None) as ws:
        t_connect = now() - t0
        print(f"[timing] ws connect (TCP+TLS+handshake): {t_connect * 1000:.0f} ms")
        # wait for connected handshake
        t0 = now()
        while now() - t0 < 10:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("type") == "connected":
                t_handshake = now() - t0
                print(f"[timing] 'connected' handshake: {t_handshake * 1000:.0f} ms after connect")
                break
        # send chat
        t_send = now()
        await ws.send(json.dumps({"type": "chat", "content": MESSAGE, "channel": "web"}))
        print(f"[timing] chat sent at +{0:.0f} ms")
        first_chunk_t = None
        final_t = None
        final_content = ""
        chunks = 0
        end = now() + TIMEOUT_S
        while now() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(30, end - now()))
            except asyncio.TimeoutError:
                print("(waiting...)")
                continue
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "stream":
                if first_chunk_t is None:
                    first_chunk_t = now()
                    print(f"[timing] FIRST STREAM CHUNK (TTFT): {(first_chunk_t - t_send) * 1000:.0f} ms")
                chunk = msg.get("content") or msg.get("data") or ""
                if isinstance(chunk, dict):
                    chunk = chunk.get("text") or chunk.get("content") or ""
                if chunk:
                    chunks += 1
            elif mtype in ("message", "response"):
                content = msg.get("content") or msg.get("data") or ""
                if isinstance(content, dict):
                    content = content.get("text") or content.get("content") or ""
                if content and msg.get("role") != "user":
                    final_content = str(content)
                    final_t = now()
                    print(f"[timing] FINAL MESSAGE: {(final_t - t_send) * 1000:.0f} ms after send (chunks={chunks})")
                    break
            elif mtype == "error":
                print(f"[error] {msg}")
                break
        if first_chunk_t is None:
            print("[timing] NO STREAM CHUNKS RECEIVED")
        if final_content:
            print(f"\n=== AGENT REPLY ({len(final_content)} chars) ===\n{final_content[:500]}")
        return final_t is not None


if __name__ == "__main__":
    tok = login()
    ok = asyncio.run(chat(tok))
    sys.exit(0 if ok else 1)
