"""The tool allowlist: the ONLY tools a generated agent may use.

`ToolName` is a Literal, so a spec naming any other tool fails validation.
Implementations live in forgeone.tools; this module only declares the contract.
"""
from typing import Literal

ToolName = Literal["web_search", "open_page", "click", "find_on_page", "write_report"]

TOOL_CATALOG = """
- web_search(query): search the web, returns titles, urls, snippets
- open_page(url): open a page, returns title, text and links
- click(text): click a link/button on the current page by its visible text
- find_on_page(query): find passages on the current page containing a phrase
- write_report(title, markdown): save the final markdown report
"""
