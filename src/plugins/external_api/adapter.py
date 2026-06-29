# -*- coding: utf-8 -*-
"""
OpenSquad External Adapter (Multi-instance)

Runs multiple External API adapter instances in a single process.
Each instance has its own port, API key, and default agent.

Config in system_config.json:
  "external_api": {
    "instances": [
      {"name": "default", "port": 9700, "default_agent_id": "coder-001", "api_key": "key1"},
      {"name": "pm-api",  "port": 9701, "default_agent_id": "pm-001",    "api_key": "key2"}
    ]
  }

Each instance provides 4 communication modes:
  1. POST /api/chat          - Sync wait
  2. POST /api/chat/stream   - SSE streaming
  3. POST /api/chat/async    - Async submit + poll
  4. WS   /ws/chat           - Full-duplex WebSocket

Usage:
  python -m plugins.external_api.adapter
  scripts/start_external.bat
"""

import asyncio
import json
import logging
import time
import uuid
import sys
import os
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn

from plugins.external_api.config import (
    ExternalApiInstanceConfig,
    load_instance_configs,
    GATEWAY_WS_URL, GATEWAY_TOKEN,
    EXTERNAL_API_LOG_LEVEL,
)
from plugins.external_api.gateway_client import GatewayWSClient, AgentEvent, _ws_is_closed

