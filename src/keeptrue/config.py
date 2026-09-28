"""Load a keeptrue.yaml into rules + prices + a scenario name."""

from __future__ import annotations

from typing import Any

import yaml

from .models import Check, Rule


def load_config(path: str) -> tuple[str, list[Rule], dict[str, Any]]:
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    scenario = cfg.get("scenario", "")
    prices = cfg.get("prices", {}) or {}

    rules: list[Rule] = []
    for r in cfg.get("rules", []) or []:
        check = None
        c = r.get("check")
        if c:
            kind = c["kind"]
            params = {k: v for k, v in c.items() if k != "kind"}
            check = Check(kind=kind, params=params)
        rules.append(Rule(id=r["id"], text=r["text"], check=check))

    return scenario, rules, prices
