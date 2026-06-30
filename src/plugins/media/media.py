"""
Media Tools v1.0
Provides audio format conversion functionality (e.g. webm -> wav).
"""

import os
from typing import Any

from opensquad.utils.media_util import convert_audio


def convert_audio_format(input_path: str, target_format: str = "wav") -> dict[str, Any]:
    """
    Convert an audio file (e.g. WebM voice message) to another format (e.g. WAV or MP3).

    Args:
        input_path: Absolute local path to the audio file.
        target_format: Target format, options: 'wav', 'mp3'. Defaults to 'wav'.
    """
    try:
        output_path = convert_audio(input_path, target_format)
        return {
            "status": "success",
            "message": f"Successfully converted to {target_format}.",
            "output_path": output_path,
            "filename": os.path.basename(output_path),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