# ── Logging ──
logging.basicConfig(
    level=getattr(logging, EXTERNAL_API_LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("external.adapter")


# ══════════════════════════════════════════════
#  Request/Response models
# ══════════════════════════════════════════════

class ChatRequest(BaseModel):
    agent_id: Optional[str] = Field(default=None, description="Target Agent ID (defaults to instance config)")
    message: str = Field(..., description="Message content")
    user_id: str = Field(default="external-user", description="Caller identity")
    images: List[str] = Field(default_factory=list, description="Image paths")
    timeout: Optional[int] = Field(default=None, description="Timeout seconds")
    channel: str = Field(default="external", description="Source channel")
    sender_name: str = Field(default="", description="Sender display name")
    chat_name: str = Field(default="", description="Chat/group name")
    source_chat_id: str = Field(default="", description="Source chat ID for reply targeting")


class ChatResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    thoughts: List[str] = Field(default_factory=list)
    tool_calls: List[dict] = Field(default_factory=list)
    duration_ms: int = 0


class AsyncSubmitResponse(BaseModel):
    status: str = "submitted"
    task_id: str = ""
    message: str = "Task submitted. Poll via GET /api/chat/result/{task_id}"


class AsyncResultResponse(BaseModel):
    status: str = "pending"
    task_id: str = ""
    result: Optional[ChatResponse] = None


# ══════════════════════════════════════════════
#  App factory: creates one FastAPI app per instance
# ══════════════════════════════════════════════

def create_app(inst_cfg: ExternalApiInstanceConfig) -> FastAPI:
    """Create a FastAPI app for one External API instance."""

    app = FastAPI(
        title=f"OpenSquad External Adapter [{inst_cfg.name}]",
        description=f"External API instance: {inst_cfg.name}. agent_id is required in all requests.",
        version="2.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:9555",
            "http://localhost:9530",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:9555",
            "http://127.0.0.1:9530",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Per-instance state ──
    gw_client: dict = {"client": None}  # mutable container for closure
    async_results: dict = {}
    inst_log = logging.getLogger(f"external.{inst_cfg.name}")

    # ── Auth ──
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def verify_api_key(api_key: str = Depends(api_key_header)):
        if not api_key or api_key != inst_cfg.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API Key")
        return api_key

    # ── Helper: resolve agent_id and timeout from request ──
    def resolve_request(req: ChatRequest) -> tuple:
        """Return (agent_id, timeout). Raises HTTPException if agent_id not specified."""
        agent_id = req.agent_id
        if not agent_id:
            raise HTTPException(
                status_code=400,
                detail="agent_id is required. Specify which agent should handle the request."
            )
        timeout = req.timeout or inst_cfg.request_timeout
        return agent_id, timeout

    # ── Startup / Shutdown ──

    @app.on_event("startup")
    async def on_startup():
        gw_client["client"] = GatewayWSClient(
            gateway_ws_url=GATEWAY_WS_URL,
            gateway_token=GATEWAY_TOKEN,
        )
        inst_log.info("=" * 56)
        inst_log.info(f"  Instance [{inst_cfg.name}] started")
        inst_log.info(f"  Listen:  http://{inst_cfg.host}:{inst_cfg.port}")
        inst_log.info(f"  Gateway: {GATEWAY_WS_URL}")
        if inst_cfg.auto_generated_key:
            inst_log.info(f"  API Key: {inst_cfg.api_key} (auto-generated)")
        else:
            inst_log.info(f"  API Key: {inst_cfg.api_key[:8]}...{inst_cfg.api_key[-4:]}")
        inst_log.info("=" * 56)

        asyncio.create_task(_cleanup_expired_results())

    @app.on_event("shutdown")
    async def on_shutdown():
        client = gw_client.get("client")
        if client:
            await client.close_all()
        inst_log.info(f"Instance [{inst_cfg.name}] stopped")

    async def _cleanup_expired_results():
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [
                tid for tid, r in async_results.items()
                if now - r.get("created_at", 0) > inst_cfg.async_result_ttl
            ]
            for tid in expired:
                del async_results[tid]

    # ── Mode 1: Sync ──

    @app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(verify_api_key)])
    async def chat_sync(req: ChatRequest):
        inst_log.info(f"[API] /api/chat from user={req.user_id}, agent={req.agent_id}, channel={req.channel}, msg={req.message[:60]}")
        agent_id, timeout = resolve_request(req)
        start = time.time()

        client = gw_client["client"]
        pending = await client.send_and_wait(
            agent_id=agent_id,
            message=req.message,
            user_id=req.user_id,
            images=req.images if req.images else None,
            timeout=timeout,
            channel=req.channel,
            sender_name=req.sender_name,
            chat_name=req.chat_name,
            source_chat_id=req.source_chat_id,
        )

        thoughts, tool_calls = _extract_events(pending.all_events)
        duration = int((time.time() - start) * 1000)

        errors = [ev for ev in pending.all_events if ev.event_type == "error"]
        if errors and not pending.final_text:
            error_msg = errors[0].content if errors[0].content else "Unknown error"
            inst_log.error(
                "[API] 502: agent=%s duration=%dms error=\"%s\" all_events=%s",
                agent_id, duration, error_msg,
                [(ev.event_type, str(ev.content)[:80]) for ev in pending.all_events],
            )
            # Build a more helpful error detail that includes the actual cause
            detail = f"Agent '{agent_id}': {error_msg}"
            raise HTTPException(status_code=502, detail=detail)

        return ChatResponse(
            status="ok",
            message=pending.final_text,
            thoughts=thoughts,
            tool_calls=tool_calls,
            duration_ms=duration,
        )

    # ── Mode 2: SSE Stream ──

    @app.post("/api/chat/stream", dependencies=[Depends(verify_api_key)])
    async def chat_stream(req: ChatRequest):
        agent_id, timeout = resolve_request(req)
        client = gw_client["client"]

        async def event_generator():
            async for event in client.send_and_stream(
                agent_id=agent_id,
                message=req.message,
                user_id=req.user_id,
                images=req.images if req.images else None,
                timeout=timeout,
                channel=req.channel,
                sender_name=req.sender_name,
                chat_name=req.chat_name,
                source_chat_id=req.source_chat_id,
            ):
                payload = json.dumps(event.to_dict(), ensure_ascii=False)
                yield f"data: {payload}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Mode 3: Async ──

    @app.post("/api/chat/async", response_model=AsyncSubmitResponse, dependencies=[Depends(verify_api_key)])
    async def chat_async(req: ChatRequest):
        task_id = str(uuid.uuid4())
        async_results[task_id] = {
            "status": "pending",
            "result": None,
            "created_at": time.time(),
        }
        asyncio.create_task(_run_async_task(task_id, req))
        return AsyncSubmitResponse(status="submitted", task_id=task_id)

    async def _run_async_task(task_id: str, req: ChatRequest):
        try:
            agent_id, timeout = resolve_request(req)
            start = time.time()
            client = gw_client["client"]
            pending = await client.send_and_wait(
                agent_id=agent_id,
                message=req.message,
                user_id=req.user_id,
                images=req.images if req.images else None,
                timeout=timeout,
                channel=req.channel,
                sender_name=req.sender_name,
                chat_name=req.chat_name,
                source_chat_id=req.source_chat_id,
            )
            thoughts, tool_calls = _extract_events(pending.all_events)
            duration = int((time.time() - start) * 1000)

            async_results[task_id] = {
                "status": "done",
                "result": ChatResponse(
                    status="ok",
                    message=pending.final_text,
                    thoughts=thoughts,
                    tool_calls=tool_calls,
                    duration_ms=duration,
                ),
                "created_at": async_results[task_id]["created_at"],
            }
        except Exception as e:
            inst_log.error(f"Async task {task_id} failed: {e}")
            async_results[task_id] = {
                "status": "error",
                "result": ChatResponse(status="error", message=str(e)),
                "created_at": async_results.get(task_id, {}).get("created_at", time.time()),
            }

    @app.get("/api/chat/result/{task_id}", response_model=AsyncResultResponse, dependencies=[Depends(verify_api_key)])
    async def chat_result(task_id: str):
        entry = async_results.get(task_id)
        if not entry:
            return AsyncResultResponse(status="not_found", task_id=task_id)
        return AsyncResultResponse(
            status=entry["status"],
            task_id=task_id,
            result=entry.get("result"),
        )

    # ── Mode 4: WebSocket ──

    @app.websocket("/ws/chat")
    async def ws_chat(
        websocket: WebSocket,
        agent_id: str = Query(default=None),
        api_key: str = Query(default=""),
    ):
        if api_key != inst_cfg.api_key:
            await websocket.close(code=4001, reason="Invalid API Key")
            return

        if not agent_id:
            await websocket.close(code=4000, reason="agent_id is required")
            return

        resolved_agent = agent_id

        await websocket.accept()
        client = gw_client["client"]

        connected = await client.ensure_connected(resolved_agent)
        if not connected:
            await websocket.send_json({
                "type": "error",
                "content": f"Agent '{resolved_agent}' is not available",
            })
            await websocket.close(code=4002, reason="Agent unavailable")
            return

        await websocket.send_json({
            "type": "connected",
            "agent_id": resolved_agent,
            "instance": inst_cfg.name,
            "message": f"Connected to agent '{resolved_agent}' via [{inst_cfg.name}]",
        })

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "content": "Invalid JSON"})
                    continue

                msg_type = data.get("type", "")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                if msg_type == "chat":
                    message = data.get("message", "").strip()
                    if not message:
                        await websocket.send_json({"type": "error", "content": "Empty message"})
                        continue

                    images = data.get("images", [])
                    ws_timeout = data.get("timeout", inst_cfg.request_timeout)
                    channel = data.get("channel", "external-ws")
                    # Allow per-message agent override
                    msg_agent = data.get("agent_id") or resolved_agent

                    async for event in client.send_and_stream(
                        agent_id=msg_agent,
                        message=message,
                        user_id=data.get("user_id", "external-ws-user"),
                        images=images if images else None,
                        timeout=ws_timeout,
                        channel=channel,
                    ):
                        try:
                            await websocket.send_json(event.to_dict())
                        except Exception:
                            break

                    try:
                        await websocket.send_json({"type": "turn_end"})
                    except Exception:
                        break
                else:
                    await websocket.send_json({
                        "type": "error",
                        "content": f"Unknown type: {msg_type}. Use 'chat' or 'ping'.",
                    })

        except WebSocketDisconnect:
            inst_log.info(f"[WS] Client disconnected (agent '{resolved_agent}')")
        except Exception as e:
            inst_log.error(f"[WS] Error: {e}")

    # ── Utility endpoints ──

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "service": "OpenSquad External Adapter",
            "instance": inst_cfg.name,
            "gateway": GATEWAY_WS_URL,
        }

    @app.get("/api/agents", dependencies=[Depends(verify_api_key)])
    async def list_agents():
        client = gw_client["client"]
        agents = []
        if client:
            for aid, ws in client._connections.items():
                agents.append({
                    "agent_id": aid,
                    "connected": not _ws_is_closed(ws) if ws else False,
                })
        return {"agents": agents}

    return app


