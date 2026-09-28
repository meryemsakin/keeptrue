"""Adapter: Claude Code session logs -> keeptrue runs.

Claude Code writes one JSONL file per session under

    ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl

where <sanitized-cwd> is the working directory with every '/' replaced by '-'.
Each line is one event. Assistant tool calls are joined to user tool results;
unconfirmed calls remain unknown. Message IDs deduplicate streaming usage.
Malformed JSON is rejected instead of silently dropping potential violations.
This is observed tool evidence, not a filesystem diff or a task-success oracle.
"""

from __future__ import annotations

import glob
import json
import os
import re
import warnings
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..models import Step, Trajectory, Usage


def default_logs_dir(cwd: str | None = None) -> Path:
    """The Claude Code log directory for a working directory."""
    cwd = cwd or os.getcwd()
    sanitized = cwd.replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized


def _events(path: str):
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{lineno}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"expected an event object at {path}:{lineno}")
            yield event


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
            inp = item.get("input") or {}
            if not isinstance(inp, dict):
                raise ValueError("tool_use input must be an object")
            converted = _tool_to_steps(name, inp)
            for step in converted:
                step.tool_use_id = item.get("id")
                if step.tool_use_id is not None and not isinstance(step.tool_use_id, str):
                    raise ValueError("tool_use id must be a string")
                step.result = "unknown"
            steps.extend(converted)
    return steps


def _final_text(content, current: str) -> str:
    if isinstance(content, list):
        texts = [it.get("text", "") for it in content
                 if isinstance(it, dict) and it.get("type") == "text"]
        return "\n\n".join(texts) if texts else current
    if isinstance(content, str) and content.strip():
        return content
    return current


def _duration(times: list[str]) -> float:
    parsed = []
    for t in times:
        try:
            dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            parsed.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
        except (ValueError, AttributeError):
            pass
    return (max(parsed) - min(parsed)).total_seconds() if len(parsed) >= 2 else 0.0


def load_session(path: str) -> Trajectory | None:
    """Parse one session file into a Trajectory, or None if it has no signal."""
    steps: list[Step] = []
    models: Counter = Counter()
    usage_by_message: dict[str, dict] = {}
    results: dict[str, dict] = {}
    seen_tools: set[str] = set()
    final_message = ""
    final_key = None
    final_texts: list[str] = []
    final_stop = None
    final_has_tools = False
    session_id: str | None = None
    times: list[str] = []

    for index, ev in enumerate(_events(path)):
        session_id = session_id or ev.get("sessionId")
        if ev.get("timestamp"):
            times.append(ev["timestamp"])
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if ev.get("type") == "user" and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    ident = item.get("tool_use_id")
                    if isinstance(ident, str) and ident:
                        results[ident] = item
        if ev.get("type") != "assistant" or msg.get("model") == "<synthetic>":
            continue
        model = msg.get("model")
        if model and model != "<synthetic>":  # Claude Code tags injected turns "<synthetic>"
            models[model] += 1
        # Streaming records repeat message-level usage. Count each message once,
        # retaining the greatest observed counter for partial usage snapshots.
        key = msg.get("id") or f"record-{index}"
        if key != final_key:
            final_key = key
            final_texts = []
            final_stop = None
            final_has_tools = False
        if msg.get("stop_reason"):
            final_stop = msg["stop_reason"]
        text = _final_text(content, "")
        if text and text not in final_texts:
            final_texts.append(text)
        usage = usage_by_message.setdefault(key, {})
        for field in ("input_tokens", "output_tokens"):
            usage[field] = max(usage.get(field, 0), int((msg.get("usage") or {}).get(field, 0) or 0))
        if isinstance(content, list):
            fresh = []
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                final_has_tools = True
                ident = item.get("id")
                if ident and ident in seen_tools:
                    continue
                if ident:
                    seen_tools.add(ident)
                fresh.append(item)
            steps.extend(_steps_from_content(fresh))

    if final_stop != "tool_use" and not final_has_tools:
        final_message = "\n\n".join(final_texts)

    for step in steps:
        result = results.get(step.tool_use_id)
        if result is None:
            continue
        step.result = "error" if result.get("is_error") else "ok"
        if step.tool == "bash":
            body = result.get("content")
            if isinstance(body, str):
                match = re.match(r"Exit code[: ]+(-?\d+)\b", body)
                if match:
                    step.exit_code = int(match.group(1))
            if step.exit_code is None and step.result == "ok":
                step.exit_code = 0

    if not steps and not final_message:
        return None

    return Trajectory(
        task_id=session_id or Path(path).stem,
        model=(next(iter(models)) if len(models) == 1 else "mixed: " + ", ".join(sorted(models)))
        if models else "unknown",
        run=0,
        steps=steps,
        final_message=final_message,
        usage=Usage(
            input_tokens=sum(u.get("input_tokens", 0) for u in usage_by_message.values()),
            output_tokens=sum(u.get("output_tokens", 0) for u in usage_by_message.values()),
            duration_s=_duration(times),
        ),
        success=None,  # a log can't tell us whether the task actually passed
    )


def all_project_files() -> list[str]:
    """Every session file across all Claude Code projects (for `scan --all-projects`)."""
    base = Path.home() / ".claude" / "projects"
    return glob.glob(str(base / "*" / "*.jsonl"))


def _iter_files(spec) -> list[str]:
    if isinstance(spec, (list, tuple)):
        return list(spec)
    if Path(spec).is_file():
        return [str(spec)]
    return glob.glob(os.path.join(str(spec), "*.jsonl"))


def load_sessions(
    spec,
    last: int | None = None,
    *,
    strict: bool = True,
    on_error: Callable[[str, Exception], None] | None = None,
) -> list[Trajectory]:
    """Load sessions by file mtime, from a file, a directory, or a list of files.

    Mtime is only a display ordering, not evidence of a model upgrade.
    Strict loading is the default for curated audits. Exploratory scans may
    skip invalid files, reporting every failure through on_error or a warning.
    --last selects the newest files before parsing; skipped files are not replaced.
    """
    if last is not None and last < 1:
        raise ValueError("--last must be a positive integer")
    def failed(path: str, exc: Exception) -> None:
        if strict:
            raise exc
        if on_error is not None:
            on_error(path, exc)
        else:
            warnings.warn(f"skipping session {path}: {exc}", RuntimeWarning, stacklevel=3)

    candidates = []
    for path in _iter_files(spec):
        try:
            candidates.append((os.path.getmtime(path), path))
        except OSError as exc:
            failed(path, exc)
    files = [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)]
    if last:
        files = files[:last]
    files.reverse()  # -> oldest first

    trajs: list[Trajectory] = []
    for f in files:
        try:
            t = load_session(f)
        except (OSError, ValueError) as exc:
            failed(f, exc)
            continue
        if t is not None:
            trajs.append(t)
    return trajs
