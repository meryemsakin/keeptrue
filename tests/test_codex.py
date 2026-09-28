"""Codex rollouts are parsed from synthetic fixtures shaped like real
~/.codex/sessions logs; no real session content is needed in CI."""

import json

import pytest

from keeptrue.adapters import codex, detect, load_any
from keeptrue.checks import run_check
from keeptrue.cli import main
from keeptrue.models import Check

TS = "2026-09-28T10:00:{:02d}Z"


def rollout(*items, session_id="sess-1", cwd="/work/repo", model="gpt-x"):
    events = [
        {
            "timestamp": TS.format(0),
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": cwd},
        }
    ]
    if model:
        events.append(
            {
                "timestamp": TS.format(1),
                "type": "turn_context",
                "payload": {"model": model, "cwd": cwd},
            }
        )
    for i, (kind, payload) in enumerate(items, 2):
        events.append({"timestamp": TS.format(i), "type": kind, "payload": payload})
    return events


def write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return str(path)


def shell(call_id, cmd):
    return (
        "response_item",
        {
            "type": "function_call",
            "name": "exec_command",
            "call_id": call_id,
            "arguments": json.dumps({"cmd": cmd, "workdir": "/work/repo"}),
        },
    )


def shell_out(call_id, text):
    return (
        "response_item",
        {"type": "function_call_output", "call_id": call_id, "output": text},
    )


def exited(code, body="ok"):
    return f"Chunk ID: 1\nWall time: 0.1 seconds\nProcess exited with code {code}\nOutput:\n{body}"


def answer(text, phase="final_answer"):
    return (
        "response_item",
        {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": text}],
        },
    )


PATCH = """*** Begin Patch
*** Update File: src/app.py
@@ def handler
-    return x
+    print('debug', x)
+    return x
*** Add File: migrations/0002.py
+ops = []
*** Delete File: old.py
*** Update File: a.py
*** Move to: b.py
@@
-A = 1
+A = 2
*** End Patch"""


def patch(call_id):
    return (
        "response_item",
        {
            "type": "custom_tool_call",
            "name": "apply_patch",
            "call_id": call_id,
            "input": PATCH,
            "status": "completed",
        },
    )


def patch_out(call_id, code):
    return (
        "response_item",
        {
            "type": "custom_tool_call_output",
            "call_id": call_id,
            "output": json.dumps({"output": "Done.", "metadata": {"exit_code": code}}),
        },
    )


def test_exec_command_with_exit_code_is_confirmed(tmp_path):
    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(
                shell("c1", "pip install requests"),
                shell_out("c1", exited(0)),
                answer("Done."),
            ),
        )
    )
    step = t.steps[0]
    assert (step.tool, step.command, step.exit_code, step.result) == (
        "bash",
        "pip install requests",
        0,
        "ok",
    )
    assert (t.model, t.task_id, t.final_message) == ("gpt-x", "sess-1", "Done.")
    assert (
        run_check(Check("forbidden_command", {"pattern": r"\bpip install\b"}), t).passed
        is False
    )


def test_command_without_exit_evidence_is_unknown(tmp_path):
    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(
                shell("c1", "pytest -q"),
                shell_out("c1", "exec command rejected by user"),
            ),
        )
    )
    assert t.steps[0].result == "unknown"
    assert (
        run_check(Check("required_command", {"pattern": r"\bpytest\b"}), t).passed
        is None
    )


def test_nonzero_exit_still_counts_as_executed(tmp_path):
    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(shell("c1", "pytest -q"), shell_out("c1", exited(1, "1 failed"))),
        )
    )
    assert (t.steps[0].exit_code, t.steps[0].result) == (1, "error")
    assert (
        run_check(Check("required_command", {"pattern": r"\bpytest\b"}), t).passed
        is True
    )


def test_apply_patch_becomes_one_edit_per_file(tmp_path):
    t = codex.load_session(
        write(tmp_path / "r.jsonl", rollout(patch("p1"), patch_out("p1", 0)))
    )
    assert [s.path for s in t.steps] == [
        "src/app.py",
        "migrations/0002.py",
        "old.py",
        "a.py",
        "b.py",
    ]
    assert all(s.tool == "edit" and s.result == "ok" for s in t.steps)
    assert t.steps[0].diff == "-    return x\n+    print('debug', x)\n+    return x"
    assert (
        run_check(Check("forbidden_path", {"pattern": r"(^|/)migrations/"}), t).passed
        is False
    )
    out = run_check(
        Check("forbidden_in_diff", {"pattern": r"^\+.*(?<![.\w])print\("}), t
    )
    assert out.evidence == "diff matched: +    print('debug', x)"


