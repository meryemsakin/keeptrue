# experiments

This repo is a lab notebook as much as a tool. Each folder investigates
**whether the rules we give agents (and gateways) actually get enforced**, or
whether keeptrue measures the evidence correctly. Each entry states its data
access and reproduction limits.

The tool (`keeptrue`, at the repo root) grew out of these. Findings lead;
the tool is how you reproduce them on your own setup.

| # | Finding | Status |
|---|---|---|
| [01](01-guardrail-bypass-across-formats/) | Tool-permission rules enforced on one API format were silently skipped on another | upstream PR submitted |
| [02](02-real-session-audit/) | A real development session exposed false passes and duplicated token accounting in keeptrue | local pilot; agent-reviewed; raw data private |
| [03](03-cross-agent-heredoc/) | A first Codex vs Claude Code scan got 3 of 7 Codex cells wrong: analysis scripts quoted the rule patterns in heredocs | fixed; one session per agent; raw data private |
| [04](04-controlled-run/) | Same AGENTS.md, 24 runs: Claude Code and Codex both obeyed every prohibition; Claude skipped tests after stopping and ran long, Codex didn't | legacy pilot; 12 runs per agent; not a benchmark |

The new [instruction experiment workflow](../docs/instruction-experiments.md)
freezes task acceptance and all planned slots. Its offline demo validates the
pipeline with synthetic programs. Real instruction-effect results remain pending.

Each experiment folder documents the available setup, results, limitations and
reproduction status. A proposed reproduction is not a completed experiment.
