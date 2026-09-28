"""The Claude Code adapter is parsed from a synthetic JSONL fixture, so this
runs in CI without any real logs. If the real transcript schema differs, adjust
`claude_code.py` and these expectations together."""

import json

from keeptrue.adapters.claude_code import load_session, load_sessions


def _write(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events))
    return str(path)


SESSION = [
    {"type": "user", "sessionId": "sess-a", "timestamp": "2026-09-28T10:00:00Z",
     "message": {"role": "user", "content": "add the endpoint"}},
    {"type": "assistant", "sessionId": "sess-a", "timestamp": "2026-09-28T10:00:05Z",
     "message": {"role": "assistant", "model": "claude-x",
                 "usage": {"input_tokens": 100, "output_tokens": 50},
                 "content": [
                     {"type": "text", "text": "working on it"},
                     {"type": "tool_use", "id": "t1", "name": "Bash",
                      "input": {"command": "pip install requests"}},
                     {"type": "tool_use", "id": "t2", "name": "Edit",
                      "input": {"file_path": "src/api.py",
                                "old_string": "x = 1",
                                "new_string": "print('debug', x)"}},
                 ]}},
    {"type": "assistant", "sessionId": "sess-a", "timestamp": "2026-09-28T10:00:40Z",
     "message": {"role": "assistant", "model": "claude-x",
                 "usage": {"input_tokens": 20, "output_tokens": 10},
                 "content": [{"type": "text", "text": "done"}]}},
]


def test_parses_commands_paths_diffs(tmp_path):
    t = load_session(_write(tmp_path / "s.jsonl", SESSION))
    assert t is not None
    assert t.model == "claude-x"
    assert "pip install requests" in [s.command for s in t.steps if s.command]
    assert "src/api.py" in [s.path for s in t.steps if s.path]
    assert "print(" in "\n".join(s.diff for s in t.steps if s.diff)


def test_final_message_and_usage_summed(tmp_path):
    t = load_session(_write(tmp_path / "s.jsonl", SESSION))
    assert t.final_message == "done"
    assert t.usage.input_tokens == 120
    assert t.usage.output_tokens == 60
    assert t.usage.duration_s == 40  # 10:00:00 -> 10:00:40


def test_empty_session_is_skipped(tmp_path):
    empty = [{"type": "system", "timestamp": "2026-09-28T10:00:00Z", "content": "boot"}]
    assert load_session(_write(tmp_path / "e.jsonl", empty)) is None


def test_load_sessions_orders_oldest_first(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _write(a, SESSION)
    _write(b, [dict(SESSION[0], sessionId="sess-b"),
               dict(SESSION[1], sessionId="sess-b",
                    message={**SESSION[1]["message"], "model": "claude-y"})])
    import os
    os.utime(a, (1, 1))          # older
    os.utime(b, (2, 2))          # newer
    trajs = load_sessions(str(tmp_path))
    assert [t.model for t in trajs] == ["claude-x", "claude-y"]  # oldest first
