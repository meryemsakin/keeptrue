# Trajectory format

A *run* (trajectory) records what an agent did on one task. `check` scores
recorded runs supplied by your own harness (Claude Code, Codex, a custom agent
loop). The experimental `ablate` workflow has a separate frozen-plan format;
see [instruction experiments](instruction-experiments.md).

Each JSON file is **either one run object or a list of runs**, so you can keep
all of one model's runs in a single file (e.g. `runs/claude-2026-09.json`).

## Schema

```jsonc
{
  "task_id": "add-endpoint",        // required: which task this run is for
  "model": "claude-code-2026-09",   // required: label for this agent/version
  "run": 0,                          // optional: repeat index (for N-run variance)
  "steps": [                         // what the agent did, in order
    {"tool": "bash",  "command": "pip install requests"},
    {"tool": "bash",  "command": "pytest -q", "exit_code": 1},
    {"tool": "edit",  "path": "src/api.py", "diff": "+    print('debug')\n"}
  ],
  "final_message": "Done — added the endpoint.",  // the agent's closing message
  "usage": {"input_tokens": 2600, "output_tokens": 1700, "duration_s": 31},
  "success": true                    // did the task's own acceptance check pass?
}
```

### Step fields (all optional except what your checks read)

| field | meaning | read by |
|---|---|---|
| `tool` | tool name (`bash`, `edit`, `read`, …) | (informational) |
| `command` | shell-ish command text | `*_command`, `no_repeat_loops` |
| `path` | file the step touched | `*_path` |
| `diff` | unified-diff-ish text of the change | `*_in_diff` |
| `exit_code` | exit status of a command | (informational) |
| `result` | `recorded` (default), `ok`, `error`, or `unknown` | evidence confirmation |
| `tool_use_id` | original tool call identifier | source review |

`final_message` is read by `max_final_length` and `*_in_final`. `usage` and `success` feed the
cost/reliability table. Anything a check doesn't read can be omitted.

For backwards compatibility, manually supplied steps default to `recorded`:
the caller asserts these events happened. The Claude Code adapter instead starts
calls as `unknown`, joins their tool results by ID, and preserves failed or
unconfirmed operations. An errored shell result with an explicit exit code proves
the shell ran, not that every subcommand ran. An errored edit is not assumed to
have made its proposed change. Matching unconfirmed evidence produces an unknown
check verdict, excluded from adherence's denominator and counted separately.

Path checks inspect all recorded paths by default. Add `tools: [edit]` to a path
check to restrict it to editing tools normalized by the Claude Code adapter.
Paths altered indirectly through a shell command are not reconstructed.

Unknown task outcomes (`success: null` or omitted) are excluded from success rate
and per-success costs. Both the token/cost numerator and success denominator use
the same outcome-labeled subset. No known outcomes yields `n/a`; known outcomes
with zero successes and complete usage yield infinity.

Omitted/null token counts and durations remain unknown. Explicit zero is an
observation. Token counts must be nonnegative integers; duration must be a finite
nonnegative number. `tokens/run` uses only runs with both input and output counts,
and average time uses recorded durations; the report shows their coverage.
Per-success token/cost estimates require complete usage for **every** run with a
known outcome. Missing usage on a failed run cannot silently make success cheaper.

The Claude Code adapter preserves the complete session ID, labels sessions with
multiple models as `mixed`, ignores synthetic assistant notices, and collects the
last non-synthetic assistant response. A later unfinished tool turn has no final
message. Repeated streaming records with the same message ID contribute the
maximum observed input/output counter once per message. Cache counters are not
included, and wall-clock session duration includes idle periods. These fields
must not be presented as a provider billing total or active execution time.

## Why so minimal?

The checks only need signals they can verify deterministically: the commands
run, the paths touched, the text of the change, and the closing message. Keep
capture cheap — you don't need a full transcript, just these fields.