# ══════════════════════════════════════════════
#  Helper
# ══════════════════════════════════════════════

def _extract_events(all_events: list) -> tuple:
    """Extract thoughts and tool_calls from event list."""
    thoughts = []
    tool_calls = []
    for ev in all_events:
        if ev.event_type == "thought" and ev.content:
            if isinstance(ev.content, dict):
                thoughts.append(ev.content.get("text", str(ev.content)))
            else:
                thoughts.append(str(ev.content))
        elif ev.event_type == "tool_call" and ev.content:
            if isinstance(ev.content, dict):
                tool_calls.append(ev.content)
            else:
                tool_calls.append({"raw": str(ev.content)})
    return thoughts, tool_calls


# ══════════════════════════════════════════════
#  Entry point: run all instances concurrently
# ══════════════════════════════════════════════

async def run_all_instances():
    """Start all configured External API instances."""
    instance_configs = load_instance_configs()

    if not instance_configs:
        logger.error("No enabled External API instances configured.")
        return

    print("=" * 60)
    print("  OpenSquad External Adapter (Multi-instance)")
    print("=" * 60)
    print(f"  Gateway:    {GATEWAY_WS_URL}")
    print(f"  Instances:  {len(instance_configs)}")
    for i, cfg in enumerate(instance_configs):
        key_display = cfg.api_key if cfg.auto_generated_key else f"{cfg.api_key[:8]}...{cfg.api_key[-4:]}"
        print(f"    [{i+1}] {cfg.name}: port={cfg.port}, key={key_display}")
    print("=" * 60)

    servers = []
    for inst_cfg in instance_configs:
        app = create_app(inst_cfg)
        config = uvicorn.Config(
            app,
            host=inst_cfg.host,
            port=inst_cfg.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        servers.append(server)

    # Run all servers concurrently
    await asyncio.gather(*(server.serve() for server in servers))


def main():
    try:
        asyncio.run(run_all_instances())
    except KeyboardInterrupt:
        logger.info("Interrupted.")


if __name__ == "__main__":
    main()
