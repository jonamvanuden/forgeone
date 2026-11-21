"""build: plan -> validated AgentSpec (structured output; invalid output fails loudly)."""
from ...llm import chat_model
from ...observability import log_artifact, log_decision, log_event, traced_node
from ...spec import TOOL_CATALOG, AgentSpec
from ..prompts import render
from ..state import ForgeState


@traced_node("build")
def build_node(state: ForgeState) -> dict:
    # json_schema: forced tool calling is not supported on the newest Claude models
    builder_llm = chat_model().with_structured_output(AgentSpec, method="json_schema")
    prompt = render("build", goal=state["goal"], plan=state["plan"], tools=TOOL_CATALOG)
    spec: AgentSpec = builder_llm.invoke(prompt)

    # decisions: record WHAT the builder chose, so builds can be compared later
    log_decision("tools_selected", "builder chose tools for the agent", tools=spec.tools)
    log_decision("step_budget", "builder set max tool calls per task", max_steps=spec.max_steps)
    log_event("spec.summary", name=spec.name,
              system_prompt_chars=len(spec.system_prompt),
              test_cases=[t.task for t in spec.test_cases])
    log_artifact("spec", spec.name, spec.model_dump())   # versioned in the DB
    return {"spec": spec.model_dump()}
