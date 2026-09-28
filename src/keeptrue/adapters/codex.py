"""Adapter: Codex CLI session logs -> keeptrue runs.

Codex writes one JSONL "rollout" per session under

    $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl   (CODEX_HOME: ~/.codex)

Every line is {timestamp, type, payload}; the first is always `session_meta`.
What we read, and how far we trust it:

- `turn_context.model` names the model per turn; a session that used several
  models is labeled mixed.
- Newer rollouts log every execution as an `item_completed` record.
  `CommandExecution` items carry the argv, exit code and status: a
  non-negative exit code is execution evidence (nonzero = ran and failed); a
  negative one (killed or never started) stays unknown; commands whose source
  names the user are not attributed to the agent. `FileChange` items carry a
  per-path unified diff or full content. When a session has these items they
  are the evidence, because code mode runs everything inside JavaScript.
- Older rollouts have no such items. There, `exec_command` calls are shell
  commands ("Process exited with code N" or a JSON `metadata.exit_code` in the
  output is execution evidence; otherwise unknown), `apply_patch` calls become
  one edit per file, and code-mode `exec` JavaScript is kept as *unconfirmed*
  command evidence, since a nested command can't be confirmed from the log.
- The final answer is the last assistant message with phase "final_answer";
  in older, unphased logs, the last assistant message not followed by a tool call.
- Tokens come from `token_usage_record.usage`, counted once per response_id,
  or from the cumulative `token_count` total in older rollouts. Input excludes
  cached input (Codex counts it inside input_tokens), matching the Claude Code
  adapter; output includes reasoning tokens.

In older rollouts, follow-ups to a running process (`write_stdin`, `wait`) are
not joined back to the command that started it, so such commands stay unknown.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter
from pathlib import Path

from ..models import Step, Trajectory, Usage
from ._jsonl import duration, read_events

_EXITED = re.compile(r"Process exited with code (-?\d+)")
_FILE_HEADER = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
_MOVE_TO = re.compile(r"^\*\*\* Move to: (.+)$")


def sessions_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"


def all_session_files(root: str | Path | None = None) -> list[str]:
    """Every rollout under the Codex sessions directory (nested by date)."""
    base = Path(root) if root else sessions_dir()
    return glob.glob(str(base / "**" / "rollout-*.jsonl"), recursive=True)


def _first_event(path: str) -> dict | None:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return None
            return event if isinstance(event, dict) else None
    return None


def is_codex_log(path: str) -> bool:
    """Codex rollouts always open with a session_meta event."""
    try:
        first = _first_event(path)
    except OSError:
        return False
    return bool(first) and first.get("type") == "session_meta"


def session_cwd(path: str) -> str | None:
    try:
        first = _first_event(path)
    except OSError:
        return None
    if first and first.get("type") == "session_meta" and isinstance(first.get("payload"), dict):
        return first["payload"].get("cwd")
    return None


def project_session_files(project: str | None = None,
                          root: str | Path | None = None) -> list[str]:
    """Sessions whose recorded working directory is the project or inside it."""
    target = Path(project or os.getcwd()).expanduser().resolve()
    selected = []
    for path in all_session_files(root):
        cwd = session_cwd(path)
        if not cwd:
            continue
        try:
            Path(cwd).expanduser().resolve().relative_to(target)
        except ValueError:
            continue
        selected.append(path)
    return selected


def _signed(text: str, sign: str) -> str:
    return "\n".join(sign + line for line in (text or "").splitlines())


def _patch_steps(patch: str) -> list[Step]:
    """One edit per file in an apply_patch envelope; the diff is its +/- lines."""
    steps: list[Step] = []
    path: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if path is not None:
            steps.append(Step(tool="edit", path=path, diff="\n".join(lines)))

    for line in (patch or "").splitlines():
        header = _FILE_HEADER.match(line)
        move = _MOVE_TO.match(line) if path is not None else None
        if header or move:
            flush()
            path, lines = (header.group(2) if header else move.group(1)).strip(), []
        elif path is not None and not line.startswith("***") and line[:1] in ("+", "-"):
            lines.append(line)
    flush()
    return steps


def _call_steps(item: dict) -> list[Step]:
    name = item.get("name") or ""
    if item.get("type") == "function_call":
        if name == "exec_command":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("exec_command arguments are not valid JSON") from exc
            cmd = args.get("cmd") if isinstance(args, dict) else None
            if isinstance(cmd, list):
                cmd = " ".join(map(str, cmd))
            return [Step(tool="bash", command=cmd if isinstance(cmd, str) else None)]
        return [Step(tool=name or "tool")]  # write_stdin, wait, js, request_user_input, ...
    source = item.get("input") if isinstance(item.get("input"), str) else ""
    if name == "apply_patch":
        return _patch_steps(source) or [Step(tool="edit")]
    if name == "exec":
        return [Step(tool="exec", command=source)]  # code-mode JavaScript; never confirmed
    return [Step(tool=name or "tool")]


def _argv_text(command) -> str | None:
    """Show the script behind a `zsh -lc '...'` wrapper; otherwise join the argv."""
    if isinstance(command, str):
        return command
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        if len(command) >= 3 and command[-2] in ("-c", "-lc"):
            return command[-1]
        return " ".join(command)
    return None


def _change_steps(path: str, change: dict) -> list[Step]:
    if "unified_diff" in change:
        diff = "\n".join(line for line in str(change["unified_diff"]).splitlines()
                         if line[:1] in ("+", "-") and not line.startswith(("+++", "---")))
    elif "content" in change:
        diff = _signed(str(change["content"] or ""), "-" if change.get("type") == "delete" else "+")
    else:
        diff = ""
    move = change.get("move_path")
    if not move:
        return [Step(tool="edit", path=path, diff=diff)]
    return [Step(tool="edit", path=path, diff=""), Step(tool="edit", path=str(move), diff=diff)]


def _item_steps(item: dict) -> list[Step]:
    """Steps from a newer rollout's item_completed record."""
    if item.get("type") == "CommandExecution":
        if "user" in str(item.get("source") or "").lower():
            return []  # the person ran it, not the agent
        step = Step(tool="bash", command=_argv_text(item.get("command")), result="unknown",
                    tool_use_id=item.get("id"))
        code = item.get("exit_code")
        if type(code) is int and code >= 0 and item.get("status") in ("completed", "failed"):
            step.exit_code = code
            step.result = "ok" if code == 0 else "error"
        return [step]
    if item.get("type") == "FileChange" and isinstance(item.get("changes"), dict):
        status = item.get("status")
        result = "ok" if status == "completed" else "error" if status == "failed" else "unknown"
        steps = []
        for path, change in item["changes"].items():
            steps.extend(_change_steps(str(path), change if isinstance(change, dict) else {}))
        for step in steps:
            step.result, step.tool_use_id = result, item.get("id")
        return steps
    return []


