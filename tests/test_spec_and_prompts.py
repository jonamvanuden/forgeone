import pytest
from pydantic import ValidationError

from forgeone.builder.prompts import render
from forgeone.spec import TOOL_CATALOG, AgentSpec

BASE = dict(name="voyagerone", description="d", system_prompt="p", tools=["web_search"], max_steps=20,
            test_cases=[{"task": "a", "success_criteria": "b"}, {"task": "c", "success_criteria": "d"}])


def test_spec_valid():
    assert AgentSpec.model_validate(BASE).tools == ["web_search"]


@pytest.mark.parametrize("patch", [{"tools": ["rm_rf"]}, {"max_steps": 500}, {"test_cases": []}])
def test_spec_rejects(patch):
    with pytest.raises(ValidationError):
        AgentSpec.model_validate({**BASE, **patch})


def test_plan_prompt_unchanged_by_refactor():
    expected = ("You are ForgeOne, an architect that designs AI agents.\nWrite a concise build plan for this agent:\n\n"
                f"GOAL\n\nThe agent can ONLY use these tools:\n{TOOL_CATALOG}\n\n"
                "Cover: scope, which tools and why, step-by-step research method,\n"
                "stopping rule, report format, and 3 test tasks with success criteria.")
    assert render("plan", goal="GOAL", tools=TOOL_CATALOG) == expected


def test_build_prompt_has_all_parts():
    out = render("build", goal="G", plan="P", tools="T")
    assert "GOAL:\nG" in out and "PLAN:\nP" in out and "TOOLS AVAILABLE:\nT" in out
