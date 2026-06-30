class TaskManager:
    """Manages and persists the task planning state for an agent."""

    def __init__(self):
        self.raw_plan = ""

    def update(self, plan_text: str):
        if plan_text:
            self.raw_plan = plan_text.strip()

    def clear(self):
        self.raw_plan = ""

    def render(self) -> str:
        """Generate the task-state text to inject into the System Prompt."""
        if not self.raw_plan:
            return "No active plan initialized."
        return self.raw_plan
