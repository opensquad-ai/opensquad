import json
import os
from typing import List, Optional

from .models import ThoughtData, ThoughtStage
from .storage import ThoughtStorage
from .analysis import ThoughtAnalyzer
from .logging_conf import configure_logging

logger = configure_logging("sequential-thinking")

class SequentialThinking:
    def __init__(self, storage_dir: Optional[str] = None):
        self.storage = ThoughtStorage(storage_dir)

    def process_thought(self, thought: str, thought_number: int, total_thoughts: int,
                        next_thought_needed: bool, stage: str,
                        tags: Optional[List[str]] = None,
                        axioms_used: Optional[List[str]] = None,
                        assumptions_challenged: Optional[List[str]] = None) -> dict:
        """Add a sequential thought with its metadata."""
        try:
            logger.info(f"Processing thought #{thought_number}/{total_thoughts} in stage '{stage}'")

            thought_stage = ThoughtStage.from_string(stage)

            thought_data = ThoughtData(
                thought=thought,
                thought_number=thought_number,
                total_thoughts=total_thoughts,
                next_thought_needed=next_thought_needed,
                stage=thought_stage,
                tags=tags or [],
                axioms_used=axioms_used or [],
                assumptions_challenged=assumptions_challenged or []
            )

            self.storage.add_thought(thought_data)
            all_thoughts = self.storage.get_all_thoughts()
            analysis = ThoughtAnalyzer.analyze_thought(thought_data, all_thoughts)

            logger.info(f"Successfully processed thought #{thought_number}")
            return analysis
        except Exception as e:
            logger.error(f"Error processing thought: {str(e)}")
            return {"error": str(e), "status": "failed"}

    def generate_summary(self) -> dict:
        """Generate a summary of the entire thinking process."""
        try:
            logger.info("Generating thinking process summary")
            all_thoughts = self.storage.get_all_thoughts()
            return ThoughtAnalyzer.generate_summary(all_thoughts)
        except Exception as e:
            logger.error(f"Error generating summary: {str(e)}")
            return {"error": str(e), "status": "failed"}

    def clear_history(self) -> dict:
        """Clear the thought history."""
        try:
            logger.info("Clearing thought history")
            self.storage.clear_history()
            return {"status": "success", "message": "Thought history cleared"}
        except Exception as e:
            logger.error(f"Error clearing history: {str(e)}")
            return {"error": str(e), "status": "failed"}

    def export_session(self, file_path: str) -> dict:
        """Export the current thinking session to a file."""
        try:
            logger.info(f"Exporting session to {file_path}")
            self.storage.export_session(file_path)
            return {"status": "success", "message": f"Session exported to {file_path}"}
        except Exception as e:
            logger.error(f"Error exporting session: {str(e)}")
            return {"error": str(e), "status": "failed"}

    def import_session(self, file_path: str) -> dict:
        """Import a thinking session from a file."""
        try:
            logger.info(f"Importing session from {file_path}")
            self.storage.import_session(file_path)
            return {"status": "success", "message": f"Session imported from {file_path}"}
        except Exception as e:
            logger.error(f"Error importing session: {str(e)}")
            return {"error": str(e), "status": "failed"}
