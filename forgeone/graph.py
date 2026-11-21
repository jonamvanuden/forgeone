from pathlib import Path
from typing import TypedDict
import os

import yaml
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, START, END

from forgeone.observability import (
    LLMCallLogger, log_artifact, log_decision, log_event, start_run, traced_node,
)
from forgeone.spec import AgentSpec, TOOL_CATALOG

load_dotenv()

MODEL = os.getenv("FORGEONE_MODEL", "claude-haiku-5-5")


class ForgeState(TypedDict):
    goal: str
    plan: str
    spec: dict


llm = ChatAnthropic(model=MODEL, max_tokens=8000)


@traced_node("plan")
def plan_node(state: ForgeState) -> dict:
    prompt = f"""You are ForgeOne, an architect that designs AI agents.
Write a concise build plan for this agent:

{state['goal']}

The agent can ONLY use these tools:
{TOOL_CATALOG}

Cover: scope, which tools and why, step-by-step research method,
stopping rule, report format, and 3 test tasks with success criteria."""
    return {"plan": llm.invoke(prompt).content}


@traced_node("build")
def build_node(state: ForgeState) -> dict:
    builder_llm = llm.with_structured_output(AgentSpec, method="json_schema")
    prompt = f"""You are ForgeOne's builder. Turn this plan into an AgentSpec.

GOAL:
{state['goal']}

PLAN:
{state['plan']}

TOOLS AVAILABLE:
{TOOL_CATALOG}

Rules:
- system_prompt is the agent's whole brain: make it specific and operational.
- Tell it to cross-check facts across 2+ sources and never invent URLs.
- Tell it to call write_report once at the end, then give a short summary.
- Test cases must be answerable from the public web."""
    spec: AgentSpec = builder_llm.invoke(prompt)

    # decisions: record WHAT the builder chose, so you can compare builds later
    log_decision("tools_selected", "builder chose tools for the agent", tools=spec.tools)
    log_decision("step_budget", "builder set max tool calls per task", max_steps=spec.max_steps)
    log_event("spec.summary", name=spec.name,
              system_prompt_chars=len(spec.system_prompt),
              test_cases=[t.task for t in spec.test_cases])
    log_artifact("spec", spec.name, spec.model_dump())   # versioned in the DB
    return {"spec": spec.model_dump()}


builder = StateGraph(ForgeState)
builder.add_node("plan", plan_node)
builder.add_node("build", build_node)
builder.add_edge(START, "plan")
builder.add_edge("plan", "build")
builder.add_edge("build", END)
graph = builder.compile()


if __name__ == "__main__":
    goal_file = Path("goals/voyagerone.md")
    goal = goal_file.read_text()
    with start_run("forgeone.build", goal=goal, goal_file=str(goal_file), model=MODEL) as run_id:
        result = graph.invoke(
            {"goal": goal},
            config={"callbacks": [LLMCallLogger()], "run_name": "forgeone.build",
                    "metadata": {"run_id": run_id}},
        )
        out = Path("runs") / run_id / "spec.yaml"
        out.write_text(yaml.safe_dump(result["spec"], sort_keys=False, allow_unicode=True))
        print(f"\nspec: {out}")