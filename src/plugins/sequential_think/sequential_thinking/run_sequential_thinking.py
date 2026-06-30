import json

try:
    from .sequential_thinking import SequentialThinking, ThoughtStage
except ImportError:
    from sequential_thinking import SequentialThinking, ThoughtStage


def process_thought(
    st: SequentialThinking,
    thought: str,
    thought_number: int,
    total_thoughts: int,
    next_thought_needed: bool,
    stage: str,
    tags: list[str] | None = None,
    axioms_used: list[str] | None = None,
    assumptions_challenged: list[str] | None = None,
) -> dict:
    """Add a sequential thought with its metadata."""
    return st.process_thought(
        thought, thought_number, total_thoughts, next_thought_needed, stage, tags, axioms_used, assumptions_challenged
    )


def generate_summary(st: SequentialThinking) -> dict:
    """Generate a summary of the entire thinking process."""
    return st.generate_summary()


def clear_history(st: SequentialThinking) -> dict:
    """Clear the thought history."""
    return st.clear_history()


def export_session(st: SequentialThinking, file_path: str) -> dict:
    """Export the current thinking session to a file."""
    return st.export_session(file_path)


def import_session(st: SequentialThinking, file_path: str) -> dict:
    """Import a thinking session from a file."""
    return st.import_session(file_path)


if __name__ == "__main__":
    # Example usage
    st_instance = SequentialThinking()

    # Process a thought
    process_thought(
        st_instance,
        thought="This is a test thought.",
        thought_number=1,
        total_thoughts=1,
        next_thought_needed=False,
        stage=ThoughtStage.ANALYSIS.value,
        tags=["test"],
    )

    # Generate a summary
    summary = generate_summary(st_instance)
    print(json.dumps(summary, indent=2))

    # Clear history
    clear_history(st_instance)
    print("History cleared.")
