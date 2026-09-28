"""Lock the demo narrative: these numbers are what `keeptrue demo` shows, and
they must stay reproducible from the committed fixtures."""

import math

from keeptrue.cli import _demo_dir
from keeptrue.config import load_config
from keeptrue.engine import cost_stats, evaluate
from keeptrue.loader import load_runs


def _evaluate_demo():
    d = _demo_dir()
    _scenario, rules, prices = load_config(str(d / "keeptrue.yaml"))
    trajs = load_runs(str(d / "runs"))
    models, matrix = evaluate(rules, trajs)
    stats = cost_stats(trajs, prices)
    return rules, models, matrix, stats


def adh(matrix, rule_id, model):
    return matrix[rule_id][model].adherence


def test_two_models_nine_runs_each():
    _rules, models, _matrix, _stats = _evaluate_demo()
    assert models == ["agent-v1-old", "agent-v2-new"]


def test_new_agent_silently_drops_uv_and_pytest():
    _rules, _models, matrix, _stats = _evaluate_demo()
    assert adh(matrix, "use-uv", "agent-v1-old") == 1.0
    assert adh(matrix, "use-uv", "agent-v2-new") == 0.0
    assert adh(matrix, "run-tests", "agent-v1-old") == 1.0
    assert adh(matrix, "run-tests", "agent-v2-new") == 0.0


def test_rules_that_survive():
    _rules, _models, matrix, _stats = _evaluate_demo()
    assert adh(matrix, "protect-migrations", "agent-v2-new") == 1.0
    assert adh(matrix, "no-force-push", "agent-v2-new") == 1.0


def test_partial_regressions():
    _rules, _models, matrix, _stats = _evaluate_demo()
    assert math.isclose(adh(matrix, "no-debug-prints", "agent-v2-new"), 3 / 9)
    assert math.isclose(adh(matrix, "concise-summary", "agent-v2-new"), 5 / 9)
    assert math.isclose(adh(matrix, "format-with-ruff", "agent-v2-new"), 6 / 9)


def test_new_agent_burns_more_tokens_per_success():
    _rules, _models, _matrix, stats = _evaluate_demo()
    old = stats["agent-v1-old"]["tokens_per_success"]
    new = stats["agent-v2-new"]["tokens_per_success"]
    assert new > old  # cheaper per token, but far more tokens to land a pass
