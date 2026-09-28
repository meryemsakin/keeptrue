"""Data model.

A *run* (Trajectory) is what an agent did on one task: the tool calls it made
and the message it ended with. A *rule* carries a deterministic *check* that
looks at a run and decides pass/fail. Everything the report shows is derived
from these two objects, so a score is always traceable back to evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Step:
    """One thing the agent did. Fields are optional so different tools
    (a shell call, a file edit, a plain message) all fit the same shape."""

    type: str = "tool_call"          # "tool_call" | "message"
    tool: Optional[str] = None       # "bash", "edit", "read", ...
    command: Optional[str] = None    # shell-ish command text, if any
    path: Optional[str] = None       # file touched, if any
    diff: Optional[str] = None       # unified-diff-ish text of the change
    exit_code: Optional[int] = None  # for commands that ran


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Trajectory:
    """One agent run on one task."""

    task_id: str
    model: str
    run: int = 0
    steps: list[Step] = field(default_factory=list)
    final_message: str = ""
    usage: Usage = field(default_factory=Usage)
    success: Optional[bool] = None   # did the task's own acceptance check pass?


@dataclass
class Check:
    kind: str
    params: dict = field(default_factory=dict)


@dataclass
class Rule:
    id: str
    text: str
    check: Optional[Check] = None    # None => not machine-verifiable yet


@dataclass
class CheckOutcome:
    passed: bool
    evidence: str = ""               # the offending (or confirming) detail


@dataclass
class RuleModelResult:
    rule_id: str
    model: str
    n: int                           # runs this rule was evaluated over
    adherence: float                 # fraction of runs that passed (NaN if n==0)
    evidence: list[str] = field(default_factory=list)
