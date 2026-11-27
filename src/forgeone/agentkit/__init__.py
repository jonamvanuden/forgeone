"""agentkit: the framework generated agents are built on.

ForgeOne builds an agent as a validated AgentBlueprint (its plan: milestones, tasks, prompts,
tools, criteria) inside a small Python package that imports this module. Every command to the
agent then runs as a Goal on ForgeOne's own work engine, so every agent works like ForgeOne:
goal -> milestones -> tasks -> actions (execute, verify, edge test), logged in the same store
and visible in the same Control Room.
"""
from .agent import Agent, check
from .blueprint import AgentBlueprint, MilestoneBlueprint, TaskBlueprint, blueprint_checks
from .cli import run_cli, run_command

__all__ = ["Agent", "AgentBlueprint", "MilestoneBlueprint", "TaskBlueprint", "blueprint_checks", "check",
           "run_cli", "run_command"]
