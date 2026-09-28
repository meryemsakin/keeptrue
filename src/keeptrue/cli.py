"""Command line entry point.

keeptrue demo                 # zero-setup, zero-cost: score bundled runs
keeptrue check --config c.yaml --runs ./runs
keeptrue init                 # write a starter keeptrue.yaml
"""

from __future__ import annotations

import argparse
import sys
from importlib import resources
from pathlib import Path

from . import __version__
from .config import load_config
from .engine import cost_stats, evaluate
from .loader import load_runs
from .report import render


def _demo_dir() -> Path:
    return Path(resources.files("keeptrue") / "_fixtures" / "demo")


def _run_report(
    config_path: str, runs_dir: str, note: str | None = None, console=None
) -> int:
    if not Path(config_path).exists():
        print(
            f"config not found: {config_path}\n"
            f"Run `keeptrue init` to create one, or pass --config.",
            file=sys.stderr,
        )
        return 1
    scenario, rules, prices = load_config(config_path)
    trajs = load_runs(runs_dir)
    if not trajs:
        print(f"no runs found in {runs_dir}", file=sys.stderr)
        return 1
    models, matrix = evaluate(rules, trajs)
    stats = cost_stats(trajs, prices)
    render(scenario, rules, models, matrix, stats, note=note, console=console)
    return 0


def _cmd_demo(_args: argparse.Namespace) -> int:
    d = _demo_dir()
    note = (
        "Demo data: hand-authored illustrative runs bundled with the tool, so this "
        "works offline and for free. `keeptrue check` scores your own runs."
    )
    return _run_report(str(d / "keeptrue.yaml"), str(d / "runs"), note=note)


def _cmd_check(args: argparse.Namespace) -> int:
    return _run_report(args.config, args.runs)


def _scan_selection(args: argparse.Namespace):
    """Session files (or a path) for the chosen agent(s), plus a description."""
    import glob

    from .adapters import claude_code, codex

    agents = ("claude", "codex") if args.agent == "all" else (args.agent,)
    if args.logs:
        logs = Path(args.logs).expanduser()
        if logs.is_dir() and "codex" in agents:  # Codex nests rollouts by date
            return glob.glob(str(logs / "**" / "*.jsonl"), recursive=True), str(logs)
        return str(logs), str(logs)
    files: list[str] = []
    where: list[str] = []
    if "claude" in agents:
        if args.all_projects:
            files += claude_code.all_project_files()
            where.append("all Claude Code projects")
        else:
            logs = claude_code.default_logs_dir(args.project)
            files += glob.glob(str(logs / "*.jsonl"))
            where.append(str(logs))
    if "codex" in agents:
        if args.all_projects:
            files += codex.all_session_files()
            where.append("all Codex sessions")
        else:
            files += codex.project_session_files(args.project)
            where.append(f"Codex sessions in {args.project or Path.cwd()}")
    return files, " + ".join(where)


