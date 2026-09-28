"""Descriptive, offline reports for a frozen paired instruction experiment.

The report retains every planned slot. A missing outcome is never converted
into a failed task, a successful task, or a zero-token run. Positive effects
mean higher task success with the full instruction set in the observed pairs.
"""

from __future__ import annotations

import hashlib
import html
import math
import random
from collections import Counter, defaultdict
from statistics import mean, median


STATUSES = ("completed", "agent_error", "timeout", "interrupted", "verification_error")
BOOTSTRAP_SAMPLES = 4000
LIMITATIONS = [
    "Task success and instruction adherence are different measurements. This report measures externally verified task outcomes; it does not infer adherence.",
    "Success rates exclude unknown and pending outcomes. Missing outcomes can bias the observed rates; inspect coverage before comparing variants.",
    "Effects are full minus without in percentage points, averaged within each task and then equally across tasks with observed pairs.",
    "Intervals, when available, use an exploratory task-cluster bootstrap. They assume the selected tasks behave like independent draws from a relevant task population; repetitions within a task are not independent tasks.",
    "Multiple unit comparisons are exploratory and are not adjusted for multiple testing. These results do not establish equivalence, justify safe deletion, or generalize beyond the selected tasks and configuration.",
]


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _number(value: object, label: str, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    allowed = type(value) is int if integer else type(value) in (int, float)
    finite = type(value) is int or (type(value) is float and math.isfinite(value))
    if not allowed or not finite or value < 0:
        raise ValueError(
            f"{label} must be a finite nonnegative {'integer' if integer else 'number'} or null"
        )
    return value


def _metric(values: list[int | float | None]) -> dict:
    known = [v for v in values if v is not None]
    return {
        "known": len(known),
        "missing": len(values) - len(known),
        "median": median(known) if known else None,
    }


def _counts(slots: list[dict], by_id: dict[str, dict]) -> dict:
    records = [by_id[slot["id"]] for slot in slots if slot["id"] in by_id]
    statuses = Counter(record["status"] for record in records)
    passes = sum(record.get("success") is True for record in records)
    fails = sum(record.get("success") is False for record in records)
    return {
        "planned": len(slots),
        "recorded": len(records),
        "completed": statuses["completed"],
        "known": passes + fails,
        "pass": passes,
        "fail": fails,
        "unknown": len(records) - passes - fails,
        "pending": len(slots) - len(records),
        "status_counts": {status: statuses[status] for status in STATUSES},
    }


def _percentile(values: list[float], q: float) -> float:
    position = (len(values) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def _interval(
    task_effects: list[float], missing: int, seed: str
) -> tuple[list[float] | None, str | None]:
    if missing:
        return None, "Some planned pairs lack two known outcomes."
    if len(task_effects) < 10:
        return None, "Fewer than 10 distinct tasks have paired outcomes."
    if len(set(task_effects)) < 2:
        return (
            None,
            "Observed task effects are constant; a zero-width bootstrap interval would overstate certainty.",
        )
    rng = random.Random(
        int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    )
    draws = sorted(
        mean(rng.choices(task_effects, k=len(task_effects))) * 100
        for _ in range(BOOTSTRAP_SAMPLES)
    )
    interval = [_percentile(draws, 0.025), _percentile(draws, 0.975)]
    if interval[0] == interval[1]:
        return (
            None,
            "The bootstrap interval is degenerate; uncertainty is not estimable from these observations.",
        )
    return interval, None


def summarize(plan: dict, records: list[dict]) -> dict:
    """Return shareable aggregates, excluding prompts, instruction text and logs.

    ``complete`` refers to outcome coverage, not confidence or experimental
    validity. All supplied records are terminal; absent slots remain pending.
    Unknown outcomes remain distinct from a verifier-confirmed task failure.
    """
    if (
        not isinstance(plan, dict)
        or type(plan.get("schema_version")) is not int
        or plan["schema_version"] != 1
    ):
        raise ValueError("unsupported plan schema_version")
    plan_id = _identifier(plan.get("plan_id"), "plan_id")
    model = _identifier(plan.get("model"), "model")
    unit_ids: list[str] = []
    task_ids: set[str] = set()
    if (
        not isinstance(plan.get("units"), list)
        or not isinstance(plan.get("tasks"), list)
        or not isinstance(records, list)
    ):
        raise ValueError("units, tasks and records must be lists")
    for unit in plan["units"]:
        if not isinstance(unit, dict):
            raise ValueError("each unit must be an object")
        unit_id = _identifier(unit.get("id"), "unit id")
        if unit_id in unit_ids:
            raise ValueError(f"duplicate unit id: {unit_id}")
        unit_ids.append(unit_id)
    for task in plan["tasks"]:
        if not isinstance(task, dict):
            raise ValueError("each task must be an object")
        task_id = _identifier(task.get("id"), "task id")
        if task_id in task_ids:
            raise ValueError(f"duplicate task id: {task_id}")
        task_ids.add(task_id)
    slots = plan.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ValueError("plan must have at least one slot")
    variants = ["full", *(f"without:{unit}" for unit in unit_ids)]
    slot_ids: set[str] = set()
    pairs: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    grouped: dict[str, list[dict]] = {variant: [] for variant in variants}
    for slot in slots:
        if not isinstance(slot, dict):
            raise ValueError("each slot must be an object")
        slot_id = _identifier(slot.get("id"), "slot id")
        if slot_id in slot_ids:
            raise ValueError(f"duplicate planned slot id: {slot_id}")
        slot_ids.add(slot_id)
        variant = slot.get("variant")
        if variant not in grouped:
            raise ValueError(f"unknown slot variant: {variant!r}")
        task_id, repeat = slot.get("task_id"), slot.get("repeat")
        if task_id not in task_ids or type(repeat) is not int or repeat < 0:
            raise ValueError(f"invalid task or repeat for slot: {slot_id}")
        expected_removed = (
            None if variant == "full" else variant.removeprefix("without:")
        )
        if slot.get("removed_unit") != expected_removed:
            raise ValueError(f"removed_unit does not match variant for slot: {slot_id}")
        pair = pairs[(task_id, repeat)]
        if variant in pair:
            raise ValueError(f"duplicate task/repeat/variant slot: {slot_id}")
        pair[variant] = slot
        grouped[variant].append(slot)

    by_id: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each record must be an object")
        slot_id = _identifier(record.get("slot_id"), "record slot_id")
        if slot_id not in slot_ids:
            raise ValueError(f"unexpected result slot_id: {slot_id}")
        if slot_id in by_id:
            raise ValueError(f"duplicate result slot_id: {slot_id}")
        if record.get("status") not in STATUSES:
            raise ValueError(f"invalid result status for slot: {slot_id}")
        if record.get("success") is not None and type(record["success"]) is not bool:
            raise ValueError(f"success must be boolean or null for slot: {slot_id}")
        for field in ("input_tokens", "output_tokens", "duration_s"):
            _number(record.get(field), field, integer=field != "duration_s")
        by_id[slot_id] = record

    variant_summaries = []
    for variant, members in grouped.items():
        counts = _counts(members, by_id)
        observations = [by_id.get(slot["id"], {}) for slot in members]
        inputs = [r.get("input_tokens") for r in observations]
        outputs = [r.get("output_tokens") for r in observations]
        totals = [
            a + b if a is not None and b is not None else None
            for a, b in zip(inputs, outputs)
        ]
        variant_summaries.append(
            {
                "id": variant,
                "removed_unit": None
                if variant == "full"
                else variant.removeprefix("without:"),
                "counts": counts,
                "success_rate": counts["pass"] / counts["known"]
                if counts["known"]
                else None,
                "usage": {
                    "input_tokens": _metric(inputs),
                    "output_tokens": _metric(outputs),
                    "total_tokens": _metric(totals),
                },
                "duration_s": _metric([r.get("duration_s") for r in observations]),
            }
        )

    effects = []
    for unit_id in unit_ids:
        variant = f"without:{unit_id}"
        task_deltas: dict[str, list[int]] = defaultdict(list)
        planned_pairs = complete_pairs = 0
        planned_tasks: set[str] = set()
        for (task_id, _), pair in pairs.items():
            if "full" not in pair and variant not in pair:
                continue
            planned_pairs += 1
            planned_tasks.add(task_id)
            full = by_id.get(pair.get("full", {}).get("id"), {})
            without = by_id.get(pair.get(variant, {}).get("id"), {})
            if (
                type(full.get("success")) is bool
                and type(without.get("success")) is bool
            ):
                task_deltas[task_id].append(
                    int(full["success"]) - int(without["success"])
                )
                complete_pairs += 1
        task_effects = [mean(task_deltas[key]) for key in sorted(task_deltas)]
        missing = planned_pairs - complete_pairs
        interval, reason = _interval(task_effects, missing, f"{plan_id}:{unit_id}")
        effects.append(
            {
                "unit_id": unit_id,
                "full_minus_without_pp": mean(task_effects) * 100
                if task_effects
                else None,
                "planned_pairs": planned_pairs,
                "complete_pairs": complete_pairs,
                "missing_pairs": missing,
                "planned_tasks": len(planned_tasks),
                "paired_tasks": len(task_effects),
                "ci95_pp": interval,
                "ci_reason": reason,
            }
        )
    counts = _counts(slots, by_id)
    complete = (
        counts["pending"] == 0
        and counts["unknown"] == 0
        and all(effect["missing_pairs"] == 0 for effect in effects)
    )
    return {
        "schema_version": 1,
        "plan_id": plan_id,
        "model": model,
        "complete": complete,
        "execution_complete": counts["pending"] == 0,
        "counts": counts,
        "variants": variant_summaries,
        "effects": effects,
        "bootstrap": {
            "samples": BOOTSTRAP_SAMPLES,
            "unit": "task",
            "minimum_tasks": 10,
            "seed": "sha256(plan_id:unit_id)",
            "confidence": 0.95,
        },
        "limitations": list(LIMITATIONS),
    }


def _value(value: object, *, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    if type(value) is float:
        return f"{value:.{digits}f}"
    return str(value)


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _coverage(metric: dict) -> str:
    return f"{_value(metric['median'])} (n={metric['known']})"


def _tables(summary: dict) -> list[tuple[str, list[str], list[list[object]]]]:
    outcome_rows, usage_rows, effect_rows, status_rows = [], [], [], []
    for variant in summary["variants"]:
        counts = variant["counts"]
        outcome_rows.append(
            [
                variant["id"],
                counts["planned"],
                counts["completed"],
                counts["pass"],
                counts["fail"],
                counts["unknown"],
                counts["pending"],
                f"{_rate(variant['success_rate'])} (n={counts['known']})",
            ]
        )
        usage_rows.append(
            [
                variant["id"],
                _coverage(variant["usage"]["input_tokens"]),
                _coverage(variant["usage"]["output_tokens"]),
                _coverage(variant["usage"]["total_tokens"]),
                _coverage(variant["duration_s"]),
            ]
        )
        status_rows.append(
            [
                variant["id"],
                *(counts["status_counts"][status] for status in STATUSES),
                counts["pending"],
            ]
        )
    for effect in summary["effects"]:
        interval = effect["ci95_pp"]
        ci = (
            f"[{_value(interval[0])}, {_value(interval[1])}]"
            if interval is not None
            else f"n/a — {effect['ci_reason']}"
        )
        effect_rows.append(
            [
                effect["unit_id"],
                _value(effect["full_minus_without_pp"]),
                f"{effect['complete_pairs']}/{effect['planned_pairs']}",
                f"{effect['paired_tasks']}/{effect['planned_tasks']}",
                effect["missing_pairs"],
                ci,
            ]
        )
    return [
        (
            "Task outcomes",
            [
                "Variant",
                "Planned",
                "Completed",
                "Pass",
                "Fail",
                "Unknown",
                "Pending",
                "Success / known",
            ],
            outcome_rows,
        ),
        (
            "Observed paired differences",
            [
                "Removed unit",
                "Full − without (pp)",
                "Complete pairs",
                "Paired tasks",
                "Missing pairs",
                "Exploratory 95% interval (pp)",
            ],
            effect_rows,
        ),
        (
            "Resource medians",
            [
                "Variant",
                "Input tokens",
                "Output tokens",
                "Total tokens",
                "Elapsed seconds",
            ],
            usage_rows,
        ),
        (
            "Run status",
            [
                "Variant",
                "Completed",
                "Agent error",
                "Timeout",
                "Interrupted",
                "Verification error",
                "Pending",
            ],
            status_rows,
        ),
    ]


def render_html(summary: dict) -> str:
    """Render a standalone HTML document with no scripts or remote resources."""

    def escape(value):
        return html.escape(str(value), quote=True)

    complete = summary["complete"]
    status = (
        "Outcome coverage complete"
        if complete
        else "Incomplete outcome coverage — descriptive results only"
    )
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>keeptrue instruction experiment</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;color:#17212b;background:#f7f8fa;max-width:1180px;margin:32px auto;padding:0 24px}h1{line-height:1.2}h2{margin-top:32px}.notice{padding:16px;background:#fff0cd;border-left:4px solid #a45f00}.table{overflow-x:auto}table{width:100%;border-collapse:collapse;background:white;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid #dce1e6;vertical-align:top}th{background:#e9edf2}code{overflow-wrap:anywhere}li{margin:10px 0}.meta{color:#52606d}footer{margin:32px 0;color:#52606d}</style></head><body>",
        "<h1>Instruction experiment</h1>",
        f'<p class="meta">Model: <strong>{escape(summary["model"])}</strong><br>Plan: <code>{escape(summary["plan_id"])}</code></p>',
        f'<p class="notice"><strong>{escape(status)}</strong><br>',
        "These observations do not establish that any instruction is safe to remove. Positive paired differences favor the full instruction set.</p>",
    ]
    counts = summary["counts"]
    parts.append(
        f"<p>{escape(counts['recorded'])}/{escape(counts['planned'])} planned slots recorded; {escape(counts['known'])} known outcomes, {escape(counts['unknown'])} unknown, {escape(counts['pending'])} pending.</p>"
    )
    for title, headers, rows in _tables(summary):
        parts.append(f'<h2>{escape(title)}</h2><div class="table"><table><thead><tr>')
        parts.extend(f'<th scope="col">{escape(header)}</th>' for header in headers)
        parts.append("</tr></thead><tbody>")
        for row in rows:
            parts.append(
                "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>"
            )
        parts.append("</tbody></table></div>")
    parts.append(
        "<p>Completed means the runner recorded status=completed. Known outcomes may differ. Resource n counts only reported measurements; missing usage is not zero.</p><h2>Interpretation</h2><ul>"
    )
    parts.extend(f"<li>{escape(note)}</li>" for note in summary["limitations"])
    parts.append(
        "</ul><footer>Generated locally by keeptrue. This report contains aggregates and identifiers only.</footer></body></html>"
    )
    return "\n".join(parts)


def render_markdown(summary: dict) -> str:
    """Render the same descriptive aggregates as portable Markdown."""

    def escape(value: object) -> str:
        escaped = (
            html.escape(str(value), quote=True)
            .replace("\\", "\\\\")
            .replace("\n", " ")
            .replace("\r", " ")
        )
        for char in "|`[]*_":
            escaped = escaped.replace(char, "\\" + char)
        return escaped

    status = (
        "Outcome coverage complete"
        if summary["complete"]
        else "Incomplete outcome coverage — descriptive results only"
    )
    lines = [
        "# Instruction experiment",
        "",
        f"**{status}**",
        "",
        f"Model: {escape(summary['model'])}",
        "",
        f"Plan: {escape(summary['plan_id'])}",
        "",
        "Positive paired differences favor the full instruction set. These observations do not establish that any instruction is safe to remove.",
        "",
    ]
    for title, headers, rows in _tables(summary):
        lines.extend(
            [
                f"## {title}",
                "",
                "| " + " | ".join(map(escape, headers)) + " |",
                "| " + " | ".join("---" for _ in headers) + " |",
            ]
        )
        lines.extend("| " + " | ".join(map(escape, row)) + " |" for row in rows)
        lines.append("")
    lines.extend(
        [
            "Completed refers to runner status; resource n counts only reported measurements. Missing usage is not zero.",
            "",
            "## Interpretation",
            "",
        ]
    )
    lines.extend(f"- {escape(note)}" for note in summary["limitations"])
    return "\n".join(lines) + "\n"
