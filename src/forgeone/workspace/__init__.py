"""Workspaces: every agent lives in its own git repository, managed by ForgeOne.

    agents.yaml (ForgeOne root)   which agents exist and where their repos are
    AgentRepo                     read goal.md / evals, publish builds into agent/ on a branch
    scaffold                      create a new agent repository from templates
"""
from .registry import AgentEntry, get_agent, load_registry
from .repo import AgentRepo, PublishResult, WorkspaceError
from .scaffold import scaffold

__all__ = ["AgentRepo", "AgentEntry", "PublishResult", "WorkspaceError", "get_agent", "load_registry", "scaffold"]
