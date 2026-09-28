# Contributing

Thanks for looking! keeptrue is small on purpose. The best contributions right
now are **new check kinds** and **run adapters** (capturing runs from Claude
Code, Codex, or your own agent loop).

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
keeptrue demo
```

## Regenerating the demo

The demo fixtures are generated, not hand-edited, so they stay reproducible:

```bash
python -m keeptrue._fixtures.build_demo   # rewrites src/keeptrue/_fixtures/demo/
python scripts/render_demo_svg.py          # refreshes docs/demo.svg
```

If you change the fixtures, update the assertions in `tests/test_demo.py` — they
intentionally lock the numbers the README shows.

## Adding a check kind

1. Write a `check_<name>(check, trajectory) -> CheckOutcome` in
   `src/keeptrue/checks.py` and register it in `REGISTRY`.
2. Return honest evidence (the offending command / line), not just a boolean.
3. Add a unit test in `tests/test_checks.py`.

Keep checks **deterministic** — no model calls. Fuzzy, natural-language rules
belong to the optional LLM judge, not here.

## Guidelines

- Format/lint touched files (`ruff format`, `ruff check`) if you have ruff.
- Keep dependencies minimal (`rich`, `pyyaml`).
- Small PRs with a test beat big ones without.

## Reference audits

Use `keeptrue audit prepare` to freeze selected recordings before labeling them.
Never commit `.keeptrue/` or raw session logs. Keep synthetic test fixtures clearly
identified; do not describe them as captured real-world evidence. A new real-data
case study should disclose selection, reviewer provenance, excluded cases and
remaining disagreements. See [the audit guide](docs/reference-audit.md).
