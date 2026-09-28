# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] - 2026-09-28

Initial release.

### Added
- `keeptrue demo` — score bundled, illustrative runs with no API key and no
  cost, showing a model upgrade silently dropping its `pip`→`uv` and `pytest`
  rules.
- `keeptrue check --config … --runs …` — score your own recorded runs.
- `keeptrue init` — write a starter `keeptrue.yaml`.
- Deterministic checks: `forbidden_command`, `required_command`,
  `forbidden_path`, `required_path`, `forbidden_in_diff`, `required_in_diff`,
  `max_final_length`, `no_repeat_loops`.
- Karne report: per-rule adherence across runs, regression callout vs the
  baseline model, and a cost/reliability table (tokens- and $-per-success).
- Trajectory format documented in `docs/trajectory-format.md`.

[0.1.0]: https://github.com/meryemsakin/keeptrue/releases/tag/v0.1.0
