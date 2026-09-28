import copy
import json

import pytest

from keeptrue.ablation.reporting import render_html, render_markdown, summarize


def plan(repetitions=None, units=("run-tests",)):
    repetitions = repetitions or {"task-a": 1}
    slots = []
    for task, count in repetitions.items():
        for repeat in range(1, count + 1):
            for unit in (None, *units):
                variant = "full" if unit is None else f"without:{unit}"
                slots.append(
                    {
                        "id": f"{task}:{repeat}:{variant}",
                        "task_id": task,
                        "repeat": repeat,
                        "variant": variant,
                        "removed_unit": unit,
                    }
                )
    return {
        "schema_version": 1,
        "plan_id": "a" * 64,
        "model": "test-model",
        "runner": {"command": ["secret-runner-argument"]},
        "units": [{"id": unit, "text": "private instruction text"} for unit in units],
        "tasks": [
            {"id": task, "prompt": "private task prompt"} for task in repetitions
        ],
        "slots": slots,
    }


def result(slot, success, **overrides):
    record = {
        "slot_id": slot["id"],
        "status": "completed",
        "success": success,
        "duration_s": 10.0,
        "input_tokens": 100,
        "output_tokens": 30,
    }
    record.update(overrides)
    return record


def test_pending_and_unknown_outcomes_remain_in_the_report():
    p = plan({"task-a": 2})
    records = [
        result(p["slots"][0], True),
        result(
            p["slots"][1], None, status="timeout", input_tokens=None, output_tokens=None
        ),
        result(p["slots"][2], False),
    ]
    summary = summarize(p, records)
    counts = summary["counts"]
    assert counts["planned"] == 4
    assert counts["completed"] == 2
    assert counts["known"] == 2
    assert (counts["pass"], counts["fail"], counts["unknown"], counts["pending"]) == (
        1,
        1,
        1,
        1,
    )
    assert counts["status_counts"]["timeout"] == 1
    assert summary["complete"] is False
    assert summary["execution_complete"] is False
    full, removed = summary["variants"]
    assert full["success_rate"] == 0.5
    assert removed["success_rate"] is None
    assert removed["usage"]["total_tokens"] == {
        "known": 0,
        "missing": 2,
        "median": None,
    }
    effect = summary["effects"][0]
    assert effect["complete_pairs"] == 0
    assert effect["missing_pairs"] == 2
    assert effect["full_minus_without_pp"] is None
    assert "Incomplete outcome coverage" in render_html(summary)


def test_absent_results_are_not_failures_or_zero_usage():
    summary = summarize(plan(), [])
    assert summary["counts"]["pending"] == 2
    assert summary["counts"]["fail"] == 0
    for variant in summary["variants"]:
        assert variant["success_rate"] is None
        assert variant["duration_s"]["median"] is None
        assert variant["usage"]["input_tokens"]["known"] == 0


def test_uneven_repetitions_weight_each_task_equally():
    p = plan({"many-repetitions": 3, "one-repetition": 1})
    records = [
        result(
            slot, (slot["variant"] == "full") == (slot["task_id"] == "many-repetitions")
        )
        for slot in p["slots"]
    ]
    summary = summarize(p, records)
    effect = summary["effects"][0]
    # Task effects are +100 pp and -100 pp. Pooling runs would wrongly give +50 pp.
    assert effect["full_minus_without_pp"] == 0
    assert effect["complete_pairs"] == 4
    assert effect["paired_tasks"] == 2
    assert effect["missing_pairs"] == 0
    assert effect["ci95_pp"] is None
    assert "Fewer than 10" in effect["ci_reason"]
    assert summary["complete"] is True


def test_effect_pairs_match_task_and_repeat_instead_of_list_order():
    p = plan({"a": 2, "b": 1}, units=("first", "second"))
    records = [
        result(slot, slot["variant"] != "without:first")
        for slot in reversed(p["slots"])
    ]
    summary = summarize(p, records)
    assert [effect["full_minus_without_pp"] for effect in summary["effects"]] == [
        100,
        0,
    ]
    assert all(effect["complete_pairs"] == 3 for effect in summary["effects"])


def test_unknown_pairs_are_excluded_from_effect_and_prevent_interval():
    p = plan({f"task-{i}": 1 for i in range(11)})
    records = [
        result(slot, slot["variant"] == "full" or slot["task_id"] == "task-0")
        for slot in p["slots"]
    ]
    records[0]["success"] = None
    summary = summarize(p, records)
    effect = summary["effects"][0]
    assert summary["execution_complete"] is True
    assert summary["complete"] is False
    assert effect["complete_pairs"] == 10
    assert effect["missing_pairs"] == 1
    assert effect["ci95_pp"] is None
    assert "lack two known outcomes" in effect["ci_reason"]