def _cmd_scan(args: argparse.Namespace) -> int:
    from collections import Counter

    from .adapters import claude_code, codex, detect

    if not Path(args.config).exists():
        print(
            f"config not found: {args.config}\nRun `keeptrue init` to create one.",
            file=sys.stderr,
        )
        return 1
    if args.logs and not Path(args.logs).expanduser().exists():
        print(f"no session logs found at:\n  {args.logs}", file=sys.stderr)
        return 1
    if args.agent == "claude" and not (args.logs or args.all_projects):
        logs = claude_code.default_logs_dir(args.project)
        if not logs.exists():
            print(
                f"no Claude Code logs found at:\n  {logs}\n"
                f"Point --logs at your session .jsonl files, use --all-projects or "
                f"--agent codex, or run from a repo where you've used Claude Code.",
                file=sys.stderr,
            )
            return 1
    spec, where = _scan_selection(args)

    skipped = 0
    kinds: dict[int, str] = {}

    def report_skipped(path: str, exc: Exception) -> None:
        nonlocal skipped
        skipped += 1
        print(f"warning: skipped session {path}: {exc}", file=sys.stderr)

    def load(path: str):
        kind = detect(path)
        run = (codex if kind == "codex" else claude_code).load_session(path)
        if run is not None:
            kinds[id(run)] = kind
        return run

    # sessions with no identifiable model are noise (injected/degenerate) — drop them
    try:
        trajs = [
            t
            for t in claude_code.load_sessions(
                spec,
                last=args.last,
                strict=args.strict,
                on_error=report_skipped,
                loader=load,
            )
            if t.model != "unknown"
        ]
    except (OSError, ValueError) as exc:
        print(f"session scan failed: {exc}", file=sys.stderr)
        return 1
    if not trajs:
        print(
            f"no usable sessions with an identifiable model found in {where}; "
            f"skipped {skipped} unreadable or invalid file(s)",
            file=sys.stderr,
        )
        return 1

    counts = Counter(kinds.get(id(t), "claude_code") for t in trajs)
    if counts["codex"] and counts["claude_code"]:
        scored = (
            f"{len(trajs)} real session(s) — Claude Code: {counts['claude_code']}, "
            f"Codex: {counts['codex']}"
        )
    elif counts["codex"]:
        scored = f"{len(trajs)} real Codex session(s)"
    else:
        scored = f"{len(trajs)} real Claude Code session(s)"

    scenario, rules, prices = load_config(args.config)
    models, matrix = evaluate(rules, trajs)
    stats = cost_stats(trajs, prices)
    note = (
        f"Scored {scored} — no new API calls. "
        "Rule applicability is not inferred. Use `keeptrue audit` to compare "
        "with reference labels. Tokens exclude cache read/write usage; session time "
        "includes idle gaps. Mixed-model sessions are labeled explicitly."
    )
    if counts["codex"]:
        note += (
            " Codex output tokens include reasoning; code-mode `exec` JavaScript is "
            "kept as unconfirmed command evidence."
        )
    if skipped:
        note = (
            f"Skipped {skipped} unreadable or invalid file(s); results cover only "
            f"successfully loaded sessions. {note}"
        )
    render(
        scenario or "your recent coding-agent sessions",
        rules,
        models,
        matrix,
        stats,
        note=note,
    )
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.output)
    if dest.exists() and not args.force:
        print(f"{dest} already exists (use --force to overwrite)", file=sys.stderr)
        return 1

    if args.from_file:
        src = Path(args.from_file)
        if not src.exists():
            print(f"rules file not found: {src}", file=sys.stderr)
            return 1
        import yaml

        from .from_rules import propose_config

        config, matched, total = propose_config(src.read_text(), source=src.name)
        dest.write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=100)
        )
        print(
            f"wrote {dest}: proposed checks for {matched}/{total} rules from {src.name}."
        )
        gaps = [r["id"] for r in config["rules"] if "check" not in r]
        if gaps:
            shown = ", ".join(gaps[:8]) + (" …" if len(gaps) > 8 else "")
            print(
                f"{len(gaps)} rule(s) had no auto-match — add a check by hand: {shown}"
            )
        print(
            "Review the checks, then run `keeptrue scan` (or `keeptrue check --runs ./runs`)."
        )
        return 0

    template = (_demo_dir() / "keeptrue.yaml").read_text()
    dest.write_text(template)
    print(f"wrote {dest}")
    print(
        "Next: `keeptrue init --from CLAUDE.md` to derive rules, or record runs "
        "as JSON and `keeptrue check --runs ./runs`."
    )
    return 0


def _cmd_audit_prepare(args: argparse.Namespace) -> int:
    import yaml

    from .audit import prepare

    try:
        manifest = prepare(
            args.config,
            args.output,
            logs=args.logs,
            runs=args.runs,
            selection_note=args.selection_note,
        )
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"audit preparation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Prepared {manifest['runs']} {manifest['unit']}(s), {manifest['rules']} rules "
        f"in {args.output}."
    )
    if manifest["excluded_files"]:
        print(
            f"Excluded {manifest['excluded_files']} source file(s) without usable model evidence."
        )
    print(
        "Private snapshot: original transcripts and normalized runs are kept locally."
    )
    print(
        f"Read {args.output}/review.md; independently fill {args.output}/labels.json."
    )
    return 0


