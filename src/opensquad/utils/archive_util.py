# -*- coding: utf-8 -*-
import os
import zipfile
import logging
import math
import shutil
from typing import List

logger = logging.getLogger(__name__)

# Default part size: 90MB (backend limit is usually 100MB, leaving 10MB buffer)
DEFAULT_PART_SIZE = 90 * 1024 * 1024 

def prepare_file_for_sending(file_path: str, part_size: int = DEFAULT_PART_SIZE) -> List[str]:
    """
    Prepare a file for sending. If the file is too large, compress and split it into parts.
    Returns a list of prepared file paths (may be the original file or multiple split parts).
    """
    if not os.path.exists(file_path):
        logger.error(f"[Archive] File not found: {file_path}")
        return []

    file_size = os.path.getsize(file_path)
    
    # 1. If below threshold, return the original file directly
    if file_size <= part_size:
        logger.info(f"[Archive] File size {file_size/1024/1024:.1f}MB is within limit.")
        return [file_path]

    # 2. If file is too large, compress and split into parts
    logger.info(f"[Archive] File too large ({file_size / 1024 / 1024:.1f}MB), processing...")
    
    # Create a temporary directory for split parts
    temp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp_archives")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)
    
    base_name = os.path.basename(file_path)
    
    # Use split-then-label approach, or simple ZIP wrapping.
    # For generality, split directly if already a compressed archive; otherwise wrap into ZIP first.
    work_file = file_path
    is_temp_zip = False
    
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ['.zip', '.7z', '.rar', '.tar', '.gz']:
        zip_path = os.path.join(temp_dir, f"{base_name}.zip")
        try:
            logger.info(f"[Archive] Compressing {base_name} to ZIP...")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(file_path, base_name)
            work_file = zip_path
            is_temp_zip = True
            file_size = os.path.getsize(work_file)
        except Exception as e:
            logger.error(f"[Archive] Compression failed, falling back to raw split: {e}")
    
    # 3. Perform binary split
    return split_file(work_file, temp_dir, part_size)

def split_file(file_path: str, output_dir: str, part_size: int) -> List[str]:
    """
    Physically split a file into multiple parts.
    Naming format: filename.zip.001, filename.zip.002 ...
    """
    file_size = os.path.getsize(file_path)
    num_parts = math.ceil(file_size / part_size)
    
    logger.info(f"[Archive] Splitting into {num_parts} parts...")
    
    parts = []
    base_name = os.path.basename(file_path)
    
    try:
        with open(file_path, 'rb') as f:
            for i in range(1, num_parts + 1):
                part_name = f"{base_name}.{i:03d}"
                part_path = os.path.join(output_dir, part_name)
                
                chunk = f.read(part_size)
                with open(part_path, 'wb') as pf:
                    pf.write(chunk)
                
                parts.append(part_path)
                logger.info(f"[Archive] Created: {part_name}")
                
        return parts
    except Exception as e:
        logger.error(f"[Archive] Splitting failed: {e}")
        return [file_path]

def cleanup_temp():
    """Clean up the temporary directory."""
    temp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp_archives")
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            pass