def _output_text(output) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(str(part.get("text", "")) for part in output if isinstance(part, dict))
    return json.dumps(output) if isinstance(output, dict) else ""


def _exit_code(output) -> int | None:
    """Execution evidence: JSON metadata.exit_code, else 'Process exited with code N'."""
    data = output
    if isinstance(output, str) and output.lstrip().startswith("{"):
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict):
        meta = data.get("metadata")
        if isinstance(meta, dict) and type(meta.get("exit_code")) is int:
            return meta["exit_code"]
    match = _EXITED.search(_output_text(output))
    return int(match.group(1)) if match else None


def _total(usage: dict) -> int:
    return int(usage.get("total_tokens", 0) or 0) if isinstance(usage, dict) else 0


def load_session(path: str) -> Trajectory | None:
    """Parse one Codex rollout into a Trajectory, or None if it has no signal."""
    # (event index, step) per evidence source; execution items win when present
    call_cmds: list[tuple[int, Step]] = []
    call_edits: list[tuple[int, Step]] = []
    other: list[tuple[int, Step]] = []
    item_cmds: list[tuple[int, Step]] = []
    item_edits: list[tuple[int, Step]] = []
    by_call: dict[str, list[Step]] = {}
    seen_items: set[str] = set()
    models: Counter = Counter()
    usage: dict[str, dict[str, int]] = {}
    totals: dict = {}  # cumulative token_count, for rollouts without usage records
    final_message = ""
    tentative: str | None = None  # an unphased message, final unless a tool call follows
    session_id: str | None = None
    times: list[str] = []

    for index, event in enumerate(read_events(path)):
        if event.get("timestamp"):
            times.append(event["timestamp"])
        kind, payload = event.get("type"), event.get("payload")
        if not isinstance(payload, dict):
            continue
        if kind == "session_meta":
            session_id = session_id or payload.get("id") or payload.get("session_id")
        elif kind == "turn_context":
            if payload.get("model"):
                models[payload["model"]] += 1
        elif kind == "token_usage_record":
            counts = payload.get("usage") or {}
            seen = usage.setdefault(payload.get("response_id") or f"record-{index}", {})
            for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
                seen[field] = max(seen.get(field, 0), int(counts.get(field, 0) or 0))
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            total = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(total, dict) and _total(total) >= _total(totals):
                totals = total
        elif kind == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("id") in seen_items:
                continue
            if item.get("id"):
                seen_items.add(item["id"])
            for step in _item_steps(item):
                (item_cmds if step.tool == "bash" else item_edits).append((index, step))
        elif kind == "response_item":
            item_type = payload.get("type")
            if item_type in ("function_call", "custom_tool_call"):
                tentative = None
                call_id = payload.get("call_id")
                if call_id and call_id in by_call:
                    continue  # replayed item
                new = _call_steps(payload)
                for step in new:
                    step.tool_use_id = call_id
                    step.result = "unknown"
                    bucket = (call_cmds if step.tool in ("bash", "exec")
                              else call_edits if step.tool == "edit" else other)
                    bucket.append((index, step))
                if call_id:
                    by_call[call_id] = new
            elif item_type in ("function_call_output", "custom_tool_call_output"):
                for step in by_call.get(payload.get("call_id"), []):
                    if step.tool not in ("bash", "edit"):
                        continue
                    code = _exit_code(payload.get("output"))
                    if code is not None:
                        step.exit_code = code
                        step.result = "ok" if code == 0 else "error"
            elif item_type == "message" and payload.get("role") == "assistant":
                text = "\n\n".join(
                    str(part.get("text", "")) for part in payload.get("content") or []
                    if isinstance(part, dict) and part.get("type") == "output_text"
                )
                if payload.get("phase") == "final_answer":
                    final_message, tentative = text, None
                elif payload.get("phase") is None and text:
                    tentative = text

    if tentative:
        final_message = tentative
    evidence = (item_cmds or call_cmds) + (item_edits or call_edits) + other
    steps = [step for _, step in sorted(evidence, key=lambda pair: pair[0])]
    if not steps and not final_message:
        return None

    if not models:
        model = "unknown"
    elif len(models) == 1:
        model = next(iter(models))
    else:
        model = "mixed: " + ", ".join(sorted(models))

    if usage:
        input_tokens = sum(max(0, u.get("input_tokens", 0) - u.get("cached_input_tokens", 0))
                           for u in usage.values())
        output_tokens = sum(u.get("output_tokens", 0) for u in usage.values())
    else:
        input_tokens = max(0, int(totals.get("input_tokens", 0) or 0)
                           - int(totals.get("cached_input_tokens", 0) or 0))
        output_tokens = int(totals.get("output_tokens", 0) or 0)

    return Trajectory(
        task_id=session_id or Path(path).stem,
        model=model,
        run=0,
        steps=steps,
        final_message=final_message,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens,
                    duration_s=duration(times)),
        success=None,  # a log can't tell us whether the task actually passed
    )