def test_failed_patch_is_unconfirmed(tmp_path):
    t = codex.load_session(
        write(tmp_path / "r.jsonl", rollout(patch("p1"), patch_out("p1", 1)))
    )
    assert {s.result for s in t.steps} == {"error"}
    assert (
        run_check(Check("forbidden_path", {"pattern": r"(^|/)migrations/"}), t).passed
        is None
    )


def test_code_mode_exec_is_unconfirmed_evidence(tmp_path):
    js = 'text(await tools.exec_command({cmd: "pytest -q"}))'
    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(
                (
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "call_id": "x1",
                        "input": js,
                    },
                ),
                (
                    "response_item",
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "x1",
                        "output": [
                            {"type": "input_text", "text": "Wall time: 1s\n1 passed"}
                        ],
                    },
                ),
            ),
        )
    )
    assert (t.steps[0].tool, t.steps[0].command, t.steps[0].result) == (
        "exec",
        js,
        "unknown",
    )
    assert (
        run_check(Check("required_command", {"pattern": r"\bpytest\b"}), t).passed
        is None
    )


def test_final_answer_selection(tmp_path):
    phased = codex.load_session(
        write(
            tmp_path / "a.jsonl",
            rollout(
                answer("thinking out loud", "commentary"),
                answer("Done: all green."),
                answer("next turn plan", "commentary"),
                shell("c1", "ls"),
                shell_out("c1", exited(0)),
            ),
        )
    )
    assert (
        phased.final_message == "Done: all green."
    )  # later commentary and tools don't replace it
    unphased = codex.load_session(
        write(
            tmp_path / "b.jsonl",
            rollout(
                answer("I'll run it", None),
                shell("c1", "ls"),
                shell_out("c1", exited(0)),
                answer("Finished.", None),
            ),
        )
    )
    assert unphased.final_message == "Finished."
    trailing_tool = codex.load_session(
        write(
            tmp_path / "c.jsonl",
            rollout(answer("I'll run it", None), shell("c1", "ls")),
        )
    )
    assert trailing_tool.final_message == ""


def test_usage_counted_once_per_response_excluding_cached_input(tmp_path):
    def usage(rid, inp, cached, out):
        return (
            "token_usage_record",
            {
                "response_id": rid,
                "usage": {
                    "input_tokens": inp,
                    "cached_input_tokens": cached,
                    "output_tokens": out,
                },
            },
        )

    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(
                usage("r1", 100, 80, 5),
                usage("r1", 120, 80, 9),
                usage("r2", 50, 0, 3),
                answer("ok"),
            ),
        )
    )
    assert (t.usage.input_tokens, t.usage.output_tokens) == ((120 - 80) + 50, 9 + 3)
    assert t.usage.duration_s > 0


def test_missing_codex_usage_and_cache_counts_are_not_assumed_zero(tmp_path):
    t = codex.load_session(write(tmp_path / "empty.jsonl", rollout(answer("done"))))
    assert t.usage.total_tokens is None
    t = codex.load_session(
        write(
            tmp_path / "partial.jsonl",
            rollout(
                (
                    "token_usage_record",
                    {
                        "response_id": "r",
                        "usage": {"input_tokens": 100, "output_tokens": 5},
                    },
                ),
                answer("done"),
            ),
        )
    )
    assert t.usage.input_tokens is None and t.usage.output_tokens == 5


def test_models_come_from_turn_context_and_mix_is_labeled(tmp_path):
    events = rollout(answer("ok"), model="gpt-a")
    events.insert(
        2,
        {
            "timestamp": TS.format(9),
            "type": "turn_context",
            "payload": {"model": "gpt-b"},
        },
    )
    assert (
        codex.load_session(write(tmp_path / "r.jsonl", events)).model
        == "mixed: gpt-a, gpt-b"
    )


