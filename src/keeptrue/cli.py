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


def _run_report(config_path: str, runs_dir: str, note: str | None = None, console=None) -> int:
    if not Path(config_path).exists():
        print(f"config not found: {config_path}\n"
              f"Run `keeptrue init` to create one, or pass --config.", file=sys.stderr)
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
        "Demo data: recorded/illustrative runs bundled with the tool, so this "
        "works offline and for free. `keeptrue check` scores your own runs."
    )
    return _run_report(str(d / "keeptrue.yaml"), str(d / "runs"), note=note)


def _cmd_check(args: argparse.Namespace) -> int:
    return _run_report(args.config, args.runs)


def _cmd_scan(args: argparse.Namespace) -> int:
    from .adapters.claude_code import all_project_files, default_logs_dir, load_sessions

    if not Path(args.config).exists():
        print(f"config not found: {args.config}\nRun `keeptrue init` to create one.",
              file=sys.stderr)
        return 1

    if args.all_projects:
        spec: object = all_project_files()
        where = "all Claude Code projects"
    else:
        logs = args.logs or str(default_logs_dir(args.project))
        if not Path(logs).exists():
            print(f"no Claude Code logs found at:\n  {logs}\n"
                  f"Point --logs at your session .jsonl files, use --all-projects, "
                  f"or run from a repo where you've used Claude Code.", file=sys.stderr)
            return 1
        spec, where = logs, logs

    # sessions with no identifiable model are noise (injected/degenerate) — drop them
    trajs = [t for t in load_sessions(spec, last=args.last) if t.model != "unknown"]
    if not trajs:
        print(f"no sessions with an identifiable model found in {where}", file=sys.stderr)
        return 1

    scenario, rules, prices = load_config(args.config)
    models, matrix = evaluate(rules, trajs)
    stats = cost_stats(trajs, prices)
    note = (f"Scored {len(trajs)} real Claude Code session(s) — no new API calls. "
            "Caveat: a rule that didn't apply to a session still counts as a miss "
            "here; tagging default-conflicting rules is on the roadmap.")
    render(scenario or "your recent Claude Code sessions",
           rules, models, matrix, stats, note=note)
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
        dest.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=100))
        print(f"wrote {dest}: proposed checks for {matched}/{total} rules from {src.name}.")
        gaps = [r["id"] for r in config["rules"] if "check" not in r]
        if gaps:
            shown = ", ".join(gaps[:8]) + (" …" if len(gaps) > 8 else "")
            print(f"{len(gaps)} rule(s) had no auto-match — add a check by hand: {shown}")
        print("Review the checks, then run `keeptrue scan` (or `keeptrue check --runs ./runs`).")
        return 0

    template = (_demo_dir() / "keeptrue.yaml").read_text()
    dest.write_text(template)
    print(f"wrote {dest}")
    print("Next: `keeptrue init --from CLAUDE.md` to derive rules, or record runs "
          "as JSON and `keeptrue check --runs ./runs`.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keeptrue", description=__doc__)
    p.add_argument("--version", action="version", version=f"keeptrue {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="score bundled example runs (no setup, no cost)")
    d.set_defaults(func=_cmd_demo)

    c = sub.add_parser("check", help="score your own runs against a keeptrue.yaml")
    c.add_argument("--config", default="keeptrue.yaml",
                   help="path to keeptrue.yaml (default: ./keeptrue.yaml)")
    c.add_argument("--runs", required=True, help="directory of run JSON files")
    c.set_defaults(func=_cmd_check)

    s = sub.add_parser("scan", help="score your real Claude Code sessions (no new API calls)")
    s.add_argument("--config", default="keeptrue.yaml",
                   help="path to keeptrue.yaml (default: ./keeptrue.yaml)")
    s.add_argument("--logs", default=None,
                   help="dir of session .jsonl files (default: Claude Code logs for this cwd)")
    s.add_argument("--project", default=None,
                   help="cwd whose Claude Code logs to score (default: current dir)")
    s.add_argument("--all-projects", action="store_true",
                   help="score sessions across every Claude Code project, not just this repo")
    s.add_argument("--last", type=int, default=None, help="only the N most recent sessions")
    s.set_defaults(func=_cmd_scan)

    i = sub.add_parser("init", help="write a starter keeptrue.yaml")
    i.add_argument("--output", default="keeptrue.yaml")
    i.add_argument("--from", dest="from_file", default=None, metavar="FILE",
                   help="derive checks from an AGENTS.md/CLAUDE.md (deterministic, no model)")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=_cmd_init)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
