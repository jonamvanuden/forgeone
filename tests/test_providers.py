"""Providers and routing: switching a task's model or provider is a models.yaml change, no code."""
from __future__ import annotations

import pytest
from test_openai_compat import Capital, Server

from forgeone.llm import models as models_mod
from forgeone.llm import registry as reg
from forgeone.llm.openai_compat import OpenAICompatChat
from forgeone.llm.registry import ModelInfo
from forgeone.work import policy
from forgeone.work.model import Goal, Task

YAML = """
providers:
  local:
    adapter: openai_compat
    base_url: http://127.0.0.1:9/v1
    key_env: LOCAL_KEY
models:
  llama-70b:
    provider: deepinfra
    model: meta-llama/Llama-3.3-70B-Instruct
    price_in: 0.23
    price_out: 0.40
routing:
  planner: claude-sonnet-5-5
  judge: llama-70b
  cheap: llama-70b
  strong: claude-opus-5-5
  types:
    write: deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507
"""


@pytest.fixture
def yaml_file(tmp_path, monkeypatch):
    (tmp_path / "models.yaml").write_text(YAML)
    monkeypatch.setattr(reg, "models_file", lambda: tmp_path / "models.yaml")
    return tmp_path / "models.yaml"


def test_deepinfra_is_built_in(yaml_file):
    info = reg.model_info("llama-70b")
    assert info.adapter == "openai_compat"
    assert info.url == "https://api.deepinfra.com/v1/openai"
    assert info.key_env == "DEEPINFRA_API_KEY"
    assert info.structured_mode == "json_object"


def test_provider_colon_model_needs_no_entry(yaml_file):
    info = reg.model_info("deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507")
    assert info.provider == "deepinfra" and info.provider_model == "Qwen/Qwen3-235B-A22B-Instruct-2507"
    with pytest.raises(KeyError):
        reg.model_info("nosuchprovider:x")


def test_own_provider_from_yaml(yaml_file):
    info = reg.model_info("local:my-model")
    assert info.url == "http://127.0.0.1:9/v1" and info.key_env == "LOCAL_KEY"


def test_chat_model_picks_adapter_by_provider(yaml_file):
    assert isinstance(models_mod.chat_model("llama-70b"), OpenAICompatChat)
    seen = {}

    @models_mod.adapter("fake")
    def _fake(info, max_tokens):
        seen["model"] = info.provider_model
        return "fake-chat"

    try:
        yaml_file.write_text(YAML.replace("adapter: openai_compat", "adapter: fake"))
        reg._load.cache_clear()
        assert models_mod.chat_model("local:m1") == "fake-chat" and seen["model"] == "m1"
    finally:
        models_mod.ADAPTERS.pop("fake")
        reg._load.cache_clear()


def test_routing_table(yaml_file):
    assert policy.planner_model() == "claude-sonnet-5-5"
    assert policy.judge_model() == "llama-70b"
    goal = Goal(title="g", agent=None)
    easy = Task(goal_id=goal.id, milestone_id="m", plan_version=1, seq=1, code="1.1", title="t", type="analyze", difficulty=2)
    hard = Task(goal_id=goal.id, milestone_id="m", plan_version=1, seq=1, code="1.1", title="t", type="analyze", difficulty=5)
    write = Task(goal_id=goal.id, milestone_id="m", plan_version=1, seq=1, code="1.1", title="t", type="write", difficulty=5)
    assert policy.route(easy, goal, False)[1] == "llama-70b"
    assert policy.route(hard, goal, False)[1] == "claude-opus-5-5"
    _, m, why = policy.route(write, goal, False)
    assert m == "deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507" and "routes write" in why


def test_no_yaml_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "models_file", lambda: tmp_path / "missing.yaml")
    monkeypatch.setenv("FORGEONE_STRONG_MODEL", "claude-opus-5-5")
    assert policy.strong_model() == "claude-opus-5-5"
    assert policy.type_model("write") is None


def test_agentkit_tasks_follow_routing(yaml_file):
    from forgeone.agentkit.agent import task_model
    from forgeone.agentkit.blueprint import TaskBlueprint

    t = TaskBlueprint(key="find", title="Find", kind="browse", type="research", instructions="x", criteria=["y"])
    assert task_model(t) == "llama-70b"
    assert task_model(t.model_copy(update={"difficulty": 4})) == "claude-opus-5-5"
    assert task_model(t.model_copy(update={"type": "write", "kind": "report"})).startswith("deepinfra:")
    assert task_model(t.model_copy(update={"model": "claude-haiku-5-5"})) == "claude-haiku-5-5"


def test_json_object_mode_on_the_wire(monkeypatch):
    s = Server()
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    try:
        info = ModelInfo(name="di", provider="deepinfra", model="Qwen/Qwen3-32B", base_url=s.url, timeout_s=10)
        out = OpenAICompatChat(info).with_structured_output(Capital).invoke("capital of France?")
        body = s.requests[0]["body"]
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["role"] == "system" and "JSON schema" in body["messages"][0]["content"]
        assert "chat_template_kwargs" not in body           # a vLLM-only flag, not sent to DeepInfra
        assert s.requests[0]["auth"] == "Bearer di-test"
        assert out == Capital(city="Paris", country="France")
    finally:
        s.httpd.shutdown()
