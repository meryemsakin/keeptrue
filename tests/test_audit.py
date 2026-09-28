"""Reference review must detect errors without creating its own ground truth."""

import json

import pytest
import yaml

from keeptrue.audit import compare, prepare, render_markdown
from keeptrue.cli import main


@pytest.fixture
def audit_inputs(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"rules": [{
        "id": "test", "text": "Run pytest", "check": {
            "kind": "required_command", "pattern": r"\bpytest\b",
        },
    }]}))
    runs = tmp_path / "runs"
    runs.mkdir()
    entries = []
    # tp, fp, fn, tn, abstained violation, n/a alert, unknown reference, pending
    for i, (command, status) in enumerate([
        ("echo hi", "ok"), ("echo hi", "ok"),
        ("pytest", "ok"), ("pytest", "ok"),
        ("pytest", "unknown"), ("echo hi", "ok"),
        ("echo hi", "ok"), ("pytest", "ok"),
    ]):
        entries.append({"task_id": f"task-{i}", "model": "private-model",
                        "steps": [{"tool": "bash", "command": command, "result": status}]})
    (runs / "sample.json").write_text(json.dumps(entries))
    return config, runs, tmp_path / "audit"


def prepare_inputs(inputs):
    config, runs, audit = inputs
    prepare(str(config), str(audit), runs=str(runs))
    return audit


def label_sample(audit):
    path = audit / "labels.json"
    labels = json.loads(path.read_text())
    labels["reviewer"] = {"name": "Private Reviewer", "kind": "human"}
    verdicts = ["fail", "pass", "fail", "pass", "fail", "not_applicable", "unknown", None]
    for label, verdict in zip(labels["labels"], verdicts):
        label.update(verdict=verdict, reason="private source evidence" if verdict else "")
    path.write_text(json.dumps(labels))
    return labels


def test_prepare_keeps_private_source_and_blank_labels(audit_inputs):
    audit = prepare_inputs(audit_inputs)
    labels = json.loads((audit / "labels.json").read_text())
    assert all(x["verdict"] is None for x in labels["labels"])
    assert "predicted" not in (audit / "labels.json").read_text()
    assert (audit / "sources/001.json").read_bytes() == (audit_inputs[1] / "sample.json").read_bytes()
    assert (audit / ".gitignore").read_text() == "*\n"
    result = compare(str(audit))
    assert result["status"] == "incomplete"
    assert result["metrics"]["precision"] is None
    assert result["metrics"]["recall"] is None
    assert result["metrics"]["pending"] == 8
    with pytest.raises(ValueError, match="already exists"):
        prepare(str(audit_inputs[0]), str(audit), runs=str(audit_inputs[1]))


def test_confusion_matrix_exclusions_and_abstentions(audit_inputs):
    audit = prepare_inputs(audit_inputs)
    label_sample(audit)
    result = compare(str(audit))
    metrics = result["metrics"]
    assert [metrics[k] for k in ("tp", "fp", "fn", "tn", "abstained")] == [1, 1, 1, 1, 1]
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == pytest.approx(1 / 3)
    assert metrics["decision_coverage"] == 0.8
    assert metrics["agreement_when_decided"] == 0.5
    assert metrics["alarms_on_not_applicable"] == 1
    assert metrics["unknown"] == metrics["pending"] == 1
    assert result["per_rule"]["test"] == metrics
    assert compare(str(audit)) == result
    summary = render_markdown(result)
    assert "incomplete" in summary
    for private in ("Private Reviewer", "private source evidence", "private-model", "echo hi", "task-0"):
        assert private not in summary


@pytest.mark.parametrize("filename", ["config.yaml", "runs/runs.json", "sources/001.json"])
def test_changed_snapshot_is_rejected(audit_inputs, filename):
    audit = prepare_inputs(audit_inputs)
    with (audit / filename).open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="snapshot changed"):
        compare(str(audit))


