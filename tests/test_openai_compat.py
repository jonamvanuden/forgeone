"""The OpenAI-compatible adapter (RunPod serverless Qwen3) against a local fake server:
plain answers, <think> removal, tool calls, structured output, retries, callbacks, the agent loop."""
from __future__ import annotations

import http.server
import json
import threading

import pytest
from pydantic import BaseModel

from forgeone.llm import registry as reg
from forgeone.llm.openai_compat import OpenAICompatChat, ProviderError
from forgeone.llm.pricing import call_cost
from forgeone.llm.registry import ModelInfo


class Server:
    def __init__(self):
        self.requests, self.fail_next = [], 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                if outer.fail_next:
                    outer.fail_next -= 1
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error": "worker is starting"}')
                    return
                msg = outer.answer(body)
                data = json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 120, "completion_tokens": 30}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v2/abc123/openai/v1"

    def answer(self, body):
        if body.get("response_format"):
            return {"role": "assistant", "content": '```json\n{"city": "Paris", "country": "France"}\n```'}
        if body.get("tools") and not any(m["role"] == "tool" for m in body["messages"]):
            return {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "web_search", "arguments": '{"query": "who first refined sugar"}'}}]}
        if body.get("tools"):
            return {"role": "assistant", "content": "India refined sugar first."}
        return {"role": "assistant", "content": "<think>The user wants one word.</think>ready"}


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test")
    yield s
    s.httpd.shutdown()


def qwen(server, **kw) -> OpenAICompatChat:
    info = ModelInfo(name="qwen3-8b", provider="runpod", model="Qwen/Qwen3-8B", base_url=server.url,
                     price_in=0.05, price_out=0.20, context_window=32768, max_retries=2, timeout_s=10, **kw)
    return OpenAICompatChat(info)


def test_plain_answer_strips_thinking_and_counts_tokens(server):
    m = qwen(server).invoke("Reply with exactly the word: ready")
    assert m.content == "ready" and m.response_metadata["reasoning"] == "The user wants one word."
    assert m.usage_metadata == {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
    r = server.requests[0]
    assert r["path"] == "/v2/abc123/openai/v1/chat/completions" and r["auth"] == "Bearer rp-test"
    assert r["body"]["model"] == "Qwen/Qwen3-8B" and r["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_runpod_url_from_endpoint_id():
    assert ModelInfo(name="q", provider="runpod", endpoint_id="xyz").url == "https://api.runpod.ai/v2/xyz/openai/v1"
    with pytest.raises(ValueError):
        ModelInfo(name="q", provider="runpod").url  # noqa: B018


def test_tool_calls_round_trip(server):
    from langchain_core.messages import HumanMessage, ToolMessage

    chat = qwen(server).bind_tools([{"name": "web_search", "description": "search",
                                     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}])
    first = chat.invoke([HumanMessage("who first refined sugar?")])
    assert first.tool_calls == [{"name": "web_search", "args": {"query": "who first refined sugar"}, "id": "call_1",
                                 "type": "tool_call"}]
    sent_tools = server.requests[0]["body"]["tools"]
    assert sent_tools[0]["function"]["parameters"]["properties"]["query"] == {"type": "string"}
    second = chat.invoke([HumanMessage("q"), first, ToolMessage("results...", tool_call_id="call_1")])
    msgs = server.requests[1]["body"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool"]
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == '{"query": "who first refined sugar"}'
    assert msgs[2]["tool_call_id"] == "call_1" and second.content == "India refined sugar first."


class Capital(BaseModel):
    city: str
    country: str


@pytest.mark.parametrize("mode", ["json_schema", "prompt"])
def test_structured_output(server, mode):
    out = qwen(server, structured=mode).with_structured_output(Capital, include_raw=True).invoke("capital of France?")
    body = server.requests[0]["body"]
    if mode == "json_schema":
        assert body["response_format"]["json_schema"]["name"] == "Capital"
        assert out["parsed"] == Capital(city="Paris", country="France")
    else:                                                        # the schema went into a system message
        assert body["messages"][0]["role"] == "system" and "JSON schema" in body["messages"][0]["content"]


def test_retries_cold_start_then_gives_up(server, monkeypatch):
    monkeypatch.setattr("forgeone.llm.openai_compat.time.sleep", lambda s: None)
    server.fail_next = 2
    assert qwen(server).invoke("hi").content == "ready" and len(server.requests) == 3
    server.fail_next = 5
    with pytest.raises(ProviderError, match="503"):
        qwen(server).invoke("hi")


def test_missing_key_is_a_clear_error(server, monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY")
    with pytest.raises(ProviderError, match="RUNPOD_API_KEY is not set"):
        qwen(server).invoke("hi")


def test_callbacks_see_the_call(server):
    seen = []

    class CB:
        def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
            seen.append(("start", kw["invocation_params"]["model"], messages[0][0].content))

        def on_llm_end(self, result, *, run_id, **kw):
            seen.append(("end", result.generations[0][0].message.usage_metadata["input_tokens"]))

        def on_llm_error(self, e, *, run_id, **kw):
            seen.append(("error",))

    qwen(server).invoke("hello", config={"callbacks": [CB()]})
    assert seen == [("start", "qwen3-8b", "hello"), ("end", 120)]


def test_registry_and_prices(tmp_path, monkeypatch):
    (tmp_path / "models.yaml").write_text("models:\n  qwen3-8b:\n    provider: runpod\n    endpoint_id: abc\n"
                                          "    model: Qwen/Qwen3-8B\n    context_window: 32768\n"
                                          "    price_in: 0.05\n    price_out: 0.20\n")
    monkeypatch.setattr(reg, "models_file", lambda: tmp_path / "models.yaml")
    info = reg.model_info("qwen3-8b")
    assert info.context_window == 32768 and info.url.endswith("/abc/openai/v1")
    assert call_cost("qwen3-8b", 1_000_000, 1_000_000) == pytest.approx(0.25)
    assert reg.model_info("claude-haiku-5-5").provider == "anthropic"
    with pytest.raises(KeyError, match="not in"):
        reg.model_info("llama-unknown")


def test_agent_runs_on_the_adapter(server):
    from test_runtime import SPEC, FakeBrowser

    from forgeone.runtime import RunConfig, run_agent

    t = run_agent(SPEC, "Who first refined sugar?", RunConfig(model="qwen3-8b", retry_backoff_s=0),
                  browser=FakeBrowser(), llm=qwen(server))
    assert t.stop_reason == "finished" and [c.name for c in t.tool_calls] == ["web_search"]
    assert t.final_answer == "India refined sugar first." and t.tokens_in == 240
    assert isinstance(server.requests[0]["body"]["messages"][0]["content"], str)    # no Anthropic cache blocks
