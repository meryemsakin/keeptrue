"""keeptrue — measure whether your coding agent actually follows the rules
in AGENTS.md / CLAUDE.md.

The core is deterministic: every check runs over a recorded agent trajectory
(the tool calls it made + its final message) and produces a reproducible
pass/fail. No model is needed to *score* a run, only to *produce* one.
"""

__version__ = "0.2.0"