def test_constant_effect_does_not_get_misleading_zero_width_interval():
    p = plan({f"task-{i}": 1 for i in range(12)})
    summary = summarize(p, [result(slot, True) for slot in p["slots"]])
    effect = summary["effects"][0]
    assert effect["full_minus_without_pp"] == 0
    assert effect["ci95_pp"] is None
    assert "constant" in effect["ci_reason"]
    assert "equivalence" in " ".join(summary["limitations"])


def test_bootstrap_is_deterministic_at_task_level():
    p = plan({f"task-{i}": 2 for i in range(10)})
    records = [
        result(
            slot, slot["variant"] == "full" or int(slot["task_id"].split("-")[1]) < 5
        )
        for slot in p["slots"]
    ]
    first = summarize(p, records)["effects"][0]
    second = summarize(p, list(reversed(records)))["effects"][0]
    assert first == second
    assert first["full_minus_without_pp"] == 50
    assert first["paired_tasks"] == 10
    assert first["complete_pairs"] == 20
    assert first["ci95_pp"][0] < 50 < first["ci95_pp"][1]
    assert first["ci_reason"] is None


def test_duplicate_and_unexpected_result_ids_are_rejected():
    p = plan()
    record = result(p["slots"][0], True)
    with pytest.raises(ValueError, match="duplicate result"):
        summarize(p, [record, record])
    with pytest.raises(ValueError, match="unexpected result"):
        summarize(p, [{**record, "slot_id": "not-in-plan"}])


def test_duplicate_slot_and_pair_ids_are_rejected():
    p = plan()
    p["slots"].append(copy.deepcopy(p["slots"][0]))
    with pytest.raises(ValueError, match="duplicate planned slot"):
        summarize(p, [])
    p["slots"][-1]["id"] = "different-id-same-pair"
    with pytest.raises(ValueError, match="duplicate task/repeat/variant"):
        summarize(p, [])


@pytest.mark.parametrize(
    "field,value",
    [
        ("success", 1),
        ("input_tokens", True),
        ("output_tokens", -1),
        ("duration_s", float("nan")),
        ("duration_s", float("inf")),
        ("status", "invented"),
    ],
)
def test_invalid_outcome_and_resource_values_are_rejected(field, value):
    p = plan()
    record = (
        result(p["slots"][0], True, **{field: value})
        if field != "success"
        else result(p["slots"][0], value)
    )
    with pytest.raises(ValueError):
        summarize(p, [record])


def test_partial_token_information_does_not_create_total():
    p = plan({"task-a": 2})
    records = [
        result(slot, True, input_tokens=0, output_tokens=None) for slot in p["slots"]
    ]
    summary = summarize(p, records)
    for variant in summary["variants"]:
        assert variant["usage"]["input_tokens"] == {
            "known": 2,
            "missing": 0,
            "median": 0.0,
        }
        assert variant["usage"]["output_tokens"]["median"] is None
        assert variant["usage"]["total_tokens"]["median"] is None


def test_cli_status_does_not_override_independently_verified_outcome():
    p = plan()
    records = [
        result(p["slots"][0], True, status="agent_error"),
        result(p["slots"][1], False, status="completed"),
    ]
    summary = summarize(p, records)
    assert summary["counts"]["completed"] == 1
    assert summary["counts"]["known"] == 2
    assert summary["effects"][0]["full_minus_without_pp"] == 100


def test_shareable_summary_does_not_contain_prompts_rules_or_raw_results():
    p = plan()
    records = [
        result(slot, True, stdout="private transcript", error="secret local path")
        for slot in p["slots"]
    ]
    summary = summarize(p, records)
    exported = json.dumps(summary) + render_html(summary) + render_markdown(summary)
    for secret in (
        "private instruction text",
        "private task prompt",
        "secret-runner-argument",
        "private transcript",
        "secret local path",
    ):
        assert secret not in exported


def test_html_escapes_all_user_controlled_values_and_has_no_remote_resources():
    p = plan(units=('<img src=x onerror="alert(1)">',))
    p["model"] = '<script>alert("model")</script>'
    p["plan_id"] = '"><iframe src="https://example.test"></iframe>'
    summary = summarize(p, [])
    rendered = render_html(summary)
    assert "<script>" not in rendered
    assert "<iframe" not in rendered
    assert "<img" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img src=x onerror=&quot;" in rendered
    assert "<link " not in rendered
    assert "<script " not in rendered


def test_markdown_escapes_table_delimiters_and_embedded_html():
    p = plan(units=("unit|<script>\nextra",))
    rendered = render_markdown(summarize(p, []))
    assert "unit\\|&lt;script&gt; extra" in rendered
    assert "<script>" not in rendered
