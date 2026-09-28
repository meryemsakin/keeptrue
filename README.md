# keeptrue

[![CI](https://github.com/meryemsakin/keeptrue/actions/workflows/ci.yml/badge.svg)](https://github.com/meryemsakin/keeptrue/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/keeptrue.svg)](https://pypi.org/project/keeptrue/)
![Python](https://img.shields.io/pypi/pyversions/keeptrue.svg)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Your coding agent has a rules file. Is it actually following it?**

You wrote an `AGENTS.md` (or `CLAUDE.md`, or a system prompt) telling your agent
to run the tests, use `uv` not `pip`, keep summaries short, never touch
`migrations/`. Then you upgraded the model. Are those rules still being
followed — or did some of them silently stop working?

`keeptrue` reads what an agent *actually did* on a set of tasks — the commands
it ran, the files it touched, the message it ended with — and scores each rule
as a **pass rate across runs**. No vibes, no eyeballing one transcript. It's
the diff you can't see today: *which of my instructions regressed.*

![keeptrue — you upgraded your coding agent; which rules did it silently stop following?](docs/demo-animated.svg)

```bash
# no API key, no cost — scores bundled illustrative runs:
pipx run --spec git+https://github.com/meryemsakin/keeptrue keeptrue demo
# once on PyPI:  pipx install keeptrue && keeptrue demo
```

The same agent, upgraded, silently stopped following its `pip`→`uv` and
`pytest` rules — 0% adherence on both — while your tests still pass and the cost
dashboard only shows it got *cheaper per token*. That gap is the whole point.


> The `demo` uses recorded, illustrative runs bundled with the tool, so it works
> offline and for free. Point `keeptrue check` at your own runs to score them.

## Why this exists

Recent studies keep finding the same thing: agents *say* they follow
instructions far more often than they *do*, and it gets worse as the model
changes or the conversation grows.

- Under strict grading, the strongest model in the **HANDBOOK.md** benchmark
  passes only ~36% of trials; most frontier models are below 25%. Failure modes
  include *doing a required check then acting against the result* and *reporting
  compliance they never achieved*.
- **Harness-IF** shows compliance has to hold across many "instruction
  surfaces" (system prompt, tool descriptions, `CLAUDE.md`, user turn) and
  often doesn't.
- Following an `AGENTS.md` instruction **does not** reliably translate into
  task success.

Everyone feels this. Almost nobody measures it *on their own repo*. That's the
gap `keeptrue` fills: not "is model X good," but **"is my rule getting
followed, here, now, after this upgrade."**

## How it works

1. **Rules → checks.** Each line of your rules file maps to a deterministic
   check in `keeptrue.yaml`:

   | Rule | Check |
   |---|---|
   | "use `uv`, never `pip install`" | `forbidden_command: \bpip install\b` |
   | "always run pytest" | `required_command: \bpytest\b` |
   | "never edit migrations/" | `forbidden_path: (^\|/)migrations/` |
   | "summary under 80 words" | `max_final_length: 80 words` |
   | "no debug prints" | `forbidden_in_diff: ^\+.*\bprint\(` |
   | "don't retry a failing command" | `no_repeat_loops: 3` |

2. **Runs → evidence.** You record what the agent did as small JSON files (one
   per model/version). keeptrue replays them through the checks.

3. **Karne.** For every rule it reports the **share of runs that obeyed it**,
   flags anything that regressed vs. the baseline model, and shows
   **tokens-per-success** (because "90% at half the price vs. 95% at triple" is
   the comparison you actually care about).

The scoring is **fully deterministic** — it never calls a model, so a score is
always reproducible and traceable to the exact command or line that broke the
rule. (Fuzzy rules that can't be pinned to a signal are handled by an optional
LLM judge, off by default.)

Here's the full report `keeptrue demo` prints — all eight rules, the regressions
callout, and the cost/reliability table:

![The full keeptrue report: per-rule adherence across two models, a silently-dropped-rules callout, and tokens-per-success](docs/demo.svg)

## Score your own agent

```bash
keeptrue init                       # writes a starter keeptrue.yaml
# record runs as JSON (see docs/trajectory-format.md)
keeptrue check --runs ./runs        # --config defaults to ./keeptrue.yaml
```

A run is just:

```json
{
  "task_id": "add-endpoint",
  "model": "claude-code-2026-09",
  "steps": [
    {"tool": "bash", "command": "pip install requests"},
    {"tool": "edit", "path": "src/api.py", "diff": "+    print('debug')\n"}
  ],
  "final_message": "Done.",
  "usage": {"input_tokens": 2600, "output_tokens": 1700, "duration_s": 31},
  "success": true
}
```

Adapters that capture these automatically from Claude Code / Codex sessions are
on the roadmap; today you produce them from your own harness.

## What this is *not*

- **Not a model leaderboard.** It scores *your* rules on *your* tasks.
- **Not proof a rule "works."** It measures adherence, not whether following the
  rule improved the outcome. (The cost table is there so you can see when a rule
  costs tokens without moving success.)
- **Not magic.** The checks are regexes and counts. That's the point: they're
  cheap, honest, and reproducible. Garbage rules in, garbage scores out.

## Roadmap

- [ ] Claude Code / Codex session adapters (auto-capture runs)
- [ ] `--ablation`: re-run with each rule removed to see which lines change behavior
- [ ] Optional LLM judge for natural-language rules
- [ ] GitHub Action: comment the karne on PRs that touch `AGENTS.md`

## License

MIT
