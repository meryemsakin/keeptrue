"""JSONL helpers shared by the session-log adapters."""

from __future__ import annotations

import json
from datetime import datetime, timezone


def read_events(path: str):
    """Yield one dict per non-empty line; malformed lines raise instead of vanishing."""
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


def duration(times: list[str]) -> float:
    """Seconds between the earliest and latest parseable ISO timestamps."""
    parsed = []
    for t in times:
        try:
            dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            parsed.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
        except (ValueError, AttributeError):
            pass
    return (max(parsed) - min(parsed)).total_seconds() if len(parsed) >= 2 else 0.0
