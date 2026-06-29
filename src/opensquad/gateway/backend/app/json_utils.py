# -*- coding: utf-8 -*-
"""
JSON serialization helpers - handle non-serializable objects such as datetime
"""
from datetime import datetime
from typing import Any
import json

def convert_to_json_serializable(obj: Any) -> Any:
    """
    Convert an object to a JSON-serializable format.
    - datetime -> millisecond timestamp (int)
    """
    if isinstance(obj, datetime):
        return int(obj.timestamp() * 1000)
    elif isinstance(obj, dict):
        return {k: convert_to_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_json_serializable(item) for item in obj]
    elif isinstance(obj, tuple):
        return [convert_to_json_serializable(item) for item in obj]
    return obj

def make_json_safe(data: Any) -> Any:
    """Ensure data is JSON-serializable"""
    try:
        # Try to serialize first
        json.dumps(data)
        return data
    except (TypeError, ValueError):
        # On error, convert and retry
        converted = convert_to_json_serializable(data)
        return converted
