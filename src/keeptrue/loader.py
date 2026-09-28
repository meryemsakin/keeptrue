"""Load recorded runs from a directory of JSON files.

Each file is either one run object or a list of them, so a whole model's runs
can live in a single file. See docs/trajectory-format.md for the schema.
"""

from __future__ import annotations

import glob
import json
import os

from .models import Step, Trajectory, Usage


def _step(d: dict) -> Step:
    return Step(
        type=d.get("type", "tool_call"),
        tool=d.get("tool"),
        command=d.get("command"),
        path=d.get("path"),
        diff=d.get("diff"),
        exit_code=d.get("exit_code"),
    )


def _usage(d: dict | None) -> Usage:
    d = d or {}
    return Usage(
        input_tokens=int(d.get("input_tokens", 0)),
        output_tokens=int(d.get("output_tokens", 0)),
        duration_s=float(d.get("duration_s", 0.0)),
    )


def _traj(d: dict) -> Trajectory:
    return Trajectory(
        task_id=d["task_id"],
        model=d["model"],
        run=int(d.get("run", 0)),
        steps=[_step(s) for s in d.get("steps", [])],
        final_message=d.get("final_message", ""),
        usage=_usage(d.get("usage")),
        success=d.get("success"),
    )


def load_runs(path: str) -> list[Trajectory]:
    files = sorted(glob.glob(os.path.join(path, "*.json")))
    trajs: list[Trajectory] = []
    for f in files:
        with open(f) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            trajs.extend(_traj(d) for d in data)
        else:
            trajs.append(_traj(data))
    return trajs
