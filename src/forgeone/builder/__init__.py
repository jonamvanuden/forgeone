"""The builder: turns an agent's goal.md into a checked agent spec and publishes it.

`run_build` runs the build as a goal (playbook build_agent); `run_build_legacy` is the original
two-node LangGraph graph.
"""
from .graph import build_graph, run_build_legacy
from .run import run_build

__all__ = ["build_graph", "run_build", "run_build_legacy"]