def test_format_is_detected_per_file(tmp_path):
    cx = write(tmp_path / "rollout.jsonl", rollout(answer("ok")))
    cc = write(
        tmp_path / "claude.jsonl",
        [
            {
                "type": "assistant",
                "sessionId": "s",
                "message": {
                    "id": "m",
                    "model": "claude-x",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        ],
    )
    assert (detect(cx), detect(cc)) == ("codex", "claude_code")
    assert (load_any(cx).model, load_any(cc).model) == ("gpt-x", "claude-x")


def test_project_filter_uses_recorded_cwd(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    day = home / "sessions" / "2026" / "09" / "28"
    inside = write(
        day / "rollout-a.jsonl", rollout(answer("ok"), cwd=str(repo / "sub"))
    )
    write(
        day / "rollout-b.jsonl", rollout(answer("ok"), cwd=str(tmp_path / "elsewhere"))
    )
    assert codex.project_session_files(str(repo)) == [inside]
    assert len(codex.all_session_files()) == 2


def test_malformed_rollout_is_rejected(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps(rollout()[0]) + "\n{broken\n")
    with pytest.raises(ValueError, match=":2"):
        codex.load_session(str(p))


def item(payload):
    return ("event_msg", {"type": "item_completed", "item": payload})


def command_item(item_id, script, code, status, source="unified_exec_startup"):
    return item(
        {
            "type": "CommandExecution",
            "id": item_id,
            "command": ["/bin/zsh", "-lc", script],
            "exit_code": code,
            "status": status,
            "source": source,
        }
    )


def test_execution_items_are_the_evidence_in_code_mode(tmp_path):
    js = 'text(await tools.exec_command({cmd: "pip install requests"}))'
    diff = "@@ -1 +1,2 @@\n-x = 1\n+x = 1\n+print('debug', x)"
    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(
                (
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "call_id": "x1",
                        "input": js,
                    },
                ),
                command_item("i1", "pip install requests", 0, "completed"),
                command_item("i2", "pytest -q", 1, "failed"),
                command_item("i3", "cargo test", -1, "failed"),
                item(
                    {
                        "type": "FileChange",
                        "id": "i4",
                        "status": "completed",
                        "changes": {
                            "src/app.py": {
                                "type": "update",
                                "unified_diff": diff,
                                "move_path": None,
                            },
                            "migrations/0003.py": {
                                "type": "add",
                                "content": "ops = []\n",
                            },
                        },
                    }
                ),
                answer("Done."),
            ),
        )
    )
    # The wrapper may contain other unconfirmed operations; child execution
    # items do not prove that every statement in arbitrary JavaScript ran.
    assert [s.tool for s in t.steps] == ["exec", "bash", "bash", "bash", "edit", "edit"]
    assert t.steps[0].result == "unknown"
    assert [(s.command, s.exit_code, s.result) for s in t.steps[1:4]] == [
        ("pip install requests", 0, "ok"),
        ("pytest -q", 1, "error"),
        ("cargo test", None, "unknown"),
    ]
    assert (
        run_check(Check("forbidden_command", {"pattern": r"\bpip install\b"}), t).passed
        is False
    )
    assert (
        run_check(Check("required_command", {"pattern": r"\bpytest\b"}), t).passed
        is True
    )
    assert (
        run_check(Check("required_command", {"pattern": r"\bcargo test\b"}), t).passed
        is None
    )
    assert (
        run_check(Check("forbidden_path", {"pattern": r"(^|/)migrations/"}), t).passed
        is False
    )
    out = run_check(
        Check("forbidden_in_diff", {"pattern": r"^\+.*(?<![.\w])print\("}), t
    )
    assert out.evidence == "diff matched: +print('debug', x)"


def test_commands_the_user_ran_are_not_attributed_to_the_agent(tmp_path):
    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(
                command_item(
                    "i1", "pip install x", 0, "completed", source="user_shell"
                ),
                answer("ok"),
            ),
        )
    )
    assert t.steps == []


@pytest.mark.parametrize(
    "output, expected",
    [
        ("exec command rejected by user", "unknown"),
        (exited(0), "fail"),
    ],
)
def test_execution_item_does_not_erase_another_command(tmp_path, output, expected):
    t = codex.load_session(
        write(
            tmp_path / "mixed.jsonl",
            rollout(
                shell("older-call", "pip install requests"),
                shell_out("older-call", output),
                command_item("newer-call", "ls", 0, "completed"),
            ),
        )
    )
    assert [s.command for s in t.steps] == ["pip install requests", "ls"]
    assert (
        run_check(
            Check("forbidden_command", {"pattern": r"\bpip install\b"}), t
        ).verdict
        == expected
    )


