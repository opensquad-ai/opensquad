# -*- coding: utf-8 -*-
import json
from typing import List, Optional

try:
    from .sequential_thinking import SequentialThinking, ThoughtStage
except (ImportError, SystemError):
    from plugins.sequential_think.sequential_thinking import SequentialThinking, ThoughtStage

st = SequentialThinking()


def process_thought(thought: str, thought_number: int, total_thoughts: int,
                    next_thought_needed: bool, stage: str,
                    tags: Optional[List[str]] = None,
                    axioms_used: Optional[List[str]] = None,
                    assumptions_challenged: Optional[List[str]] = None) -> dict:
    """Add a sequential thought with its metadata."""
    print('call process_thought')
    return st.process_thought(
        thought, thought_number, total_thoughts, next_thought_needed, stage,
        tags, axioms_used, assumptions_challenged
    )


def generate_summary() -> dict:
    """Generate a summary of the entire thinking process."""
    print('call generate_summary')
    return st.generate_summary()


def clear_history() -> dict:
    """Clear the thought history."""
    print('call clear_history')
    return st.clear_history()


def export_session(st: SequentialThinking, file_path: str) -> dict:
    """Export the current thinking session to a file."""
    print('call export_session')
    return st.export_session(file_path)


def import_session(st: SequentialThinking, file_path: str) -> dict:
    """Import a thinking session from a file."""
    print('call import_session')
    return st.import_session(file_path)


if __name__ == '__main__':
    # Example usage
    st = SequentialThinking()

    # Process a thought
    process_thought(
        thought="This is a test thought.",
        thought_number=1,
        total_thoughts=1,
        next_thought_needed=False,
        stage=ThoughtStage.ANALYSIS.value,
        tags=["test"]
    )

    # Generate a summary
    summary = generate_summary()
    print(json.dumps(summary, indent=2))

    # Clear history
    clear_history()
    print("History cleared.")
