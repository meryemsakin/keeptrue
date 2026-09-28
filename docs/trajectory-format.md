# Trajectory format

A *run* (trajectory) records what an agent did on one task. keeptrue scores
runs; it does not produce them. You capture them from your own harness (Claude
Code, Codex, a custom agent loop) and drop them in a directory as JSON.

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

`final_message` is read by `max_final_length`. `usage` and `success` feed the
cost/reliability table. Anything a check doesn't read can be omitted.

## Why so minimal?

The checks only need signals they can verify deterministically: the commands
run, the paths touched, the text of the change, and the closing message. Keep
capture cheap — you don't need a full transcript, just these fields.
