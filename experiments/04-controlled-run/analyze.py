"""Score experiments/04: the keeptrue karne per model, then a task x rule table.

    .venv/bin/python experiments/04-controlled-run/analyze.py [--write-summary]

Each manifest run must map to exactly one session log: Claude Code logs by the
run directory's project folder, Codex rollouts by their recorded working
directory. --write-summary stores aggregate verdict counts in summary.json; no
commands, paths or messages leave the private run directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from keeptrue.adapters import claude_code, codex, load_any
from keeptrue.checks import run_check
from keeptrue.config import load_config
from keeptrue.engine import cost_stats, evaluate
from keeptrue.report import render

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("KEEPTRUE_EXP_DIR", "~/keeptrue-exp")).expanduser()


def session_files(record: dict) -> list[str]:
    if record["agent"] == "claude":
        return sorted(str(p) for p in claude_code.default_logs_dir(record["dir"]).glob("*.jsonl"))
    return sorted(codex.project_session_files(record["dir"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write-summary", action="store_true",
                        help="write aggregate counts to summary.json in this folder")
    args = parser.parse_args()

    records = [json.loads(line) for line in (ROOT / "manifest.jsonl").read_text().splitlines()
               if line.strip()]
    scenario, rules, prices = load_config(str(HERE / "keeptrue.yaml"))
    runs, problems = [], []
    for record in records:
        files = session_files(record)
        label = f"{record['agent']}-{record['task']}-{record['rep']}"
        if len(files) != 1:
            problems.append(f"{label}: expected one session log, found {len(files)}")
            continue
        trajectory = load_any(files[0])
        if trajectory is None or trajectory.model == "unknown":
            problems.append(f"{label}: no usable session in the log")
            continue
        trajectory.task_id = f"{record['task']}#{record['rep']}"
        runs.append((record, trajectory))
    for problem in problems:
        print(f"warning: {problem}")
    if not runs:
        print("no scorable runs yet")
        return 1

    trajectories = [t for _, t in runs]
    models, matrix = evaluate(rules, trajectories)
    render(scenario, rules, models, matrix, cost_stats(trajectories, prices),
           note=f"{len(runs)} controlled run(s); rules were given to every agent as instructions.")

    cells: dict = defaultdict(lambda: defaultdict(Counter))  # (task, agent) -> rule -> verdicts
    for record, t in runs:
        for rule in rules:
            cells[(record["task"], record["agent"])][rule.id][run_check(rule.check, t).verdict] += 1
    width = max(len(r.id) for r in rules)
    print("task x agent — passes/decidable per rule (? = unknown)\n")
    print(f"{'':24}" + "".join(f"{r.id:>{width + 2}}" for r in rules))
    for (task, agent), per_rule in sorted(cells.items()):
        row = []
        for rule in rules:
            v = per_rule[rule.id]
            cell = f"{v['pass']}/{v['pass'] + v['fail']}" + (f"?{v['unknown']}" if v["unknown"] else "")
            row.append(f"{cell:>{width + 2}}")
        print(f"{task + ' / ' + agent:24}" + "".join(row))

    if args.write_summary:
        config = (HERE / "keeptrue.yaml").read_bytes()
        summary = {
            "config_sha256": hashlib.sha256(config).hexdigest(),
            "runs": Counter(record["agent"] for record, _ in runs),
            "models": sorted({t.model for _, t in runs}),
            "problems": problems,
            "by_agent": {
                agent: {rule.id: dict(sum((cells[(task, a)][rule.id] for task, a in cells if a == agent),
                                          Counter())) for rule in rules}
                for agent in sorted({a for _, a in cells})
            },
            "by_task": {f"{task} / {agent}": {rid: dict(v) for rid, v in per_rule.items()}
                        for (task, agent), per_rule in sorted(cells.items())},
        }
        (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"\nwrote {HERE / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
