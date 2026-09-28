"""Turn runs + rules into the numbers the report shows.

Two things come out of here:
  - an adherence matrix: for each rule, what fraction of each model's runs
    obeyed it (with a few examples of the violation);
  - cost/reliability stats per model, including tokens-per-success, because
    "90% at half the price" and "95% at triple" are the comparison people
    actually argue about.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from statistics import mean
from typing import Any

from .checks import run_check
from .models import Rule, RuleModelResult, Trajectory


def group_by_model(trajs: list[Trajectory]) -> "OrderedDict[str, list[Trajectory]]":
    """Preserve first-seen order so the first file loaded is the baseline."""
    g: "OrderedDict[str, list[Trajectory]]" = OrderedDict()
    for t in trajs:
        g.setdefault(t.model, []).append(t)
    return g


def evaluate(
    rules: list[Rule], trajs: list[Trajectory]
) -> tuple[list[str], dict[str, dict[str, RuleModelResult]]]:
    by_model = group_by_model(trajs)
    models = list(by_model.keys())
    matrix: dict[str, dict[str, RuleModelResult]] = {}

    for rule in rules:
        matrix[rule.id] = {}
        for model in models:
            ts = by_model[model]
            passed = 0
            n = 0
            unknown = 0
            evidence: list[str] = []
            for t in ts:
                if rule.check is None:
                    unknown += 1
                    continue
                out = run_check(rule.check, t)
                if out.passed is None:
                    unknown += 1
                    continue
                n += 1
                if out.passed:
                    passed += 1
                elif out.evidence and len(evidence) < 3:
                    evidence.append(out.evidence)
            adherence = passed / n if n else math.nan
            matrix[rule.id][model] = RuleModelResult(
                rule_id=rule.id, model=model, n=n, adherence=adherence,
                evidence=evidence, unknown=unknown,
            )

    return models, matrix


def cost_stats(
    trajs: list[Trajectory], prices: dict[str, Any] | None = None
) -> "OrderedDict[str, dict[str, float]]":
    prices = prices or {}
    by_model = group_by_model(trajs)
    stats: "OrderedDict[str, dict[str, float]]" = OrderedDict()

    for model, ts in by_model.items():
        known = [t for t in ts if t.success is not None]
        successes = [t for t in known if t.success is True]
        n_succ = len(successes)
        total_tokens = sum(t.usage.total_tokens for t in ts)
        known_tokens = sum(t.usage.total_tokens for t in known)
        row: dict[str, float] = {
            "runs": float(len(ts)),
            "success_known": float(len(known)),
            "success_rate": (n_succ / len(known)) if known else math.nan,
            "tokens_per_run": (total_tokens / len(ts)) if ts else math.nan,
            "tokens_per_success": ((known_tokens / n_succ) if n_succ else math.inf)
            if known else math.nan,
            "avg_duration": mean([t.usage.duration_s for t in ts]) if ts else math.nan,
        }

        price = prices.get(model)
        if price:
            usd = sum(
                t.usage.input_tokens / 1_000_000 * price.get("input", 0)
                + t.usage.output_tokens / 1_000_000 * price.get("output", 0)
                for t in ts
            )
            row["usd_per_run"] = usd / len(ts) if ts else math.nan
            known_usd = sum(
                t.usage.input_tokens / 1_000_000 * price.get("input", 0)
                + t.usage.output_tokens / 1_000_000 * price.get("output", 0)
                for t in known
            )
            row["usd_per_success"] = ((known_usd / n_succ) if n_succ else math.inf) \
                if known else math.nan

        stats[model] = row

    return stats
