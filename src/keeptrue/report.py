"""Render the karne — the adherence report — to the terminal.

The layout is built to be screenshot/GIF friendly: one adherence grid, one
loud "regressions" panel (the reason the tool exists), and one cost table.
"""

from __future__ import annotations

import math
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import Rule, RuleModelResult

REGRESSION_THRESHOLD = 0.15  # drop in adherence vs baseline worth shouting about


def _pct(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x * 100:.0f}%"


def _adherence_style(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "dim"
    if x >= 0.9:
        return "bold green"
    if x >= 0.5:
        return "yellow"
    return "bold red"


def _short(model: str, width: int = 16) -> str:
    return model if len(model) <= width else model[: width - 1] + "…"


def render(
    scenario: str,
    rules: list[Rule],
    models: list[str],
    matrix: dict[str, dict[str, RuleModelResult]],
    stats: dict[str, dict[str, float]],
    *,
    note: str | None = None,
    console: Console | None = None,
) -> None:
    console = console or Console()
    baseline = models[0] if models else None

    title = "keeptrue — do the rules actually get followed?"
    console.print()
    console.print(Panel(Text(title, style="bold"), expand=False))
    if scenario:
        console.print(Text(scenario, style="dim italic"))

    # ---- adherence grid -------------------------------------------------
    grid = Table(title="Rule adherence (share of runs that obeyed the rule)")
    grid.add_column("Rule", style="cyan", no_wrap=False, max_width=40)
    for m in models:
        header = _short(m) + ("  ·baseline" if m == baseline else "")
        grid.add_column(header, justify="right")

    for rule in rules:
        row = [Text(rule.text, style="cyan")]
        base_res = matrix[rule.id].get(baseline)
        base_adh = base_res.adherence if base_res else math.nan
        for m in models:
            res = matrix[rule.id].get(m)
            adh = res.adherence if res else math.nan
            cell = Text(_pct(adh), style=_adherence_style(adh))
            if (
                m != baseline
                and not math.isnan(base_adh)
                and not math.isnan(adh)
                and (base_adh - adh) > REGRESSION_THRESHOLD
            ):
                cell.append("  ⬇", style="bold red")
            row.append(cell)
        grid.add_row(*row)

    console.print(grid)

    # ---- regressions callout -------------------------------------------
    regressions = _collect_regressions(rules, models, matrix, baseline)
    if regressions:
        body = Text()
        for r in regressions:
            body.append("• ", style="bold red")
            body.append(r["text"], style="bold")
            body.append(
                f"\n    {_short(baseline)} {_pct(r['base'])} → "
                f"{_short(r['model'])} {_pct(r['adh'])}  "
                f"(−{r['delta'] * 100:.0f} pts)\n",
                style="red",
            )
            if r["evidence"]:
                body.append(f"    e.g. {r['evidence'][0]}\n", style="dim")
        console.print(
            Panel(
                body,
                title="[bold red]Silently dropped rules[/]",
                border_style="red",
                expand=False,
            )
        )
    elif baseline and len(models) > 1:
        console.print(
            Panel(
                Text("No rule regressed by more than 15 points. ✅", style="green"),
                border_style="green",
                expand=False,
            )
        )

    # ---- cost / reliability --------------------------------------------
    cost = Table(title="Cost & reliability")
    cost.add_column("Model", style="cyan")
    cost.add_column("Success", justify="right")
    cost.add_column("Tokens/run", justify="right")
    cost.add_column("Tokens/success", justify="right")
    has_usd = any("usd_per_success" in s for s in stats.values())
    if has_usd:
        cost.add_column("$/success", justify="right")
    cost.add_column("Avg time", justify="right")

    for m in models:
        s = stats.get(m, {})
        row = [
            _short(m),
            Text(_pct(s.get("success_rate", math.nan)),
                 style=_adherence_style(s.get("success_rate", math.nan))),
            f"{s.get('tokens_per_run', float('nan')):,.0f}",
            _fmt_per_success(s.get("tokens_per_success", math.inf)),
        ]
        if has_usd:
            row.append(_fmt_usd(s.get("usd_per_success")))
        row.append(f"{s.get('avg_duration', float('nan')):.0f}s")
        cost.add_row(*row)

    console.print(cost)

    footer = note or (
        "Scores are computed deterministically from each run's tool calls and "
        "final message — re-run to reproduce exactly."
    )
    console.print(Text(footer, style="dim"))
    console.print()


def _collect_regressions(rules, models, matrix, baseline) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not baseline:
        return out
    for rule in rules:
        base_res = matrix[rule.id].get(baseline)
        if not base_res or math.isnan(base_res.adherence):
            continue
        worst = None
        for m in models:
            if m == baseline:
                continue
            res = matrix[rule.id].get(m)
            if not res or math.isnan(res.adherence):
                continue
            delta = base_res.adherence - res.adherence
            if delta > REGRESSION_THRESHOLD and (worst is None or delta > worst["delta"]):
                worst = {
                    "text": rule.text,
                    "model": m,
                    "base": base_res.adherence,
                    "adh": res.adherence,
                    "delta": delta,
                    "evidence": res.evidence,
                }
        if worst:
            out.append(worst)
    out.sort(key=lambda r: r["delta"], reverse=True)
    return out


def _fmt_per_success(x: float) -> str:
    if x == math.inf:
        return "∞ (0 passed)"
    if math.isnan(x):
        return "n/a"
    return f"{x:,.0f}"


def _fmt_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    if x == math.inf:
        return "∞"
    return f"${x:,.2f}"
