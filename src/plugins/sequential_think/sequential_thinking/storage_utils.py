import json
from datetime import datetime
from pathlib import Path
from typing import Any

import portalocker

from .logging_conf import configure_logging
from .models import ThoughtData

logger = configure_logging("sequential-thinking.storage-utils")


def prepare_thoughts_for_serialization(thoughts: list[ThoughtData]) -> list[dict[str, Any]]:
    """Prepare thoughts for serialization with IDs included.

    Args:
        thoughts: List of thought data objects to prepare

    Returns:
        List[Dict[str, Any]]: List of thought dictionaries with IDs
    """
    return [thought.to_dict(include_id=True) for thought in thoughts]


def save_thoughts_to_file(
    file_path: Path, thoughts: list[dict[str, Any]], lock_file: Path, metadata: dict[str, Any] | None = None
) -> None:
    """Save thoughts to a file with proper locking.

    Args:
        file_path: Path to the file to save
        thoughts: List of thought dictionaries to save
        lock_file: Path to the lock file
        metadata: Optional additional metadata to include
    """
    data = {"thoughts": thoughts, "lastUpdated": datetime.now().isoformat()}

    if metadata:
        data.update(metadata)

    with portalocker.Lock(lock_file, timeout=10) as _, open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.debug(f"Saved {len(thoughts)} thoughts to {file_path}")


def load_thoughts_from_file(file_path: Path, lock_file: Path) -> list[ThoughtData]:
    """Load thoughts from a file with proper locking.

    Args:
        file_path: Path to the file to load
        lock_file: Path to the lock file

    Returns:
        List[ThoughtData]: Loaded thought data objects

    Raises:
        json.JSONDecodeError: If the file is not valid JSON
        KeyError: If the file doesn't contain valid thought data
    """
    if not file_path.exists():
        return []

    try:
        with portalocker.Lock(lock_file, timeout=10) as _, open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        thoughts = [ThoughtData.from_dict(thought_dict) for thought_dict in data.get("thoughts", [])]

        logger.debug(f"Loaded {len(thoughts)} thoughts from {file_path}")
        return thoughts

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error loading from {file_path}: {e}")
        backup_file = file_path.with_suffix(f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
        file_path.rename(backup_file)
        logger.info(f"Created backup of corrupted file at {backup_file}")
        return []
