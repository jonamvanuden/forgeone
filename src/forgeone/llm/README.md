# llm/

How ForgeOne talks to models.

| File | What it does |
|---|---|
| `registry.py` | Every model ForgeOne can use. Claude models are built in; others come from `models.yaml` in the ForgeOne root. `model_info(name)` gives context window, max output, prices and provider. |
| `models.py` | `chat_model(name)`: `ChatAnthropic` for Claude, `OpenAICompatChat` for `runpod` and `openai_compatible`. |
| `openai_compat.py` | The adapter for OpenAI-compatible servers (RunPod serverless vLLM, Ollama, gateways). Same interface as the Claude client: `invoke`, `bind_tools`, `with_structured_output`, `usage_metadata`, logging callbacks. Strips Qwen3 `<think>` blocks, retries 408/409/425/429/5xx and connection errors. |
| `pricing.py` | Cost per call, including Anthropic cache reads (10%) and writes (125%). Registered models use the prices in `models.yaml`. |

## Adding a RunPod model
1. RunPod: Serverless > New endpoint > vLLM, `MODEL_NAME=Qwen/Qwen3-8B`, `ENABLE_AUTO_TOOL_CHOICE=true`, `TOOL_CALL_PARSER=hermes`.
2. Copy `models.example.yaml` to `models.yaml`, fill in `endpoint_id`.
3. `RUNPOD_API_KEY=...` in `.env`.
4. `forgeone models check qwen3-8b`: a plain answer, a tool call and structured JSON must all pass.

Use it with `FORGEONE_MODEL`, `FORGEONE_JUDGE_MODEL`, `FORGEONE_AGENT_MODEL`, `FORGEONE_STRONG_MODEL`,
or `model: qwen3-8b` in an eval variant.

The base URL is `https://api.runpod.ai/v2/<endpoint_id>/openai/v1`. Nothing secret goes in
`models.yaml`.
