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


def _cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.output)
    if dest.exists() and not args.force:
        print(f"{dest} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    template = (_demo_dir() / "keeptrue.yaml").read_text()
    dest.write_text(template)
    print(f"wrote {dest}")
    print("Next: record some agent runs as JSON, then `keeptrue check "
          f"--config {dest} --runs ./runs`.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keeptrue", description=__doc__)
    p.add_argument("--version", action="version", version=f"keeptrue {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="score bundled example runs (no setup, no cost)")
    d.set_defaults(func=_cmd_demo)

    c = sub.add_parser("check", help="score your own runs against a keeptrue.yaml")
    c.add_argument("--config", required=True, help="path to keeptrue.yaml")
    c.add_argument("--runs", required=True, help="directory of run JSON files")
    c.set_defaults(func=_cmd_check)

    i = sub.add_parser("init", help="write a starter keeptrue.yaml")
    i.add_argument("--output", default="keeptrue.yaml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=_cmd_init)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
