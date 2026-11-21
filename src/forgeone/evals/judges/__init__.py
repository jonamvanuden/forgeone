"""Model judges (Day 2+): for what code cannot check (correctness, completeness, reasoning).

Rules for a trustworthy judge (docs/day-01, chapter 12.3):
  - a rubric with anchored 1/3/5 descriptions per dimension (rubrics/*.md)
  - evidence required: every score quotes the transcript or report
  - pairwise (challenger vs champion) beats absolute scores; ask twice with positions swapped
  - judge with a different, stronger model than the agent
  - calibrate against human grades before trusting it with promotion decisions
"""
from pathlib import Path

RUBRICS = Path(__file__).parent / "rubrics"


def rubric(name: str) -> str:
    return (RUBRICS / f"{name}.md").read_text(encoding="utf-8")