@pytest.mark.parametrize("change", ["duplicate", "missing", "verdict", "reason", "reviewer", "snapshot"])
def test_bad_reference_labels_are_rejected(audit_inputs, change):
    audit = prepare_inputs(audit_inputs)
    labels = label_sample(audit)
    if change == "duplicate":
        labels["labels"].append(labels["labels"][0])
    elif change == "missing":
        labels["labels"].pop()
    elif change == "verdict":
        labels["labels"][0]["verdict"] = "almost"
    elif change == "reason":
        labels["labels"][0]["reason"] = ""
    elif change == "reviewer":
        labels["reviewer"] = {}
    else:
        labels["snapshot_sha256"] = "different"
    (audit / "labels.json").write_text(json.dumps(labels))
    with pytest.raises(ValueError):
        compare(str(audit))


def test_invalid_regex_does_not_create_audit(audit_inputs):
    config, runs, audit = audit_inputs
    cfg = yaml.safe_load(config.read_text())
    cfg["rules"][0]["check"]["pattern"] = "["
    config.write_text(yaml.safe_dump(cfg))
    assert main(["audit", "prepare", "--config", str(config), "--runs", str(runs),
                 "--output", str(audit)]) == 1
    assert not audit.exists()


def test_duplicate_source_contents_rejected(audit_inputs):
    config, runs, audit = audit_inputs
    (runs / "duplicate.json").write_bytes((runs / "sample.json").read_bytes())
    with pytest.raises(ValueError, match="duplicate source contents"):
        prepare(str(config), str(audit), runs=str(runs))


def test_duplicate_run_identity_in_different_files_rejected(audit_inputs):
    config, runs, audit = audit_inputs
    (runs / "duplicate.json").write_text(json.dumps({"task_id": "task-0", "model": "private-model"}))
    with pytest.raises(ValueError, match="duplicate run identities"):
        prepare(str(config), str(audit), runs=str(runs))


@pytest.mark.parametrize("success", ["false", 0, []])
def test_ambiguous_task_outcome_rejected(audit_inputs, success):
    config, runs, audit = audit_inputs
    (runs / "sample.json").write_text(json.dumps({"task_id": "t", "model": "m", "success": success}))
    with pytest.raises(ValueError, match="success must be"):
        prepare(str(config), str(audit), runs=str(runs))


def test_cli_report_distinguishes_incomplete_and_complete(audit_inputs):
    config, runs, audit = audit_inputs
    assert main(["audit", "prepare", "--config", str(config), "--runs", str(runs),
                 "--output", str(audit)]) == 0
    assert main(["audit", "report", "--input", str(audit)]) == 2
    labels = label_sample(audit)
    labels["labels"][-1].update(verdict="pass", reason="pytest completed")
    (audit / "labels.json").write_text(json.dumps(labels))
    assert main(["audit", "report", "--input", str(audit)]) == 0
    assert "**complete**" in (audit / "report.md").read_text()
    assert json.loads((audit / "results.json").read_text())["metrics"]["tn"] == 2


def test_unhashed_run_file_rejected(audit_inputs):
    audit = prepare_inputs(audit_inputs)
    (audit / "runs/extra.json").write_text("[]")
    with pytest.raises(ValueError, match="unexpected run"):
        compare(str(audit))


def test_selected_log_end_to_end(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("rules:\n- id: test\n  text: Run pytest\n  check:\n    kind: required_command\n    pattern: pytest\n")
    source = tmp_path / "session.jsonl"
    events = [
        {"type": "assistant", "sessionId": "stable-id", "message": {
            "id": "msg-1", "model": "model-a", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "pytest"}}],
        }},
        {"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "tool-1", "is_error": True,
            "content": "Permission denied before execution",
        }]}},
    ]
    source.write_text("\n".join(json.dumps(e) for e in events))
    audit = tmp_path / "audit"
    prepare(str(config), str(audit), logs=[str(source)])
    assert compare(str(audit))["cases"][0]["predicted"] == "unknown"
    assert json.loads((audit / "runs/runs.json").read_text())[0]["steps"][0]["tool_use_id"] == "tool-1"
