"""Adapter: Claude Code session logs -> keeptrue runs.

Claude Code writes one JSONL file per session under

    ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl

where <sanitized-cwd> is the working directory with every '/' replaced by '-'.
Each line is one event. We read the assistant events: their `tool_use` items
(the commands run and files touched) and token `usage`, plus the final
assistant text. That's everything the deterministic checks need — so you can
score your *real* sessions against your rules at zero new API cost.

The parsing is intentionally defensive (`.get` everywhere): the transcript
schema evolves, and a field we don't recognize should be skipped, not crash a
scan of 50 sessions.
"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..models import Step, Trajectory, Usage


def default_logs_dir(cwd: str | None = None) -> Path:
    """The Claude Code log directory for a working directory."""
    cwd = cwd or os.getcwd()
    sanitized = cwd.replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized


def _events(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _added(text: str) -> str:
    return "\n".join("+" + ln for ln in (text or "").splitlines())


def _diff(old: str, new: str) -> str:
    lines = ["-" + ln for ln in (old or "").splitlines()]
    lines += ["+" + ln for ln in (new or "").splitlines()]
    return "\n".join(lines)


def _tool_to_steps(name: str, inp: dict) -> list[Step]:
    """Map one Claude Code tool call to keeptrue steps (the signal the checks read)."""
    if name == "bash":
        return [Step(tool="bash", command=inp.get("command"))]
    if name == "write":
        return [Step(tool="edit", path=inp.get("file_path"), diff=_added(inp.get("content", "")))]
    if name == "edit":
        return [Step(tool="edit", path=inp.get("file_path"),
                     diff=_diff(inp.get("old_string", ""), inp.get("new_string", "")))]
    if name == "multiedit":
        out = []
        for e in inp.get("edits", []) or []:
            out.append(Step(tool="edit", path=inp.get("file_path"),
                            diff=_diff(e.get("old_string", ""), e.get("new_string", ""))))
        return out or [Step(tool="edit", path=inp.get("file_path"))]
    if name == "notebookedit":
        return [Step(tool="edit", path=inp.get("notebook_path") or inp.get("file_path"),
                     diff=_added(inp.get("new_source", "")))]
    # read / grep / glob / task / webfetch / ...: keep a path if there is one
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    return [Step(tool=name or "tool", path=path)]


def _steps_from_content(content) -> list[Step]:
    steps: list[Step] = []
    if not isinstance(content, list):
        return steps
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            name = (item.get("name") or "").lower()
            steps.extend(_tool_to_steps(name, item.get("input") or {}))
    return steps


def _final_text(content, current: str) -> str:
    if isinstance(content, list):
        texts = [it.get("text", "") for it in content
                 if isinstance(it, dict) and it.get("type") == "text"]
        return texts[-1] if texts else current
    if isinstance(content, str) and content.strip():
        return content
    return current


def _duration(times: list[str]) -> float:
    parsed = []
    for t in times:
        try:
            parsed.append(datetime.fromisoformat(str(t).replace("Z", "+00:00")))
        except (ValueError, AttributeError):
            pass
    return (max(parsed) - min(parsed)).total_seconds() if len(parsed) >= 2 else 0.0


def load_session(path: str) -> Trajectory | None:
    """Parse one session file into a Trajectory, or None if it has no signal."""
    steps: list[Step] = []
    models: Counter = Counter()
    in_tok = out_tok = 0
    final_message = ""
    session_id: str | None = None
    times: list[str] = []

    for ev in _events(path):
        session_id = session_id or ev.get("sessionId")
        if ev.get("timestamp"):
            times.append(ev["timestamp"])
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        model = msg.get("model")
        if model and model != "<synthetic>":  # Claude Code tags injected turns "<synthetic>"
            models[model] += 1
        usage = msg.get("usage") or {}
        in_tok += int(usage.get("input_tokens", 0) or 0)
        out_tok += int(usage.get("output_tokens", 0) or 0)
        content = msg.get("content")
        steps.extend(_steps_from_content(content))
        final_message = _final_text(content, final_message)

    if not steps and not final_message:
        return None

    return Trajectory(
        task_id=(session_id or Path(path).stem)[:12],
        model=models.most_common(1)[0][0] if models else "unknown",
        run=0,
        steps=steps,
        final_message=final_message,
        usage=Usage(input_tokens=in_tok, output_tokens=out_tok, duration_s=_duration(times)),
        success=None,  # a log can't tell us whether the task actually passed
    )


def all_project_files() -> list[str]:
    """Every session file across all Claude Code projects (for `scan --all-projects`)."""
    base = Path.home() / ".claude" / "projects"
    return glob.glob(str(base / "*" / "*.jsonl"))


def _iter_files(spec) -> list[str]:
    if isinstance(spec, (list, tuple)):
        return list(spec)
    return glob.glob(os.path.join(str(spec), "*.jsonl"))


def load_sessions(spec, last: int | None = None) -> list[Trajectory]:
    """Load sessions oldest-first, from a directory, a list of files, or a glob.

    Oldest-first means the earliest model becomes the report's baseline, so a
    later model shows up as a regression against it (not the other way round).
    """
    files = sorted(_iter_files(spec), key=os.path.getmtime, reverse=True)  # newest first
    if last:
        files = files[:last]
    files.reverse()  # -> oldest first

    trajs: list[Trajectory] = []
    for i, f in enumerate(files):
        t = load_session(f)
        if t is not None:
            t.task_id = f"{t.task_id}#{i}"  # keep each session distinct
            trajs.append(t)
    return trajs
