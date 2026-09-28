"""Load a keeptrue.yaml into rules + prices + a scenario name."""

from __future__ import annotations

from typing import Any

import yaml

from .models import Check, Rule


def load_config(path: str) -> tuple[str, list[Rule], dict[str, Any]]:
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError("config must be a YAML mapping")

    scenario = cfg.get("scenario", "")
    prices = cfg.get("prices", {}) or {}

    entries = cfg.get("rules", []) or []
    if not isinstance(entries, list):
        raise ValueError("rules must be a list")
    rules: list[Rule] = []
    for r in entries:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not isinstance(r.get("text"), str):
            raise ValueError("each rule must have string id and text fields")
        check = None
        c = r.get("check")
        if c is not None and not isinstance(c, dict):
            raise ValueError(f"{r['id']}: check must be a mapping")
        if c:
            kind = c["kind"]
            params = {k: v for k, v in c.items() if k != "kind"}
            check = Check(kind=kind, params=params)
        rules.append(Rule(id=r["id"], text=r["text"], check=check))

    return scenario, rules, prices
