"""The Claude Code adapter is parsed from a synthetic JSONL fixture, so this
runs in CI without any real logs. If the real transcript schema differs, adjust
`claude_code.py` and these expectations together."""

import json

import pytest

from keeptrue.adapters.claude_code import load_session, load_sessions


def _write(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events))
    return str(path)


def test_missing_or_partial_claude_usage_stays_unknown(tmp_path):
    def assistant(mid, usage):
        return {
            "type": "assistant",
            "message": {
                "id": mid,
                "model": "claude-example",
                "usage": usage,
                "content": [{"type": "text", "text": "Done"}],
            },
        }

    t = load_session(
        _write(
            tmp_path / "partial.jsonl",
            [
                assistant("a", {"input_tokens": 3, "output_tokens": 5}),
                assistant("b", {}),
            ],
        )
    )
    assert t.usage.input_tokens is None and t.usage.output_tokens is None
    assert t.usage.duration_s is None
    t = load_session(
        _write(
            tmp_path / "complete.jsonl",
            [
                assistant("a", {}),
                assistant("a", {"input_tokens": 0, "output_tokens": 0}),
            ],
        )
    )
    assert t.usage.total_tokens == 0


SESSION = [
    {
        "type": "user",
        "sessionId": "sess-a",
        "timestamp": "2026-09-28T10:00:00Z",
        "message": {"role": "user", "content": "add the endpoint"},
    },
    {
        "type": "assistant",
        "sessionId": "sess-a",
        "timestamp": "2026-09-28T10:00:05Z",
        "message": {
            "role": "assistant",
            "model": "claude-x",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [
                {"type": "text", "text": "working on it"},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install requests"},
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Edit",
                    "input": {
                        "file_path": "src/api.py",
                        "old_string": "x = 1",
                        "new_string": "print('debug', x)",
                    },
                },
            ],
        },
    },
    {
        "type": "assistant",
        "sessionId": "sess-a",
        "timestamp": "2026-09-28T10:00:40Z",
        "message": {
            "role": "assistant",
            "model": "claude-x",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "content": [{"type": "text", "text": "done"}],
        },
    },
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
    _write(
        b,
        [
            dict(SESSION[0], sessionId="sess-b"),
            dict(
                SESSION[1],
                sessionId="sess-b",
                message={**SESSION[1]["message"], "model": "claude-y"},
            ),
        ],
    )
    import os

    os.utime(a, (1, 1))  # older
    os.utime(b, (2, 2))  # newer
    trajs = load_sessions(str(tmp_path))
    assert [t.model for t in trajs] == ["claude-x", "claude-y"]  # oldest first


def test_tool_results_usage_dedup_and_mixed_models(tmp_path):
    first = {
        "type": "assistant",
        "sessionId": "full-stable-session-id",
        "message": {
            "id": "msg-a",
            "model": "a",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 100, "output_tokens": 5},
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool-a",
                    "name": "Bash",
                    "input": {"command": "pytest"},
                }
            ],
        },
    }
    updated = {
        **first,
        "message": {
            **first["message"],
            "usage": {"input_tokens": 100, "output_tokens": 15},
        },
    }
    result = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-a",
                    "is_error": True,
                    "content": "Exit code 1\nassertion failed",
                }
            ]
        },
    }
    final = {
        "type": "assistant",
        "message": {
            "id": "msg-b",
            "model": "b",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "content": [
                {"type": "text", "text": "Tests failed."},
                {"type": "text", "text": "Investigate."},
            ],
        },
    }
    t = load_session(_write(tmp_path / "s.jsonl", [first, updated, result, final]))
    assert len(t.steps) == 1
    assert (t.steps[0].result, t.steps[0].exit_code) == ("error", 1)
    assert t.task_id == "full-stable-session-id"
    assert t.model == "mixed: a, b"
    assert (t.usage.input_tokens, t.usage.output_tokens) == (120, 25)
    assert t.final_message == "Tests failed.\n\nInvestigate."


def test_later_tool_call_does_not_reuse_old_final_message(tmp_path):
    events = [SESSION[-1], SESSION[1]]
    t = load_session(_write(tmp_path / "s.jsonl", events))
    assert t.final_message == ""
    assert all(s.result == "unknown" for s in t.steps)


def test_rejected_edit_keeps_request_but_marks_result_error(tmp_path):
    result = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "is_error": True,
                    "content": "Permission denied before execution",
                }
            ]
        },
    }
    t = load_session(_write(tmp_path / "s.jsonl", [SESSION[1], result]))
    assert t.steps[1].result == "error"
    assert t.steps[0].result == "unknown"


def test_malformed_log_is_not_silently_scored(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(SESSION[1]) + "\n{broken\n")
    with pytest.raises(ValueError, match=":2"):
        load_session(str(p))


def test_synthetic_only_session_is_ignored(tmp_path):
    event = {
        "type": "assistant",
        "message": {
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "injected error"}],
        },
    }
    assert load_session(_write(tmp_path / "s.jsonl", [event])) is None
    t = load_session(_write(tmp_path / "real.jsonl", [*SESSION, event]))
    assert t.final_message == "done"


def test_identity_stable_across_last_selection(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write(a, SESSION)
    _write(b, [{**SESSION[1], "sessionId": "sess-b"}])
    import os

    os.utime(a, (1, 1))
    os.utime(b, (2, 2))
    assert (
        load_sessions(str(tmp_path), last=1)[0].task_id
        == load_sessions(str(tmp_path))[-1].task_id
    )


def test_missing_tool_ids_cannot_confirm_each_other(tmp_path):
    events = [
        {
            "type": "assistant",
            "message": {
                "model": "a",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "pytest"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "ok"}]},
        },
    ]
    t = load_session(_write(tmp_path / "s.jsonl", events))
    assert t.steps[0].result == "unknown"
    assert t.steps[0].exit_code is None


def test_streamed_final_message_replaces_partial_snapshot(tmp_path):
    from keeptrue.checks import run_check
    from keeptrue.models import Check

    def snapshot(content):
        return {
            "type": "assistant",
            "message": {
                "id": "final-1",
                "model": "claude-x",
                "stop_reason": "end_turn",
                "content": content,
            },
        }

    t = load_session(
        _write(
            tmp_path / "streamed.jsonl",
            [
                snapshot([{"type": "text", "text": "Tests"}]),
                snapshot(
                    [
                        {"type": "text", "text": "Tests passed."},
                        {"type": "text", "text": "All done."},
                    ]
                ),
                snapshot([]),  # a later usage-only snapshot must not erase the text
            ],
        )
    )
    assert t.final_message == "Tests passed.\n\nAll done."
    assert run_check(Check("max_final_length", {"limit": 4}), t).passed is True


def test_revised_final_snapshot_replaces_old_wording(tmp_path):
    events = [
        {
            "type": "assistant",
            "message": {
                "id": "final-1",
                "model": "claude-x",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": text}],
            },
        }
        for text in ("The old draft.", "Final answer.")
    ]
    assert (
        load_session(_write(tmp_path / "revised.jsonl", events)).final_message
        == "Final answer."
    )
