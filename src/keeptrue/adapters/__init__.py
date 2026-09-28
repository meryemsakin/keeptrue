"""Adapters turn a coding agent's own session logs into keeptrue runs, so you
can score real work without producing JSON by hand — and without any new model
calls."""

from __future__ import annotations

from ..models import Trajectory
from . import claude_code, codex


def detect(path: str) -> str:
    """'codex' for a Codex rollout (it opens with session_meta), else 'claude_code'."""
    return "codex" if codex.is_codex_log(path) else "claude_code"


def load_any(path: str) -> Trajectory | None:
    """Parse a Claude Code or Codex session file, whichever it is."""
    if detect(path) == "codex":
        return codex.load_session(path)
    return claude_code.load_session(path)
