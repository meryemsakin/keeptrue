# experiments

This repo is a lab notebook as much as a tool. Each folder is one finding about
**whether the rules we give agents (and gateways) actually get enforced** —
reproducible, with the code that produced it.

The tool (`keeptrue`, at the repo root) grew out of these. Findings lead;
the tool is how you reproduce them on your own setup.

| # | Finding | Status |
|---|---|---|
| [01](01-guardrail-bypass-across-formats/) | Tool-permission rules enforced on one API format were silently skipped on another | shipped upstream |
| 02 | *(next)* Which `AGENTS.md` lines actually change agent behavior — and which just burn tokens | planned |

Each experiment folder has its own README with the setup, the numbers, and how
to reproduce.
