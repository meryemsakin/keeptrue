"""Deterministic checks.

Each check reads one trajectory and returns pass/fail + a short piece of
evidence. These are heuristics, on purpose: a regex over the commands the
agent actually ran is dumb, but it is reproducible and impossible to fudge,
which is exactly what a credibility-sensitive audience needs. Fuzzy rules that
can't be pinned to a signal like this belong to the optional LLM judge, not
here.
"""

from __future__ import annotations

import re
from collections import Counter

from .models import Check, CheckOutcome, Trajectory


def _commands(t: Trajectory) -> list[str]:
    return [s.command for s in t.steps if s.command]


def _paths(t: Trajectory) -> list[str]:
    return [s.path for s in t.steps if s.path]


def _diffs(t: Trajectory) -> list[str]:
    return [s.diff for s in t.steps if s.diff]


def check_forbidden_command(check: Check, t: Trajectory) -> CheckOutcome:
    pat = re.compile(check.params["pattern"])
    for c in _commands(t):
        if pat.search(c):
            return CheckOutcome(False, f"ran `{c}`")
    return CheckOutcome(True)


def check_required_command(check: Check, t: Trajectory) -> CheckOutcome:
    pat = re.compile(check.params["pattern"])
    for c in _commands(t):
        if pat.search(c):
            return CheckOutcome(True, f"ran `{c}`")
    return CheckOutcome(False, f"never ran anything matching /{check.params['pattern']}/")


def check_forbidden_path(check: Check, t: Trajectory) -> CheckOutcome:
    pat = re.compile(check.params["pattern"])
    for p in _paths(t):
        if pat.search(p):
            return CheckOutcome(False, f"touched `{p}`")
    return CheckOutcome(True)


def check_required_path(check: Check, t: Trajectory) -> CheckOutcome:
    pat = re.compile(check.params["pattern"])
    for p in _paths(t):
        if pat.search(p):
            return CheckOutcome(True, f"touched `{p}`")
    return CheckOutcome(False, f"never touched a path matching /{check.params['pattern']}/")


def check_forbidden_in_diff(check: Check, t: Trajectory) -> CheckOutcome:
    pat = re.compile(check.params["pattern"], re.MULTILINE)
    for d in _diffs(t):
        m = pat.search(d)
        if m:
            return CheckOutcome(False, f"added `{m.group(0).strip()}`")
    return CheckOutcome(True)


def check_required_in_diff(check: Check, t: Trajectory) -> CheckOutcome:
    pat = re.compile(check.params["pattern"], re.MULTILINE)
    for d in _diffs(t):
        if pat.search(d):
            return CheckOutcome(True)
    return CheckOutcome(False, f"no change matched /{check.params['pattern']}/")


def check_max_final_length(check: Check, t: Trajectory) -> CheckOutcome:
    unit = check.params.get("unit", "words")
    limit = int(check.params["limit"])
    text = t.final_message or ""
    count = len(text.split()) if unit == "words" else len(text)
    if count <= limit:
        return CheckOutcome(True, f"{count} {unit}")
    return CheckOutcome(False, f"{count} {unit} (limit {limit})")


def check_no_repeat_loops(check: Check, t: Trajectory) -> CheckOutcome:
    """Flag the agent getting stuck: the same command run k+ times."""
    k = int(check.params.get("threshold", 3))
    counts = Counter(_commands(t))
    for cmd, n in counts.items():
        if n >= k:
            return CheckOutcome(False, f"repeated {n}×: `{cmd}`")
    return CheckOutcome(True)


REGISTRY = {
    "forbidden_command": check_forbidden_command,
    "required_command": check_required_command,
    "forbidden_path": check_forbidden_path,
    "required_path": check_required_path,
    "forbidden_in_diff": check_forbidden_in_diff,
    "required_in_diff": check_required_in_diff,
    "max_final_length": check_max_final_length,
    "no_repeat_loops": check_no_repeat_loops,
}


def run_check(check: Check, t: Trajectory) -> CheckOutcome:
    fn = REGISTRY.get(check.kind)
    if fn is None:
        return CheckOutcome(False, f"unknown check kind: {check.kind!r}")
    try:
        return fn(check, t)
    except KeyError as e:
        return CheckOutcome(False, f"check {check.kind!r} missing param {e}")
    except re.error as e:
        return CheckOutcome(False, f"bad regex in {check.kind!r}: {e}")
