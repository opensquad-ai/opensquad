# -*- coding: utf-8 -*-
import os
import logging
from pydub import AudioSegment
import imageio_ffmpeg as ffmpeg_lib

logger = logging.getLogger(__name__)

# Automatically configure pydub to use the binary provided by imageio-ffmpeg
ffmpeg_path = ffmpeg_lib.get_ffmpeg_exe()
AudioSegment.converter = ffmpeg_path

def convert_audio(input_path: str, output_format: str = "wav") -> str:
    """
    Convert audio format.
    
    Args:
        input_path: Input file path (e.g. .webm)
        output_format: Target format (e.g. wav, mp3)
        
    Returns:
        Path to the converted file
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    # Extract base name and replace extension
    base_name = os.path.splitext(input_path)[0]
    output_path = f"{base_name}.{output_format}"
    
    logger.info(f"[Media] Converting {input_path} -> {output_path}...")
    
    try:
        # Load audio (webm typically contains opus encoding)
        audio = AudioSegment.from_file(input_path)
        
        # Export to target format
        audio.export(output_path, format=output_format)
        
        logger.info(f"[Media] Conversion successful.")
        return output_path
    except Exception as e:
        logger.error(f"[Media] Conversion failed: {e}")
        raise e
