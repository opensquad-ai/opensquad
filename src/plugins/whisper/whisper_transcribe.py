# -*- coding: utf-8 -*-
"""
Whisper Speech-to-Text Tool
Allows the Agent to actively call the Whisper service to transcribe audio files.
"""
import os
import sys
import requests
import logging
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from opensquad.system_config import syscfg

logger = logging.getLogger("tool_whisper")


def _get_service_url() -> str:
    """
    Dynamically resolve the Whisper service URL.
    Port resolution priority (high to low):
    1. data/plugins/whisper/config.json → port (Web UI runtime config)
    2. system_config.json ports.whisper (deployment-level override)
    3. Default value 5001
    """
    import json as _json
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    _cfg_path = os.path.join(_root, "data", "plugins", "whisper", "config.json")
    if os.path.isfile(_cfg_path):
        try:
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _cfg = _json.load(_f)
            if "port" in _cfg:
                return f"http://localhost:{int(_cfg['port'])}"
        except Exception:
            pass
    return syscfg.whisper_url()


def transcribe_audio_file(audio_path: str, language: str = "zh") -> Dict[str, Any]:
    """
    Transcribe an audio file using the Whisper service.
    
    Args:
        audio_path: Absolute path to the audio file (supports mp3, wav, webm, m4a, etc.)
        language: Language code, default 'zh' (Chinese); use 'en' for English, etc.
        
    Returns:
        {"success": True, "text": "transcription result", "duration": 1.23}
        
    Example:
        result = transcribe_audio_file("C:/uploads/voice.wav")
        if result["success"]:
            print(f"Transcription: {result['text']}")
    """
    if not os.path.exists(audio_path):
        return {
            "success": False,
            "error": f"File not found: {audio_path}"
        }
    
    try:
        # Prefer path-based approach (same-machine deployment, no upload needed)
        logger.info(f"[Whisper] Starting transcription: {audio_path}")
        
        response = requests.post(
            f"{_get_service_url()}/transcribe/url",
            json={
                "path": audio_path,
                "language": language
            },
            timeout=300
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                logger.info(f"[Whisper] Transcription successful, took {result.get('duration', 0)} seconds")
                return {
                    "success": True,
                    "text": result["text"],
                    "language": result.get("language"),
                    "duration": result.get("duration"),
                    "file": os.path.basename(audio_path)
                }
        
        # If path-based approach fails, try uploading
        logger.warning("[Whisper] Path-based approach failed, attempting file upload")
        with open(audio_path, 'rb') as f:
            files = {'file': f}
            data = {'language': language}
            response = requests.post(
                f"{_get_service_url()}/transcribe",
                files=files,
                data=data,
                timeout=300
            )
        
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                return {
                    "success": True,
                    "text": result["text"],
                    "duration": result.get("duration")
                }
        
        return {
            "success": False,
            "error": f"Whisper service returned an error (HTTP {response.status_code}): {response.text}"
        }
        
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": f"Cannot connect to Whisper service ({_get_service_url()}), please ensure the service is running"
        }
    except Exception as e:
        logger.error(f"[Whisper] Transcription failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def check_whisper_service() -> Dict[str, Any]:
    """
    Check the Whisper service status.
    
    Returns:
        {"available": True, "model_loaded": True, "uptime": 123.45, ...}
    """
    try:
        response = requests.get(f"{_get_service_url()}/health", timeout=5)
        if response.status_code == 200:
            health = response.json()
            
            # Get detailed status
            status_response = requests.get(f"{_get_service_url()}/status", timeout=5)
            stats = status_response.json() if status_response.status_code == 200 else {}
            
            return {
                "available": True,
                "status": health.get("status"),
                "model_loaded": health.get("model_loaded", False),
                "uptime": health.get("uptime", 0),
                "total_requests": stats.get("total_requests", 0),
                "success_rate": f"{stats.get('successful_requests', 0)}/{stats.get('total_requests', 0)}"
            }
        else:
            return {
                "available": False,
                "error": f"Service returned error status: {response.status_code}"
            }
    except Exception as e:
        return {
            "available": False,
            "error": f"Cannot connect: {str(e)}"
        }


# Tool registration metadata
TOOLS = {
    "transcribe_audio_file": {
        "function": transcribe_audio_file,
        "description": "Transcribe an audio file to text using the Whisper model, supporting Chinese and English speech recognition",
        "parameters": {
            "audio_path": {
                "type": "string",
                "description": "Absolute path to the audio file",
                "required": True
            },
            "language": {
                "type": "string",
                "description": "Language code (zh=Chinese, en=English), default zh",
                "required": False,
                "default": "zh"
            }
        }
    },
    "check_whisper_service": {
        "function": check_whisper_service,
        "description": "Check the status and availability of the Whisper transcription service",
        "parameters": {}
    }
}
