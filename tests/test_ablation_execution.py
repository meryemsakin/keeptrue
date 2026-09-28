"""Offline process integration tests. The fake agent is explicitly synthetic."""

import json
import sys

import pytest
import yaml

from keeptrue.ablation.execution import _process, _usage, read_records, run_plan
from keeptrue.ablation.specification import prepare_plan
from keeptrue.ablation.reporting import summarize
from keeptrue.cli import main


@pytest.fixture
def experiment(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "original.txt").write_text("unchanged")
    (repo / "policy.txt").write_text("preserve")
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    verifier = tmp_path / "verify.py"
    verifier.write_text(
        "import pathlib,sys\np=pathlib.Path(sys.argv[1])/'answer.txt'\n"
        "raise SystemExit(0 if p.exists() and p.read_text()=='correct' else 1)\n"
    )
    spec = {
        "schema_version": 1,
        "repo": {"path": str(repo), "ref": "HEAD"},
        "runner": {
            "kind": "codex",
            "model": "synthetic-model",
            "version": "synthetic-cli",
        },
        "instructions": {
            "file": "AGENTS.md",
            "preamble": "Common instructions.",
            "units": [{"id": "check-result", "text": "Check the result."}],
        },
        "tasks": [
            {
                "id": "fix-bug",
                "prompt": "Solve it.",
                "verifier": str(verifier),
                "protected_paths": ["policy.txt"],
            }
        ],
        "repetitions": 2,
        "timeout_s": 2,
    }
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(spec))
    root = tmp_path / "plan"
    plan = prepare_plan(spec_file, root)
    return root, plan, repo


class FakeRunner:
    def __init__(self, root, script=None):
        self.path = root.parent / "fake-agent.py"
        self.path.write_text(
            script
            or """import pathlib,sys,json
workspace=pathlib.Path(sys.argv[1])
assert not (workspace/'answer.txt').exists(), 'run leaked across repetitions'
sys.stdin.read()
answer='correct' if 'Check the result.' in (workspace/'AGENTS.md').read_text() else 'wrong'
(workspace/'answer.txt').write_text(answer)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':10}}))
"""
        )

    def preflight(self, root):
        return {"synthetic": True}

    def command(self, workspace, instructions):
        return [sys.executable, "-I", str(self.path), str(workspace)]

    def verifier_command(self, workspace, verifier):
        return [sys.executable, "-I", str(verifier), str(workspace)]


def test_interrupt_during_verification_stops_before_the_next_paid_run(
    experiment, monkeypatch
):
    import keeptrue.ablation.execution as execution

    root, _, _ = experiment
    real = execution._process

    def process(argv, cwd, output, errors, timeout, prompt=None):
        if output.name == "verify.stdout":
            return "interrupted", None, 0.0  # Ctrl-C while the verifier runs
        return real(argv, cwd, output, errors, timeout, prompt)

    monkeypatch.setattr(execution, "_process", process)
    records = run_plan(root, 4, runner=FakeRunner(root))
    assert [r["status"] for r in records] == ["interrupted"]
    assert records[0]["success"] is None
    assert len(list((root / "runs").iterdir())) == 1  # no further agent launch


def test_frozen_paired_run_verification_resume_and_source_unchanged(experiment):
    root, plan, repo = experiment
    fake = FakeRunner(root)
    first = run_plan(root, 1, runner=fake)
    assert len(first) == 1
    report = summarize(plan, first)
    assert report["counts"]["pending"] == 3
    records = run_plan(root, 3, runner=fake)
    assert len(records) == 4
    by_id = {s["id"]: s for s in plan["slots"]}
    assert all(
        r["success"] == (by_id[r["slot_id"]]["variant"] == "full") for r in records
    )
    assert all((r["input_tokens"], r["output_tokens"]) == (80, 10) for r in records)
    assert not (repo / "answer.txt").exists()
    assert (repo / "policy.txt").read_text() == "preserve"
    assert run_plan(root, 4, runner=fake) == records  # no implicit stochastic rerun
    assert main(["ablate", "report", "--input", str(root)]) == 0
    assert (root / "report.html").exists()


def test_agent_exit_is_not_task_success_and_missing_usage_is_unknown(experiment):
    root, plan, _ = experiment
    fake = FakeRunner(
        root, "import json\nprint(json.dumps({'type':'turn.completed'}))\n"
    )
    record = run_plan(root, 1, runner=fake)[0]
    assert record["status"] == "completed" and record["success"] is False
    assert record["input_tokens"] is None and record["output_tokens"] is None


def test_agent_cannot_pass_by_editing_its_own_tests_or_protected_file(experiment):
    root, plan, _ = experiment
    fake = FakeRunner(
        root,
        """import pathlib,sys,json
p=pathlib.Path(sys.argv[1]); (p/'answer.txt').write_text('correct')
(p/'policy.txt').write_text('deleted policy'); (p/'test.py').write_text('assert True')
print(json.dumps({'type':'turn.completed'}))
""",
    )
    record = run_plan(root, 1, runner=fake)[0]
    assert record["verify_exit_code"] == 0
    assert record["protected_path_violations"] == ["policy.txt"]
    assert record["success"] is False


