"""
JSON serialization helpers - handle non-serializable objects such as datetime
"""

import json
from datetime import datetime
from typing import Any


def convert_to_json_serializable(obj: Any) -> Any:
    """
    Convert an object to a JSON-serializable format.
    - datetime -> millisecond timestamp (int)
    """
    if isinstance(obj, datetime):
        return int(obj.timestamp() * 1000)
    elif isinstance(obj, dict):
        return {k: convert_to_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list | tuple):
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
