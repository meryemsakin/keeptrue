"""Configuration identities must not silently merge independent rules."""

import pytest

from keeptrue.config import load_config


@pytest.mark.parametrize(
    "rule_ids", [("same", "same"), ("", "other"), ("   ", "other")]
)
def test_rule_ids_must_be_nonempty_and_unique(tmp_path, rule_ids):
    import json

    path = tmp_path / "keeptrue.yaml"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {"id": ident, "text": f"Rule {i}"}
                    for i, ident in enumerate(rule_ids)
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="duplicate rule ID|rule IDs must be nonempty"):
        load_config(str(path))


def test_unique_rule_ids_preserve_both_rules(tmp_path):
    path = tmp_path / "keeptrue.yaml"
    path.write_text("rules:\n- {id: first, text: One}\n- {id: second, text: Two}\n")
    _, rules, _ = load_config(str(path))
    assert [r.id for r in rules] == ["first", "second"]
