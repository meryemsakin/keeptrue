"""Run experiments/04 for one agent.

Every run gets a fresh copy of template/ with its own .venv and git repo. The
agent sees only the task prompt; the rules reach it through the repo's
AGENTS.md (Codex) and CLAUDE.md, a symlink to the same file (Claude Code).

    .venv/bin/python experiments/04-controlled-run/run.py --agent claude --reps 2
    .venv/bin/python experiments/04-controlled-run/run.py --agent codex --reps 2  # after `codex login`

Runs live outside the repo, under $KEEPTRUE_EXP_DIR (default ~/keeptrue-exp),
because their session logs are private. manifest.jsonl there records each run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template"
TASKS = json.loads((HERE / "tasks.json").read_text())
ROOT = Path(os.environ.get("KEEPTRUE_EXP_DIR", "~/keeptrue-exp")).expanduser()

# Tools are pre-approved so that a rule can actually be broken; each run
# directory is a throwaway copy of the template.
BINARIES = {  # override when the CLI on PATH is too old for the model you pin
    "claude": os.environ.get("KEEPTRUE_CLAUDE_BIN", "claude"),
    "codex": os.environ.get("KEEPTRUE_CODEX_BIN", "codex"),
}
COMMANDS = {
    "claude": lambda run_dir, prompt: [
        BINARIES["claude"], "-p", prompt, "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash",
        "--output-format", "json"],
    # `-a never` + workspace-write is what `exec --full-auto` meant before newer
    # CLIs dropped that flag. AGENTS.md discovery stays native, as shipped.
    "codex": lambda run_dir, prompt: [
        BINARIES["codex"], "-a", "never", "exec", "--sandbox", "workspace-write",
        "-C", str(run_dir), prompt],
}
MODEL_FLAG = {"claude": "--model", "codex": "-m"}


def wheel_cache() -> Path:
    """Download pytest once so each run's venv installs it offline."""
    cache = ROOT / "wheels"
    if not any(cache.glob("pytest-*.whl")):
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pip", "download", "-q", "-d", str(cache), "pytest"],
                       check=True)
    return cache


def prepare(run_dir: Path, wheels: Path) -> None:
    shutil.copytree(TEMPLATE, run_dir, symlinks=True)
    venv = run_dir / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "-q", "--no-index",
                    "--find-links", str(wheels), "pytest"], check=True)
    git = ["git", "-C", str(run_dir), "-c", "user.name=keeptrue",
           "-c", "user.email=keeptrue@example.invalid", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "initial state"]):
        subprocess.run(git + args, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--agent", choices=sorted(COMMANDS), required=True)
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--tasks", nargs="*", help="task ids to run (default: all)")
    parser.add_argument("--timeout", type=int, default=900, help="seconds per run")
    parser.add_argument("--model", help="pin the agent's model (default: the CLI's own default)")
    parser.add_argument("--stop-on-failure", action="store_true",
                        help="stop after the first run that exits non-zero (e.g. a usage limit)")
    args = parser.parse_args()

    if shutil.which(BINARIES[args.agent]) is None:
        print(f"`{BINARIES[args.agent]}` not found", file=sys.stderr)
        return 1
    tasks = [t for t in TASKS if not args.tasks or t["id"] in args.tasks]
    wheels = wheel_cache()
    manifest = ROOT / "manifest.jsonl"
    for rep in range(1, args.reps + 1):  # repetitions outermost: tasks interleave over time
        for task in tasks:
            tag = f"{args.agent}-{args.model.removeprefix('claude-')}" if args.model else args.agent
            run_dir = ROOT / "runs" / f"{tag}-{task['id']}-{rep}"
            if run_dir.exists():
                print(f"skip {run_dir.name}: already exists", flush=True)
                continue
            prepare(run_dir, wheels)
            command = COMMANDS[args.agent](run_dir, task["prompt"])
            if args.model:
                command += [MODEL_FLAG[args.agent], args.model]
            started = time.time()
            try:
                proc = subprocess.run(command, cwd=run_dir, capture_output=True, text=True,
                                      timeout=args.timeout)
                code, output = proc.returncode, proc.stdout + "\n--- stderr ---\n" + proc.stderr
            except subprocess.TimeoutExpired:
                code, output = "timeout", ""
            (run_dir.parent / f"{run_dir.name}.out").write_text(output or "")
            record = {"agent": args.agent, "model_arg": args.model, "task": task["id"], "rep": rep,
                      "dir": str(run_dir), "exit": code, "seconds": round(time.time() - started, 1),
                      "finished": datetime.now(timezone.utc).isoformat()}
            with manifest.open("a") as f:
                f.write(json.dumps(record) + "\n")
            print(f"{run_dir.name}: exit={code} in {record['seconds']}s", flush=True)
            if args.stop_on_failure and code != 0:
                print("stopping after a failed run (--stop-on-failure)", flush=True)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
