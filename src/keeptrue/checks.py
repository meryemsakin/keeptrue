"""Deterministic checks.

Each check reads one trajectory and returns pass/fail + a short piece of
evidence. These are heuristics, on purpose: a regex over the commands the
agent actually ran is reproducible, but does not prove shell semantics or rule
applicability. Missing tool results are inconclusive, not proof of compliance.
"""

from __future__ import annotations

import re
from collections import Counter

from .models import Check, CheckOutcome, Trajectory


def _commands(t: Trajectory) -> list[str]:
    return [s.command for s in t.steps if s.command]


_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1")
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "ssh"}
_PREFIXES = {"env", "sudo", "time", "exec", "nohup", "command"}


def _heredoc_reader(before: str) -> str:
    """The program receiving a heredoc: 'python' in `cd x && .venv/bin/python - <<'PY'`."""
    words = [w for w in re.split(r"&&|\|\||[;|]", before)[-1].split()
             if "=" not in w and w not in _PREFIXES]
    return words[0].rsplit("/", 1)[-1] if words else ""


def _shell_text(command: str) -> str:
    """Drop heredoc bodies fed to a non-shell program (python, cat, node, ...).

    That text is data for another program, so a command regex over it matches
    words the shell never runs. Bodies fed to a shell (bash, sh, ssh, ...) stay.
    """
    lines = command.split("\n")
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        i += 1
        opener = _HEREDOC.search(line)
        if opener and _heredoc_reader(line[:opener.start()]) not in _SHELLS:
            while i < len(lines) and lines[i].strip() != opener.group(2):
                i += 1
            i += 1  # the terminator line
    return "\n".join(kept)


def _quote(value: str, match: re.Match, field: str) -> str:
    """Quote what matched: for a diff, the matching line, not the change's first line."""
    if field != "diff":
        return value[:400]
    start = value.rfind("\n", 0, match.start()) + 1
    end = value.find("\n", match.end())
    return (value[start:] if end == -1 else value[start:end])[:400]


def _match_signal(check: Check, t: Trajectory, field: str, required: bool) -> CheckOutcome:
    """Use confirmed evidence; an errored edit may have made no or partial changes."""
    pat = re.compile(check.params["pattern"], re.MULTILINE if field == "diff" else 0)
    uncertain = False
    tools = check.params.get("tools")
    for step in t.steps:
        if tools is not None and step.tool not in tools:
            continue
        value = getattr(step, field)
        if value and field == "command":
            value = _shell_text(value)
        match = pat.search(value) if value else None
        if match is None:
            continue
        confirmed = step.result in ("recorded", "ok") or (
            field == "command" and step.exit_code is not None
        )
        if not confirmed:
            uncertain = True
            continue
        return CheckOutcome(required, f"{field} matched: {_quote(value, match, field)}")
    if uncertain:
        return CheckOutcome(None, f"unconfirmed {field} evidence; inspect the tool result")
    return CheckOutcome(not required, f"no confirmed {field} matched /{check.params['pattern']}/")


def check_forbidden_command(check: Check, t: Trajectory) -> CheckOutcome:
    return _match_signal(check, t, "command", required=False)


def check_required_command(check: Check, t: Trajectory) -> CheckOutcome:
    return _match_signal(check, t, "command", required=True)


def check_forbidden_path(check: Check, t: Trajectory) -> CheckOutcome:
    return _match_signal(check, t, "path", required=False)


def check_required_path(check: Check, t: Trajectory) -> CheckOutcome:
    return _match_signal(check, t, "path", required=True)


def check_forbidden_in_diff(check: Check, t: Trajectory) -> CheckOutcome:
    return _match_signal(check, t, "diff", required=False)


def check_required_in_diff(check: Check, t: Trajectory) -> CheckOutcome:
    return _match_signal(check, t, "diff", required=True)


def check_max_final_length(check: Check, t: Trajectory) -> CheckOutcome:
    unit = check.params.get("unit", "words")
    limit = int(check.params["limit"])
    text = t.final_message or ""
    if not text:
        return CheckOutcome(None, "no final message was recorded")
    count = len(text.split()) if unit == "words" else len(text)
    if count <= limit:
        return CheckOutcome(True, f"{count} {unit}")
    return CheckOutcome(False, f"{count} {unit} (limit {limit})")


def check_no_repeat_loops(check: Check, t: Trajectory) -> CheckOutcome:
    """Flag the agent getting stuck: the same command run k+ times."""
    k = int(check.params.get("threshold", 3))
    confirmed = [s.command for s in t.steps if s.command and (
        s.result in ("recorded", "ok") or s.exit_code is not None
    )]
    counts = Counter(confirmed)
    for cmd, n in counts.items():
        if n >= k:
            return CheckOutcome(False, f"repeated {n}×: `{cmd}`")
    if len(confirmed) < len(_commands(t)):
        return CheckOutcome(None, "some commands have no confirmed execution result")
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
        return CheckOutcome(None, f"unknown check kind: {check.kind!r}")
    try:
        return fn(check, t)
    except KeyError as e:
        return CheckOutcome(None, f"check {check.kind!r} missing param {e}")
    except re.error as e:
        return CheckOutcome(None, f"bad regex in {check.kind!r}: {e}")
    except (TypeError, ValueError) as e:
        return CheckOutcome(None, f"invalid parameter in {check.kind!r}: {e}")
