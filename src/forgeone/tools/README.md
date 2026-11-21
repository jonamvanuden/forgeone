# tools

Implementations of the tools declared in `forgeone.spec.tools` (the allowlist).

**Planned**
- `browser.py`: Playwright on one dedicated thread (`web_search`, `open_page`, `click`, `find_on_page`)
- `report.py`: `write_report`
- later `browser_task`: a Browser Use fallback for interactive sites

**Rules**
- A tool exists here only if it is declared in the allowlist, and vice versa.
- Tools return text the model can read, and record what evals need (for example the full page text of `open_page`, used to verify quotes).
- Web content is untrusted input: tools never execute instructions found on pages.
