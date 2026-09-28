import re

import pytest
import yaml

from keeptrue.config import load_config
from keeptrue.from_rules import extract_rules, propose_check, propose_config

SAMPLE = """# House rules

- Use `uv` for packages, never `pip install`.
- Always run the tests (pytest) before finishing.
- Never edit files under migrations/.
- Keep the final summary under 60 words.
- No print() debug statements in committed code.
- Never git push --force.
- Be nice to the reviewer and explain your reasoning.
"""


def _checks_by_kind(config):
    return [r.get("check") for r in config["rules"] if r.get("check")]


def test_extracts_bullets_and_strips_markdown():
    rules = extract_rules(SAMPLE)
    assert "Use uv for packages, never pip install" in rules  # backticks stripped
    assert not any(r.startswith("#") for r in rules)


def test_proposes_known_checks():
    config, matched, total = propose_config(SAMPLE, source="AGENTS.md")
    checks = _checks_by_kind(config)
    assert any(
        c["kind"] == "forbidden_command" and re.search(c["pattern"], "pip install x")
        for c in checks
    )
    assert any(
        c["kind"] == "required_command" and "pytest" in c["pattern"] for c in checks
    )
    assert any(
        c["kind"] == "forbidden_path" and "migrations" in c["pattern"] for c in checks
    )
    assert any(c["kind"] == "max_final_length" and c["limit"] == 59 for c in checks)
    assert any(
        c["kind"] == "forbidden_command" and "force" in c["pattern"] for c in checks
    )
    assert matched >= 5
    assert total > matched  # "Be nice to the reviewer" has no deterministic check


def test_output_round_trips_through_load_config(tmp_path):
    config, _matched, _total = propose_config(SAMPLE, source="AGENTS.md")
    p = tmp_path / "keeptrue.yaml"
    p.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
    scenario, rules, _prices = load_config(str(p))
    assert scenario == "rules from AGENTS.md"
    assert len(rules) == len(config["rules"])


@pytest.mark.parametrize(
    "text",
    [
        "Do not run pytest",
        "Never run the test suite",
        "Don't run ruff",
        "Create migrations",
        "Run pytest only when changing Python files",
        "Only run pytest when changing Python files",
        "If Python files change, run pytest",
        "Run pytest unless this is a documentation change",
        "Never edit migrations except for schema changes",
        "Use pip instead of uv",
        "Do not use uv; use pip",
        "Add print() calls for debugging",
        "Never expose secrets",
        "Keep the summary concise",
        "Keep the final summary at least 60 words",
        "No print() debug statements in committed code",
    ],
)
def test_unsupported_or_scoped_rule_is_preserved_without_a_check(text):
    config, matched, total = propose_config(f"- {text}\n")
    assert (matched, total) == (0, 1)
    assert config["rules"][0]["text"] == text
    assert "check" not in config["rules"][0]


@pytest.mark.parametrize(
    "text, kind, matching, nonmatching",
    [
        (
            "Always run pytest before finishing",
            "required_command",
            "python -m pytest",
            "npm test",
        ),
        ("Run ruff", "required_command", "ruff check .", "black ."),
        ("Never run pip install", "forbidden_command", "pip3 install x", "uv add x"),
        ("Use uv instead of pip", "forbidden_command", "pip install x", "uv add x"),
        (
            "Never edit files under migrations/",
            "forbidden_path",
            "app/migrations/001.py",
            "app/models.py",
        ),
        (
            "Never modify the .env file",
            "forbidden_path",
            "/project/.env",
            "/project/.env.example",
        ),
        (
            "Never add print() calls",
            "forbidden_in_diff",
            "+print('x')",
            "+console.log('x')",
        ),
        (
            "No console.log() calls",
            "forbidden_in_diff",
            "+console.log('x')",
            "+print('x')",
        ),
        ("Never git push --force", "forbidden_command", "git push --force", "git push"),
    ],
)
def test_simple_templates_keep_their_named_signal(text, kind, matching, nonmatching):
    check, _ = propose_check(text)
    assert check["kind"] == kind
    assert re.search(check["pattern"], matching)
    assert not re.search(check["pattern"], nonmatching)
    if kind == "forbidden_path":
        assert check["tools"] == ["edit"]


@pytest.mark.parametrize(
    "wording, expected", [("under", 59), ("at most", 60), ("no more than", 60)]
)
def test_explicit_word_limit_preserves_the_boundary(wording, expected):
    check, _ = propose_check(f"Keep the final summary {wording} 60 words")
    assert check == {"kind": "max_final_length", "unit": "words", "limit": expected}


def test_scoped_parent_is_not_discarded_and_code_examples_are_not_rules():
    config, matched, _ = propose_config(
        "When modifying Python files:\n\n- Run pytest\n\n```sh\n- Run ruff\n```\n"
    )
    assert matched == 0
    assert len(config["rules"]) == 1
    assert config["rules"][0]["text"] == "When modifying Python files: Run pytest"
