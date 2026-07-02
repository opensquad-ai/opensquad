import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def to_camel_case(snake_str: str) -> str:
    """Convert a snake_case string to camelCase."""
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def to_snake_case(camel_str: str) -> str:
    """Convert a camelCase string to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", camel_str)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


class ThoughtStage(Enum):
    """Basic thinking stages for structured sequential thinking."""

    PROBLEM_DEFINITION = "Problem Definition"
    RESEARCH = "Research"
    ANALYSIS = "Analysis"
    SYNTHESIS = "Synthesis"
    CONCLUSION = "Conclusion"

    @classmethod
    def from_string(cls, value: str) -> "ThoughtStage":
        """Convert a string to a thinking stage."""
        for stage in cls:
            if stage.value.casefold() == value.casefold():
                return stage
        valid_stages = ", ".join(stage.value for stage in cls)
        raise ValueError(f"Invalid thinking stage: '{value}'. Valid stages are: {valid_stages}")


class ThoughtData(BaseModel):
    """Data structure for a single thought in the sequential thinking process."""

    thought: str
    thought_number: int
    total_thoughts: int
    next_thought_needed: bool
    stage: ThoughtStage
    tags: list[str] = Field(default_factory=list)
    axioms_used: list[str] = Field(default_factory=list)
    assumptions_challenged: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    id: UUID = Field(default_factory=uuid4)

    def __hash__(self):
        """Make ThoughtData hashable based on its ID."""
        return hash(self.id)

    def __eq__(self, other):
        """Compare ThoughtData objects based on their ID."""
        if not isinstance(other, ThoughtData):
            return False
        return self.id == other.id

    @field_validator("thought")
    @classmethod
    def thought_not_empty(cls, v: str) -> str:
        """Validate that thought content is not empty."""
        if not v or not v.strip():
            raise ValueError("Thought content cannot be empty")
        return v

    @field_validator("thought_number")
    @classmethod
    def thought_number_positive(cls, v: int) -> int:
        """Validate that thought number is positive."""
        if v < 1:
            raise ValueError("Thought number must be positive")
        return v

    @field_validator("total_thoughts")
    @classmethod
    def total_thoughts_valid(cls, v: int, values: Any) -> int:
        """Validate that total thoughts is valid."""
        thought_number = values.data.get("thought_number")
        if thought_number is not None and v < thought_number:
            raise ValueError("Total thoughts must be greater or equal to current thought number")
        return v

    def to_dict(self, include_id: bool = False) -> dict:
        """Convert the thought data to a dictionary representation."""
        data = self.model_dump()
        data["stage"] = self.stage.value
        if not include_id:
            data.pop("id", None)
        else:
            data["id"] = str(data["id"])

        result = {}
        for key, value in data.items():
            camel_key = to_camel_case(key)
            result[camel_key] = value

        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ThoughtData":
        """Create a ThoughtData instance from a dictionary."""
        snake_data = {}
        for key, value in data.items():
            snake_key = to_snake_case(key)
            snake_data[snake_key] = value

        if "stage" in snake_data:
            snake_data["stage"] = ThoughtStage.from_string(snake_data["stage"])

        if "id" in snake_data:
            try:
                snake_data["id"] = UUID(snake_data["id"])
            except (ValueError, TypeError):
                snake_data["id"] = uuid4()

        return cls(**snake_data)

    model_config = {"arbitrary_types_allowed": True}
