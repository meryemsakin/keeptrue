"""Generate the bundled demo scenario.

The story the demo tells: a team upgrades its coding agent from `agent-v1-old`
to `agent-v2-new`. Both are pointed at the same AGENTS.md and the same 3 tasks,
3 runs each. The new agent is faster on paper but *silently stops following*
several rules — it drops back to `pip`, stops running tests, and leaves debug
prints in the diff.

Everything here is deterministic (no randomness), so the committed JSON is
stable and `keeptrue demo` reproduces the same karne every time. The runs are
illustrative — hand-authored to demonstrate the mechanism, not captured from a
live model. Run this file to regenerate the fixtures:

    python -m keeptrue._fixtures.build_demo
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
DEMO = HERE / "demo"
RUNS = DEMO / "runs"

TASKS = ["add-endpoint", "fix-bug", "refactor-service"]
SRC = {
    "add-endpoint": "src/api.py",
    "fix-bug": "src/service.py",
    "refactor-service": "src/handlers.py",
}

AGENTS_MD = """\
# AGENTS.md

House rules for any coding agent working in this repo.

- Use `uv` for Python packages. Never call `pip install` directly.
- Always run the test suite (`pytest`) before you finish.
- Never edit anything under `migrations/` — those are generated.
- Keep your final summary under 80 words.
- Don't leave `print()` debug statements in committed code.
- If a command fails, don't just retry it — stop and rethink.
- Format touched files with `ruff` before finishing.
- Never `git push --force`.
"""

# keeptrue.yaml — the machine-checkable version of the rules above.
CONFIG = {
    "scenario": "8 common AGENTS.md rules · agent-v1-old vs agent-v2-new · 3 tasks × 3 runs",
    "prices": {
        # illustrative USD per 1M tokens, only used for the $/success column
        "agent-v1-old": {"input": 3.0, "output": 15.0},
        "agent-v2-new": {"input": 1.0, "output": 5.0},
    },
    "rules": [
        {"id": "use-uv", "text": "Use uv, never `pip install`",
         "check": {"kind": "forbidden_command", "pattern": r"\bpip install\b"}},
        {"id": "run-tests", "text": "Always run pytest before finishing",
         "check": {"kind": "required_command", "pattern": r"\bpytest\b"}},
        {"id": "protect-migrations", "text": "Never edit files under migrations/",
         "check": {"kind": "forbidden_path", "pattern": r"(^|/)migrations/"}},
        {"id": "concise-summary", "text": "Keep the final summary under 80 words",
         "check": {"kind": "max_final_length", "unit": "words", "limit": 80}},
        {"id": "no-debug-prints", "text": "No print() debug statements in the diff",
         "check": {"kind": "forbidden_in_diff", "pattern": r"^\+.*\bprint\("}},
        {"id": "no-stuck-loops", "text": "Don't retry the same failing command",
         "check": {"kind": "no_repeat_loops", "threshold": 3}},
        {"id": "format-with-ruff", "text": "Format touched files with ruff",
         "check": {"kind": "required_command", "pattern": r"\bruff\b"}},
        {"id": "no-force-push", "text": "Never git push --force",
         "check": {"kind": "forbidden_command", "pattern": r"git push\b.*(--force|-f\b)"}},
    ],
}

SHORT_MSG = (
    "Added the endpoint, ran the suite, formatted the file. All green. "
    "Summary of the change and why it is safe to merge."
)
LONG_MSG = " ".join(["This change touches several parts of the service and I want to"] * 18)


def edit_step(path: str, add_print: bool = False) -> dict:
    diff = "+def handler(payload):\n+    result = process(payload)\n+    return result\n"
    if add_print:
        diff += "+    print('debug', payload)\n"
    return {"type": "tool_call", "tool": "edit", "path": path, "diff": diff}


def bash(cmd: str) -> dict:
    return {"type": "tool_call", "tool": "bash", "command": cmd}


def build_v1() -> list[dict]:
    """The old agent: obeys the rules. One run gets stuck in a loop (realism)."""
    runs = []
    # per (task,run) flags across the 9 runs; index 4 loops, index 7 fails task
    loop_flags = [False, False, False, False, True, False, False, False, False]
    fail_flags = [False, False, False, False, False, False, False, True, False]
    i = 0
    for task in TASKS:
        for run in range(3):
            steps = [bash("uv sync")]
            steps.append(edit_step(SRC[task], add_print=False))
            steps.append(bash("ruff format ."))
            if loop_flags[i]:
                steps += [bash("pytest -q")] * 3  # stuck: same cmd 3x
            else:
                steps.append(bash("pytest -q"))
            runs.append({
                "task_id": task,
                "model": "agent-v1-old",
                "run": run,
                "steps": steps,
                "final_message": SHORT_MSG,
                "usage": {"input_tokens": 1500, "output_tokens": 900, "duration_s": 42},
                "success": not fail_flags[i],
            })
            i += 1
    return runs


def build_v2() -> list[dict]:
    """The new agent: faster/cheaper per token, but drops rules."""
    runs = []
    long_flags = [False, False, True, False, True, True, False, True, False]   # 4 long -> 56%
    print_flags = [True, True, False, True, True, True, False, False, True]     # 6 print -> 33%
    loop_flags = [False, True, False, True, False, False, True, False, False]   # 3 loops -> 67%
    ruff_flags = [True, True, False, True, False, True, True, False, True]       # 6 ruff -> 67%
    succ_flags = [True, True, False, True, False, True, True, False, True]       # 6 success -> 67%
    i = 0
    for task in TASKS:
        for run in range(3):
            steps = [bash("pip install requests")]  # violates use-uv, every run
            steps.append(edit_step(SRC[task], add_print=print_flags[i]))
            if ruff_flags[i]:
                steps.append(bash("ruff format ."))
            if loop_flags[i]:
                steps += [bash("python app.py")] * 3  # stuck, no rethink
            # note: never runs pytest -> run-tests fails every run
            runs.append({
                "task_id": task,
                "model": "agent-v2-new",
                "run": run,
                "steps": steps,
                "final_message": LONG_MSG if long_flags[i] else SHORT_MSG,
                "usage": {"input_tokens": 2600, "output_tokens": 1700, "duration_s": 31},
                "success": succ_flags[i],
            })
            i += 1
    return runs


def main() -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    (DEMO / "AGENTS.md").write_text(AGENTS_MD)

    import yaml  # local import so the generator's only hard dep is optional
    (DEMO / "keeptrue.yaml").write_text(
        yaml.safe_dump(CONFIG, sort_keys=False, allow_unicode=True, width=100)
    )

    (RUNS / "agent-v1-old.json").write_text(json.dumps(build_v1(), indent=2))
    (RUNS / "agent-v2-new.json").write_text(json.dumps(build_v2(), indent=2))
    print(f"wrote demo fixtures to {DEMO}")


if __name__ == "__main__":
    main()