@pytest.mark.parametrize("script", ["raise SystemExit(1)", 'print("broken json")'])
def test_agent_errors_remain_unknown_and_are_not_silently_retried(experiment, script):
    root, plan, _ = experiment
    records = run_plan(root, 1, runner=FakeRunner(root, script))
    assert records[0]["status"] == "agent_error" and records[0]["success"] is None
    counts = summarize(plan, records)["counts"]
    assert counts["unknown"] == 1 and counts["pending"] == 3


def test_claimed_interrupted_slot_is_retained(experiment):
    root, plan, _ = experiment
    (root / "runs" / plan["slots"][0]["id"]).mkdir(parents=True)
    record = read_records(root, plan)[0]
    assert record["status"] == "interrupted" and record["success"] is None
    assert len(run_plan(root, 1, runner=FakeRunner(root))) == 2


def test_modified_frozen_verifier_refused_before_execution(experiment):
    root, plan, _ = experiment
    (root / plan["tasks"][0]["verifier"]).write_text("raise SystemExit(0)")
    with pytest.raises(ValueError):
        run_plan(root, 1, runner=FakeRunner(root))


def test_modified_raw_result_artifact_refused(experiment):
    root, plan, _ = experiment
    result = run_plan(root, 1, runner=FakeRunner(root))[0]
    (root / "runs" / result["slot_id"] / "agent.jsonl").write_text("{}")
    with pytest.raises(ValueError, match="artifact changed"):
        read_records(root, plan)


def test_runtime_change_cannot_mix_with_existing_repetitions(experiment):
    root, plan, _ = experiment
    runner = FakeRunner(root)
    run_plan(root, 1, runner=runner)
    runner.preflight = lambda root: {"different": True}
    with pytest.raises(ValueError, match="runtime changed"):
        run_plan(root, 1, runner=runner)


def test_timeout_preserves_partial_logs_and_kills_descendants(tmp_path):
    marker = tmp_path / "survived"
    child = (
        "import time,pathlib; time.sleep(.4); pathlib.Path("
        + repr(str(marker))
        + ").write_text('bad')"
    )
    script = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"
        + repr(child)
        + "]); print('partial',flush=True); time.sleep(5)"
    )
    status, _, _ = _process(
        [sys.executable, "-c", script],
        tmp_path,
        tmp_path / "out",
        tmp_path / "err",
        0.15,
    )
    assert status == "timeout" and "partial" in (tmp_path / "out").read_text()
    import time

    time.sleep(0.4)
    assert not marker.exists()


def test_usage_with_invalid_counters_abstains(tmp_path):
    path = tmp_path / "out"
    path.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": True,
                    "output_tokens": 1,
                    "cached_input_tokens": 0,
                },
            }
        )
    )
    assert _usage(path) == (None, None, True)


def test_cli_plan_and_incomplete_report_need_no_runner(experiment, capsys):
    root, plan, _ = experiment
    assert main(["ablate", "report", "--input", str(root)]) == 2
    text = (root / "report.json").read_text()
    assert "Solve it" not in text and "Check the result" not in text
    assert main(["ablate", "run", "--input", str(root), "--max-runs", "0"]) == 1
    assert "positive integer" in capsys.readouterr().err


def test_verifier_cannot_rewrite_candidate_then_claim_success(experiment):
    root, plan, _ = experiment

    class RewritingVerifier(FakeRunner):
        def verifier_command(self, workspace, verifier):
            return [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('answer.txt').write_text('tampered')",
            ]

    record = run_plan(root, 1, runner=RewritingVerifier(root))[0]
    assert record["status"] == "verification_error" and record["success"] is None


def test_generated_symlinks_are_unknown_not_unreproducible_success(experiment):
    root, plan, _ = experiment
    runner = FakeRunner(
        root,
        "import pathlib,json; pathlib.Path('link').symlink_to('policy.txt'); print(json.dumps({'type':'turn.completed'}))",
    )
    record = run_plan(root, 1, runner=runner)[0]
    assert record["status"] == "verification_error" and record["success"] is None


def test_extra_run_directory_is_not_silently_ignored(experiment):
    root, plan, _ = experiment
    (root / "runs" / "unexpected").mkdir(parents=True)
    with pytest.raises(ValueError, match="unexpected run directory"):
        read_records(root, plan)


def test_cli_version_mismatch_fails_before_agent_execution(tmp_path):
    from keeptrue.ablation.execution import CodexRunner

    binary = tmp_path / "runner"
    binary.write_text('#!/bin/sh\necho "codex-cli wrong"\n')
    binary.chmod(0o755)
    runner = CodexRunner(
        {"executable": str(binary), "version": "codex-cli expected", "model": "example"}
    )
    with pytest.raises(ValueError, match="version mismatch"):
        runner.preflight(tmp_path)
