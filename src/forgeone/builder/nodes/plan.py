"""plan: goal -> free-text build plan (scope, tools, method, stopping rule, tests)."""
from ...llm import chat_model
from ...observability import traced_node
from ...spec import TOOL_CATALOG
from ..prompts import render
from ..state import ForgeState


@traced_node("plan")
def plan_node(state: ForgeState) -> dict:
    prompt = render("plan", goal=state["goal"], tools=TOOL_CATALOG)
    return {"plan": chat_model().invoke(prompt).content}
