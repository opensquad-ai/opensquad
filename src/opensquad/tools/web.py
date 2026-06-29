# -*- coding: utf-8 -*-
"""
Web/AI Chat communication tools for agent to interact with web interface users.
Uploads files to Gateway via HTTP, then Gateway pushes to frontend via WebSocket.
"""

import os
import json
from typing import List, Dict, Any, Union
import logging

from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)


def _list_runtime_agent_ids() -> List[str]:
    """List agent_ids under runtime_dir/agents for diagnostics and fallback."""
    ids: List[str] = []
    runtime_dir = os.environ.get('OPENSQUAD_RUNTIME_DIR', '') or syscfg.get_workspace()

    if not runtime_dir:
        return ids

    agents_dir = os.path.join(runtime_dir, 'agents')
    if not os.path.isdir(agents_dir):
        return ids

    for name in os.listdir(agents_dir):
        config_path = os.path.join(agents_dir, name, 'config.json')
        if not os.path.exists(config_path):
            continue
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                aid = cfg.get('agent_id', '')
                if aid:
                    ids.append(aid)
        except Exception:
            continue

    return sorted(set(ids))


def _get_agent_id() -> str:
    """Get agent ID from config or environment."""
    # Method 1: Environment variable
    agent_id = os.environ.get('OPENSQUAD_AGENT_ID', '')
    if agent_id:
        return agent_id

    # Method 2: Agent directory from environment
    agent_dir = os.environ.get('OPENSQUAD_AGENT_DIR', '')
    if agent_dir and os.path.exists(os.path.join(agent_dir, 'config.json')):
        with open(os.path.join(agent_dir, 'config.json'), 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            return cfg.get('agent_id', 'unknown')

    # Method 3: Detect from current working directory
    cwd = os.getcwd().replace('\\', '/')
    if 'agents/' in cwd:
        parts = cwd.split('agents/')
        if len(parts) > 1:
            agent_name = parts[1].split('/')[0]
            potential_dir = os.path.join(parts[0], 'agents', agent_name)
            config_path = os.path.join(potential_dir, 'config.json')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    return cfg.get('agent_id', 'unknown')

    # Method 4: Check if we're in an agent directory directly
    if os.path.exists('config.json'):
        with open('config.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            return cfg.get('agent_id', 'unknown')

    # Method 5: Runtime scan fallback
    # IMPORTANT: if multiple agent IDs exist, do NOT guess the first one.
    # Returning a guessed agent_id causes silent misrouting (chat connected to A,
    # send_file pushed to B). In that case return 'unknown' and require explicit id.
    agent_ids = _list_runtime_agent_ids()
    if len(agent_ids) == 1:
        return agent_ids[0]

    return 'unknown'


def _get_gateway_url() -> str:
    """Get Gateway HTTP URL from system config."""
    try:
        config_path = os.environ.get('OPENSQUAD_SYSTEM_CONFIG', '') or syscfg.workspace_config_path()
        
        if config_path and os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                hosts = cfg.get('hosts', {})
                ports = cfg.get('ports', {})
                host = hosts.get('gateway', '127.0.0.1')
                port = ports.get('gateway', 9555)
                return f"http://{host}:{port}"
    except Exception as e:
        logger.debug(f"[web] Could not load system config: {e}")

    return "http://127.0.0.1:9555"


def send_file(
    file_paths: Union[str, List[str]],
    message: str = "",
    agent_id: str = ""
) -> Dict[str, Any]:
    """
    Upload one or more files to the AI Web chat panel.
    Files are uploaded to Gateway, which then displays them in the chat.
    Supports images (displayed inline), videos, audio, and any other file type.
    
    Args:
        file_paths: List of absolute file paths or single path string. E.g., ["C:/data/chart.png"] or "C:/data/chart.png"
        message: Optional accompanying text message
        agent_id: Optional explicit target agent_id. Strongly recommended in multi-agent runtime.
    
    Returns:
        Dict with status and message
    
    Example:
        web.send_file(file_paths=["C:/workspace/chart.png"], message="分析结果")
        web.send_file(file_paths="C:/workspace/chart.png")
    """
    # Normalize file_paths to list
    if isinstance(file_paths, str):
        try:
            parsed = json.loads(file_paths)
            if isinstance(parsed, list):
                file_paths = parsed
            else:
                file_paths = [file_paths]
        except json.JSONDecodeError:
            file_paths = [file_paths]
    elif not isinstance(file_paths, list):
        file_paths = [str(file_paths)]
    
    if not file_paths:
        return {"status": "error", "message": "No file paths provided."}

    # Normalize file paths: trim whitespace/quotes and resolve relative paths.
    def _normalize_file_path(raw_path: Any) -> str:
        p = str(raw_path or "").strip()
        if len(p) >= 2 and ((p[0] == '"' and p[-1] == '"') or (p[0] == "'" and p[-1] == "'")):
            p = p[1:-1].strip()
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.abspath(os.path.join(os.getcwd(), p))
        return p

    normalized_paths = [_normalize_file_path(fp) for fp in file_paths]

    # Validate files exist
    missing = [fp for fp in normalized_paths if not os.path.exists(fp)]
    if missing:
        return {
            "status": "error",
            "message": f"Files not found: {missing}",
            "hint": "Use absolute path without extra quotes; relative paths are resolved from current working directory.",
            "normalized_paths": normalized_paths,
        }

    resolved_agent_id = (agent_id or '').strip() or _get_agent_id()
    gateway_url = _get_gateway_url()

    if resolved_agent_id in ('', 'unknown'):
        agent_ids = _list_runtime_agent_ids()
        return {
            "status": "error",
            "message": "Cannot determine target agent_id for web file push.",
            "hint": "Pass explicit agent_id in send_file_to_web(..., agent_id=...) or set OPENSQUAD_AGENT_ID.",
            "available_agent_ids": agent_ids,
        }
    
    # Upload files via HTTP to Gateway
    from contextlib import ExitStack
    with ExitStack() as stack:
        try:
            import requests

            url = f"{gateway_url}/api/ai-web/agent-push/upload-and-chat"

            # Prepare multipart files — ExitStack guarantees every opened fd is closed
            files_to_upload = []
            for fp in normalized_paths:
                filename = os.path.basename(fp)
                ext = os.path.splitext(filename)[1].lower()

                # Detect content type
                if ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']:
                    content_type = f"image/{ext[1:]}"
                elif ext in ['.svg']:
                    content_type = "image/svg+xml"
                elif ext in ['.mp4', '.webm', '.mov', '.avi', '.mkv']:
                    content_type = f"video/{ext[1:]}"
                elif ext in ['.mp3', '.wav', '.ogg', '.m4a', '.webm']:
                    content_type = f"audio/{ext[1:]}"
                else:
                    content_type = "application/octet-stream"

                fh = stack.enter_context(open(fp, "rb"))
                files_to_upload.append(("files", (filename, fh, content_type)))

            params = {
                "agent_id": resolved_agent_id,
                "message": message or "",
            }

            resp = requests.post(url, params=params, files=files_to_upload, timeout=30)
            resp.raise_for_status()

            result = resp.json()

            # File handles are automatically closed by ExitStack here

            uploaded_files = [os.path.basename(fp) for fp in normalized_paths]
            sent_to = 0
            if isinstance(result, dict):
                try:
                    sent_to = int(result.get("sent_to", 0) or 0)
                except Exception:
                    sent_to = 0

            # IMPORTANT: Gateway returns 200 even when no web client is connected.
            # Treat sent_to=0 as delivery failure to avoid "false success".
            if sent_to <= 0:
                return {
                    "status": "error",
                    "message": (
                        f"Files uploaded but not delivered to any connected web client "
                        f"(agent_id={resolved_agent_id}, sent_to=0)."
                    ),
                    "files": uploaded_files,
                    "agent_id": resolved_agent_id,
                    "gateway_response": result,
                    "hint": "Open AI Web chat for this exact agent_id, then resend.",
                }

            return {
                "status": "success",
                "message": f"Uploaded {len(file_paths)} file(s) to web chat (delivered to {sent_to} client(s))",
                "files": uploaded_files,
                "agent_id": resolved_agent_id,
                "gateway_response": result,
            }

        except requests.exceptions.ConnectionError:
            return {
                "status": "error",
                "message": f"Cannot connect to Gateway at {gateway_url}. Make sure Gateway is running.",
                "gateway_url": gateway_url,
            }
        except Exception as e:
            logger.error(f"[web.send_file] Error: {e}")
            return {"status": "error", "message": f"Failed to send files: {e}"}
        # ExitStack.__exit__ closes all opened file handles even on exception paths


def send_message(
    content: str,
    agent_id: str = ""
) -> Dict[str, Any]:
    """
    Send a text message to the AI Web chat panel.
    
    Args:
        content: Message text to send
        agent_id: Optional explicit target agent_id. Strongly recommended in multi-agent runtime.
    
    Returns:
        Dict with status and message
    
    Example:
        web.send_message(content="分析完成！")
    """
    try:
        import requests
        
        resolved_agent_id = (agent_id or '').strip() or _get_agent_id()
        gateway_url = _get_gateway_url()

        if resolved_agent_id in ('', 'unknown'):
            agent_ids = _list_runtime_agent_ids()
            return {
                "status": "error",
                "message": "Cannot determine target agent_id for web message push.",
                "hint": "Pass explicit agent_id in send_message_to_web(..., agent_id=...) or set OPENSQUAD_AGENT_ID.",
                "available_agent_ids": agent_ids,
            }

        url = f"{gateway_url}/api/ai-web/agent-push/chat"
        payload = {
            "agent_id": resolved_agent_id,
            "message": content,
            "files": [],
        }

        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()

        result = resp.json() if resp.content else {}
        sent_to = 0
        if isinstance(result, dict):
            try:
                sent_to = int(result.get("sent_to", 0) or 0)
            except Exception:
                sent_to = 0

        if sent_to <= 0:
            return {
                "status": "error",
                "message": (
                    f"Message accepted by Gateway but not delivered to any connected web client "
                    f"(agent_id={resolved_agent_id}, sent_to=0)."
                ),
                "agent_id": resolved_agent_id,
                "gateway_response": result,
                "hint": "Open AI Web chat for this exact agent_id, then resend.",
            }

        return {
            "status": "success",
            "message": f"Message sent to web chat (delivered to {sent_to} client(s))",
            "agent_id": resolved_agent_id,
            "gateway_response": result,
        }
        
    except requests.exceptions.ConnectionError:
        return {
            "status": "error",
            "message": f"Cannot connect to Gateway at {gateway_url}. Make sure Gateway is running.",
            "gateway_url": gateway_url,
        }
    except Exception as e:
        logger.error(f"[web.send_message] Error: {e}")
        return {"status": "error", "message": f"Failed to send message: {e}"}
