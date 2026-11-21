"""write_report: the agent's final deliverable."""
from __future__ import annotations

import re
from datetime import datetime

from .base import ToolContext, ToolDef, ToolResult


def write_report(ctx: ToolContext, args: dict) -> ToolResult:
    title, markdown = args.get("title", "Report"), args["markdown"]
    ctx.report = markdown
    if ctx.reports_dir is not None:
        ctx.reports_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "report"
        path = ctx.reports_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.md"
        path.write_text(f"# {title}\n\n{markdown}\n", encoding="utf-8")
        ctx.report_path = str(path)
    return ToolResult(content=f"Report saved ({len(markdown)} characters). Now give a short final summary.",
                      data={"chars": len(markdown)})


TOOLS = [
    ToolDef("write_report", "Save the final research report as markdown. Call it exactly once, at the end. "
            "End the report with a Sources list of the URLs you actually opened.",
            {"type": "object", "properties": {"title": {"type": "string"}, "markdown": {"type": "string"}},
             "required": ["title", "markdown"]}, write_report),
]
