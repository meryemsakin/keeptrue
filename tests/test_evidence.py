"""Unknown evidence must never earn a confident score or a zero success rate."""

import math
from io import StringIO

import pytest
from rich.console import Console

from keeptrue.checks import run_check
from keeptrue.engine import cost_stats, evaluate
from keeptrue.models import Check, Rule, Step, Trajectory, Usage
from keeptrue.report import render


@pytest.mark.parametrize("result", ["unknown", "error"])
def test_unconfirmed_matching_tool_abstains(result):
    t = Trajectory("t", "m", steps=[Step(tool="bash", command="pytest", result=result)])
    for kind in ("required_command", "forbidden_command"):
        assert run_check(Check(kind, {"pattern": "pytest"}), t).passed is None


def test_nonzero_exit_is_execution_evidence():
    t = Trajectory("t", "m", steps=[Step(command="pytest", result="error", exit_code=1)])
    assert run_check(Check("required_command", {"pattern": "pytest"}), t).passed is True


def test_unconfirmed_edit_not_reported_as_completed_change():
    t = Trajectory("t", "m", steps=[Step(tool="edit", path="migrations/001.py",
                                         diff="+print('x')", result="error")])
    assert run_check(Check("forbidden_path", {"pattern": "migrations/"}), t).passed is None
    assert run_check(Check("forbidden_in_diff", {"pattern": "print"}), t).passed is None


def test_path_check_can_scope_edits_without_counting_reads():
    check = Check("forbidden_path", {"pattern": "migrations/", "tools": ["edit"]})
    t = Trajectory("t", "m", steps=[Step(tool="read", path="migrations/001.py", result="ok")])
    assert run_check(check, t).passed is True
    t.steps.append(Step(tool="edit", path="migrations/001.py", result="ok"))
    assert run_check(check, t).passed is False


def test_unknown_predictions_excluded_from_adherence_denominator():
    rule = Rule("r", "run pytest", Check("required_command", {"pattern": "pytest"}))
    _, matrix = evaluate([rule], [
        Trajectory("yes", "m", steps=[Step(command="pytest", result="ok")]),
        Trajectory("no", "m", steps=[]),
        Trajectory("maybe", "m", steps=[Step(command="pytest", result="unknown")]),
    ])
    result = matrix["r"]["m"]
    assert (result.n, result.unknown, result.adherence) == (2, 1, 0.5)


def test_unknown_success_is_na_and_never_zero_in_report():
    ts = [Trajectory("t", "m", usage=Usage(input_tokens=100))]
    stats = cost_stats(ts, {"m": {"input": 1}})
    assert math.isnan(stats["m"]["success_rate"])
    assert math.isnan(stats["m"]["tokens_per_success"])
    assert math.isnan(stats["m"]["usd_per_success"])
    out = StringIO()
    render("", [], ["m"], {}, stats, console=Console(file=out, width=120))
    assert "0 passed" not in out.getvalue()
    assert "$nan" not in out.getvalue()
    assert "known for 0/1 runs" in out.getvalue()


def test_partial_success_labels_use_same_cohort_for_costs():
    ts = [Trajectory("a", "m", success=True, usage=Usage(input_tokens=100)),
          Trajectory("b", "m", success=False, usage=Usage(input_tokens=200)),
          Trajectory("c", "m", usage=Usage(input_tokens=900))]
    row = cost_stats(ts, {"m": {"input": 1_000_000}})["m"]
    assert row["success_rate"] == 0.5
    assert row["success_known"] == 2
    assert row["tokens_per_success"] == row["usd_per_success"] == 300
    assert row["tokens_per_run"] == 400


def test_known_zero_success_remains_infinity():
    row = cost_stats([Trajectory("a", "m", success=False)])["m"]
    assert row["success_rate"] == 0
    assert row["tokens_per_success"] == math.inf


def test_missing_final_message_abstains():
    assert run_check(Check("max_final_length", {"limit": 80}), Trajectory("a", "m")).passed is None


@pytest.mark.parametrize("check", [Check("typo"), Check("required_command", {"pattern": "["}),
                                  Check("max_final_length", {"limit": "bad"})])
def test_bad_check_is_not_scored_as_agent_violation(check):
    assert run_check(check, Trajectory("a", "m")).passed is None
