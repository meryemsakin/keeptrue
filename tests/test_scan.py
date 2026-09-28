"""Exploratory scans recover per file; curated audits stay strict."""

import json
import os

import pytest

from keeptrue.adapters import claude_code
from keeptrue.cli import main


@pytest.fixture
def sample(tmp_path):
    config = tmp_path / "keeptrue.yaml"
    config.write_text("rules:\n- id: short\n  text: Keep responses short\n  check:\n"
                      "    kind: max_final_length\n    limit: 80\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    good = logs / "good.jsonl"
    event = {"type": "assistant", "sessionId": "good-session", "message": {
        "id": "msg", "model": "good-model", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "Done."}],
    }}
    good.write_text(json.dumps(event) + "\n")
    bad = logs / "broken.jsonl"
    partial = {**event, "message": {**event["message"], "model": "partial-model"}}
    bad.write_text(json.dumps(partial) + "\n{truncated\n")
    os.utime(good, (1, 1))
    os.utime(bad, (2, 2))
    return config, logs, good, bad


@pytest.mark.parametrize("all_projects", [False, True])
def test_scan_reports_bad_file_and_scores_healthy_files(sample, capsys, monkeypatch, all_projects):
    config, logs, good, bad = sample
    monkeypatch.setattr(claude_code, "all_project_files", lambda: [str(good), str(bad)])
    selector = ["--all-projects"] if all_projects else ["--logs", str(logs)]
    assert main(["scan", "--config", str(config), *selector]) == 0
    output = capsys.readouterr()
    assert str(bad) in output.err
    assert "invalid JSONL" in output.err and ":2" in output.err
    assert "Skipped 1 unreadable or invalid file(s)" in output.out
    assert "Scored 1 real Claude Code session(s)" in output.out
    assert "good-model" in output.out
    assert "partial-model" not in output.out


def test_strict_scan_stops_without_partial_report(sample, capsys):
    config, logs, _, bad = sample
    assert main(["scan", "--config", str(config), "--logs", str(logs), "--strict"]) == 1
    output = capsys.readouterr()
    assert "session scan failed" in output.err and str(bad) in output.err
    assert "Rule adherence" not in output.out


def test_scan_with_no_usable_files_fails_and_explains(sample, capsys):
    config, _, _, bad = sample
    assert main(["scan", "--config", str(config), "--logs", str(bad)]) == 1
    output = capsys.readouterr()
    assert "warning: skipped session" in output.err
    assert "no usable sessions" in output.err and "skipped 1" in output.err
    assert "Rule adherence" not in output.out


def test_last_does_not_backfill_a_broken_newest_file(sample, capsys):
    config, logs, _, _ = sample
    assert main(["scan", "--config", str(config), "--logs", str(logs), "--last", "1"]) == 1
    assert "good-model" not in capsys.readouterr().out


def test_missing_and_unreadable_files_reported_per_file(sample, monkeypatch):
    _, _, good, bad = sample
    missing = str(bad.parent / "disappeared.jsonl")
    original = claude_code.load_session

    def read(path):
        if path == str(bad):
            raise PermissionError("cannot read selected log")
        return original(path)

    monkeypatch.setattr(claude_code, "load_session", read)
    errors = []
    runs = claude_code.load_sessions([missing, str(good), str(bad)], strict=False,
                                    on_error=lambda path, error: errors.append((path, error)))
    assert [t.model for t in runs] == ["good-model"]
    assert [p for p, _ in errors] == [missing, str(bad)]
    assert isinstance(errors[1][1], PermissionError)


def test_tolerant_api_without_callback_still_warns(sample):
    _, logs, _, _ = sample
    with pytest.warns(RuntimeWarning, match="skipping session.*broken.jsonl"):
        runs = claude_code.load_sessions(str(logs), strict=False)
    assert len(runs) == 1


def test_audit_rejects_entire_selection_on_corrupt_file(sample, capsys, tmp_path):
    config, logs, _, _ = sample
    dest = tmp_path / "audit"
    assert main(["audit", "prepare", "--config", str(config), "--logs", str(logs),
                 "--output", str(dest)]) == 1
    assert not dest.exists()
    output = capsys.readouterr()
    assert "audit preparation failed" in output.err
    assert "Prepared" not in output.out


def test_loading_remains_strict_by_default(sample):
    _, logs, _, _ = sample
    with pytest.raises(ValueError, match="invalid JSONL"):
        claude_code.load_sessions(str(logs))
