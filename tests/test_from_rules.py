import yaml

from keeptrue.config import load_config
from keeptrue.from_rules import extract_rules, propose_config

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
    assert "Use uv for packages, never pip install" in rules   # backticks stripped
    assert not any(r.startswith("#") for r in rules)


def test_proposes_known_checks():
    config, matched, total = propose_config(SAMPLE, source="AGENTS.md")
    checks = _checks_by_kind(config)
    assert any(c["kind"] == "forbidden_command" and "pip install" in c["pattern"] for c in checks)
    assert any(c["kind"] == "required_command" and "pytest" in c["pattern"] for c in checks)
    assert any(c["kind"] == "forbidden_path" and "migrations" in c["pattern"] for c in checks)
    assert any(c["kind"] == "max_final_length" and c["limit"] == 60 for c in checks)
    assert any(c["kind"] == "forbidden_command" and "force" in c["pattern"] for c in checks)
    assert matched >= 5
    assert total > matched  # "Be nice to the reviewer" has no deterministic check


def test_output_round_trips_through_load_config(tmp_path):
    config, _matched, _total = propose_config(SAMPLE, source="AGENTS.md")
    p = tmp_path / "keeptrue.yaml"
    p.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
    scenario, rules, _prices = load_config(str(p))
    assert scenario == "rules from AGENTS.md"
    assert len(rules) == len(config["rules"])
