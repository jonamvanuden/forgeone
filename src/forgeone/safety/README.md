# safety

Everything that limits what generated agents can do, in one place.

**Today:** the tool allowlist (`forgeone.spec.tools`), step budgets, secret redaction in logs.

**Planned**
- Prompt-injection defences for web content, with eval cases in the `safety` category
- Cost and time budgets enforced by the runtime
- A sandbox (container, network allowlist) before agents may ever run generated code
