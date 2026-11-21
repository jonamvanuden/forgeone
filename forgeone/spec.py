from typing import Literal

from pydantic import BaseModel, Field

# The ONLY tools a generated agent may use. We implement them in step 3.
ToolName = Literal["web_search", "open_page", "click", "find_on_page", "write_report"]

TOOL_CATALOG = """
- web_search(query): search the web, returns titles, urls, snippets
- open_page(url): open a page, returns title, text and links
- click(text): click a link/button on the current page by its visible text
- find_on_page(query): find passages on the current page containing a phrase
- write_report(title, markdown): save the final markdown report
"""


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
