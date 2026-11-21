"""AgentSpec: the contract between the builder and the runtime.

The builder never writes code. It fills in this form, which is validated,
versioned and diffable. Changing a field here changes what every builder can produce.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .tools import ToolName


class TestCase(BaseModel):
    task: str = Field(description="A realistic research command a user would give.")
    success_criteria: str = Field(description="Concrete, checkable pass/fail criteria.")


class AgentSpec(BaseModel):
    name: str = Field(description="kebab-case, e.g. 'voyagerone'")
    description: str = Field(description="One paragraph: what the agent does.")
    system_prompt: str = Field(
        description="The agent's full system prompt: role, step-by-step research "
        "method, source rules, stopping rule, exact report format."
    )
    tools: list[ToolName] = Field(description="Tools the agent needs, from the catalog.")
    max_steps: int = Field(ge=5, le=60, description="Max tool calls per task.")
    test_cases: list[TestCase] = Field(min_length=2, max_length=4)
