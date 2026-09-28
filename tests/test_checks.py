from keeptrue.checks import run_check
from keeptrue.models import Check, Step, Trajectory, Usage


def traj(**kw) -> Trajectory:
    kw.setdefault("task_id", "t")
    kw.setdefault("model", "m")
    return Trajectory(**kw)


def test_forbidden_command_fails_on_match():
    t = traj(steps=[Step(command="pip install requests")])
    out = run_check(Check("forbidden_command", {"pattern": r"\bpip install\b"}), t)
    assert not out.passed
    assert "pip install requests" in out.evidence


def test_forbidden_command_passes_when_absent():
    t = traj(steps=[Step(command="uv sync")])
    out = run_check(Check("forbidden_command", {"pattern": r"\bpip install\b"}), t)
    assert out.passed


def test_required_command():
    assert run_check(
        Check("required_command", {"pattern": r"\bpytest\b"}),
        traj(steps=[Step(command="pytest -q")]),
    ).passed
    assert not run_check(
        Check("required_command", {"pattern": r"\bpytest\b"}),
        traj(steps=[Step(command="echo hi")]),
    ).passed


def test_forbidden_path():
    assert not run_check(
        Check("forbidden_path", {"pattern": r"(^|/)migrations/"}),
        traj(steps=[Step(path="db/migrations/0001.py")]),
    ).passed


def test_max_final_length_words():
    short = traj(final_message="one two three")
    long = traj(final_message=" ".join(["w"] * 50))
    chk = Check("max_final_length", {"unit": "words", "limit": 10})
    assert run_check(chk, short).passed
    assert not run_check(chk, long).passed


def test_forbidden_in_diff_only_added_lines():
    added = traj(steps=[Step(diff="+    print('x')\n")])
    removed = traj(steps=[Step(diff="-    print('x')\n")])
    chk = Check("forbidden_in_diff", {"pattern": r"^\+.*\bprint\("})
    assert not run_check(chk, added).passed
    assert run_check(chk, removed).passed  # removing a print is fine


def test_no_repeat_loops():
    stuck = traj(steps=[Step(command="python app.py")] * 3)
    ok = traj(steps=[Step(command="python app.py")] * 2)
    chk = Check("no_repeat_loops", {"threshold": 3})
    assert not run_check(chk, stuck).passed
    assert run_check(chk, ok).passed


def test_unknown_kind_is_reported_not_raised():
    out = run_check(Check("does_not_exist", {}), traj())
    assert not out.passed
    assert "unknown check kind" in out.evidence


def test_diff_evidence_quotes_the_matching_line():
    diff = "+def handler(x):\n+    y = x\n+    print('debug', y)\n+    return y"
    out = run_check(Check("forbidden_in_diff", {"pattern": r"^\+.*(?<![.\w])print\("}),
                    traj(steps=[Step(diff=diff)]))
    assert out.passed is False
    assert out.evidence == "diff matched: +    print('debug', y)"