def test_file_change_does_not_erase_rejected_edit(tmp_path):
    t = codex.load_session(
        write(
            tmp_path / "mixed.jsonl",
            rollout(
                patch("rejected-patch"),
                patch_out("rejected-patch", 1),
                item(
                    {
                        "type": "FileChange",
                        "id": "other-edit",
                        "status": "completed",
                        "changes": {"README.md": {"type": "add", "content": "hello"}},
                    }
                ),
            ),
        )
    )
    assert any(s.path == "migrations/0002.py" for s in t.steps)
    assert (
        run_check(Check("forbidden_path", {"pattern": "migrations/"}), t).verdict
        == "unknown"
    )


def test_matching_execution_item_replaces_its_own_call(tmp_path):
    t = codex.load_session(
        write(
            tmp_path / "same-call.jsonl",
            rollout(
                shell("c1", "pytest -q"),
                command_item("c1", "pytest -q", 0, "completed"),
                shell_out("c1", exited(0)),
            ),
        )
    )
    assert len(t.steps) == 1
    assert (t.steps[0].tool_use_id, t.steps[0].result) == ("c1", "ok")


def test_identical_command_with_different_id_is_not_deduplicated(tmp_path):
    t = codex.load_session(
        write(
            tmp_path / "different-calls.jsonl",
            rollout(
                shell("denied", "pytest -q"),
                shell_out("denied", "rejected by user"),
                command_item("executed", "pytest -q", 0, "completed"),
            ),
        )
    )
    assert [s.result for s in t.steps] == ["unknown", "ok"]


def test_completed_child_does_not_erase_unconfirmed_code_mode_operation(tmp_path):
    js = 'text(await tools.exec_command({cmd: "pip install x"})); text(await tools.exec_command({cmd: "ls"}))'
    t = codex.load_session(
        write(
            tmp_path / "partial-code-mode.jsonl",
            rollout(
                (
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "call_id": "wrapper",
                        "input": js,
                    },
                ),
                command_item("child", "ls", 0, "completed"),
            ),
        )
    )
    assert (
        run_check(
            Check("forbidden_command", {"pattern": r"\bpip install\b"}), t
        ).verdict
        == "unknown"
    )


def test_older_rollouts_use_cumulative_token_count(tmp_path):
    def count(inp, cached, out):
        return (
            "event_msg",
            {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": inp,
                        "cached_input_tokens": cached,
                        "output_tokens": out,
                        "total_tokens": inp + out,
                    }
                },
            },
        )

    t = codex.load_session(
        write(
            tmp_path / "r.jsonl",
            rollout(count(100, 60, 10), count(300, 200, 25), answer("ok")),
        )
    )
    assert (t.usage.input_tokens, t.usage.output_tokens) == (100, 25)


CONFIG = (
    "rules:\n- id: no-pip\n  text: Never pip install\n  check:\n"
    "    kind: forbidden_command\n    pattern: '\\bpip install\\b'\n"
)


def test_scan_agent_codex_scores_this_repos_sessions(tmp_path, monkeypatch, capsys):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "keeptrue.yaml"
    config.write_text(CONFIG)
    day = home / "sessions" / "2026" / "09" / "28"
    write(
        day / "rollout-a.jsonl",
        rollout(
            shell("c1", "pip install x"),
            shell_out("c1", exited(0)),
            answer("done"),
            cwd=str(repo),
            model="gpt-6-astra",
        ),
    )
    write(
        day / "rollout-b.jsonl",
        rollout(answer("elsewhere"), cwd=str(tmp_path / "other"), model="gpt-other"),
    )
    assert (
        main(
            [
                "scan",
                "--agent",
                "codex",
                "--project",
                str(repo),
                "--config",
                str(config),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Scored 1 real Codex session(s)" in out
    assert "gpt-6-astra" in out and "gpt-other" not in out


def test_scan_all_combines_claude_and_codex_logs(tmp_path, capsys):
    logs = tmp_path / "logs"
    config = tmp_path / "keeptrue.yaml"
    config.write_text(CONFIG)
    write(
        logs / "claude.jsonl",
        [
            {
                "type": "assistant",
                "sessionId": "s",
                "message": {
                    "id": "m",
                    "model": "claude-x",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        ],
    )
    write(logs / "2026" / "rollout-a.jsonl", rollout(answer("ok"), model="gpt-y"))
    assert (
        main(["scan", "--agent", "all", "--logs", str(logs), "--config", str(config)])
        == 0
    )
    out = capsys.readouterr().out
    assert "Claude Code: 1, Codex: 1" in out
    assert "claude-x" in out and "gpt-y" in out