def _cmd_audit_report(args: argparse.Namespace) -> int:
    import json

    import yaml

    from .audit import compare, render_markdown

    try:
        result = compare(args.input)
        root = Path(args.input).expanduser()
        (root / "results.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = root / "report.md"
        report.write_text(render_markdown(result), encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"audit report failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Wrote {report} and local results.json. Reference review: {result['status']}."
    )
    if result["status"] != "complete":
        print(
            f"{result['metrics']['pending']} labels still need review; metrics are provisional."
        )
        return 2
    return 0


def _cmd_ablate_plan(args: argparse.Namespace) -> int:
    from .ablation.specification import prepare_plan

    plan = prepare_plan(args.spec, args.output)
    count = len(plan["slots"])
    print(
        f"Frozen {len(plan['tasks'])} tasks × {len(plan['units']) + 1} variants × "
        f"{plan['repetitions']} repetitions = {count} planned runs."
    )
    print(
        f"Plan: {args.output}/plan.json\nNo model calls made. "
        f"Agent time limit: {plan['timeout_s']}s/run; up to {count * plan['timeout_s'] / 60:g} minutes total."
    )
    print(
        "Review the frozen plan, then use `keeptrue ablate run --input ... --max-runs N`."
    )
    return 0


def _cmd_ablate_run(args: argparse.Namespace) -> int:
    from .ablation.execution import run_plan

    records = run_plan(
        args.input,
        args.max_runs,
        on_progress=lambda s: print(
            f"Running {s['task_id']} / {s['variant']} / repeat {s['repeat']}",
            flush=True,
        ),
    )
    print(
        f"{len(records)} slots recorded. Run `keeptrue ablate report --input {args.input}`."
    )
    return 0 if all(r["status"] == "completed" for r in records) else 2


def _cmd_ablate_report(args: argparse.Namespace) -> int:
    import json
    from .ablation.execution import read_records
    from .ablation.reporting import summarize, render_html, render_markdown
    from .ablation.specification import load_plan

    root = Path(args.input).expanduser().resolve()
    plan = load_plan(root)
    result = summarize(plan, read_records(root, plan))
    (root / "report.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (root / "report.html").write_text(render_html(result), encoding="utf-8")
    (root / "report.md").write_text(render_markdown(result), encoding="utf-8")
    print(render_markdown(result))
    print(f"Reports: {root}/report.{{json,html,md}}")
    return 0 if result["complete"] else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keeptrue", description=__doc__)
    p.add_argument("--version", action="version", version=f"keeptrue {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="score bundled example runs (no setup, no cost)")
    d.set_defaults(func=_cmd_demo)

    c = sub.add_parser("check", help="score your own runs against a keeptrue.yaml")
    c.add_argument(
        "--config",
        default="keeptrue.yaml",
        help="path to keeptrue.yaml (default: ./keeptrue.yaml)",
    )
    c.add_argument("--runs", required=True, help="directory of run JSON files")
    c.set_defaults(func=_cmd_check)

    s = sub.add_parser(
        "scan", help="score your real Claude Code / Codex sessions (no new API calls)"
    )
    s.add_argument(
        "--config",
        default="keeptrue.yaml",
        help="path to keeptrue.yaml (default: ./keeptrue.yaml)",
    )
    s.add_argument(
        "--agent",
        choices=("claude", "codex", "all"),
        default="claude",
        help="whose session logs to read (default: claude); 'all' combines both",
    )
    s.add_argument(
        "--logs",
        default=None,
        help="a session .jsonl file or directory; Claude Code vs Codex is detected per file",
    )
    s.add_argument(
        "--project",
        default=None,
        help="repo whose sessions to score (default: current dir)",
    )
    s.add_argument(
        "--all-projects",
        action="store_true",
        help="score every project's sessions for the chosen agent(s), not just this repo",
    )
    s.add_argument(
        "--last", type=int, default=None, help="only the N most recent sessions"
    )
    s.add_argument(
        "--strict",
        action="store_true",
        help="stop on the first unreadable or invalid session instead of warning and skipping",
    )
    s.set_defaults(func=_cmd_scan)

    i = sub.add_parser("init", help="write a starter keeptrue.yaml")
    i.add_argument("--output", default="keeptrue.yaml")
    i.add_argument(
        "--from",
        dest="from_file",
        default=None,
        metavar="FILE",
        help="derive checks from an AGENTS.md/CLAUDE.md (deterministic, no model)",
    )
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=_cmd_init)

    a = sub.add_parser(
        "audit", help="validate checks against independently reviewed evidence"
    )
    audit = a.add_subparsers(dest="audit_command", required=True)
    ap = audit.add_parser(
        "prepare", help="freeze selected runs and create blank reference labels"
    )
    ap.add_argument("--config", default="keeptrue.yaml")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--logs", nargs="+", help="explicit Claude Code .jsonl files or directories"
    )
    source.add_argument("--runs", help="directory containing recorded trajectory JSON")
    ap.add_argument("--output", default=".keeptrue/audits/pilot")
    ap.add_argument(
        "--selection-note", default="", help="why these sources were chosen"
    )
    ap.set_defaults(func=_cmd_audit_prepare)
    ar = audit.add_parser(
        "report", help="compare reference labels with current check predictions"
    )
    ar.add_argument("--input", default=".keeptrue/audits/pilot")
    ar.set_defaults(func=_cmd_audit_report)

    ab = sub.add_parser(
        "ablate",
        help="experimental: freeze, run and report instruction-removal experiments",
    )
    ab_sub = ab.add_subparsers(dest="ablate_command", required=True)
    plan = ab_sub.add_parser(
        "plan", help="freeze a balanced plan offline; no model calls"
    )
    plan.add_argument("--spec", required=True, help="reviewed experiment YAML")
    plan.add_argument("--output", required=True, help="new private plan directory")
    plan.set_defaults(func=_cmd_ablate_plan)
    run = ab_sub.add_parser(
        "run", help="execute up to N pending slots; consumes model quota"
    )
    run.add_argument("--input", required=True, help="frozen plan directory")
    run.add_argument(
        "--max-runs",
        type=int,
        required=True,
        help="cap new agent launches in this invocation",
    )
    run.set_defaults(func=_cmd_ablate_run)
    report = ab_sub.add_parser(
        "report", help="write aggregate JSON, Markdown and offline HTML"
    )
    report.add_argument("--input", required=True, help="frozen plan directory")
    report.set_defaults(func=_cmd_ablate_report)

    return p


def main(argv: list[str] | None = None) -> int:
    import subprocess
    import yaml

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, yaml.YAMLError, subprocess.SubprocessError) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
