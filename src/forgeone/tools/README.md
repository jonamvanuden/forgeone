# tools

Implementations of the tools declared in `forgeone.spec.tools` (the allowlist). Importing this
package fails if the allowlist and the implementations ever differ.

| Tool | File | Notes |
|---|---|---|
| `web_search` | `web.py` | DuckDuckGo HTML via the browser, no API key |
| `open_page` | `web.py` | Returns truncated text to the model, records the full text for quote checks |
| `click`, `find_on_page` | `web.py` | Act on the current page |
| `write_report` | `report.py` | The final deliverable, saved under the run folder |

`browser.py` runs Playwright on one dedicated thread, with `--no-sandbox` when running as root.
Setup: `pip install playwright && playwright install --with-deps chromium`.

**Rules:** tools are deterministic code (no LLM inside). Web content is labelled as untrusted data
in every result. Where traces show the agent doing the same mechanical step with an LLM every
time, that step becomes a new deterministic tool here, and the eval suite proves it is as good.
