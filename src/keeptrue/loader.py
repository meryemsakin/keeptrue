"""Load recorded runs from a directory of JSON files.

Each file is either one run object or a list of them, so a whole model's runs
can live in a single file. See docs/trajectory-format.md for the schema.
"""

from __future__ import annotations

import glob
import json
import math
import os

from .models import Step, Trajectory, Usage


def _step(d: dict) -> Step:
    if not isinstance(d, dict):
        raise ValueError("each step must be an object")
    if d.get("result", "recorded") not in ("recorded", "ok", "error", "unknown"):
        raise ValueError("step result must be recorded, ok, error or unknown")
    for name in ("command", "path", "diff", "tool", "tool_use_id"):
        if d.get(name) is not None and not isinstance(d[name], str):
            raise ValueError(f"step {name} must be a string or null")
    if d.get("exit_code") is not None and type(d["exit_code"]) is not int:
        raise ValueError("exit_code must be an integer or null")
    return Step(
        type=d.get("type", "tool_call"),
        tool=d.get("tool"),
        command=d.get("command"),
        path=d.get("path"),
        diff=d.get("diff"),
        exit_code=d.get("exit_code"),
        result=d.get("result", "recorded"),
        tool_use_id=d.get("tool_use_id"),
    )


def _usage(d: dict | None) -> Usage:
    d = {} if d is None else d
    if not isinstance(d, dict):
        raise ValueError("usage must be an object or null")
    for name in ("input_tokens", "output_tokens", "duration_s"):
        value = d.get(name)
        if value is None:
            continue
        numeric = (
            type(value) in (int, float) if name == "duration_s" else type(value) is int
        )
        if not numeric or value < 0 or not math.isfinite(value):
            raise ValueError(
                f"usage {name} must be a finite nonnegative number or null"
            )
    return Usage(
        input_tokens=d.get("input_tokens"),
        output_tokens=d.get("output_tokens"),
        duration_s=d.get("duration_s"),
    )


def _traj(d: dict) -> Trajectory:
    if not isinstance(d, dict):
        raise ValueError("each run must be an object")
    for name in ("task_id", "model"):
        if not isinstance(d.get(name), str) or not d[name]:
            raise ValueError(f"run {name} must be a nonempty string")
    if d.get("success") is not None and type(d["success"]) is not bool:
        raise ValueError("success must be true, false or null")
    if not isinstance(d.get("steps", []), list):
        raise ValueError("run steps must be a list")
    if not isinstance(d.get("final_message", ""), str):
        raise ValueError("final_message must be a string")
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
